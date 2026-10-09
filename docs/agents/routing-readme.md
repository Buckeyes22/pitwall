# Pitwall Agent Routing

You probably pay for more than one AI coding subscription. Delegating a task between Claude Code, Codex, Grok, Kimi, GLM, MiniMax, or a local model still means copy-pasting the prompt, watching another CLI, and pasting the answer back. That friction makes cross-model review easy to skip.

**Pitwall Agent Routing** is the `pitwall agents` command group of the one `pitwall` package. It makes that handoff
explicit with three pieces:

- **Fourteen thin CLI shims** (`codex-shim.sh`, `claude-shim.sh`, `agy-shim.sh`, `kimi-shim.sh`, `opencode-shim.sh`, `grok-shim.sh`, `qwen-shim.sh`, `pi-shim.sh`, `hermes-shim.sh`, `cline-shim.sh`, `muse-shim.sh`, `goose-shim.sh`, `zcode-shim.sh`, and experimental `dsh-shim.sh`), plus `route-shim.sh`, which dispatches a named model/harness profile through any of them, backed by a shared local Python runtime. `agy-shim.sh` routes Gemini through the Antigravity CLI. They dispatch a prompt to another agentic CLI and return the answer with a `SHIM-DONE exit=<n>` sentinel, while retaining private run records for inspection and recovery.
- **A routing skill** that defines when to delegate, which model to use, and how to phrase the dispatch, plus **tripwire guardrails** that catch silent delegation failures (missing sentinel, nonzero exit, truncated output) before they enter your context as usable results.
- **A model ledger** that records every dispatch's wall time, exit code, and outcome, so routing decisions improve from your own observations rather than someone else's defaults.

It's built for **Claude Code, Codex, and GitHub Copilot CLI users** who want to route work through the other installed agentic CLIs. Each host stays native for its own model family: Claude Code uses native Claude agents, Codex keeps Codex work inline, and shims handle cross-harness delegation.

`pitwall agents install` writes the `pitwall` shim layer under `~/.claude/scripts/`: the fourteen shims plus `route-shim.sh`. Each shim is a two-line wrapper around `pitwall agents _shim`. The shims are `codex-shim.sh` (GPT models via Codex), `claude-shim.sh` (Claude models via Claude Code), `agy-shim.sh` (Gemini through Antigravity CLI), `kimi-shim.sh` (Kimi models via Kimi Code), `grok-shim.sh` (Grok via Grok Build), `qwen-shim.sh` (Qwen and any OpenAI-compatible endpoint via Qwen Code, local llama.cpp included), `opencode-shim.sh` (GLM, MiniMax, local models, and any OpenCode provider), `pi-shim.sh`, `hermes-shim.sh`, `cline-shim.sh`, `muse-shim.sh`, `goose-shim.sh`, `zcode-shim.sh` (the saved ZCode model/account), and experimental `dsh-shim.sh`. `route-shim.sh <name[@harness]> <prompt-file>` dispatches a named agent profile.

## How this compares

**Isn't this just an LLM router?** No. Routers like LiteLLM or OpenRouter multiplex API requests to a single endpoint — you send a completion request, they pick a backend and proxy it. Agent Routing operates a layer above that: it delegates whole units of agentic work to full CLI harnesses, each with its own tools, workspace access, and subscription auth, then verifies the work actually finished. Nothing here proxies API calls.

**Why not just use OpenCode directly?** You can — and this project composes with OpenCode rather than replacing it. OpenCode remains the generic harness for GLM, MiniMax, and local/custom providers, while Kimi and Qwen can use their dedicated Kimi Code and Qwen Code harnesses. What Agent Routing adds on top is the delegation doctrine, completion verification through the sentinel contract, guardrails against silent delegation failure, and a ledger that learns which model to trust for what from your own outcomes.

**Why not do everything in Claude Code?** If one subscription covers all your usage, you don't need this. It exists for people who hold several model subscriptions and want their orchestrator to spend each one where it is strongest — without copy-pasting prompts between terminals by hand.

## Prerequisites

