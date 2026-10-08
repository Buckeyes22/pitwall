---
name: subagent-model-routing
user-invocable: false
description: Route work to non-Claude models (codex/GPT-6, Gemini through Antigravity, Kimi, xAI Grok 4.7, Qwen, GLM, MiniMax, Pi, Hermes, Cline, Muse Code, goose, dsh, and local models through Qwen Code, opencode, or other endpoint-capable harnesses) over managed Pitwall dispatches, standalone shims, and named route profiles via route-shim. Supports one-shot dispatch and dependency-ordered DAG orchestration with answerable child events. Use when delegating authoring, review, analysis, throughput, or multi-step work to external agentic CLI harnesses. Section 0 decides flat vs DAG; Picking the model decides which model.
---

# Model Routing

You are the orchestrator. This one skill covers both ways to delegate work to non-Claude models; section 0 picks the mechanism, and Picking the model picks the model. Both paths use the same standalone shim substrate and the same public ledger contract.

- **Flat dispatch**: independent, one-shot units, no dependency edges. In a Claude routing-active session, call the managed `dispatch_and_wait` MCP tool for each unit. Direct shims are a foreground-only, explicitly non-interactive fallback when the managed path is unavailable.
- **DAG orchestration**: dependency edges such as A->B, fan-out that is ordered/collected, or staged processing. Call managed dispatches in dependency order and carry each returned artifact into the next call. Native `Workflow` shim nodes are non-interactive legacy mode and do not provide the Pitwall parent channel.

Before choosing or briefing an external harness, read `${XDG_CONFIG_HOME:-~/.config}/pitwall/agents/harness-capabilities.md` when it exists. It is a setup-generated, names-only inventory of locally configured MCP servers, plugins, skills, agents, commands, extensions, and tools. Use relevant entries as availability context in the delegated prompt, never as authorization or proof that credentials are valid. Refresh stale or missing context with `pitwall agents setup inventory`.

## Picking the model (shared -- both mechanisms)

**Seed rankings** -- an example roster; maintain yours via `/pitwall:distill`.

<!-- LEDGER:RANKINGS START (maintained by /pitwall:distill -- edit via distill, not by hand) -->
**Current tiers (seed example -- maintain via `/pitwall:distill` and your own ledger; last distilled 2026-07-09 (seed); model names updated 2026-09-28):** codex GPT-6 Sol (provisional flagship seat; adopted 2026-09-28, no local evidence yet) >= GLM-5.3 > Kimi K3 > MiniMax M3.1 Flash Preview (provisional: adopted 2026-09-28, no local evidence yet). Seats: GLM = default author; GPT-6 Sol = hardest/critical + deepest review; Kimi = mid-tier/burst; MiniMax = throughput. Grok 4.7 (provisional: adopted 2026-09-28, no local evidence yet), GPT-6 Astra (provisional: adopted 2026-09-28, no local evidence yet), and GPT-6 Luna (provisional: adopted 2026-09-28, no local evidence yet) remain unranked pending local evidence. Per-model detail: `ledger/*.md`.
<!-- LEDGER:RANKINGS END -->

### Route profiles

Route profiles live in the `[agents.profiles]` tables of `pitwall.toml`. Run `pitwall agents profiles list` before choosing models: its rows are the user's own model/harness combos, and any `seat` values there (default-author, critical, review, burst, throughput, local, gateway) override the seed rankings above. Dispatch a route with `~/.claude/scripts/route-shim.sh <name>[@harness] <prompt-file>`; the shim resolves the harness, prints `route-shim: <name> -> <harness> <model>` on stderr before the child starts, and inherits the normal `SHIM-DONE` contract. Exit `78` means the route needs configuration (`pitwall agents profiles sync --harness <h>` or an unset key variable) — report the stderr line and stop; never reroute silently. Never pass a Claude model spec (`sonnet`, `opus`, `fable`, `haiku`, `claude-*`) to route-shim from this host; Claude work stays native. The one exception is a saved route whose `env` sets `CLAUDE_CONFIG_DIR`: it reaches a second Claude account, and route-shim may dispatch it. Details: `docs/agents/routes.md`.

Dispatches self-heal Pitwall routes; `autoServe` is the opt-in for spending, and an unsuccessful revival exits `78` with `not revived: <code>`.

Local endpoints are first-class routes. Expect about 1–2 minutes for a cold start. On exit `78`, apply the configuration remedy printed on stderr; when a probe reports `warming`, wait and probe again rather than rerouting. Never hard-code model IDs—run `pitwall agents profiles discover` and select from the endpoint's catalog. New to attaching one? Load the `attach-local-endpoint` skill instead of improvising.

Run `pitwall agents harnesses` (or `--json`) to see which harnesses are installed, their version, and their effort control before choosing; routes and `harnesses.<id>` defaults in `pitwall.toml` can pin an `effort`, which the resolver renders as that harness's own flag (`--effort`, `--thinking`, `-c model_reasoning_effort=…`), so do not repeat it on the command line unless overriding.

### Subscription usage

1. Before choosing a subscription-backed route, run `pitwall usage --json`. Each row is one plan and account: `windows` holds percent used and the reset time, `status` is `ok`, `warn`, `limit`, `error`, `stale`, or `unknown`, and `routes` names the routes that reach that account (empty means the harness default).
2. Do not choose an account whose status is `limit`.
3. Avoid an account with any window at or above 90 percent when another suitable one is below it. This keeps a 10 percent reserve.
4. When a plan has more than one account, use the route of the account with the most room.
5. Treat `error`, `stale`, and `unknown` as no information. They are not a reason to avoid an account.
6. When this host's own account is at `warn` or `limit` and no other account of that plan is configured, say so to the user and continue.

Details: `docs/agents/usage.md`.

### Self-hosted through Pitwall

For a leased, self-hosted model, serve its capability through the Pitwall CLI or
MCP tools, add it with `pitwall agents profiles add <name> --from-pitwall
<capability>`, then dispatch `route-shim.sh <name> prompt.md`. On exit `78` or
a `503` in child output, run `pitwall agents profiles probe <name>` and report the
result and the refusal code; never silently reroute. Manual refresh and serve
commands remain the fallback. See `docs/agents/pitwall.md`.

**The test:** use the cheapest model/effort that can notice when it is wrong. A model running inside an agentic shim can read files, write files, run checks, and iterate. A plain completion cannot, so it needs either a trivially verifiable task or a stronger model plus an explicit verify step.

**The noticer must be deterministic.** "Can notice when it is wrong" means a real gate: typecheck, lint, tests, validators, or static detectors. Model cross-review can supplement the gate, but it never replaces it.

**The flow:**

1. Mechanically checkable work (format, rename, extract, classify) goes to the cheapest reliable route or to a script/template.
2. Work that reads several files and changes code defaults to GLM-5.3 through opencode; use Grok 4.7 or Kimi K3 for independent candidates until local evidence earns Grok a fixed seat.
3. User-visible breakage risk escalates to codex and must include a deterministic gate.
4. Auth, money, data loss, security, migrations, concurrency, and production infrastructure stay high-effort and high-gate; the critical synthesis stays inline.
5. Broad discovery fans out across cheaper routes, then the orchestrator synthesizes.
6. Ambiguous judgment should not be over-parallelized. Use the strongest route or keep it inline.

**Two axes, kept separate:**

- **External work-model**: codex, Gemini through Antigravity, Grok, Kimi, GLM, MiniMax, or a local/self-hosted model exposed through opencode.
- **Claude node-model**: the transport node is Sonnet. Do not use Haiku for any content-bearing shim node.

The detailed seed roster and per-task routing table are in Part B.

## Prompt Reference Cards (shared -- both mechanisms)

Lines between `MODEL-FACTS` markers are generated from `model-facts/` and cite their sources. "Stated by vendor", "Stated by harness", "Stated by host", and "Shown by artifact" say where a statement comes from; the ledger records what was observed locally. Change a marked block by editing `model-facts/families/<family>/facts.json`, never by hand.

The canonical host-filtered transport/model inventory is generated from `src/pitwall/agents/resources/config/harness-registry.json` and bundled at [`references/routes.generated.md`](references/routes.generated.md). It intentionally omits Claude because Claude work stays native in this host; the prose below owns routing judgment and prompt construction rather than duplicating the machine-readable inventory.

These cards are compact runtime summaries. (Model tiers and capability cards, by contrast, are ledger-maintained seeds — see §The ledger.) For non-trivial prompts, high-stakes tasks, broad fan-out, or reusable templates, load the linked package-local reference section first.

If your CLI has MCP tools configured, prompts may direct their use.

### codex / GPT

- **Use for:** strongest implementer in the seed roster; hardest units, deepest review.
- **GPT-6 routes:** current Codex runtime model IDs are Astra (`gpt-6-astra`), Sol (`gpt-6.1-sol`, which replaces `gpt-6-sol`), and Luna (`gpt-6-luna`); the GPT-5.6 IDs (`gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`) stay registered. Codex starts demanding agents on Sol and fast, narrowly scoped agents on Luna.
- **Prompt shape:** include Goal, Context, Constraints, Completion Criteria, exact files, allowed edits, validation commands, and done criteria.
- **Authorization boundary:** state what may be changed, which destructive actions are forbidden or require confirmation, and what must remain untouched. GPT-5.6 system-card evaluations found a greater tendency than GPT-5.5 to go beyond user intent.
- **Verification:** name deterministic checks in the prompt and inspect the actual artifacts. A completion claim is not proof, especially after tool failures.
- **Reasoning control:** use the cheapest effort that can notice failure; raise effort only when the task needs it.
- **Gotcha:** do not omit validation criteria. Codex becomes much more reliable when it can prove its own work.
<!-- MODEL-FACTS:codex START (generated from docs/agents/model-facts/families/codex; edit facts.json, not this block) -->
- **Models:** `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- **Context:** 1,050,000 tokens, output 128,000.
- **Effort:** `gpt-6-astra`: low, medium, high, xhigh, max, cannot be disabled; `gpt-6.1-sol`: low, medium, high, xhigh, max (default medium), cannot be disabled; `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`: none, low, medium, high, xhigh, max (default medium).
- **Effort on other values:** `gpt-6-astra`: other values are rejected.
- **Facts checked:** 2026-10-07. Sources: `docs/agents/model-facts/families/codex/FACTS.md`.
<!-- MODEL-FACTS:codex END -->
- Full reference: `references/model-prompting.md#openai-gpt-6-through-codex`

### xAI / Grok

- **Use for:** coding, agentic, and knowledge work through the Grok Build harness; keep it provisional in the roster until the local ledger supports a ranked seat.
- **Route:** `grok-shim.sh` defaults to `grok-4.7` and accepts `-m`/`--model` overrides.
- **Prompt shape:** state objective, repository context, scope and authorization boundaries, requested work, deterministic validation, and completion criteria.
- **Verification:** inspect the resulting artifacts and rerun decisive checks; the harness is agentic, but its completion report is still only a receipt.
- **Reasoning control:** Grok 4.7 defaults to `high`; use `--effort low` or `--effort medium` for routine, tightly scoped work.
- **Security:** Grok Build's sandbox is off by default. Forward `--sandbox workspace` when isolation is required; approvals are not auto-accepted unless `PITWALL_AGENTS_UNRESTRICTED=1` is set.
<!-- MODEL-FACTS:grok START (generated from docs/agents/model-facts/families/grok; edit facts.json, not this block) -->
- **Models:** `grok-4.7`, `grok-4.6`
- **Context:** `grok-4.7`: 500,000 tokens.
- **Effort:** low, medium, high, xhigh (default high), cannot be disabled.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/grok/FACTS.md`.
<!-- MODEL-FACTS:grok END -->
- Full reference: `references/model-prompting.md#xai-grok-47-through-grok-build`

