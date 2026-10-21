# pitwall

Claude Code package for routing work to **non-Claude models** (codex/GPT-6,
Grok 4.7, Kimi, Qwen, GLM, MiniMax, Pi, Hermes, Cline, Muse Code, goose, dsh, ZCode, MiniMax Code, and local/self-hosted models)
over the repo-installed codex/agy/kimi/opencode/grok/qwen/pi/hermes/cline/muse/goose/dsh/zcode/mcode shims. The primary `subagent-model-routing` skill handles
**both** dispatch shapes; an internal §0 decision picks which:

- **Flat dispatch** — independent, one-shot units (no dependency edges). Launch a native `Agent` with `subagent_type: pitwall:<harness>-shim`; the `pitwall-asks` monitor delivers questions and background Agents are allowed.
- **DAG orchestration** — dependency edges (A→B, fan-out you collect/order). Launch ready nodes as shim Agents and carry their artifacts in dependency order. Native `Workflow` scripts containing shim nodes remain denied in routing-active sessions.

**Why one skill, not two:** flat-vs-DAG is a *branch over shared substrate*, not two separate tools. The model-picking logic, the roster, and the transport lore are needed by **both** paths, so they live **once, inline**, in-context for every task — no "referenced but not loaded" gap. (`/pitwall:dag-routing` is the entry command; §0 down-routes a flat task to the flat half on its own.)

## Install and validate

From this repo checkout:

```bash
claude plugin marketplace add <pitwall> --scope user
claude plugin install pitwall@pitwall-local --scope user
claude plugin details pitwall@pitwall-local
claude plugin validate <pitwall>/plugins/claude
```

## What's in the plugin

- `skills/subagent-model-routing/SKILL.md` — the whole thing: §0 router, shared model-picking, **Part A** (DAG orchestration), **Part B** (flat dispatch + shared transport substrate). Companion doc: `ARCHITECTURE.md` (DAG internals).
- `skills/attach-local-endpoint/SKILL.md` — detect a locally hosted model server (Ollama, vLLM, llama.cpp, LM Studio, SGLang, a swapper) and attach it as a route, end to end.
- `commands/dag-routing.md` — the `/pitwall:dag-routing` entry command.
- `commands/distill.md` — the `/pitwall:distill` ledger-promotion command.
- `commands/model-facts.md` — the `/pitwall:model-facts` command: review changed vendor, harness, host and Hugging Face sources and update the model facts.
- `agents/{codex,agy,kimi,opencode,grok,qwen,route,pi,hermes,cline,muse,goose,dsh,zcode,mcode}-shim.md` — the Sonnet transport agent contracts.
- `monitors/monitors.json` — the `pitwall-asks` monitor, which runs `pitwall agents inbox --watch` in every interactive session.
- `hooks/` — the `PreToolUse` steering gate (fails closed; exempts the `pitwall-channel` tools `read_steering`, `ack_steer`, and `ask_orchestrator` by exact server name), the launch guard (once the routing skill or a managed `dispatch_and_wait` call activates the session marker, it denies recognizable raw provider launches and native `Workflow` scripts that contain shim nodes, while shim Agents and `*-shim.sh` commands are allowed because they connect their own channel; its `PostToolUse`, `Stop`, and `SessionEnd` entries release the dispatch leases and clear the marker), and the Stop-hook tripwires (one flags Claude models routed through `route-shim` or `agy-shim`, the other a direct shim run with a non-ok outcome and no ledger note). Disable via the plugin or `hooks/hooks.json`.

## Subagent questions: the `pitwall-asks` monitor

The plugin declares one monitor, `pitwall-asks`, which runs `pitwall agents inbox --watch`. It prints every question a Pitwall subagent asks in this session as a `PITWALL ASK` line, including questions already open when it starts. Answer a tier-1 question with `answer_ask`; answer a tier-4 question and then resume the run with `pitwall agents runs resume <dispatch-id>`.

While it runs, the watcher keeps a heartbeat file fresh under the Pitwall state root (`watchers/<session-id>.json`). That fresh heartbeat is what turns a shim's channel on by itself: a launch with no channel flag connects to this session's orchestrator only while the listener is alive, so a `-p`, SDK, script or CI launch with no listener behaves exactly as before. The heartbeat goes stale after 30 seconds without a refresh.

Monitors run only in interactive sessions; `claude -p`, the SDK, and scripted runs do not start one. A plugin update needs a session restart before the monitor picks up the new manifest. To disable it, disable the whole plugin with `/plugin`, or remove the `pitwall-asks` entry from `monitors/monitors.json` in the installed plugin directory.

## Shared runtime prerequisite