- **Python 3.14** and `uv`. Installing the release wheel with `uv tool install` puts the `pitwall` command on your `PATH` with its own interpreter.
- Nothing else: the shims run Python-supervised child process groups themselves, so there is no `timeout` or `gtimeout` prerequisite.
- **Provider CLIs**: install any transport harnesses you plan to use—[Codex](https://github.com/openai/codex), [Claude Code](https://code.claude.com/docs/en/getting-started), [Antigravity CLI](https://antigravity.google/docs/cli/install/), [Grok Build](https://docs.x.ai/build/overview), [Kimi Code](https://moonshotai.github.io/kimi-code/), [Qwen Code](https://github.com/QwenLM/qwen-code), [OpenCode](https://opencode.ai), Pi, Hermes Agent, Cline CLI, [Muse Code](https://developer.meta.com/ai/products/muse-code/), [goose](https://goose-docs.ai/docs/getting-started/installation/), ZCode, and experimental dsh. Interactive bootstrap offers a checkbox installer for missing CLIs; manual provider installation remains supported.
- **Provider authentication** remains a separate post-install step: `codex login`, `claude auth login`, `grok login` (or `XAI_API_KEY`), `kimi login`, `opencode auth login`, `hermes setup`, and `cline auth`. Muse 1.3+ API-key setup reads from stdin with `muse auth set --provider meta --api-key-stdin`; do not put the key in command arguments. OpenCode also needs at least one configured provider (`opencode models`); Qwen Code reads its endpoint and key from `~/.qwen/.env`; Pi verifies its configured provider with `pi --list-models`, dsh endpoint credentials are an `apiKeyEnv` in its generated profile, and Antigravity CLI (`agy`) authenticates through an interactive sign-in flow (or `GEMINI_API_KEY` in API-key mode).
- GitHub Copilot CLI users also need that CLI installed; the transport shims invoke Codex, Claude Code, Kimi Code, Grok Build, Qwen Code, or OpenCode independently of the host client.

## Install

Install Pitwall once, then let it wire the hosts:

```bash
uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl
pitwall agents install
```

`pitwall agents install` writes exactly these things and records each one in an install manifest
(`${XDG_DATA_HOME:-~/.local/share}/pitwall/install-manifest.json`):

- `~/.claude/scripts/<harness>-shim.sh` for every registered harness, plus `route-shim.sh`;
- the `claude`, `codex`, and `copilot` plugins under `${XDG_DATA_HOME:-~/.local/share}/pitwall/plugins/`, with the generated profile references copied in and one `pitwall-local` marketplace manifest per host;
- the `pitwall-channel` MCP server, registered as `pitwall mcp serve channel`, for every detected harness (`--harness` limits this, repeatable).

It then registers the plugins with each host CLI found on `PATH` (`--plugin-host` limits this, repeatable). It refuses to overwrite a file that is neither its own nor identical to what it would write. `pitwall agents uninstall` reads the manifest and removes exactly that, hook registrations first.

By default, the shims keep each dispatched CLI's own sandbox and approval prompts. Unattended runs that must not stop for approval need an explicit `PITWALL_AGENTS_UNRESTRICTED=1`, which passes the CLI's bypass flag (for Codex, `--dangerously-bypass-approvals-and-sandbox`). Kimi Code and dsh refuse to dispatch without it because their prompt modes have no restricted form. The [Security note](#security-note) covers the tradeoff.

Install any missing harness CLIs with the optional selector:

```bash
pitwall agents setup harnesses
pitwall agents setup harnesses --dry-run
```

Provider installation never performs authentication or model/provider configuration. See [optional harness CLI setup](harness-cli-setup.md) for sources, platform support, failure recovery, and security controls.

A workstation that ran the standalone Agent Routing tool runs `pitwall agents migrate` once; see the [migration guide](../operator/agents-migration.md).

The host sections below show the plugin commands `pitwall agents install` runs, for the cases where you register a plugin by hand. Replace `<plugins>` with `${XDG_DATA_HOME:-~/.local/share}/pitwall/plugins`.

### Claude Code

```bash
claude plugin marketplace add --scope user <plugins>
claude plugin install pitwall@pitwall-local --scope user
```

Useful management and validation commands:

```bash
claude plugin marketplace list
claude plugin marketplace update pitwall-local
claude plugin list
claude plugin details pitwall@pitwall-local
claude plugin validate <plugins>/claude
```

The install enables the plugin automatically; if `claude plugin list` ever shows it disabled, re-enable with `claude plugin enable pitwall@pitwall-local --scope user`.

When refreshing a local checkout that keeps the same plugin version, `claude plugin marketplace update pitwall-local`
may report no update while the cached skill still comes from the prior marketplace source. Force a
same-version local refresh by removing and reinstalling only this plugin, retaining its data:

```bash
claude plugin uninstall --keep-data --scope user pitwall@pitwall-local
claude plugin install pitwall@pitwall-local --scope user
claude plugin details pitwall@pitwall-local
```

Use the marketplace path in the preceding `marketplace add` command before reinstalling. Verify
the reported source and installed files before starting a new Claude Code session.

Manual settings equivalent if the CLI marketplace flow is unavailable:

```json
{
  "extraKnownMarketplaces": {
    "pitwall-local": {
      "source": { "source": "directory", "path": "<plugins>" }
    }
  },
  "enabledPlugins": { "pitwall@pitwall-local": true }
}
```

Then restart Claude Code (or `/hooks`) to load hook changes.

### Codex

```bash
codex plugin marketplace add <plugins>
codex plugin add pitwall-codex@pitwall-local
```

Useful management and validation commands:

```bash
codex plugin marketplace list
codex plugin list
python3 -m json.tool <plugins>/.agents/plugins/marketplace.json >/dev/null
python3 -m json.tool <plugins>/codex/.codex-plugin/plugin.json >/dev/null
```

After changing the Codex package, reinstall it and start a new Codex thread so the updated skill is loaded from the plugin cache.

If the local marketplace has the same name as an existing marketplace but points at a different source,
`codex plugin marketplace add` rejects the source switch. Remove only that marketplace and its plugin,
then add the intended local source again:

```bash
codex plugin remove pitwall-codex@pitwall-local
codex plugin marketplace remove pitwall-local
codex plugin marketplace add <plugins>
codex plugin add pitwall-codex@pitwall-local
codex plugin list
```

This preserves unrelated Codex marketplaces. Start a new Codex thread after the reinstall so the plugin
cache cannot retain the old same-version package.

### GitHub Copilot CLI

```bash
copilot plugin marketplace add <plugins>
copilot plugin install pitwall-copilot@pitwall-local
```

Useful management commands:

```bash
copilot plugin marketplace list
copilot plugin marketplace browse pitwall-local
copilot plugin marketplace update pitwall-local
copilot plugin list
copilot plugin update pitwall-copilot@pitwall-local
copilot plugin uninstall pitwall-copilot
```

## Quickstart

### 1. Transport smoke test

Confirm a shim can reach a model and return the sentinel (substitute any configured provider/model):

```bash
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/opencode-shim.sh <provider/model> -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/kimi-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/grok-shim.sh - --effort low
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/qwen-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/claude-shim.sh - --model haiku
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/pi-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/hermes-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/cline-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/muse-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/goose-shim.sh -
printf 'Reply with exactly: pong\n' | ~/.claude/scripts/dsh-shim.sh -
```

Expected output:

```
pong
SHIM-DONE exit=0
```

The body before the sentinel varies by child CLI. The `SHIM-DONE exit=<n>` line is the stable tripwire: if it is missing or reports a nonzero exit, the dispatch failed and the skill will not treat the output as successful.

For machine-readable transport metadata, opt in with `SHIM_RESULT=1`:

```bash
printf 'Reply with exactly: pong\n' | SHIM_RESULT=1 ~/.claude/scripts/claude-shim.sh - --model haiku
```

A completed run then ends with this pair:

```
SHIM-RESULT {"ts":"...","shim":"claude","model":"haiku","event":"finished","exit":0,"wall_s":2,"outcome":"ok","profile":"unrestricted","dispatch_id":"...","source":"shim"}
SHIM-DONE exit=0
```

The receipt is the exact `finished` ledger record for that `dispatch_id`. Only a `SHIM-RESULT` immediately before the final `SHIM-DONE` is authoritative, because the child controls its own stdout and can print lookalike lines earlier. Read only the last two lines and cross-check the receipt's exit against the sentinel's; `pitwall.agents.result:parse_shim_receipt` implements exactly that check and rejects any mismatch.

The receipt proves transport completion and reports the policy profile the child actually ran under. It does not prove the requested files are correct or that project checks passed. Failures before dispatch begins — usage errors and a missing process supervisor — write no ledger record and so emit only `SHIM-DONE`.

A usage error exits `64`, prints the reason and the harness's usage line to stderr, and ends stdout with `SHIM-DONE exit=64`. A word that begins with `-` in the prompt-source position (or, for OpenCode, in the `<provider/model>` position) is a usage error, not a prompt file: `codex-shim.sh -m gpt-6.1-sol prompt.md` fails with `'-m' is a flag, not a prompt source`, because the prompt source comes first. Pass a file whose name begins with `-` as `./-name`, and stdin as `-`. A leading `-h` or `--help` prints the harness's usage line and `SHIM-DONE exit=0` for every harness, creating no run record and no ledger row. The `SHIM-DONE` line is always the last line on stdout, including after `--help` text and usage errors.

### 2. First routed dispatch from Claude Code

With the Claude Code plugin installed, ask in natural language:

> Use subagent-model-routing to send a review of src/parser.ts to kimi, get a second opinion from codex, and compare the findings.

The routing skill triggers automatically on routing or delegation requests; you do not invoke it by hand.

### 3. Workflow DAG orchestration

For multi-step delegated work that should run as a dependency graph:

```
/pitwall:dag-routing review src/parser.ts across kimi and codex, then summarize
```

### 4. Direct-shell usage (Codex / Copilot CLI)

Write a prompt to a file and dispatch it:

```bash
export PATH="$HOME/.claude/scripts:$PATH"   # pitwall agents install writes the shims there
opencode-shim.sh <provider/model> prompt.md
kimi-shim.sh prompt.md --model kimi-code/k3
grok-shim.sh prompt.md --effort medium
qwen-shim.sh prompt.md
claude-shim.sh prompt.md --model opus
pi-shim.sh prompt.md
hermes-shim.sh prompt.md
cline-shim.sh prompt.md
muse-shim.sh prompt.md
goose-shim.sh prompt.md
dsh-shim.sh prompt.md
```

Each shim prints the model's answer followed by `SHIM-DONE exit=<n>`. Use `codex-shim.sh` for GPT models, `claude-shim.sh` for Claude models, `kimi-shim.sh` for Kimi Code, `grok-shim.sh` for Grok 4.7, and `qwen-shim.sh` for Qwen Code against any OpenAI-compatible endpoint. Pi, Hermes, Cline, goose, and dsh are model-agnostic alternatives; Muse is model-bound, Cline supports one custom endpoint, and dsh is experimental.

Direct shim calls use the current directory by default. For an implementation that must not touch the caller's worktree, opt into an isolated Git worktree and declare that the task writes:

```bash
codex-shim.sh prompt.md --routing-workspace isolated --routing-task-mode write
pitwall agents runs diff <dispatch-id>
pitwall agents runs apply <dispatch-id> --target <repo>
pitwall agents runs discard <dispatch-id> --yes
```

Nothing is applied or discarded automatically.

### 5. Durable dependency workflow (Codex / Copilot)

For a dependency graph that needs bounded concurrency, context handoff, retries, verification, cancellation, or resume, use the host-neutral runner:

```bash
pitwall agents workflow run workflow.json --host copilot
pitwall agents workflow list
pitwall agents workflow show <workflow-id>
pitwall agents workflow cancel <workflow-id>
pitwall agents workflow resume <workflow-id> --host copilot
```

Codex-hosted workflows keep Codex work inline and may contain Claude, Grok, Kimi, OpenCode, and Pi transport tasks — the workflow-capable set the durable runner dispatches today; other registered providers are rejected at validation. Copilot-hosted workflows accept the same set. Claude Code continues to prefer native Workflow; its external-only runner usage does not replace the tripwire hooks. See [host-neutral workflows](workflows.md).

### 6. Agent profiles

```bash
pitwall agents harnesses
pitwall agents setup inventory
pitwall agents profiles add glimmer --model meta-models/Muse-Glimmer-30B --base-url http://gpu-1:8000/v1 --api-key-env GLIMMER_API_KEY --seat local
pitwall agents profiles add sol --model gpt-6.1-sol --seat critical --effort high
pitwall agents profiles list
pitwall agents dispatch route glimmer prompt.md
```

See **[Agent profiles](routes.md)** for harness overrides, endpoint syncing, and validation details.

### 7. Orchestrator channel (asks and steering)

For a managed interactive external-model task, the parent calls the `pitwall-channel` MCP tool `dispatch_and_wait`. The tool returns a child ask, a terminal receipt, or a bounded `still_running` handle. The parent answers an ask with `answer_and_wait` and can reattach with `wait_dispatch` or steer with `steer_and_wait`. This keeps the question in the parent's normal tool-result flow; the parent does not need a separate inbox-polling routine. The child must have its MCP channel configured before a managed interactive launch.

The CLI remains available for recovery and older dispatches:

```bash
pitwall agents setup mcp                       # register the pitwall-channel MCP server (`pitwall agents install` does this too)
pitwall agents inbox                           # unresolved asks and unacknowledged steering across runs
pitwall agents answer <dispatch-id> <ask-id> <choice> --note "why"
pitwall agents steer <dispatch-id> --kind scope --message "SQLite only"
pitwall agents runs stop <dispatch-id>         # graceful stop with a grace window
pitwall agents runs resume <dispatch-id>       # continue a paused dispatch (expired asks take their default)
```

MCP registration enables the child-side tools but does not by itself prove an end-to-end parent-child exchange. Harnesses without the child MCP channel use the file-backed pause/resume contract. Asks past their deadline take their stated default automatically. See **[the orchestrator channel](orchestrator-channel.md)**.

`setup inventory` reads local harness configuration on demand. It reads known config files and capability directories for detected harnesses, retaining only names of MCP servers, plugins, skills, agents, commands, extensions, and tools. It writes private JSON plus a compact model-context Markdown file under `${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/` (`harness-capabilities.json` and `harness-capabilities.md`); the three host skills consult that snapshot before routing. Config values, environment values, credentials, and file contents from skill/agent directories are never copied. Re-run the command after changing a harness configuration.

## Day-to-day use

In normal use you don't run the shims by hand — you ask in plain language: "route this to Claude", "get Grok's second opinion", or "fan this review out across Codex and GLM and compare". The routing skill decides the shape of the work: independent one-shot dispatches run flat, Claude-hosted dependency graphs use native Workflow by default, and Codex/Copilot can execute durable JSON graphs through `pitwall agents workflow`.

Set expectations on time. A routed dispatch is a full agentic run: the external model reads files, edits, and runs checks in your workspace, then reports back. Substantive dispatches typically take several minutes, not seconds. The shim enforces a per-run ceiling of `PITWALL_AGENTS_TIMEOUT_SECS` (default 1140s, ~19 min), so a single dispatch that runs longer than that will be clipped.

Ctrl+C in the terminal running a shim stops the dispatch cleanly: the harness's process group gets SIGTERM, then SIGKILL after a short grace, the run is recorded as `cancelled`, and the shim exits `130` with `SHIM-DONE exit=130`. A harness is never left running behind a Ctrl+C. See [run records](run-records.md#interrupts-and-exit-codes).

The important part — the two Stop-hook nudges. After a turn that dispatched shims, you may see one of two messages appear. Both are normal operation, not errors.

- **Ledger nudge** — a line beginning `pitwall LEDGER: shims were dispatched this turn and no ledger note was written…`. It's asking whether anything notable happened on that dispatch. Claude answers it — noting anything notable, or stating nothing was — and continues. The ledger improves only if you actually feed it observations.
- **DAG tripwire** — if a DAG-shaped request ran as flat direct dispatches instead, you may see `dag-routing TRIPWIRE: …`, which asks Claude to either redo the work via the Workflow tool or state that flat dispatch was deliberate. Usually it was deliberate, and saying so clears it.

These are advisory, fail-open guardrails. Seeing one is the system working as designed — it never blocks your turn. If you'd rather not see them at all, disable the plugin entirely, or delete the `Stop` entries in `plugins/claude/hooks/hooks.json`.

## Routing a local model

Running a model on your own hardware — Ollama, vLLM, llama.cpp, LM Studio, SGLang, or a swapper like llama-swap? `python3 tools/agents/detect_local_endpoints.py` finds it, names the product, and prints the `pitwall agents profiles add`/`profiles discover` commands to attach it as a named agent profile. See [Attach a locally hosted model](attach-local-endpoint.md) for the full walkthrough — cold starts, tool-calling flags, per-harness caveats — or hand an agent the `attach-local-endpoint` skill. This path works with any harness that takes a custom endpoint: `qwen`, `goose`, `opencode`, `pi`, `cline`, `dsh`, and `hermes`.

OpenCode also supports custom providers directly, if you would rather configure one by hand: add a local llama.cpp server (or any OpenAI-compatible endpoint) to `~/.config/opencode/opencode.json` as described in the [OpenCode providers docs](https://opencode.ai/docs/providers):

```jsonc
// ~/.config/opencode/opencode.json
{
  "provider": {
    "local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Local llama.cpp",
      "options": { "baseURL": "http://localhost:8080/v1" },
      "models": {
        "my-model": { "name": "My local model" }
      }
    }
  }
}
// then: ~/.claude/scripts/opencode-shim.sh local/my-model <prompt-file>
```

The shim will dispatch the prompt file to `local/my-model` and return the result with `SHIM-DONE exit=<n>`.

## OpenCode Go subscription models

OpenCode's $10/month [Go plan](https://opencode.ai/docs/go/) serves a curated model list (Kimi, GLM, DeepSeek V4, MiniMax, Qwen, LongCat, MiMo, Tencent Hy, Grok 4.6, GPT 5.6 Luna, Muse Spark Contributor, and more) through the `opencode-go` provider. After `/connect` selects OpenCode Go, route any of them with `opencode-shim.sh opencode-go/<model-id> <prompt-file>` or a named route entry. Request budgets vary by orders of magnitude between tiers, and the Muse Spark Contributor tiers may train on your prompts. See **[OpenCode Go subscription models](opencode-go.md)** for the full model table, per-tier facts, and the open-weight cross-references into the Pitwall dossiers.

For a model served on a leased GPU through Pitwall, use the explicit serve →
route → dispatch flow instead: `pitwall agents profiles add <name> --from-pitwall
<capability>`, then `pitwall agents dispatch route <name> prompt.md`. The [Pitwall handoff
guide](pitwall.md) covers its environment, liveness probe, lease expiry,
and no-silent-reroute failure behavior.

## Updating and uninstalling

### Update

```bash
uv tool upgrade pitwall
pitwall agents install
```

`pitwall agents install` is idempotent: it rewrites only the files it owns and refreshes the plugin registrations. Then refresh the Claude Code marketplace so the plugin picks up the new version:

```bash
claude plugin marketplace update pitwall-local
```

### Uninstall

```bash
pitwall agents uninstall
```

It reads the install manifest and removes exactly what `install` wrote: the shims, the plugin copies and marketplace manifests, the `pitwall-channel` MCP registrations, and the hook registrations (hooks first). Profiles in `pitwall.toml`, run records, events, workflows, and plugin data are retained for review.

## Troubleshooting

- Smoke test returns nothing or no pong → the provider is not authenticated or named wrong; run `kimi login`, `opencode models`, `codex login`, `claude auth status`, or `grok login` for the selected route and retry.
- Provider setup partially failed → rerun `pitwall agents setup harnesses`; successful installs are detected and disabled, so only missing CLIs remain selectable. Use `--dry-run` to download each installer and check its pinned SHA-256 without running it.
- Provider setup was skipped in CI or a pipe → this is expected without `/dev/tty`; run `pitwall agents setup harnesses` later from a terminal.
- A same-version local Claude refresh still loads an older skill → run the scoped uninstall/install sequence in the Claude Code section; an update that reports no change does not prove the cached files came from the new source.
- Codex marketplace add reports a same-name/different-source conflict → remove only `pitwall-codex@pitwall-local` and `pitwall-local`, add the intended marketplace, and reinstall as shown in the Codex section.
- Output ends without `SHIM-DONE exit=<n>` → the run was clipped or timed out; split the task or raise `PITWALL_AGENTS_TIMEOUT_SECS` deliberately.
- The skill doesn't trigger → mention the routing skill or a model by name in your ask, or use `/pitwall:dag-routing` directly.
- A `LEDGER:` or `TRIPWIRE:` message appeared → that's the guardrail layer working; see [Day-to-day use](#day-to-day-use).

## Configuration

### Shim environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PITWALL_AGENTS_TIMEOUT_SECS` | `1140` (~19 min) | per-dispatch wall ceiling enforced by process-group supervision; raise deliberately for long jobs |
| `SHIM_RESULT` | `0` | `1` emits the `finished` ledger record as `SHIM-RESULT <json>` immediately before `SHIM-DONE` |
| `PITWALL_AGENTS_UNRESTRICTED` | `0` (unset) | `1` = bypass the child CLI's sandbox/approval prompts (unattended dispatch); unset or `0` = keep the CLI's own policy |
| `PITWALL_AGENTS_LEDGER` | `${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/ledger/observations.jsonl` | where quantitative dispatch records append |
| `PITWALL_AGENTS_HOME` | `~/.local/share/pitwall` | Pitwall repository root considered by Claude `/distill` after the current Git checkout |
| `PITWALL_AGENTS_STATE_HOME` | `${XDG_STATE_HOME:-~/.local/state}/pitwall/agents` | optional override for the whole state root: run records, mailbox, receipts, usage cache, ledger, and the global lifecycle event stream |
| `PITWALL_AGENTS_PROFILES` | the `pitwall.toml` the settings loader uses (`PITWALL_CONFIG_FILE`, `./pitwall.toml`, else `${XDG_CONFIG_HOME:-~/.config}/pitwall/pitwall.toml`) | alternative file holding the `[agents.profiles]` tables; see [Agent profiles](routes.md) |
| `PITWALL_AGENTS_ASK_SUPPORT` | unset | set to `1` to opt a dispatch into the orchestrator channel (same as `--routing-ask-support`); see [the orchestrator channel](orchestrator-channel.md) |
| `PITWALL_AGENTS_MAX_ASKS` | `5` | per-run ask cap when ask support is on, `1`–`50` (same as `--routing-max-asks`) |
| `PITWALL_AGENTS_ABORT_GRACE_SECS` | `10` | grace seconds between SIGTERM and SIGKILL on a graceful abort (SIGTERM, SIGHUP, or an ignored `stop` steer) |
| `PITWALL_AGENTS_CHANNEL_DISPATCH_ID` / `_STATE_ROOT` / `_ATTEMPT` | set by the dispatcher | identify the run to the `pitwall-channel` MCP server; a UUID in the first selects the subagent tool set, its absence the orchestrator tools |
| `MCP_TOOL_TIMEOUT` | `3660000` for Claude Code dispatches | MCP tool timeout forwarded to the harness so a blocking tier-1 ask can outlast it |
| `CODEX_BIN` / `CLAUDE_BIN` / `AGY_BIN` / `KIMI_BIN` / `GROK_BIN` / `QWEN_BIN` / `OPENCODE_BIN` / `PI_BIN` / `HERMES_BIN` / `CLINE_BIN` / `MUSE_BIN` / `GOOSE_BIN` / `ZCODE_BIN` / `DSH_BIN` | auto-detected | explicit executable override per harness (e.g. `CODEX_BIN` overrides the Codex binary; each defaults to the CLI on `PATH`) |
| `KIMI_MODEL_NAME` | unset | Kimi Code's documented temporary model override; lower priority than a shim `-m`/`--model` argument and higher priority than `config.toml` |
| `KIMI_DISABLE_TELEMETRY` | unset | set to `1` to disable Kimi Code's native anonymous telemetry; shared shim run records remain enabled |
| `OPENCODE_OTLP_ENDPOINT` | unset | setting it enables opencode telemetry and auto-fills companion vars (see [Observability](#observability)) |
| `OTEL_RESOURCE_ATTRIBUTES` | unset | codex-shim appends `gen_ai.request.model=<model>` for span attribution |

### Run records and lifecycle hooks

Each accepted dispatch gets a UUID and a private directory under `${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/runs/`. Inspect it without parsing shim output:

```bash
pitwall agents runs list
pitwall agents runs show <dispatch-id-or-unique-prefix>
pitwall agents runs logs <dispatch-id-or-unique-prefix> --channel both
pitwall agents runs resume <dispatch-id-or-unique-prefix>
pitwall agents runs cleanup --older-than 30
```

A dispatch that opted into the orchestrator channel adds operator verbs over the same run records: `inbox` lists unresolved asks and unacknowledged steering, `answer` resolves an ask, `steer` and `runs stop` redirect or gracefully stop a live run, and `runs resume` continues a paused dispatch with its answers. See [the orchestrator channel](orchestrator-channel.md).

Directories use mode `0700` and files use `0600`. `request.json` retains only prompt metadata and a SHA-256 digest by default; pass `--routing-retain-prompt` to a shim only when you explicitly want `prompt.md` retained. Provider stdout and stderr are always logged because they are needed for recovery, and may themselves contain source code or secrets.

Portable lifecycle hooks are configured in `${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/hooks.json`. Hook commands are argument arrays, receive event JSON on stdin, have independent timeouts, and fail open. Their stdout and stderr are captured below the run directory and never mixed with provider output or printed after the sentinel. See [Lifecycle hooks](lifecycle-hooks.md).

### Doctor and isolated worktrees

The default doctor is local, changes nothing itself, and performs no live model discovery. It does run each installed harness's local help and version commands, and some harness CLIs create their own first-run, state, or log files when invoked, which is most visible on a fresh home directory:

```bash
pitwall agents doctor
pitwall agents doctor --json
pitwall agents doctor --harness codex
pitwall agents doctor --installation-only
pitwall agents doctor --harness claude --live-auth
pitwall agents doctor --discover-models
pitwall agents doctor --harness opencode --discover-models
pitwall agents doctor --harness kimi --discover-models
```

`--live-auth` is required before a documented local authentication-status probe can run. It never initiates login or credential changes. Antigravity has no local status command, so its explicit `--live-auth` path sends one bounded pong inference request and may consume provider usage; the default doctor never sends that prompt. Kimi Code exposes no read-only authentication-status command, so its auth check remains `SKIP`; the default Kimi provider check instead runs the documented, non-mutating `kimi doctor config`. `--discover-models` is separately explicit: OpenCode invokes `opencode models`, Kimi invokes `kimi provider list --json` and retains only validated model-alias keys, and Codex reads its CLI-managed local model cache. Changed output or unavailable discovery is a warning, and the default doctor/preflight never discovers models. Unknown IDs remain pass-through wherever the registry permits them.

Workspace flags are additive and removed before provider arguments are forwarded:

- `--routing-workspace shared|isolated|auto`
- `--routing-task-mode read|write` (`auto` refuses to guess when this is absent)
- `--routing-base <commit>` (required when isolating from a dirty source worktree)

Channel flags are additive the same way:

- `--routing-ask-support` (or `PITWALL_AGENTS_ASK_SUPPORT=1`) — let the dispatch ask blocking questions mid-run
- `--routing-max-asks N` — cap asks per run (`1`–`50`, default `5`)

Isolated branches and worktrees remain available for review until `runs discard`. `runs cleanup` deliberately skips active worktrees. See [Doctor](doctor.md), [isolated worktree dispatch](worktree-dispatch.md), and [host-neutral workflows](workflows.md).

### Installer selection

- `pitwall agents install --harness <id>` (repeatable) limits which harnesses get the `pitwall-channel` MCP registration; the default is every detected channel harness.
- `pitwall agents install --plugin-host claude|codex|copilot` (repeatable) limits which host CLIs get plugin registration; the default is every host CLI found on `PATH`.
- `PITWALL_AGENTS_HOME` names the Pitwall source checkout that Claude `/distill` considers after the current Git checkout.

### Choosing models per dispatch

- **opencode-shim** — the first argument IS the model: any `provider/model` from `opencode models`. Extra flags after the prompt file are forwarded to `opencode run` (e.g. `--variant high`, `--thinking`).
- **kimi-shim** — model precedence is explicit `-m`/`--model`, then `KIMI_MODEL_NAME`, then `default_model` from `${KIMI_CODE_HOME:-~/.kimi-code}/config.toml`. It invokes Kimi's non-interactive `--prompt` mode with text output. That mode applies Kimi's auto permission policy while preserving static deny rules, so the shim rejects the incompatible `-y`/`--yolo`/`--auto` flags and reserves `-p`/`--prompt`/`--output-format` for its own transport contract. Kimi Code exposes no per-invocation effort flag in this CLI surface.
- **codex-shim** — uses the default model in `$CODEX_HOME/config.toml` (default `~/.codex`); override per dispatch with `-m <model>` (or `--model=<model>`) and reasoning effort with `-c model_reasoning_effort=<effort>`. Current Codex runtime model IDs are `gpt-6-astra` for Astra, `gpt-6-sol` for Sol (demanding agents), and `gpt-6-luna` for Luna (fast and affordable); the GPT-5.6 IDs `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` stay registered. The shim is a generic pass-through and will forward other model IDs accepted by the installed Codex CLI.
- **grok-shim** — defaults to `grok-4.7`; override with `-m <model>` or `--model=<model>`, and select Grok 4.7 reasoning effort with `--effort low|medium|high|xhigh` (xAI's default is `high`). Extra flags after the prompt source are forwarded to `grok`.
- **claude-shim** — defaults to the latest `sonnet` alias; use Sonnet 5.5 as the normal workhorse, `--model opus` for difficult or verification-heavy work, and `--model fable` for the hardest generally available Claude work where its safeguards and possible fallback are acceptable. `--model haiku` and full-name overrides remain available, and `--effort` must be compatible with the selected model. The shim runs print mode with text output and no session persistence; extra flags such as `--max-turns` or `--max-budget-usd` are forwarded. This project intentionally defines no Mythos-specific reference or route.

### Where the deeper config lives

- **opencode providers/auth** → the [Routing a local model](#routing-a-local-model) section + the [OpenCode docs](https://opencode.ai/docs/providers)
- **Kimi models/auth** → the [Kimi Code CLI docs](https://moonshotai.github.io/kimi-code/)
- **Model tiers and capability cards** → the [Ledger](#the-ledger-and-pitwalldistill) section (`/pitwall:distill`)
- **Guardrail hooks** → `plugins/claude/hooks/hooks.json` (delete `Stop` entries or disable the plugin to turn them off)

## Security note

The shims keep each child CLI's own sandbox and approval policy unless `PITWALL_AGENTS_UNRESTRICTED=1` is set explicitly. That setting bypasses the sandbox or interactive approval prompts where the CLI supports a compatible flag. Kimi's `--prompt` mode is inherently unattended and applies its auto approval policy while retaining static deny rules; it has no compatible restricted-mode switch, so the Kimi shim (like dsh) refuses to dispatch unless `PITWALL_AGENTS_UNRESTRICTED=1` is set, and it rejects `-y`/`--yolo`/`--auto` instead of forwarding conflicting flags. Grok Build's sandbox is off by default, so forward an explicit policy such as `--sandbox workspace` when you need isolation. A Git worktree separates files from the caller's checkout but is not a container or permission boundary. Review model-generated patches before applying them.

The optional provider selector is a separate explicit mutation surface. It downloads only checked, missing providers from fixed first-party HTTPS URLs, validates every redirect hop and the final host, bounds size, verifies a reviewed SHA-256 digest when one is available, shows the source and checksum status, confirms again, executes a private temporary script without shell interpolation, and never logs in. A provider whose endpoint could not be checksum-pinned is labeled with an explicit warning and still fails closed on unexpected redirects. Review the full [provider setup security boundary](harness-cli-setup.md#security-boundary) before using it.

## Observability

The built-in, always-on layers are the ledger JSONL (`${XDG_STATE_HOME:-~/.local/state}/pitwall/agents/ledger/observations.jsonl`) and the structured run store. `started` records mark the beginning of dispatch and `finished` records carry `wall_s`, `exit`, and `outcome`; additive fields include the dispatch UUID, the active policy `profile`, and whether the Python supervisor actually fired its timeout. Set `SHIM_RESULT=1` when a caller needs that `finished` record on stdout instead of reading the ledger.

For OpenCode spans, export `OPENCODE_OTLP_ENDPOINT` before dispatch and the shim fills the companion telemetry variables:

```bash
export OPENCODE_OTLP_ENDPOINT=http://localhost:4318
```

Spans flow to any OTLP-compatible collector or an observability platform like Langfuse.

For Codex spans, Codex honors standard `OTEL_*` environment configuration, and the shim appends `gen_ai.request.model` to `OTEL_RESOURCE_ATTRIBUTES` so each dispatch carries model attribution.

Kimi Code does not expose an OTLP export surface in its documented CLI configuration. Set `KIMI_DISABLE_TELEMETRY=1` to disable its native anonymous telemetry; the shim's local ledger and private run records still provide dispatch attribution and outcomes.

Nothing is emitted unless you configure a collector.

## The ledger and `/pitwall:distill`

The shim logs `event: started` when dispatch begins and `event: finished` when it ends; finished records carry `wall_s`, `exit`, and `outcome`. Rankings, model cards, and capability claims in this repo are seed examples only. Run `/pitwall:distill` to promote your own observations into the warm-tier skill cards and rankings, then evolve them from your own records instead of treating defaults as authority.

## Documentation

- **Per-client guides** — [Claude Code package](../../plugins/claude/README.md), [Codex package](../../plugins/codex/README.md), [GitHub Copilot CLI package](../../plugins/copilot/README.md): install details, what each package contains, and client-specific usage.
- **[The routing skill](../../plugins/claude/skills/subagent-model-routing/SKILL.md)** — the full doctrine: the flat-vs-DAG decision, model picking, dispatch patterns, failure modes, and the routing gates. This is what Claude loads at runtime.
- **[Architecture internals](../../plugins/claude/skills/subagent-model-routing/ARCHITECTURE.md)** — how a Workflow DAG node actually reaches an external model, layer by layer.
- **[Pitwall handoff](pitwall.md)** — serve a leased model as an expiry-aware route profile.
- **[Subscription usage](usage.md)** — how much of each coding subscription is used, and how a second account is declared.
- **[Runtime architecture](architecture.md)**, **[harness registry](harness-registry.md)**, **[harness CLI setup](harness-cli-setup.md)**, **[OpenCode Go subscription models](opencode-go.md)**, **[run records](run-records.md)**, **[lifecycle hooks](lifecycle-hooks.md)**, **[doctor](doctor.md)**, **[isolated worktree dispatch](worktree-dispatch.md)**, **[host-neutral workflows](workflows.md)**, **[Agent profiles](routes.md)**, **[orchestrator channel](orchestrator-channel.md)**, **[Attach a local endpoint](attach-local-endpoint.md)**, **[Self-hosted endpoints](self-hosted.md)**, and **[public releases](releasing.md)** — the execution, diagnostics, installation, state, generation, integration, and publication contracts.
- **[v0.3 runtime migration](migration-v0.3.md)**, **[v0.4 diagnostics/worktree migration](migration-v0.4.md)**, **[v0.5 discovery/workflow migration](migration-v0.5.md)**, and **[v0.8 route profiles migration](migration-v0.8.md)** — historical upgrade notes for the standalone releases, kept as records.
- **[Migrating from standalone Agent Routing](../operator/agents-migration.md)** — `pitwall agents migrate`: what it moves, renames, and removes.
- **[Prompting references](../prompting/00-prompt-reference-index.md)** — model and transport guides for [Codex/GPT](../prompting/openai-codex-gpt-prompting-reference.md), [Claude Code transport](../prompting/anthropic-claude-code-prompting-reference.md), [Claude Sonnet 5](../prompting/anthropic-claude-sonnet-5-prompting-reference.md), [Claude Opus 4.8](../prompting/anthropic-claude-opus-4.8-prompting-reference.md), [Claude Fable 5](../prompting/anthropic-claude-fable-5-prompting-reference.md), [Gemini / Antigravity](../prompting/google-gemini-prompting-reference.md), [Grok](../prompting/xai-grok-prompting-reference.md), [Kimi](../prompting/kimi-moonshot-prompting-reference.md), [GLM](../prompting/glm-zhipu-prompting-reference.md), [MiniMax](../prompting/minimax-prompting-reference.md), and [Qwen](../prompting/qwen-alibaba-prompting-reference.md).
- **[Worked example](../../examples/agents/fan-out-review/README.md)** — a real two-model fan-out review of a planted-bug module, with the actual (lightly trimmed) shim outputs.
- **[Capability cards](../../plugins/claude/skills/subagent-model-routing/ledger/)** — the seed per-model cards the ledger system maintains.
- **[Contributing](CONTRIBUTING.md)** — local checks and conventions for PRs.

## Packages and repo layout

| Client | Marketplace | Package | Dispatch style |
|--------|-------------|---------|----------------|
| Claude Code | `pitwall-local` | [`plugins/claude`](../../plugins/claude/README.md) (plugin `pitwall`) | native Claude + Codex/Gemini/Kimi/OpenCode/Grok/Qwen targets; flat dispatch + Workflow DAG orchestration |
| Codex | `pitwall-local` | [`plugins/codex`](../../plugins/codex/README.md) (plugin `pitwall-codex`) | native Codex + direct or dependency-workflow Claude/Gemini/Kimi/OpenCode/Grok/Qwen targets |
| GitHub Copilot CLI | `pitwall-local` | [`plugins/copilot`](../../plugins/copilot/README.md) (plugin `pitwall-copilot`) | direct or dependency-workflow dispatch through all fourteen shims |

```text
plugins/claude/                         # Claude skill, agents, commands, hooks
plugins/codex/                          # Codex plugin package
plugins/copilot/                        # Copilot plugin package
.claude-plugin/marketplace.json         # Claude marketplace (repository root)
.agents/plugins/marketplace.json        # Codex marketplace (repository root)
.github/plugin/marketplace.json         # Copilot marketplace (repository root)
src/pitwall/agents/                     # the `pitwall agents` runtime, shims, profiles, workflows
src/pitwall/agents/resources/config/    # harness registry and open-weight model catalog
docs/prompting/                         # model-specific prompt guidance
```

## Project status

Agent Routing is part of Pitwall `0.2.0a1`, an early-stage project built and maintained by a single maintainer. Issues and pull requests are welcome, with best-effort response times. The public contract (the `SHIM-DONE` sentinel, the `PITWALL_AGENTS_*` environment-variable names, the namespaced agent types, and the versioned workflow schema) is versioned with the rest of Pitwall in the root [CHANGELOG.md](../../CHANGELOG.md).

## License

Pitwall, including Agent Routing, is licensed under [Apache-2.0](../../LICENSE). The MIT terms under which Agent Routing was released before the merge are reproduced in the root [NOTICE](../../NOTICE); [docs/agents/LICENSE](LICENSE) keeps the historical MIT file.

### ZCode subscription login

For a ZCode CLI already signed into Z.AI, add a `zcode` profile with model
`zcode-default` and harness `zcode`. This uses ZCode's saved model and account
without another API key. See [ZCode setup and dispatch](zcode.md) for commands,
permission modes and the documented limits on per-run overrides.