### Kimi / Moonshot

- **Use for:** mid-tier authoring, parallel candidates, subscription-friendly burst work.
- **Prompt shape:** be explicit and detailed; use delimiters for source/context; define steps; provide complete examples for style or output shape.
- **Thinking control:** K3 thinking is always on (`reasoning_content` returned unconditionally); steer with top-level `reasoning_effort` (`low`/`high`/`max`, default `max`). Preserved thinking: multi-turn flows must pass full assistant messages back.
- **Tool-use caveat:** Kimi Code owns the tool harness. Keep the task prompt focused on the work and output contract.
- **Reliability:** pilot new templates before fan-out.
- **Gotcha:** grounded review depends on the agentic harness reading real files. Do not use a non-tool completion route for review.
<!-- MODEL-FACTS:kimi START (generated from docs/agents/model-facts/families/kimi; edit facts.json, not this block) -->
- **Models:** `kimi-k3`, `kimi-k2.7-code`, `kimi-k2.6`
- **Context:** `kimi-k3`: 1,048,576 tokens; `kimi-k2.7-code`, `kimi-k2.6`: 262,144 tokens.
- **Effort:** `kimi-k3`: low, high, max (default max), cannot be disabled.
- **Sampling:** `kimi-k2.7-code`: temperature 1.0, top_p 0.95.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`kimi-k3`, `kimi-k2.7-code`).
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/kimi/FACTS.md`.
<!-- MODEL-FACTS:kimi END -->
- Full reference: `references/model-prompting.md#kimi`

### GLM / Z.ai

- **Use for:** default routed authoring and review; balanced throughput and structured tasks.
- **Prompt shape:** define role/system behavior, use delimiters, specify output format, and split complex tasks into simple subtasks.
- **Reasoning control:** GLM-5.3 reasoning is mandatory (`thinking.type` accepts only `enabled`); steer with `reasoning_effort` (`low`/`high`/`max`, default `max`; use `max` for coding).
- **Structured output:** through the shim, still demand parseable JSON when needed and validate it after return.
- **Gotcha:** coding-plan traffic uses the provider/model selected in the opencode command; do not duplicate endpoint details in prompts.
<!-- MODEL-FACTS:glm START (generated from docs/agents/model-facts/families/glm; edit facts.json, not this block) -->
- **Models:** `glm-5.3`, `glm-5.3-flash`, `glm-5.2`, `glm-5.1`
- **Context:** `glm-5.3`, `glm-5.3-flash`, `glm-5.2`: 1,048,576 tokens, output 131,072; `glm-5.1`: 202,752 tokens.
- **Effort:** `glm-5.3`, `glm-5.3-flash`: low, high, max (default max), cannot be disabled; `glm-5.2`: high, max (default max).
- **Effort on other values:** `glm-5.3`, `glm-5.3-flash`, `glm-5.2`: any other value runs as max.
- **Sampling:** temperature 1.0, top_p 0.95.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`glm-5.3`, `glm-5.3-flash`, `glm-5.2`, `glm-5.1`).
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/glm/FACTS.md`.
<!-- MODEL-FACTS:glm END -->
- Full reference: `references/model-prompting.md#glm`

### MiniMax

- **Use for:** throughput work when a Sonnet-grade route is enough; stall-retry policy applies.
- **Prompt shape:** use structured prompting: clear role, task, constraints, success criteria, and output shape.
- **Coding behavior:** allow or request a planning phase when that helps the task.
- **Thinking control:** `--thinking` is a binary visibility toggle for M3, not an effort dial.
- **Gotcha:** MiniMax can stall: opencode exits `0` with empty stdout, which the shim records as exit `77`. Retry the same model up to 3 times; do not reroute without reporting it.
<!-- MODEL-FACTS:minimax START (generated from docs/agents/model-facts/families/minimax; edit facts.json, not this block) -->
- **Models:** `minimax-m3`, `minimax-m2.7`, `minimax-m3.1-flash-preview`
- **Context:** `minimax-m3`: 1,048,576 tokens; `minimax-m2.7`: 204,800 tokens; `minimax-m3.1-flash-preview`: 1,000,000 tokens.
- **Effort:** `minimax-m3.1-flash-preview`: low, medium, high, xhigh, max (default max), cannot be disabled.
- **Effort on other values:** `minimax-m3.1-flash-preview`: other values are rejected.
- **Sampling:** `minimax-m3`: temperature 1.0, top_p 0.95; `minimax-m2.7`: temperature 1.0, top_p 0.95, top_k 40; `minimax-m3.1-flash-preview`: top_p 0.95.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`minimax-m3`, `minimax-m2.7`, `minimax-m3.1-flash-preview`).
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/minimax/FACTS.md`.
<!-- MODEL-FACTS:minimax END -->
- Full reference: `references/model-prompting.md#minimax`

### Qwen / Alibaba

- **Route status:** first-class provider since v0.7.0 over the dedicated `qwen-shim`; other local/self-hosted models route through opencode custom providers, goose, pi, cline, dsh, or hermes (see README: routing a local model).
- **Use for:** Qwen-specific prompt experiments and independent candidate/review passes; local/self-hosted Qwen endpoints via the dedicated `qwen-shim` route.
- **Prompt shape:** Qwen's official guide centers Context, Objective, Style, Tone, Audience, and Response. Add output examples, explicit steps, and high-recognizability separators for complex prompts.
- **Thinking control:** Qwen3 supports `enable_thinking` plus `/think`//`no_think` soft switches; Qwen3.8 replaces the soft switches with `reasoning_effort` (`low`/`medium`/`xhigh`, default `xhigh`). Use thinking for complex reasoning and drop the effort for latency-sensitive work.
- **Transport:** dedicated `qwen-shim` agent over the Qwen Code CLI. Point Qwen Code at any OpenAI-compatible endpoint (local llama.cpp/llama-swap included) via `~/.qwen/.env`; the shim never injects `-m`, so Qwen Code's own config picks the model unless you pass `--model`.
<!-- MODEL-FACTS:qwen START (generated from docs/agents/model-facts/families/qwen; edit facts.json, not this block) -->
- **Models:** `qwen3.8-flash`, `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.6-27b`, `qwen3.6-35b-a3b`, `qwen3-coder-next`, `qwen3.8-max`, `qwen3.8-2.4t-a95b`
- **Context:** `qwen3.8-flash`, `qwen3.8-max`: 1,000,000 tokens; `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.6-27b`, `qwen3.6-35b-a3b`, `qwen3-coder-next`, `qwen3.8-2.4t-a95b`: 262,144 tokens.
- **Effort:** `qwen3.8-flash-next`, `qwen3.8-27b`: xhigh, medium, low (default xhigh); `qwen3.8-2.4t-a95b`: xhigh, medium, low (default xhigh), cannot be disabled.
- **Effort on other values:** `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.8-2.4t-a95b`: other values are rejected.
- **Sampling:** `qwen3.8-flash-next`, `qwen3.8-27b`, `qwen3.6-27b`, `qwen3.6-35b-a3b`, `qwen3.8-2.4t-a95b`: temperature 1.0, top_p 0.95, top_k 20; `qwen3-coder-next`: temperature 1.0, top_p 0.95, top_k 40.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/qwen/FACTS.md`.
<!-- MODEL-FACTS:qwen END -->
- Full reference: `references/model-prompting.md#qwen`

### LongCat (OpenCode Go)

- **Use for:** long-context and high-volume work at the cheapest Go request tiers (Meituan 1.6T-class MoE, 256K context).
- **Route:** `opencode-shim.sh opencode-go/longcat-2.5-preview-free <file>` (self-hosting needs 2 TB-class SGLang nodes — not a Pod recipe).
- **Thinking control:** `enable_thinking` chat kwarg, configured in the harness, not the prompt. No sampling defaults are published; validate locally.
- **Gotcha:** tool-call `arguments` must be dicts, not OpenAI-style strings.
<!-- MODEL-FACTS:longcat START (generated from docs/agents/model-facts/families/longcat; edit facts.json, not this block) -->
- **Models:** `longcat-2.0`, `longcat-2.5-preview`
- **Context:** `longcat-2.0`: 262,144 tokens, output 131,072; `longcat-2.5-preview`: 1,000,000 tokens, output 131,072.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/longcat/FACTS.md`.
<!-- MODEL-FACTS:longcat END -->
- Full reference: `references/model-prompting.md#longcat`

### MiMo (OpenCode Go)

- **Use for:** omnimodal (text/image/video/audio) work at 15B active parameters and 1M context, or the text-only Pro tier for harder reasoning; very high Go request tiers.
- **Route:** `opencode-shim.sh opencode-go/mimo-v2.6-flash <file>` or `opencode-go/mimo-v2.6-pro`.
- **Sampling:** card-recommended `temperature=1.0, top_p=0.95`. Thinking can be switched off with `thinking.type`; custom sampling is ignored while thinking.
<!-- MODEL-FACTS:mimo START (generated from docs/agents/model-facts/families/mimo; edit facts.json, not this block) -->
- **Models:** `mimo-v2.5` (retires 2026-10-21), `mimo-v2.5-pro` (retires 2026-10-21), `mimo-v2.6-pro`, `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`
- **Context:** 1,048,576 tokens, output 131,072.
- **Sampling:** `mimo-v2.5`, `mimo-v2.6-pro`, `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`: temperature 1.0, top_p 0.95; custom values are ignored; `mimo-v2.5-pro`: custom values are ignored.
- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn (`mimo-v2.5`, `mimo-v2.5-pro`, `mimo-v2.6-pro`, `mimo-v2.6-flash`, `mimo-v2.6-pro-ultraspeed`).
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/mimo/FACTS.md`.
<!-- MODEL-FACTS:mimo END -->
- Full reference: `references/model-prompting.md#mimo`

### Tencent Hy (OpenCode Go)

- **Use for:** tool-heavy coding with an explicit thinking dial; accepted values differ by model.
- **Route:** `opencode-shim.sh opencode-go/hy3 <file>` (256K context) or `opencode-go/hy4-preview` (1M context, defaults to `high`).
- **Sampling:** card-recommended `temperature=0.9, top_p=1.0` — do not copy the 1.0/0.95 defaults other families use.
<!-- MODEL-FACTS:hy START (generated from docs/agents/model-facts/families/hy; edit facts.json, not this block) -->
- **Models:** `hy3`, `hy4-preview`
- **Context:** `hy3`: 262,144 tokens, output 131,072; `hy4-preview`: 1,048,576 tokens, output 65,536.
- **Effort:** `hy3`: no_think, low, high (default no_think); `hy4-preview`: no_think, high (default high).
- **Effort on other values:** other values are rejected.
- **Sampling:** temperature 0.9, top_p 1, top_k -1.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/hy/FACTS.md`.
<!-- MODEL-FACTS:hy END -->
- Full reference: `references/model-prompting.md#hy-tencent`

### Gemini / Antigravity