The plugin package does not duplicate the executable runtime. Install the `pitwall` command (`uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl`), then run `pitwall agents install`. The registry has fifteen provider harnesses; install writes their fifteen shims and `route-shim.sh` (sixteen total): `codex-shim`, `agy-shim`, `kimi-shim`, `opencode-shim`, `grok-shim`, `qwen-shim`, `pi-shim`, `hermes-shim`, `cline-shim`, `muse-shim`, `goose-shim`, `dsh-shim`, `zcode-shim`, `mcode-shim`, `route-shim`, and `claude-shim` under `~/.claude/scripts/` (each a two-line wrapper around `pitwall agents _shim`), installs the Claude Code, Codex, and Copilot plugins from the `pitwall-local` marketplace, and registers the channel MCP server as `pitwall mcp serve channel`. `pitwall agents uninstall` removes exactly what install wrote, hook registrations first. This Claude Code package actively routes through every non-Claude shim; `claude-shim` is a target route for the Codex and Copilot packages, while Claude-hosted work uses native Claude `Agent` calls. The steering-gate hook fails closed: when `pitwall` cannot be found, times out, or errors, it blocks the tool call and says why. Recovery must not use a tool call: type `! uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` at the prompt, or disable the plugin with `/plugin`; check the install with `pitwall doctor`. Provider executables are external dependencies, and authentication is never automated. The shared runtime requires Python 3.14.

```bash
test -x ~/.claude/scripts/codex-shim.sh && test -x ~/.claude/scripts/agy-shim.sh && test -x ~/.claude/scripts/kimi-shim.sh && test -x ~/.claude/scripts/opencode-shim.sh && test -x ~/.claude/scripts/grok-shim.sh && test -x ~/.claude/scripts/qwen-shim.sh && test -x ~/.claude/scripts/pi-shim.sh && test -x ~/.claude/scripts/hermes-shim.sh && test -x ~/.claude/scripts/cline-shim.sh && test -x ~/.claude/scripts/muse-shim.sh && test -x ~/.claude/scripts/goose-shim.sh && test -x ~/.claude/scripts/dsh-shim.sh && test -x ~/.claude/scripts/zcode-shim.sh && test -x ~/.claude/scripts/mcode-shim.sh && test -x ~/.claude/scripts/route-shim.sh && echo "shims present"
pitwall agents runs list
pitwall doctor
```

Active routes are GPT via `codex-shim`, Gemini via `agy-shim`, Kimi via `kimi-shim`, Grok 4.7 via `grok-shim`, Qwen via `qwen-shim`, configured Pi/Hermes/Cline/goose endpoints via their respective shims, model-bound Muse Code via `muse-shim`, experimental DeepSeek Harness profiles via `dsh-shim`, ZCode's saved model and account via `zcode-shim`, MiniMax Code via `mcode-shim`, and GLM/MiniMax/other local models via `opencode-shim`. The repo-installed shims log quantitative ledger records with `"source":"shim"` (`event`: `started`/`finished`) and emit a final `SHIM-DONE exit=<n>` sentinel per run.

Named route profiles (`pitwall agents profiles list`, `pitwall agents setup profiles`) dispatch through `route-shim` to any of those harnesses; see `docs/agents/routes.md`.

Write dispatches through any non-Claude shim can opt into `--routing-workspace isolated --routing-task-mode write`; review with `pitwall agents runs diff`, apply explicitly, and discard the retained worktree explicitly. Claude work remains native to Claude Code's Agent/Workflow surfaces.

Claude-hosted external-model work uses native shim Agents. The `pitwall-asks` monitor reports their questions; `dispatch_and_wait` remains available for `-p`, SDK, and other sessions where plugin monitors do not run. The launch guard still denies native Workflow scripts containing shim nodes in routing-active sessions. The shared `pitwall agents workflow run workflow.json --host claude` command is available only for explicitly requested external-only graphs and does not replace `/dag-routing` or the tripwire hooks. The self-declared host value is advisory; observed Claude tool use remains the enforcement signal.

Model discovery is explicit with `pitwall agents doctor --discover-models`; the default doctor and dispatch preflight never run it.

**Tier example (seed — maintain via `/pitwall:distill` and your own ledger):**
codex GPT-6 Sol (provisional flagship seat) ≥ GLM-5.3 (default author) > Kimi K3 > MiniMax M3.1 Flash Preview; Grok 4.7, GPT-6 Astra/Luna, and local/self-hosted models remain unranked pending local evidence.

## Prompt references

The runtime skill includes compact prompt cards for its active codex/GPT, xAI/Grok, Kimi, GLM, MiniMax, and Qwen routes. Its self-contained detail is bundled at `skills/subagent-model-routing/references/model-prompting.md`, so it remains readable from an isolated plugin install. The Claude Code transport and system-card-grounded Claude Sonnet 5.5, Opus 5.5, and Fable 5.1 sections are route guidance for the Codex and Copilot packages; this Claude-hosted package keeps Claude work native. No Mythos-specific reference or route is defined. In a source checkout, the canonical authoring references live in the repo-level `docs/prompting/` directory; start with `docs/prompting/00-prompt-reference-index.md` when updating guidance. Qwen (local llama.cpp endpoints included, configured via `~/.qwen/.env`) routes through the dedicated `qwen-shim`; other local/self-hosted models route through the `opencode-shim` custom-provider path — see the `## Routing a local model` section of `docs/agents/routing-readme.md` for an example, or use the `attach-local-endpoint` skill to detect and attach one automatically.