- **Route:** `agy-shim.sh` defaults to `gemini-3.8-flash` with medium effort and always adds the dispatch workspace.
- **Prompt shape:** be direct and precise; state the goal, constraints, success criteria, and output shape. Use consistent Markdown headings or XML-style delimiters. For long context, put the context first and the exact task last.
- **Reasoning control:** use `--effort low|medium|high` with a base Gemini ID. Do not combine `--effort` with an effort-suffixed live slug. Gemini 3.1 Pro supports low and high in the verified catalog.
- **Sampling:** retain Gemini 3.x defaults; Google warns that changing temperature/top-p/top-k can degrade complex reasoning.
- **Failure contract:** `authentication required` means sign in interactively or configure Gemini API-key mode. A stderr line beginning `jetski: no output produced` is a soft-denied command tool; the shim converts such runs to exit `77` (EX_NOPERM) — re-run unrestricted or add a permissions.allow rule.
<!-- MODEL-FACTS:gemini START (generated from docs/agents/model-facts/families/gemini; edit facts.json, not this block) -->
- **Models:** `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.1-pro`
- **Context:** `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.5-flash`, `gemini-3.1-pro`: 1,048,576 tokens, output 65,536.
- **Effort:** `gemini-3.8-flash`, `gemini-3.7-flash`: low, medium, high (default medium); `gemini-3.6-flash`, `gemini-3.5-flash`: minimal, low, medium, high (default medium); `gemini-3.1-pro`: low, medium, high (default high).
- **Effort on other values:** `gemini-3.8-flash`: other values are rejected.
- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/gemini/FACTS.md`.
<!-- MODEL-FACTS:gemini END -->
- Full reference: `references/model-prompting.md#gemini-through-antigravity`

---

## The ledger (observation-driven across sessions)

The ledger keeps routing opinions from freezing into folklore. It has two tiers, with one-way flow: hot -> warm.

- **Hot -- machine-local observations.** `~/.local/state/pitwall/agents/ledger/observations.jsonl`, append-only JSONL. The repo-installed shims log quantitative records automatically with `"source":"shim"`: `event:"started"` at dispatch start and `event:"finished"` at terminal with `model`, `wall_s`, `exit`, and `outcome`. Override the path with `PITWALL_AGENTS_LEDGER`.
- **Warm -- repo-committed knowledge.** `ledger/{claude-fable-5,claude-opus-4.8,claude-sonnet-5,codex,deepseek,gemini,gemma,glm,grok,kimi,minimax,muse-glimmer,muse-spark,qwen}.md` capability cards plus the marked rankings block at the top of Picking the model. The three Claude cards record Codex/Copilot-only shim targets; this Claude-hosted skill still uses native `Agent` calls for Claude work. These are seed examples -- maintain them via `/pitwall:distill` and your own ledger.

Distill counts finished shim records for quality/rate math; started records are for orphan visibility.

**Qualitative records are the orchestrator's job.** The shim already wrote the quantitative line; it cannot know whether the result was notable. After a dispatch with a notable outcome (failure, surprise, tier-breaking quality, stall, clipped run), append one qualitative line:

```bash
printf '%s\n' '{"ts":"'"$(date -u +%Y-%m-%dT%H:%M:%S)"'","project":"<repo>","shim":"<shim>","model":"<model>","task_kind":"<author|review|extract|...>","outcome":"<ok|clipped|stall|error|timeout>","note":"<one sentence>","source":"orchestrator"}' >> "${PITWALL_AGENTS_LEDGER:-$HOME/.local/state/pitwall/agents/ledger/observations.jsonl}"
```

Nothing notable means no entry. Distill with `/pitwall:distill`.

---

### Long dispatches and outage checks

- Any dispatch expected to run longer than 15 minutes uses the managed `dispatch_and_wait` MCP tool with an explicit dispatch timeout and a bounded event wait. Direct shim calls remain foreground-only during migration: never use a backgrounded Agent or Bash call, `&`, `nohup`, `setsid`, or `codex-shim-bg.sh` for routed work.
- Before assuming a usage-limit or provider outage, probe: endpoint routes with `pitwall agents profiles probe <name>`; codex/opencode with the pong smoke test in the Smoke test section (a 30-second dispatch). Limits are rolling windows and usually clear within hours; do not idle work for a day on a single 429.
- The ledger Stop hook only nudges when a shim finished with a non-ok outcome this turn; a clean turn is silent.

## Part A -- DAG orchestration (dependency edges -> managed dispatches)

Use this part when a task is multi-step with dependencies and you want the actual work delegated to non-Claude models. It fuses two capabilities:

- **Managed dispatches**: deterministic dependency ordering with `dispatch_and_wait`, `answer_and_wait`, `wait_dispatch`, and `steer_and_wait`. Each call returns a child ask or terminal event to the Claude parent.
- **The Workflow tool**: a legacy, deterministic non-interactive DAG surface with `agent()`, `pipeline()`, `parallel()`, `phase()`, and resume. Its shim nodes do not provide a proven parent ask/answer channel.
- **The shims**: `codex-shim` routes GPT work through codex; `agy-shim` routes Gemini through Antigravity; `kimi-shim` routes Kimi through Kimi Code; `grok-shim` routes Grok 4.7 through Grok Build; `qwen-shim` routes Qwen (and any OpenAI-compatible endpoint) through Qwen Code; `pi-shim` wraps Pi with config-synced endpoint delivery; `hermes-shim` wraps Hermes Agent with config-synced endpoint delivery; `cline-shim` wraps Cline CLI with config-synced endpoint delivery; `muse-shim` wraps Meta Muse Code as a model-bound harness; `goose-shim` wraps goose with environment endpoint delivery; `dsh-shim` wraps the experimental DeepSeek Harness with config-synced profile delivery; `zcode-shim` wraps ZCode with its saved model and account; `route-shim` dispatches a named route profile (`name[@harness]`) through whichever non-Claude harness it resolves to; `opencode-shim` routes GLM, MiniMax, OpenCode Go subscription models (`opencode-go/*`), and custom/local providers through opencode. They run the CLI and return stdout verbatim.
- **`pi-shim`** wraps Pi with config-synced endpoint delivery.
- **`hermes-shim`** wraps Hermes Agent with config-synced endpoint delivery.
- **`cline-shim`** wraps Cline CLI with config-synced endpoint delivery.
- **`muse-shim`** wraps Meta Muse Code as a model-bound harness.
- **`goose-shim`** wraps goose with environment endpoint delivery.
- **`dsh-shim`** wraps the experimental DeepSeek Harness with config-synced profile delivery.
- **`zcode-shim`** wraps ZCode with its saved model and account.

Invoking this skill activates the Claude routing guard before child work begins. Use managed dispatches for every child that may ask the parent.

Two misroutes must be prevented:

| # | Misroute | What it looks like | Gate |
|---|---|---|---|
| 1 | Transport misroute | A dependency graph is run through direct `Agent`, shell, or native Workflow shim calls instead of managed dispatches. The models may run, but the parent event channel is absent. | Section 0 |
| 2 | Node misroute | A managed dependency step lacks its explicit route or dispatch receipt and silently falls back to an unrelated child. | The managed dispatch result |

When a child may ask the orchestrator, use the managed `dispatch_and_wait` / `answer_and_wait` tools for each dependency step and carry the returned artifact into the next step. Workflow shim nodes are a non-interactive mode; they must not be presented as a working parent conversation.

## Section 0 -- Mechanism is mandatory

**Step 0: is this actually a DAG?** A DAG has dependency edges: A->B, staged/ordered processing, a mechanical reduce that needs upstream outputs, a resume boundary, or a point where the orchestrator must judge/synthesize before downstream work.

- **No edges:** flat, independent, one-shot dispatch. Do not build a Workflow. Use Part B.
- **Has edges:** call `dispatch_and_wait` for each node whose prerequisites are complete, review its event, answer with `answer_and_wait` when needed, and pass the terminal artifact to the next node. Use native `Workflow({ scriptPath })` only in an explicitly non-interactive ordinary session; its shim nodes cannot ask the parent. Do not run an interactive DAG as direct `Agent` calls or direct shell invocations.

Fan-out alone is width, not depth. If each unit has an internal `spec -> build` edge, the whole thing is a DAG even when units are independent. Conversely, do not invent stages: a single agentic shim node already authors, verifies, and fixes within its own loop.

The node string `Run verbatim: ~/.claude/scripts/codex-shim.sh ...` is byte-identical in flat dispatch and DAG nodes. The Workflow wrapper is what makes it a DAG node.

Once inside Workflow, every work node routes to a shim via `agentType`. A bare `agent(prompt)` is a bug.

## Legacy Workflow pre-flight -- non-interactive sessions only

The examples in this section are retained for an explicitly non-interactive ordinary Claude session. They do not provide parent ask/answer delivery and must not be used in a routing-active session.

Run a small Workflow pilot on a fresh machine, a new setup, or any time you doubt wiring.

```js
export const meta = { name: 'dag-pilot', description: 'prove shim routing inside Workflow',
  phases: [{ title: 'Pilot' }] }
const codex = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/codex-shim.sh ${file}`, { agentType: 'pitwall:codex-shim', model: 'sonnet', ...o })
const grok  = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/grok-shim.sh ${file}`, { agentType: 'pitwall:grok-shim', model: 'sonnet', ...o })
const kimi  = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/kimi-shim.sh ${file}`, { agentType: 'pitwall:kimi-shim', model: 'sonnet', ...o })
const pi = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/pi-shim.sh ${file}`, { agentType: 'pitwall:pi-shim', model: 'sonnet', ...o })
const hermes = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/hermes-shim.sh ${file}`, { agentType: 'pitwall:hermes-shim', model: 'sonnet', ...o })
const cline = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/cline-shim.sh ${file}`, { agentType: 'pitwall:cline-shim', model: 'sonnet', ...o })
const muse = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/muse-shim.sh ${file}`, { agentType: 'pitwall:muse-shim', model: 'sonnet', ...o })
const goose = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/goose-shim.sh ${file}`, { agentType: 'pitwall:goose-shim', model: 'sonnet', ...o })
const dsh = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/dsh-shim.sh ${file}`, { agentType: 'pitwall:dsh-shim', model: 'sonnet', ...o })
phase('Pilot')
const [g, x, k] = await parallel([
  () => codex('/tmp/dag-pilot/pong.md', { label: 'codex-pong', phase: 'Pilot' }),
  () => grok('/tmp/dag-pilot/pong.md', { label: 'grok-pong', phase: 'Pilot' }),
  () => kimi('/tmp/dag-pilot/pong.md', { label: 'kimi-pong', phase: 'Pilot' }),
])
return { g, x, k }
```

Stage it and launch:

```bash
mkdir -p /tmp/dag-pilot
printf 'Reply with exactly: pong\n' > /tmp/dag-pilot/pong.md
# write the script above to /tmp/dag-pilot/pilot.mjs, then launch Workflow({ scriptPath: '/tmp/dag-pilot/pilot.mjs' })
```

When it completes, confirm the nodes routed to the shims by inspecting the run transcript directory:

```bash
TD=<transcript-dir-from-the-Workflow-launch-result>
grep -rhoE '~?/[^ "]*(codex|kimi|opencode|grok|qwen|agy)-shim\.sh[^"\\]*' "$TD" | sort -u
grep -rhoE '"agentType":"[^"]*"' "$TD" | sort | uniq -c
grep -rhoE '(gpt-6|grok-4\.7|kimi-|qwen)' "$TD" | sort | uniq -c
```

Rows showing `pitwall:codex-shim`, `pitwall:agy-shim`, `pitwall:kimi-shim`, `pitwall:grok-shim`, `pitwall:qwen-shim`, `pitwall:pi-shim`, `pitwall:hermes-shim`, `pitwall:cline-shim`, `pitwall:muse-shim`, `pitwall:goose-shim`, `pitwall:dsh-shim`, and `pitwall:opencode-shim`, plus the shim commands in transcripts, prove routing end-to-end. If routing is broken, stop and fix it before fan-out. A direct Part B dispatch is only an acknowledged stopgap for a flat task or for auth probing.

## Architecture in one paragraph

Express the task as a Workflow DAG. Each work node is an `agent()` call whose `agentType` is `pitwall:codex-shim`, `pitwall:agy-shim`, `pitwall:kimi-shim`, `pitwall:grok-shim`, `pitwall:qwen-shim`, `pitwall:pi-shim`, `pitwall:hermes-shim`, `pitwall:cline-shim`, `pitwall:muse-shim`, `pitwall:goose-shim`, `pitwall:dsh-shim`, or `pitwall:opencode-shim`, so the node's work runs inside an external agentic CLI harness. Nodes hand off through the filesystem. You write every prompt file up front, launch the workflow, and synthesize raw returns or file artifacts inline after the run. Judgment edges split the workflow into segments.

## How a node routes to a model

A DAG node is an `agent()` call carrying `agentType`:

```js
agent("Run verbatim: ~/.claude/scripts/codex-shim.sh /tmp/dag-x/n1.md",
      { agentType: "pitwall:codex-shim", model: "sonnet" })
```

- **No `schema`.** You want the shim's raw stdout, not a structured object forced by the Workflow layer. Prefer filesystem artifacts for structured output.
- **`agentType` carries the shim system prompt** into the node.
- **The names are namespaced.** Use `pitwall:codex-shim`, `pitwall:agy-shim`, `pitwall:kimi-shim`, `pitwall:grok-shim`, `pitwall:qwen-shim`, `pitwall:pi-shim`, `pitwall:hermes-shim`, `pitwall:cline-shim`, `pitwall:muse-shim`, `pitwall:goose-shim`, `pitwall:dsh-shim`, and `pitwall:opencode-shim`. A bare shim name or a typo can misroute to a default node. If the plugin is ever renamed, the namespace prefix changes with it: a stale-but-well-formed prefix fails loudly with *'agent type not found'* (verified 2026-07-28 on a live pilot) — a clean error, not a fallback — whereas a typo'd agent name falls back to a default subagent without erroring.
- **Argument shape differs:** `codex-shim.sh <file> [flags]`; `agy-shim.sh <file> [flags]`; `kimi-shim.sh <file> [flags]`; `grok-shim.sh <file> [flags]`; `qwen-shim.sh <file> [flags]`; `route-shim.sh <name[@harness]> <file> [flags]`; `opencode-shim.sh <provider/model> <file> [flags]`.

Author nodes only through helpers. Keep helper definitions on one line so the audit can match them:

```js
export const meta = { name: 'dag-task', description: 'example DAG',
  phases: [{ title: 'Spec' }, { title: 'Build' }] }

const DIR = '/tmp/dag-task'

const codex   = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/codex-shim.sh ${file}`, { agentType: 'pitwall:codex-shim', model: 'sonnet', ...o })
const grok    = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/grok-shim.sh ${file}`, { agentType: 'pitwall:grok-shim', model: 'sonnet', ...o })
const kimi    = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/kimi-shim.sh ${file}`, { agentType: 'pitwall:kimi-shim', model: 'sonnet', ...o })
const qwen    = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/qwen-shim.sh ${file}`, { agentType: 'pitwall:qwen-shim', model: 'sonnet', ...o })
const agy = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/agy-shim.sh ${file}`, { agentType: 'pitwall:agy-shim', model: 'sonnet', ...o })
const pi = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/pi-shim.sh ${file}`, { agentType: 'pitwall:pi-shim', model: 'sonnet', ...o })
const hermes = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/hermes-shim.sh ${file}`, { agentType: 'pitwall:hermes-shim', model: 'sonnet', ...o })
const cline = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/cline-shim.sh ${file}`, { agentType: 'pitwall:cline-shim', model: 'sonnet', ...o })
const muse = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/muse-shim.sh ${file}`, { agentType: 'pitwall:muse-shim', model: 'sonnet', ...o })
const goose = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/goose-shim.sh ${file}`, { agentType: 'pitwall:goose-shim', model: 'sonnet', ...o })
const dsh = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/dsh-shim.sh ${file}`, { agentType: 'pitwall:dsh-shim', model: 'sonnet', ...o })
const route   = (spec, file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/route-shim.sh ${spec} ${file}`, { agentType: 'pitwall:route-shim', model: 'sonnet', ...o })
const glm     = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/opencode-shim.sh zai-coding-plan/glm-5.3 ${file}`, { agentType: 'pitwall:opencode-shim', model: 'sonnet', ...o })
const minimax = (file, o = {}) => agent(`Run verbatim: ~/.claude/scripts/opencode-shim.sh minimax/MiniMax-M3.1-Flash-Preview ${file}`, { agentType: 'pitwall:opencode-shim', model: 'sonnet', ...o })

phase('Spec')
const spec = await codex(`${DIR}/n1-spec.md`, { label: 'spec', phase: 'Spec' })

phase('Build')
const built = await parallel(UNITS.map((u) =>
  () => kimi(`${DIR}/n2-${u}.md`, { label: `impl:${u}`, phase: 'Build' })))

return { spec, built }
```

All helpers are peers. `codex` routes GPT work, `agy` routes Gemini through Antigravity, `kimi` routes Kimi Code, `grok` routes Grok Build, `qwen` routes Qwen Code, `pi`, `hermes`, `cline`, and `goose` route their configured endpoint-capable harnesses, `muse` routes model-bound Muse Code, `dsh` routes an experimental profile-defined harness, `route` dispatches a named profile from `pitwall agents profiles list` (`route('glimmer', file)` or `route('glimmer@opencode', file)`), and `glm`, `minimax`, and any other local/custom provider route through opencode by changing the provider/model string. The model identity is chosen in the shim command or the provider's configured default, not by the Workflow node.

**Node model policy:** set `model: 'sonnet'` on every transport helper. Haiku is not reliable for content-bearing transport.

## The Workflow substrate -- mechanics you must know

- `meta` must be the first statement and a pure literal.
- The script is transformed before it runs; top-level `await` and `return` are legal in Workflow even though raw `node --check` needs a wrapper.
- The script has no filesystem, clock, or random APIs. The shim subagents do filesystem work.
- Use `phase(title)`, `log(msg)`, `agent(prompt, opts)`, `pipeline(items, ...stages)`, `parallel(thunks)`, `args`, `budget`, and one-level nested `workflow(...)` when needed.
- `pipeline` has no barrier between stages; `parallel` is a barrier.
- Concurrency is not provider-aware. Cap fan-out yourself.
- The Workflow call returns a run id and persisted script path. Edit that path and resume when needed.
- The final `return` is what Workflow hands back to you.

## The default node shape -- one self-verifying node per unit

An agentic shim node runs a full agent loop: read source, author, run gates, fix, and report. The default unit of work is one node, not `build -> review -> fix`.

Use a separate skeptical review node only for critical/contract units or when the user explicitly asks for independent review. Keep review off the per-unit critical path when possible: run builds in parallel, then one review-all node, then inline triage.

## The DAG-shape catalog

Pick the shape, then route every work node through a helper.

### Single node

```js
export const meta = { name: 'dag-one', description: 'single delegated node', phases: [{ title: 'Do' }] }
// helpers required
phase('Do')
const out = await codex('/tmp/dag-one/n1.md', { label: 'do', phase: 'Do' })
return { out }
```

### Fan-out (barrier) -- N independent nodes, collect all

```js
export const meta = { name: 'dag-fanout', description: 'N independent nodes, collected', phases: [{ title: 'Map' }] }
// helpers required
phase('Map')
const results = await parallel(UNITS.map((u) =>
  () => glm(`/tmp/dag-fanout/n-${u}.md`, { label: `unit:${u}`, phase: 'Map' })))
return { results: results.filter(Boolean) }
```

### Pipeline (no barrier) -- each item flows through stages independently

```js
export const meta = { name: 'dag-pipe', description: 'per-unit spec -> build, no barrier',
  phases: [{ title: 'Spec' }, { title: 'Build' }] }
// helpers required
const built = await pipeline(UNITS,
  (u) => codex(`/tmp/dag-pipe/spec-${u}.md`, { label: `spec:${u}`, phase: 'Spec' }),
  (_specOut, u) => kimi(`/tmp/dag-pipe/build-${u}.md`, { label: `build:${u}`, phase: 'Build' }))
return { built }
```

### Diamond / map-then-mechanical-reduce

```js
export const meta = { name: 'dag-diamond', description: 'spec -> N impls -> mechanical merge',
  phases: [{ title: 'Spec' }, { title: 'Build' }, { title: 'Merge' }] }
// helpers required
phase('Spec')
const spec = await codex('/tmp/dag-diamond/spec.md', { label: 'spec', phase: 'Spec' })
phase('Build')
const parts = await parallel(UNITS.map((u) =>
  () => glm(`/tmp/dag-diamond/build-${u}.md`, { label: `build:${u}`, phase: 'Build' })))
phase('Merge')
const merged = await codex('/tmp/dag-diamond/merge.md', { label: 'merge', phase: 'Merge' })
return { spec, parts: parts.filter(Boolean), merged }
```

### Loop-until-budget (scale depth to a token target)

```js
export const meta = { name: 'dag-loop', description: 'accumulate until budget runs low', phases: [{ title: 'Round' }] }
// helpers required
phase('Round')
const found = []
let i = 0
while (budget.total && budget.remaining() > 60000) {
  const r = await kimi(`/tmp/dag-loop/round-${i}.md`, { label: `round:${i}`, phase: 'Round' })
  found.push(r)
  i++
  log(`round ${i} done`)
}
return { rounds: found.length, found }
```

### Build -> review-all -> inline triage

```js
export const meta = { name: 'dag-build-review', description: 'parallel builds -> one review-all -> inline triage',
  phases: [{ title: 'Build' }, { title: 'Review' }] }
// helpers required
phase('Build')
const builds = await parallel(UNITS.map((u) =>
  () => glm(`/tmp/dag-build-review/build-${u}.md`, { label: `build:${u}`, phase: 'Build' })))
phase('Review')
const review = await codex('/tmp/dag-build-review/review-all.md', { label: 'review-all', phase: 'Review' })
return { builds: builds.filter(Boolean), review }
```

Write `review-all.md` so it iterates file-by-file and reports one findings section per artifact. If one review node would exceed the timeout, split the review by slices.

## Edges: mechanical vs judgment

A Workflow cannot call back to you mid-run. A dependency edge is one of two kinds:

- **Mechanical edge:** B's input is a deterministic function of A's output. Keep it inside the Workflow with `await`, `pipeline`, or `parallel`.
- **Judgment edge:** B's prompt needs you to evaluate, synthesize, or choose among A's outputs. Split the workflow there.

One Workflow call covers one dependency segment between judgment points.

Mechanical verification is not a judgment point. Typecheck/test/lint ordering belongs inside the same DAG unless you need cross-artifact synthesis.

## Extracting parallelism -- decompose on build-time edges

Parallelism comes from independent authoring units. Decompose on what a unit needs to compile and edit, not on the runtime dataflow the finished system will execute.

- **Parallel set:** units whose build-time dependencies are already present and whose file writes are disjoint.
- **Sequential set:** units with real code-import edges or shared-file writes.

A shared convention is a contract, not a dependency. Put the exact convention in every relevant prompt and verify afterward.

## Delegate the whole graph -- glue is nodes, not orchestrator work

When the mandate is "delegate everything," encode the whole reversible graph as nodes:

- Authoring and skeptical review.
- Integration/build/wiring nodes that read authored artifacts, edit shared registration files, and run gates.
- Branch work that is reversible.

Dispatched models must not run `git add` or `git commit`. They edit files only; the orchestrator reviews and commits.

Stays inline: cross-artifact synthesis, PRs, deploys, production registration, and any irreversible action.

## Handoff: filesystem-as-truth + write prompt files up front

The Workflow script has no filesystem, but shim subagents do. Pass paths by convention:

- Pick a run dir such as `/tmp/dag-<task>`.
- Node A writes `${DIR}/artifact`; node B reads that path.
- The control flow guarantees order; the paths carry data.

Write prompt files before launch. Each prompt should include task, read paths, write paths, no-suppression rule, validation commands, and completion criteria.

```bash
mkdir -p /tmp/dag-feature
for u in forecast aviation tropical; do
  cat > /tmp/dag-feature/n2-${u}.md <<EOF
READ /tmp/dag-feature/n1-spec.md, then implement the ${u} module.
WRITE the result to /tmp/dag-feature/impl-${u}.ts.
Do not resolve typecheck or lint errors via @ts-expect-error, @ts-ignore, or eslint-disable directives.
Address the root cause. If blocked, leave the file failing and document the blocker in the file.
Return a one-line summary; the artifact on disk is the deliverable.
EOF
done
```

After the run, verify artifacts on disk:

```bash
ls -la /tmp/dag-feature/impl-*.ts
wc -l /tmp/dag-feature/impl-*.ts
```

## The routing gate

**Layer 0 -- helpers only.** Nodes are created through `codex()`, `agy()`, `grok()`, `kimi()`, `qwen()`, `pi()`, `hermes()`, `cline()`, `muse()`, `goose()`, `dsh()`, `route()`, `glm()`, and `minimax()` helpers only.

**Layer 1 -- hard rule.** Every `agent(` call site must carry one of the namespaced `codex-shim`, `agy-shim`, `kimi-shim`, `grok-shim`, `qwen-shim`, `pi-shim`, `hermes-shim`, `cline-shim`, `muse-shim`, `goose-shim`, `dsh-shim`, `route-shim`, or `opencode-shim` agent types.

**Layer 2 -- mechanical audit before launch.**

```bash
S=/tmp/dag-<task>/script.mjs
total=$(grep -cE '\bagent\(' "$S")
defs=$(grep -cE '^\s*const (codex|grok|kimi|qwen|route|agy|pi|hermes|cline|muse|goose|dsh|zcode|glm|minimax) *=.*\bagent\(' "$S")
[ "$total" -eq "$defs" ] || { echo "MISROUTE: $total agent( sites but only $defs helper defs"; exit 1; }
types=$(grep -oE "agentType: *'[^']+'" "$S" || true)
bad=$(printf '%s\n' "$types" | grep -vE "^agentType: *'pitwall:(codex|kimi|opencode|grok|qwen|route|agy|pi|hermes|cline|muse|goose|dsh|zcode)-shim'$" || true)
[ -z "$bad" ] || { echo "MISROUTE: bad/non-namespaced agentType:"; printf '%s\n' "$bad"; exit 1; }
[ "$(printf '%s\n' "$types" | grep -c .)" -eq "$defs" ] || { echo "MISROUTE: missing agentType in helper"; exit 1; }
bad_models=$(grep -E '^\s*const (codex|grok|kimi|qwen|route|agy|pi|hermes|cline|muse|goose|dsh|zcode|glm|minimax) *=.*\bagent\(' "$S" | grep -vE "model: *['\"]sonnet['\"]" || true)
[ -z "$bad_models" ] || { echo "MISROUTE: helper missing model: 'sonnet':"; printf '%s\n' "$bad_models"; exit 1; }
printf '%s\n' "$types" | sort | uniq -c
```

**Layer 3 -- red flags.**

| Thought | Reality |
|---|---|
| "I will just batch direct shim calls." | Transport misroute. Use managed dispatches for routing-active DAGs; Workflow is legacy non-interactive mode only. |
| "This node is trivial." | Trivial work still routes through a shim helper. |
| "Synthesis can be one quick node." | Synthesis is a segment boundary and stays inline. |
| "The workflow can pick the model." | You choose the model in the helper command. |
| "A generic reviewer agent is fine." | Reviewer nodes route through a shim too. |

**Layer 4 -- Definition of Done.**

- The DAG is launched via `Workflow({ scriptPath })`.
- Every work node is created through a helper.
- All node prompt files exist before launch.
- Every read path is written by an upstream node.
- Judgment edges are segment boundaries.
- The current contract permits the namespaced codex, agy, kimi, grok, qwen, pi, hermes, cline, muse, goose, dsh, zcode, route, and opencode shim agent types. The historical pilot below predates the dedicated Kimi, Qwen, and Antigravity transports.
- Artifacts are verified on disk after the run.

## Parsing node returns

Standalone shims run the CLIs in plain-text mode. They do not inject `--json`, there is no JSONL contract, and there is no stderr preamble to strip.

The node return is the CLI's human-readable stream. The reply is the text before the trailing sentinel line:

```js
const replyText = (stdout) => stdout.split('\n').filter((line) => !/^SHIM-DONE exit=\d+$/.test(line)).join('\n').trimEnd()
```

Prefer filesystem artifacts for anything structured. If a node should produce JSON, a patch, or a report, instruct it to write that artifact to disk and verify the file.

A MiniMax stall is empty output before `SHIM-DONE exit=<n>`; opencode exiting `0` with empty stdout is recorded as `exit=77`. Retry the same model up to 3 times. Do not reroute without reporting it.

## Picking the model per node

**Example roster (seed) -- maintain via `/pitwall:distill` and your own ledger.**

| Node job | Route |
|---|---|
| First-draft authoring | `glm` through `pitwall:opencode-shim`; `kimi` for burst/parallel candidates |
| Independent coding/agentic candidate | `grok` through `pitwall:grok-shim`, provisional until local evidence ranks it |
| Deep one-off reasoning or critical verification | `codex` through `pitwall:codex-shim` |
| Throughput or bulk classification | `minimax` through `pitwall:opencode-shim`, pilot first |
| Local/self-hosted Qwen or OpenAI-compatible endpoint | `qwen` through `pitwall:qwen-shim`, unranked pending local evidence |
| Gemini coding / independent candidate | `agy` through `pitwall:agy-shim`, unranked pending local evidence |
| Balanced extraction/structured tasks | `glm` through `pitwall:opencode-shim` |
| Skeptical code review | `codex` or `glm`, both through agentic shims |
| Cross-node synthesis | Not a node; stays inline |

For diversity at a judgment edge, fan out to different routes.

## Scale, cost & concurrency

A shim-routed DAG is wall-clock expensive: each node is both a Workflow agent and an external CLI run. Planning is about rate-limit headroom, endpoint capacity, and wall-clock, not Claude token cost.

- Workflow concurrency is not provider-aware. Cap fan-out yourself.
- Pilot N=1 before broad fan-out.
- Log width caps and retry policy.
- Failed nodes come back as `null`; filter, inspect, and re-dispatch only the failed units.
- MiniMax stalls are retried on MiniMax up to 3 times.

## Resume & iteration

- Every Workflow invocation persists its script path. Edit that file and re-run `Workflow({ scriptPath })`.
- Resume with `Workflow({ scriptPath, resumeFromRunId: '<prior runId>' })` when the unchanged prefix can be cached.
- Keep scripts deterministic; pass variation through `args`.

## Failure modes

| Symptom | Likely cause | Action |
|---|---|---|
| Transcript shows a default Claude subagent | Missing or mistyped `agentType` | Route through helpers, run the audit, re-pilot. |
| Node ran on Haiku | Missing `model: 'sonnet'` | Pin Sonnet on every helper. |
| Workflow rejects `meta` | Non-literal or not first statement | Make `meta` the first pure literal. |
| Date/RNG error | Disallowed runtime API | Pass values through `args`. |
| `node --check` complains about top-level return | Checked raw script instead of transformed wrapper | Use the smoke-test wrapper. |
| Node returns empty before `SHIM-DONE exit=<n>` (OpenCode: `exit=77`) | MiniMax stall or upstream empty result | Retry MiniMax same model up to 3 times; otherwise inspect artifacts and exit code. |
| Missing `SHIM-DONE exit=<n>` | Clipped output or still-running child | Check the artifact on disk and split the unit or raise timeouts deliberately. |
| Input file absent | Prompt read/write path mismatch | Enforce every read path has an upstream write path. |
| Artifact missing or 0 bytes | Silent agent failure | Verify files and re-dispatch with tighter path instructions. |
| Rate-limit wave | Provider headroom exceeded | Chunk fan-out or switch failed units to a different route, except MiniMax stall policy. |
| Suppression directive added | Agent shortcut | Re-dispatch with the no-suppression rule and inspect the artifact. |
| Output copied the prompt exemplar | Prompt exemplar was skeletal | Use a complete real exemplar and pilot first. |

## All shims are agentic -- DAG implications

`opencode`, `codex`, and Grok Build run full agent loops inside a node: read source, author or quote, run gates, fix, and report.

- A single node already authors and verifies; do not add reflexive per-unit review/fix stages.
- One unit of work per node; split multi-unit prompts.
- A node needing more than the configured ceiling should be split, or the operator should deliberately raise `PITWALL_AGENTS_TIMEOUT_SECS` and `BASH_MAX_TIMEOUT_MS`.
- Suppression-shortcut risk survives into artifacts; inspect authored files.

## The boundary -- what stays inline with Opus

| Stays inline | Why |
|---|---|
| Cross-node synthesis / picking the best candidate | Synthesis is the intelligence work. |
| Choosing node models and writing prompts | The prompt is the artifact spec. |
| Schemas, type hierarchies, scoring methods | Wrong shape cascades. |
| DAG topology | The graph determines all downstream work. |
| Final integration review | Node gates are necessary, not sufficient. |

## Smoke test (always fresh)

```bash
mkdir -p /tmp/dag-pilot
printf 'Reply with exactly: pong\n' > /tmp/dag-pilot/pong.md
~/.claude/scripts/kimi-shim.sh /tmp/dag-pilot/pong.md | tail -n 1

S=/tmp/dag-pilot/pilot.mjs
total=$(grep -cE '\bagent\(' "$S")
defs=$(grep -cE '^\s*const (codex|grok|kimi|qwen|route|agy|pi|hermes|cline|muse|goose|dsh|zcode|glm|minimax) *=.*\bagent\(' "$S")
[ "$total" -eq "$defs" ] && echo "audit OK" || echo "MISROUTE"

python3 - "$S" <<'PY'
import sys, subprocess, tempfile, os
b = open(sys.argv[1]).read().replace('export const meta', 'const meta')
p = os.path.join(tempfile.gettempdir(), 'wfchk.js')
open(p, 'w').write('async function f(){\n' + b + '\nreturn 0\n}\n')
r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
print('syntax OK' if r.returncode == 0 else 'syntax FAIL: ' + r.stderr.strip())
PY

# After the live pilot, inspect transcripts for the namespaced codex, agy, kimi, grok, qwen, pi, hermes, cline, muse, goose, dsh, and opencode shim agent types.
```

## Provenance -- what's been verified

- **2026-06-15, run `wf_aefe2be3-052`**: a legacy non-interactive 2-node DAG (`codex` spec -> `kimi` implementation, filesystem handoff) executed via the Workflow tool. Verified end-to-end: `agentType` routed Workflow nodes to shim agents, transcripts showed `codex-shim.sh` and `opencode-shim.sh` invocations with `pitwall:codex-shim` and `pitwall:opencode-shim`, filesystem handoff worked, and mechanical ordering held. This does not establish a parent ask/answer channel.
- Static: embedded helper patterns pass the transform-then-`node --check` syntax gate and the misroute audit.

## See also -- DAG layer

- `scripts/*.sh` in this repo: the public standalone shim scripts installed to `~/.claude/scripts/*-shim.sh`.
- `plugins/claude/agents/{codex,kimi,opencode,grok,qwen,route,agy,pi,hermes,cline,muse,goose,dsh,zcode}-shim.md`: transport agent definitions registered under their namespaced shim agent types.
- `plugins/claude/commands/dag-routing.md`: managed dependency-ordered routing entry command.
- `plugins/claude/skills/subagent-model-routing/ARCHITECTURE.md`: architecture reference.
- `references/model-prompting.md`: self-contained runtime model prompting reference.

---

## Part B -- Flat dispatch & shared transport substrate

Use this part for independent one-shot dispatches with no dependency edges. The model roster, parsing, pre-flight, failure modes, and cost guidance are shared by flat dispatch and DAG nodes.

You are the orchestrator. You decide what work needs doing, which model is right, and how to dispatch. The shim subagents are transport pipes: they run one shell command through a CLI agentic harness and return stdout verbatim.

Active shims:

- **`codex-shim`** wraps the Codex CLI for GPT models.
- **`agy-shim`** wraps Google Antigravity CLI for Gemini models.
- **`kimi-shim`** wraps the Kimi Code CLI for Kimi models.
- **`grok-shim`** wraps the Grok Build CLI and defaults to Grok 4.7.
- **`qwen-shim`** wraps the Qwen Code CLI for Qwen models and any OpenAI-compatible endpoint configured in `~/.qwen/.env`.
- **`pi-shim`** wraps Pi with config-synced endpoint delivery.
- **`hermes-shim`** wraps Hermes Agent with config-synced endpoint delivery.
- **`cline-shim`** wraps Cline CLI with config-synced endpoint delivery.
- **`muse-shim`** wraps Meta Muse Code as a model-bound harness.
- **`goose-shim`** wraps goose with environment endpoint delivery.
- **`dsh-shim`** wraps the experimental DeepSeek Harness with config-synced profile delivery.
- **`zcode-shim`** wraps ZCode with its saved model and account.
- **`route-shim`** dispatches a named route profile (`pitwall agents profiles list`) through whichever of the above it resolves to.
- **`opencode-shim`** wraps opencode for GLM, MiniMax, and any custom provider configured in opencode.

There is no central router. Each CLI manages its own provider credentials and agent loop.

## Pre-flight (30-second probe)

```bash
echo "Reply with exactly: pong" > /tmp/pong.md

# Kimi Code transport health
~/.claude/scripts/kimi-shim.sh /tmp/pong.md

# Qwen Code transport health (local endpoints are cheap to probe)
~/.claude/scripts/qwen-shim.sh /tmp/pong.md

# Antigravity transport health (may require interactive sign-in first)
~/.claude/scripts/agy-shim.sh /tmp/pong.md --effort low

# codex transport health; optional when quota matters
~/.claude/scripts/codex-shim.sh /tmp/pong.md -c model_reasoning_effort=low

# Grok Build transport health; optional when quota matters
~/.claude/scripts/grok-shim.sh /tmp/pong.md --effort low

# auth surfaces
opencode auth list
agy --version
kimi login
codex login
grok login
```

Use `opencode auth list` to check configured providers. Use `kimi login`, `codex login`, or `grok login` when the corresponding CLI reports an authorization failure; headless Grok Build can use `XAI_API_KEY`. Do not parse private auth files in this public skill.

## Architecture in one paragraph -- transport layer

Thirteen external CLIs, thirteen harness families. Antigravity handles Gemini routes through the user's Google tier and authentication. Kimi Code handles Kimi routes through the user's Kimi CLI configuration. Qwen Code handles Qwen routes and any OpenAI-compatible endpoint named in `~/.qwen/.env`. Pi, Hermes, Cline, goose, and dsh support configured endpoints through their respective delivery mechanisms; Muse Code and ZCode are model-bound (ZCode uses its saved model and account). `opencode` handles GLM, MiniMax, and custom/local providers. `codex` handles GPT routes through the user's Codex CLI login. Grok Build handles Grok 4.7 through `grok` authentication or `XAI_API_KEY`. A flat dispatch spawns the matching Sonnet transport subagent, runs its standalone shim, and returns the CLI stream. The final line of a complete run is `SHIM-DONE exit=<n>`.

The shared runtime is the `pitwall agents` command group, which also keeps private structured run records. Run `pitwall agents doctor` before first use or when provider/plugin drift is suspected; its default path performs no discovery. Use explicit `doctor --discover-models` only for a deliberate catalog refresh, and `doctor --probe-routes` only to check route reachability. For recovery, use `pitwall agents runs list`, `runs show <id>`, or `runs logs <id> --channel both`; no routing metadata may be appended after the sentinel. Prompt bodies are not retained by default. Add `--routing-retain-prompt` only when explicitly needed, and treat retained model output as potentially sensitive.

Claude's native Workflow tool remains the default and enforcement target for Claude-hosted DAGs. A child that needs parent input must use the managed event-return tools instead of a Workflow shim node. The shared `pitwall agents workflow run ... --host claude` runner is only for an explicitly requested external-only graph over any registered non-Claude harness (validation rejects a `claude` task), and it must never be used to route Claude through `claude-shim`. A task route may optionally select an existing named profile with `"name": "<route-name>"`; keep `provider` and `model` beside it as the expected resolved identity because the scheduler rejects mismatches before starting the run. Its `--host` value is self-declared advisory metadata, so the existing tripwire hooks remain the actual enforcement layer.

For a non-Claude-native write task (any of the thirteen harnesses) that must not touch Claude's caller worktree, add `--routing-workspace isolated --routing-task-mode write` to the managed dispatch request or shim invocation. Inspect with `pitwall agents runs diff <id>`, apply only after review with `runs apply <id> --target <repo>`, and remove the owned branch/worktree explicitly with `runs discard <id> --yes`. Claude work remains on native `Agent`/Workflow surfaces only in ordinary or explicitly non-interactive sessions; a routing-active session uses the managed event path and the launch guard enforces that boundary.

## Picking the model

### Tier by artifact novelty

**Example roster (seed) -- maintain via `/pitwall:distill` and your own ledger.**

| Tier | Examples | Dispatch pattern |
|---|---|---|
| Schema / shared contract / runtime selector | Direction records, token cascade rules, hooks | Inline; no dispatch |
| Novel layout / performance-sensitive / prose template | SVG math, mobile rendering paths, discriminated prose | Codex plus optional GLM candidate; synthesize inline |
| Composition of existing widgets | Most page templates and glue code | Codex or opencode autonomous-verify |
| Template scaffolding | Many schema-conformant entries from source files | Script/template first |
| Code review / verification | Session diff review, compliance audit | Codex or GLM through agentic shims |
| Test infrastructure | Smoke specs, ignored-pattern lists | Inline; quality decision |

### Allowed routes

**Example roster (seed) -- maintain via `/pitwall:distill` and your own ledger.**

| Shim | Default route | Alternates |
|---|---|---|
| `pitwall:kimi-shim` -> Kimi | configured Kimi default | override with `-m`/`--model` |
| `pitwall:opencode-shim` -> GLM | `zai-coding-plan/glm-5.3` | routes listed by `opencode models` |
| `pitwall:opencode-shim` -> MiniMax | `minimax/MiniMax-M3.1-Flash-Preview` | routes listed by `opencode models`; stall policy applies |
| `pitwall:codex-shim` -> GPT | Codex CLI default | GPT-6 Astra: `gpt-6-astra`; Sol: `gpt-6.1-sol`; Luna: `gpt-6-luna` |
| `pitwall:grok-shim` -> Grok | `grok-4.7` | override with `-m`/`--model`; effort is `low`, `medium`, `high`, or `xhigh` |
| `pitwall:qwen-shim` -> Qwen | configured Qwen Code default (`QWEN_MODEL` / `~/.qwen/settings.json`) | override with `-m`/`--model`; no per-invocation effort flag |
| `pitwall:agy-shim` -> Gemini | `gemini-3.8-flash` at medium effort | `--model` base ID plus `--effort low|medium|high`; never route Claude slugs through agy on this host |
| `pitwall:pi-shim` -> Pi | configured Pi default | override with `-m`/`--model` |
| `pitwall:hermes-shim` -> Hermes | configured Hermes default | override with `-m`/`--model` |
| `pitwall:cline-shim` -> Cline | configured Cline default | override with `-m`/`--model` |
| `pitwall:muse-shim` -> Muse Code | `muse-spark-1.3` | override with `--model` |
| `pitwall:goose-shim` -> goose | configured goose default | override with `--model` |
| `pitwall:dsh-shim` -> DeepSeek Harness (experimental) | configured dsh default | profile-defined |
| `pitwall:zcode-shim` -> ZCode | saved ZCode model and account (`zcode-default`) | none: no model or effort override |
| `pitwall:route-shim` -> any non-Claude harness | a route name from `pitwall agents profiles list` | `name@harness` overrides the profile's harness; exit `78` = needs `profiles sync` or a key variable |
| `pitwall:opencode-shim` -> local/custom | any configured provider/model | use `opencode models` to find the route |

Refresh the opencode catalog with `opencode models`, Grok Build models with `grok models`, and Codex model assumptions from the Codex CLI documentation or `codex` help output. Keep roster changes as seed examples until your ledger supports them.

### Which to pick per task shape

**Example roster (seed) -- maintain via `/pitwall:distill` and your own ledger.**

| Task shape | Route |
|---|---|
| Authoring narrative / first-draft TypeScript or frontend work | `pitwall:opencode-shim` with GLM-5.3; `pitwall:kimi-shim` as parallel candidate |
| Throughput / bulk classification | `pitwall:opencode-shim` with MiniMax-M3, pilot first |
| Balanced extraction / structured tasks | `pitwall:opencode-shim` with GLM-5.3 |
| Deep one-off reasoning / autonomous verification | `pitwall:codex-shim` |
| Independent coding / agentic candidate | `pitwall:grok-shim`, provisional until local evidence ranks it |
| Gemini coding / independent candidate | `pitwall:agy-shim`, unranked pending local evidence |
| Local/self-hosted model experiment | `qwen-shim`, `goose-shim`, `hermes-shim`, `pi-shim`, `cline-shim`, or `dsh-shim` as endpoint-capable alternatives; `pitwall:opencode-shim` with the custom provider/model otherwise |
| Skeptical code review | `pitwall:codex-shim` or GLM through `pitwall:opencode-shim` |
| Claude-only work | regular Claude `Agent`, not a shim |

## MCP tools in dispatched CLIs

Dispatched CLIs may expose MCP tools the user configured.
Treat those tools as additive grounding, not a guaranteed dependency.
Keep pong probes tool-free.

## Asking and steering through the orchestrator channel

A dispatched model can ask you a blocking question mid-task instead of guessing. For routed work,
call the managed `dispatch_and_wait` tool on the `pitwall-channel` MCP server (`pitwall agents setup mcp`
registers it for this host). It starts the child with the channel on and returns the child's next ask
or its terminal result; answer with `answer_and_wait`, keep waiting with `wait_dispatch`, and redirect
with `steer_and_wait`. A shim you run yourself (`*-shim.sh`, `route-shim.sh`) is non-interactive: its
child hears nothing about the channel unless you pass `--routing-ask-support` (cap it with
`--routing-max-asks N`; the default is 5), and on a harness with a registered `pitwall-channel`
server it then gets the tier-1 asking rules: the child blocks in `ask_orchestrator`, you answer with
`pitwall agents answer` while it runs, and it continues and can finish without ever pausing (an
unanswered ask applies its default). Without a registered channel the child can only pause on the
file contract (exit 75) until you run `pitwall agents runs resume`. Prefer `--routing-workspace isolated` for ask-prone shim
work, so a paused run resumes against its own worktree. In a routing-active Claude session the
PreToolUse guard blocks direct shim launches, so use the managed tools.

The server picks its tool set by role. In your session (the orchestrator) it serves `dispatch_and_wait`, `wait_dispatch`, `answer_and_wait`, `steer_and_wait`, and the recovery tools `inbox` and `answer_ask`. `dispatch_and_wait` takes `prompt` plus either `route` (a configured route spec) or `provider` (a direct harness), never both; `workspace` (`shared`, `isolated`, or `auto`), `task_mode` (`read` or `write`), `base`, `timeout_seconds`, `max_asks`, `retain_prompt`, `extra_args`, and `wait_seconds` are optional, and `ask_support` must stay `true`. `answer_and_wait` takes the full `dispatch_id`, the four-digit `ask_id`, and a `choice` (an option id or `abort`); `steer_and_wait` takes `dispatch_id`, `kind`, `message`, and optional `requires_ack` and `deadline_s`; `wait_dispatch` takes `dispatch_id` or its `reattach_handle`. The dispatched child sees a different set, `ask_orchestrator`, `read_steering`, and `ack_steer`, which the dispatcher's prompt tells it to use. Every wait returns one event: `ask`, `defaulted`, `steer_ack`, `terminal`, `orphan`, or `still_running`, and each lists `defaults_applied`.

`pitwall agents setup mcp` registers `pitwall-channel` in the user config of each installed harness: Claude Code, Codex, Copilot CLI, OpenCode, Kimi, Cline, Qwen Code, ZCode, Grok Build, Antigravity, Muse Code, Hermes, and goose. `dispatch_and_wait` refuses a child harness that has no registration or fails a short tool-list handshake, and names `setup mcp` as the fix. Pi and dsh have no registration, so it refuses them; report the refusal.

The dispatcher appends the asking rules to the prompt; you do not write them. The model asks only when its default is dangerous and irreversible, gives 2–8 options with a real default and rationale, points at files instead of pasting diffs, and never asks what the worktree answers. A registered `pitwall-channel` server proves only that the child-side MCP tools are available; registration and a stdio handshake do not prove that the Claude parent receives events.

For Claude-hosted routing, use the managed `dispatch_and_wait` MCP tool. It returns the next ask or terminal event; answer with `answer_and_wait`, continue with `wait_dispatch`, and steer with `steer_and_wait`. Do not make `inbox` polling part of the normal path. Keep `inbox`, `answer`, and `runs resume` as recovery tools for older file-contract callers until installed event-driven acceptance passes. If managed event tools are unavailable, use the documented file fallback and report that interactive delivery is unverified.

For recovery from an older file-contract caller, run `pitwall agents inbox` to inspect unresolved asks, `pitwall agents answer <dispatch-id> <ask-id> <choice>` to record a choice, and `pitwall agents runs resume <dispatch-id>` for a paused run. Use `pitwall agents steer <dispatch-id> --kind scope --message "…"` to redirect a running dispatch or `pitwall agents runs stop <dispatch-id>` to end it gracefully. Escalate dangerous and irreversible defaults to the operator.

Steering rules:

- Steer `kind` is `budget`, `note`, `priority`, `scope`, or `stop`. While a `priority`, `scope`, or `stop` steer that requires acknowledgement (`requires_ack`) is unacknowledged, the steering gate hook (Claude Code and Codex, for runs whose child has the channel tools) denies the child's every other tool call except the channel's own `read_steering`, `ack_steer`, and `ask_orchestrator`, so the child must read and acknowledge first.
- A non-stop steer to a run dispatched without the channel (a shim run without `--routing-ask-support`) is refused, because the child can never read it. `stop` is always accepted. Its abort window is `deadline_s` from the acknowledgement when the child sent one and from the send otherwise, so a run that never acknowledges is aborted at its deadline. A run still preparing, before it has recorded its channel, refuses a non-stop steer with "still preparing": retry once it is running, or send `stop`. Terminal runs refuse every steer.
- A malformed steer (unknown kind, empty message, bad `deadline_s`) is rejected before anything is written: an error result from `steer_and_wait`, exit 1 from `pitwall agents steer`, HTTP 400 from the loopback broker's `POST /steer`.
- When a run ends, steers that require acknowledgement and that the child never acknowledged are reported, whatever their kind: a `steer.unacked` event with `atExit`, `unackedSteerIds` on the terminal event and the ledger row, and `unacked_steer_ids` on a managed `terminal` or `orphan` result. The all-runs `pitwall agents inbox` lists live and paused runs only; name a `dispatch_id` (the `inbox` tool) to see a finished run's asks and steers.

Native Claude `Agent` and `Workflow` children use Claude's native lifecycle in ordinary sessions. During a routing-active session, use the managed dispatch tool for external-model child work; the PreToolUse guard blocks shim and provider launches — `pitwall:*shim`, direct `*-shim.sh`, provider CLI, `pitwall agents dispatch`, and backgrounded Bash calls — including launches a native `Agent` attempts through its own tool calls, while native research agents remain allowed. Opaque wrappers remain outside its detection boundary.

## MiniMax stall handling

MiniMax can stall: opencode exits `0` with no useful text. The shim records an empty or whitespace-only stdout from an exit `0` as `SHIM-DONE exit=77` and stores the reason as `softDenialReason`. This is a provider/adapter observation, not a parsing failure.

Policy:

- A stall is empty text before the sentinel, normally with `exit=77`.
- Re-dispatch the same MiniMax route up to 3 times.
- If it still stalls, report the failure and stop; do not reroute without reporting it to GLM or Kimi.

Check opencode's log directory only when you need provider diagnostics. The operational signal for dispatch is the text before `SHIM-DONE exit=<n>` plus the artifact on disk.

## Why all shims must wrap agentic harnesses

For code review or any task requiring accurate quotes from existing files, the model must run inside an agent harness with file-reading tools. Without tools, smart models invent plausible file:line citations. With tools, the model can inspect source and ground claims.

Prior project post-mortems showed the same lesson repeatedly: non-agentic completions are unsafe for grounded review; agentic shims are safe only when prompts require inspection and deterministic checks.

Operating rule:

- Review, verification, and bug-finding use codex, agy, kimi, grok, qwen, or opencode shims.
- Authoring/generation can use any agentic shim, but must be verified by deterministic project gates.
- Bare non-tool completions are not a review route.

## Dispatch mechanics (managed event channel)

- In a Claude routing-active session, launch children only through `dispatch_and_wait`. It returns an ask, a terminal receipt, a bounded `still_running` handle, or an honest orphan/error result.
- Set `route` or `provider`, include the complete task in `prompt`, and set `workspace`, `task_mode`, `timeout_seconds`, and `ask_support` deliberately.
- Answer a returned ask with `answer_and_wait`, reattach with `wait_dispatch`, and steer with `steer_and_wait`. Do not run a model-driven `inbox` loop.
- Fire independent managed dispatches in one message when you need throughput. A sibling terminal result must not release another active dispatch.
- Direct shims and native `Agent` calls are foreground-only, non-interactive fallback surfaces outside routing-active sessions. Never background them or put them behind `&`, `nohup`, `setsid`, or an opaque wrapper.

## Dispatch pattern -- single call

1. Decide the route and effort flags.
2. Put the task and expected artifacts in the dispatch prompt; use an isolated workspace for write tasks.
3. Call the managed MCP tool and follow its returned event.

```text
mcp__pitwall-channel__dispatch_and_wait({
  "provider": "codex",
  "prompt": "Inspect the forecast module, implement the parser, run its decisive tests, and report artifact paths.",
  "workspace": "isolated",
  "task_mode": "write",
  "timeout_seconds": 1200,
  "ask_support": true,
  "wait_seconds": 30
})
```

The result is a structured child event. If it is an ask, answer it with the full dispatch and ask IDs; if it is `still_running`, use its reattach handle; if it is terminal, inspect the listed artifacts and rerun the decisive host checks.

## Dispatch pattern -- parallel N (throughput priority)

1. Prepare all prompts and artifact paths.
2. Call `dispatch_and_wait` once per independent unit in one assistant message.
3. Handle each event independently. Do not treat completion delivery as proof that the child can ask the parent.

For rate-limit waves, reduce fan-out or choose another configured route. Retry MiniMax only for its documented stall, up to three times. Keep provider headroom conservative until the ledger proves higher concurrency.

## Pilot before fan-out

For any new prompt template, dispatch N=1 first. Inspect output and artifacts. Check that the model did not copy placeholders from the prompt exemplar. Use complete real exemplars, not skeletons.

## Filesystem-as-truth

The completion text is a receipt, not the source of truth. After every authoring dispatch:

```bash
ls -la <expected-path>
wc -l <expected-path>
head -20 <expected-path>
```

For code, run the project gate. Missing, empty, or wrong-shape files are failures regardless of the summary.

## All shims wrap agentic harnesses -- implications

When dispatched, opencode, codex, and Grok Build can read the tree, write files, run commands, fix errors, and report completion.

- "Dispatch -> stage -> synthesize -> integrate" is often wrong: one agentic dispatch can author and verify.
- Suppression-shortcut risk remains. Prompt against suppression directives and inspect artifacts.
- One unit of work per call.
- If a unit exceeds the ceiling, split it or deliberately raise `PITWALL_AGENTS_TIMEOUT_SECS` and `BASH_MAX_TIMEOUT_MS`.

## The boundary -- what stays inline (transport restatement)

| Stays inline | Why |
|---|---|
| Picking model and shim | Strategic. |
| Writing prompt body | The prompt is the spec. |
| Designing schemas/type hierarchies/scoring | Cross-cutting. |
| Synthesizing across responses | Synthesis is judgment. |
| Final production wording | User-facing judgment. |
| Final integration review | Gate output still needs inspection. |
| Test infrastructure quality decisions | Determines whether gates matter. |

## Parsing the response

Standalone shims run CLIs in plain-text mode. Do not pass or expect injected JSON flags. Do not write jq recipes for shim output.

The complete output ends with:

```text
SHIM-DONE exit=<n>
```

The reply is everything before that final sentinel line:

```js
const replyText = (stdout) => stdout.split('\n').filter((line) => !/^SHIM-DONE exit=\d+$/.test(line)).join('\n').trimEnd()
```

If the sentinel is absent, treat the output as clipped or still running. Prefer artifacts on disk for structured data. For MiniMax, empty text before the sentinel is a stall (OpenCode records it as exit `77`); retry the same model up to 3 times.

Exit codes a dispatch reports (the last line is always `SHIM-DONE exit=<n>`):

- `0` the harness finished. `75` the run paused on an ask: answer it, then `pitwall agents runs resume <dispatch-id>`.
- `64` usage error, with no run and no ledger row: any invalid invocation, such as a flag where the prompt source or the model belongs, a `PITWALL_AGENTS_TIMEOUT_SECS` that is not a positive duration, or a dispatch ID that already exists. The prompt file (or `-` for stdin) comes first, and a file named `-x` is written `./-x`. `--help` or `-h` prints usage ending in the sentinel, exits `0`, and creates no run.
- `77` the harness exited `0` without doing the work: Antigravity's `jetski: no output produced` soft denial, OpenCode exiting `0` with empty stdout, or Hermes exiting `0` after an HTTP failure line because it did not attach credentials for a non-loopback endpoint (run `pitwall agents profiles sync --harness hermes`). Treat it as a failed run. The reason is recorded as `softDenialReason` in `run.json`, the ledger row, and the terminal event, and as `soft_denial_reason` on a managed `terminal` result.
- `78` the route needs configuration: apply the remedy printed on stderr and never reroute silently.
- `124` the run hit its timeout (`PITWALL_AGENTS_TIMEOUT_SECS`, or the managed dispatch's `timeout_seconds`).
- `130` Ctrl+C. The supervisor stops the harness's whole process group before it exits, so no child is orphaned, and the run is recorded as cancelled. Lifecycle hooks still pending for that event are skipped, each leaving a status file `{"skipped": "interrupted"}`.
- `143` an abort (a `stop` steer not honored in time, or SIGTERM or SIGHUP to the supervisor), reported even when the harness itself exited `0`; `137` when the grace period ended in SIGKILL.

## codex-shim model / effort overrides

The Codex CLI owns available model strings and effort flags. Typical forms:

```bash
~/.claude/scripts/codex-shim.sh /tmp/task.md -m gpt-6.1-sol -c model_reasoning_effort=low
~/.claude/scripts/codex-shim.sh /tmp/task.md --model=gpt-6-astra
~/.claude/scripts/codex-shim.sh /tmp/task.md -m gpt-6-luna
~/.claude/scripts/codex-shim.sh /tmp/task.md -c model_reasoning_effort=medium
```

Consult the Codex CLI help/docs for current model and effort names.

## grok-shim model / effort overrides

The shim defaults to `grok-4.7`. Grok Build accepts `-m`/`--model` and Grok 4.7 supports `--effort low|medium|high|xhigh`; xAI documents `high` as the default.

```bash
~/.claude/scripts/grok-shim.sh /tmp/task.md --effort medium
~/.claude/scripts/grok-shim.sh /tmp/task.md --model=grok-4.7 --sandbox workspace
```

Scripted invocations automatically include `--no-auto-update`, `--no-alt-screen`, plain output, and the prompt via `-p`. In unrestricted mode the shim also passes `--always-approve`.

## opencode-shim profile overrides

opencode exposes provider/model routes via `opencode models`. It may also expose flags such as `--variant`, `--thinking`, and `--agent <name>` depending on version and provider.

Pass flags through after the prompt file:

```bash
~/.claude/scripts/opencode-shim.sh zai-coding-plan/glm-5.3 /tmp/p.md --variant high --agent plan
```

## MiniMax M3 thinking toggle

MiniMax M3 has a reasoning visibility toggle surfaced by opencode as `--thinking`. It is binary, not an effort dial. Leave it off for normal dispatch; turn it on only when the reasoning trace is itself useful.

## GLM empty-completion via opencode

Historical provider/adapter observation: older opencode builds could return no text for GLM despite a successful process exit; the shim now records that as exit `77`. If GLM goes empty again, first upgrade opencode and rerun a small pong. If the route remains empty, switch the affected work to Kimi or codex and record the failure in the ledger.

## Why Sonnet, not Haiku

Transport agents use Sonnet because they must ferry long stdout faithfully. Haiku has historically truncated or stalled on content-bearing transport. The shim's job is not deep reasoning, but reliable byte transport still needs a robust Claude node.

## Failure modes -- transport layer

| Symptom | Likely cause | Action |
|---|---|---|
| `opencode` command not found | CLI missing | Install opencode and verify with `opencode --version`. |
| `opencode auth list` shows no usable providers | Provider login missing | Run `opencode auth login` for the needed providers. |
| `kimi` command not found | Kimi Code CLI missing | Install Kimi Code and verify with `kimi --version`. |
| Kimi Code authorization failure | Missing Kimi login | Run `kimi login`. |
| Codex authorization failure | Expired/missing Codex login | Run `codex login`. |
| `grok` command not found | Grok Build CLI missing | Install Grok Build and verify with `grok version`. |
| `qwen` command not found | Qwen Code CLI missing | Install Qwen Code and verify with `qwen --version`. |
| `agy` command not found | Antigravity CLI missing | Install Antigravity CLI and verify with `agy --version`. |
| `pi` command not found | Pi CLI missing | Install Pi and verify with `pi --version`. |
| `hermes` command not found | Hermes Agent CLI missing | Install Hermes Agent and verify with `hermes --version`. |
| `cline` command not found | Cline CLI missing | Install Cline CLI and verify with `cline --version`. |
| `muse` command not found | Muse Code CLI missing | Install Muse Code and verify with `muse --version`. |
| `goose` command not found | goose CLI missing | Install goose and verify with `goose --version`. |
| `dsh` command not found | DeepSeek Harness CLI missing | Install dsh and verify with `dsh --version`. |
| `zcode` command not found | ZCode CLI missing | Install ZCode (or set `ZCODE_BIN`); see `docs/agents/zcode.md`. |
| `authentication required` | Antigravity is not signed in | Run `agy` once interactively, or configure `modelProvider` `gemini` plus `GEMINI_API_KEY`. |
| `jetski: no output produced` | Antigravity command tool was soft-denied in restricted headless mode | The shim exits `77` (EX_NOPERM); re-run unrestricted or add a permissions.allow rule in `~/.gemini/antigravity-cli/settings.json`. |
| `SHIM-DONE exit=64` | Invalid invocation: a flag where the prompt source or model belongs, a bad `PITWALL_AGENTS_TIMEOUT_SECS`, or a dispatch ID that already exists | Fix the invocation (prompt file or `-` first); no run or ledger row was created. |
| `SHIM-DONE exit=77` | The harness exited 0 without doing the work | Treat as failed; `softDenialReason` in `run.json` says whether a permission was denied, stdout was empty, or Hermes reported an HTTP failure. |
| `SHIM-DONE exit=124` or `130` | Timeout, or Ctrl+C | Split the unit or raise the timeout deliberately; a Ctrl+C leaves no orphaned child. |
| `route-shim.sh` exits `78` | Route needs configuration | Run the `pitwall agents profiles sync --harness <h>` command from stderr or export the named key variable; do not reroute. |
| Grok Build authorization failure | Missing browser/device login or API key | Run `grok login`, `grok login --device-auth`, or set `XAI_API_KEY` for headless use. |
| GLM returns no text (`SHIM-DONE exit=77`) | opencode/provider adapter issue | Upgrade opencode, retry pong, then route failed work elsewhere if needed. |
| First run hangs during setup | CLI initializing local state | Wait briefly; rerun small pong. |
| Missing `SHIM-DONE exit=<n>` | Clipped output or still-running child | Check artifact on disk; split unit or raise `PITWALL_AGENTS_TIMEOUT_SECS` and `BASH_MAX_TIMEOUT_MS`. |
| `SHIM-DONE exit=<n>` is present but artifact missing | Agent failed to write expected file | Re-dispatch with exact write path and verify. |
| Multi-unit prompt drops later units | Per-invocation context/turn cap | One unit per call. |
| Suppression directive added | Shortcut around gate | Re-dispatch with no-suppression instruction and inspect files. |
| Reviewer hallucinates file lines | Non-agentic route used | Use codex, agy, kimi, grok, qwen, or opencode shims with file inspection. |
| Routed model ignores optional MCP instruction | Tool not configured in that CLI | Treat MCP as additive; reroute only if the task requires that tool. |

## Observability (optional)

The ledger JSONL is the quantitative record for every dispatch (`started`/`finished` with `wall_s`, `exit`, and `outcome`). Users with an OTLP collector can set `OPENCODE_OTLP_ENDPOINT` for OpenCode spans and standard `OTEL_*` environment variables for Codex spans; the shim appends `gen_ai.request.model` for per-dispatch attribution. See the root `README.md` `## Observability` section for setup. A localhost example is `http://localhost:4318`.

## Cost / quota

Use whatever subscriptions/endpoints your CLIs are authenticated to. Planning is about rate limits, endpoint capacity, and wall-clock. Failed retries cost time even when they do not cost per-call money, so track success rate and pivot to scripts/templates when dispatch is not paying off.

## Smoke test -- transport layer

```bash
echo "Reply with exactly: pong" > /tmp/pong.md
opencode auth list
~/.claude/scripts/kimi-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/codex-shim.sh /tmp/pong.md -c model_reasoning_effort=low | tail -n 1
~/.claude/scripts/grok-shim.sh /tmp/pong.md --effort low | tail -n 1
~/.claude/scripts/qwen-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/agy-shim.sh /tmp/pong.md --effort low | tail -n 1
~/.claude/scripts/pi-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/hermes-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/cline-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/muse-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/goose-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/dsh-shim.sh /tmp/pong.md | tail -n 1
~/.claude/scripts/zcode-shim.sh /tmp/pong.md | tail -n 1
pitwall agents profiles list
opencode models
grok models
```

Expected complete shim output ends with `SHIM-DONE exit=<n>`.

## See also

- `scripts/codex-shim.sh`, `scripts/agy-shim.sh`, `scripts/kimi-shim.sh`, `scripts/opencode-shim.sh`, `scripts/grok-shim.sh`, `scripts/qwen-shim.sh`, `scripts/pi-shim.sh`, `scripts/hermes-shim.sh`, `scripts/cline-shim.sh`, `scripts/muse-shim.sh`, `scripts/goose-shim.sh`, `scripts/dsh-shim.sh`, `scripts/zcode-shim.sh`, and `scripts/route-shim.sh` in this repo.
- `plugins/claude/agents/{codex,kimi,opencode,grok,qwen,route,agy,pi,hermes,cline,muse,goose,dsh,zcode}-shim.md`.
- `plugins/claude/skills/subagent-model-routing/ARCHITECTURE.md`.
- `references/model-prompting.md` for the self-contained runtime model reference.
- `plugins/claude/README.md` for installation and custom-provider routing.

### ZCode account route

Use `~/.claude/scripts/zcode-shim.sh <prompt-source>` or
`pitwall agents dispatch route zcode <prompt-source>` for the saved ZCode
model/account. The profile model is `zcode-default`; this CLI has no per-run
model or effort override. It uses the account already configured in ZCode.
