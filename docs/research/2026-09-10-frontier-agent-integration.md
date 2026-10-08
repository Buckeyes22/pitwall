# Frontier agent integration research

- **Date:** 2026-09-10
- **Status:** Research and proposal only. Nothing in this document is authorized work. Per workspace
  discipline, an idea is implemented only when it is explicitly selected as current work.
  Proposals 4 (`pitwall mcp install <harness>`), 5 (`pitwall doctor --json`), and 7 (committed
  agent-facing installation docs) were selected as a first wave and are implemented on
  `feat/agent-install-first-wave`, tracked in
  `docs/superpowers/plans/2026-09-11-agent-install-first-wave.md`. The other eight proposals
  remain unselected.
- **Scope:** How Pitwall can integrate tightly with frontier coding agents and harnesses — Claude
  Code, Codex, and OpenCode (GLM, MiniMax, local models) — with the goal of making Pitwall feel
  *native* inside those harnesses rather than like an external tool the agent shells out to.

## What "native feel" means

An integration is native when the user never leaves their harness and Pitwall appears through the
harness's own idioms:

| Native idiom | Harness expression | Pitwall equivalent today |
| --- | --- | --- |
| Model provider config | base-URL/auth entries the harness already reads | gateway wire translation, unused by harnesses |
| MCP server registration | `.mcp.json`, `config.toml`, `opencode.json` entries | `pitwall-mcp` exists, manual registration only |
| Slash commands / prompts | `/pitwall status`, Codex prompts, OpenCode commands | agent-routing plugin packs; nothing for the broker |
| Permission dialogs | approve/deny on guarded tools | TUI confirmation modals |
| Status surfacing | statuslines, hooks output, watch loops | polling `pitwall status` |

Today an agent that wants to help with Pitwall must read prose docs, shell out to the CLI, and
parse JSON. Every tier below replaces one of those clamps with a native idiom.

## Existing assets this builds on

- **`pitwall-mcp`** — 76 statically registered tools over the same feature services as REST/CLI
  (`src/pitwall/mcp/registry.py`, `ToolSpec` dataclass, `register_all()`). Tools only: no MCP
  prompts or resources are served today.
- **`@pitwall/gateway`** — hardened loopback HTTP shim (port 20130, `PITWALL_GATEWAY_TOKEN`
  required, `PITWALL_GATEWAY_UPSTREAM_URL` upstream) translating `openai`, `claude`, `gemini`, and
  `responses` inbound shapes (`packages/gateway/src/shim.ts`, `config.ts`, `translation.ts`).
- **Personal serving** — `pitwall serve --model ... --route ornith` plus `route-shim.sh ornith`
  (`src/pitwall/personal/routes.py`, `state.py`, `service.py`); a no-database local serving path.
- **Agent Routing** — thirteen CLI shims, `route-shim.sh`, routing skills, tripwire hooks, ledger,
  and per-harness plugin packs (`plugins/claude`, `pitwall-codex`,
  `pitwall-copilot`) plus `.claude-plugin/marketplace.json`. These are the proven patterns for
  shipping harness-native UX from this repository.
- **Audit / readiness** — the pre-spend capability audit (eight required checks plus
  ready-to-invoke, `src/pitwall/audit/capability.py`) and its MCP exposure
  (`src/pitwall/mcp/tools/audit.py`).
- **Operator TUI** — Textual `PitwallApp` with per-screen data-source abstractions
  (`src/pitwall/tui/app.py`).
- **Kill-switch, budget admission, cost exporter** — the safety spine any agent surface must
  route through.

---

## Tier 1 — Pitwall as the agent's model backend

These proposals give the deepest integration: the harness's *own* model traffic flows through
Pitwall, so the agent benefits from routing, cost gating, and the kill-switch without knowing
Pitwall exists.

### 1. `pitwall connect <harness>`

**Idea.** One command per harness that writes the harness's own provider configuration so that the
harness's native completion calls are served by Pitwall — either by a personal-serving pod
(`pitwall serve`) or through the free-tier gateway.

**Why it advances native feel.** The tightest possible coupling is not "the agent can call Pitwall
tools" but "the agent's model *is* Pitwall's business." When Claude Code's completions hit a
Pitwall-brokered endpoint, every line the model generates passes through cost admission, budget
caps, quota signals, and the kill-switch. The user experiences their harness exactly as before;
the control plane moves underneath it.

**How it would work.**

- `pitwall connect claude-code` writes the env/settings shape Claude Code already honors for
  custom model endpoints: an `ANTHROPIC_BASE_URL` pointing at the gateway's loopback listener with
  the `claude` inbound shape, plus the gateway token wired as the auth env var. The user's next
  Claude Code session talks Anthropic wire protocol to `127.0.0.1:20130`, and the gateway
  translates to whatever upstream Pitwall selected.
- `pitwall connect codex` writes a `config.toml` model-provider sketch (name, `base_url`,
  `env_key` for the token, `wire_api`) pointing at the same listener with the `openai` or
  `responses` inbound shape selected per what the upstream serves.
- `pitwall connect opencode` writes/merges an `opencode.json` provider entry
  (`@ai-sdk/openai-compatible` with `baseURL` on loopback, `apiKey` from a Pitwall env var) plus
  model ids matching the personal route or gateway catalog.
- Each variant prints exactly what it changed, where, and the one-line rollback. Illustrative
  shapes only — the precise keys get pinned during implementation against each harness's current
  docs.

**Sub-problems to solve.**

- **Route selection:** `connect` needs a target — an existing personal route name, a model from
  the catalogue, or a gateway free-tier executor. `pitwall connect claude-code --route ornith`
  reuses the route registry rather than inventing a new selection mechanism.
- **Lifecycle:** the connection should survive only as long as its backend. If the personal pod is
  stopped or its TTL expires, `connect` state should degrade visibly (a probe command, not a
  silent hang) — `routes.py` already models `probe`/`exists`, and the same semantics extend to
  harness connections.
- **Never widen the blast radius:** all emitted configs point at loopback only. The gateway
  already refuses non-loopback binds; `connect` must never emit a non-loopback URL, and the
  fail-closed posture (refuse on missing token) carries through.

**What exists today.** The gateway's translation layer and hardening (bearer token, 1 MiB body
cap, 120 rpm per-token rate limit, structured error envelopes) are done and CI-gated. Personal
serving can produce an OpenAI-compatible pod endpoint. The missing piece is purely the config
emission layer and its per-harness tests.

**Verification shape.** Hermetic tests that run `connect` against a temp `HOME` and assert the
emitted files parse and contain only loopback URLs; a mocked harness-config round-trip
(write → read back → detach restores prior content). Live verification against a real harness
session is an explicitly authorized live operation, same as other live lanes.

### 2. Unified route registry

**Idea.** Make the personal-serving route table the single named-model registry shared by the
agent-routing shims and by harness provider configs, so `ornith` is one backend identity usable
from `route-shim.sh`, Claude Code, Codex, and OpenCode alike.

**Why it advances native feel.** Today "a model I can use" lives in several places: harness
provider configs, `route-shim` profiles in agent-routing, and Pitwall routes. A user who serves a
model with Pitwall should be able to point any harness at it by the same name, and an agent
orchestrating shims should see the same registry. One identity per backend removes an entire
class of "which URL was that" friction that currently forces copy-paste between tools.

**How it would work.**

- `routes.py` already has the right verbs: `attach(route, base_url, model_id, key_env)`,
  `probe(route)`, `remove(route)`, `exists(route)`. The route record becomes the canonical unit:
  `{name, base_url, model_id, auth env, backend kind (personal pod | gateway executor), TTL
  deadline, cost envelope}`.
- Agent-routing's `route-shim.sh` resolves profiles through this registry when the runtime is
  available (today it has its own profile files; the registry becomes an additional resolution
  source, not a replacement — agent-routing stays functional standalone per its stdlib-only
  contract).
- `pitwall connect` (proposal 1) consumes the same registry instead of asking for a URL, so the
  config a harness receives and the shim a CLI invokes cannot drift apart.
- `pitwall routes list --json` gives agents one introspectable view: what backends exist, what
  they cost, whether they are currently probeable.

**Design considerations.**

- **Ownership boundary:** the registry lives in the root Pitwall project (personal serving).
  Agent-routing reads it opportunistically but keeps its own fallback; no new cross-package
  dependency is introduced and the two uv projects stay independent.
- **Secrets stay indirect:** the registry stores `key_env` names, never key material — same rule
  as `routes.py` today.
- **Naming:** route names must remain DNS/CLI-safe single tokens (`ornith`, not
  `My Model (test)`), since they appear in filenames, shell profiles, and harness model-id fields.

**Verification shape.** Unit tests over registry CRUD and probe semantics; an agent-routing test
that resolves a registry-backed profile and a standalone profile through the same code path;
drift-style test that `connect` output for a route matches `routes list` for the same route.

---

## Tier 2 — Agent-facing operations surface

These proposals make Pitwall operable *by* agents through MCP and self-describing state, instead
of by parsing prose.

### 3. MCP prompts and resources

**Idea.** Extend `pitwall-mcp` beyond tools: serve guided workflows as MCP *prompts* (install,
onboard, serve, teardown, incident) and reference/runbook/state material as MCP *resources*.

**Why it advances native feel.** Every harness that speaks MCP already has UI for prompts and
resources: Claude Code surfaces server prompts as slash commands; Codex and OpenCode list them in
their MCP tooling surfaces. A guided-install prompt means the "clone the repo and point an agent
at it" story works with *zero* repository reading — the agent asks the server for the install
workflow and receives a curated, version-matched script. The server becomes its own documentation
for agentic use, and the docs and the guidance can never go stale relative to the shipped binary
because they ship together.

**How it would work.**

- **Prompts** (`prompts/list`, `prompts/get`): each is a named, argument-bearing template that
  returns a ready-to-run instruction block — for example `pitwall_guided_install` (args:
  `mode=registry|personal`), `pitwall_onboard_runpod`, `pitwall_serve_model` (args: model,
  gpu-class, ttl), `pitwall_teardown`, `pitwall_cost_review`. The prompt text embeds the exact
  tool calls and verification steps, so the receiving agent does not improvise.
- **Resources** (`resources/list`, `resources/read`): stable URIs such as
  `pitwall://readiness`, `pitwall://routes`, `pitwall://budget`, `pitwall://support-matrix`,
  `pitwall://runbook/<name>`. Read-only, cheap, cacheable state the agent can poll or cite.
- **Registration symmetry:** mirror the existing `ToolSpec` pattern — a `PromptSpec`/`ResourceSpec`
  table in `src/pitwall/mcp/` with the same static-registration discipline
  (no dynamic surface area, reviewed descriptions, safe boundary honored).

**Design considerations.**

- **Static-only, same as tools:** prompts and resources are declared in code, not loaded from
  user-writable files at runtime, preserving the auditability guarantee the MCP surface currently
  makes (`registry.py` header documents this contract for tools; prompts inherit it).
- **Prompt text is product surface:** it must follow the same docs-check/style discipline as
  operator docs, and tests should assert prompt bodies mention the verify step (`doctor` from
  proposal 5) so walkthroughs always self-check.
- **Scope creep guard:** prompts are *workflows over existing tools*, not a second scripting
  language. If a prompt needs a capability no tool provides, the tool gets built — the prompt
  never shells out.

**Verification shape.** Unit tests asserting the prompt/resource catalogs enumerate completely,
URIs resolve, and each install/onboard prompt references only tools that exist; a golden test
pinning prompt bodies so wording changes are deliberate diffs.

### 4. `pitwall mcp install <harness>`

**Idea.** One command that detects installed harnesses and registers `pitwall-mcp` into each
one's native config, idempotently, printing what changed and how to verify.

**Why it advances native feel.** The gap between "Pitwall has an MCP server" and "my agent uses
it" is hand-editing JSON/TOML in three different formats. That gap is where most users stop. A
single idempotent registrar that speaks all three formats turns MCP adoption from a setup
project into a one-liner, and it composes with proposal 3: register the server, and the guided
workflows arrive with it.

**How it would work.**

- `pitwall mcp install` with no argument detects harnesses (presence of `~/.claude.json` or
  `.mcp.json`, `~/.codex/config.toml`, `opencode.json` / `.opencode/`) and reports what it would
  do; `pitwall mcp install claude-code codex opencode` scopes explicitly; `--project` vs
  `--user` chooses config scope.
- Emitted entries (illustrative shapes, pinned at implementation):
  - Claude Code: a `pitwall` entry in the project `.mcp.json` `mcpServers` map —
    `{"command": "pitwall-mcp", "args": [], "env": {}}`.
  - Codex: a `[mcp_servers.pitwall]` table in `config.toml` with `command` and `args`.
  - OpenCode: a local-server entry in `opencode.json` —
    `"mcp": {"pitwall": {"type": "local", "command": ["pitwall-mcp"]}}`.
- **Idempotency and merge discipline:** the registrar parses the existing file, merges by key,
  preserves foreign entries byte-for-byte, and refuses (rather than overwrites) if a `pitwall`
  entry exists with a different shape unless `--force`. Backups go to a sibling file before any
  write; `pitwall mcp uninstall` is the exact inverse.
- It ends by printing the harness-native verification step (`claude mcp list` / restart Codex /
  `opencode` command listing) so the user confirms in-harness, not in a log.

**Design considerations.**

- **Fail-closed on ambiguity:** unparseable configs abort with the parse error and no write;
  partial multi-harness installs continue per-harness but report per-harness failures honestly.
- **Resolution of the server command:** emit `pitwall-mcp` from the same environment that runs
  the registrar (`sys.executable -m` fallback or absolute console-script path) so a uv-managed
  install produces a config that survives shell changes.
- **No network, no telemetry:** detection is filesystem-only.

**Verification shape.** Table-driven tests over fixture configs for each harness (empty file,
existing foreign servers, existing conflicting `pitwall` entry, malformed TOML/JSON), asserting
merge results, backup creation, and uninstall round-trips to the original bytes.

### 5. `pitwall doctor --json`

**Idea.** One readiness surface answering "what works, what's missing, what next" across install,
config, services, and spend controls — machine-readable for agents, human-readable for operators.

**Why it advances native feel.** A guided-install agent needs a progress meter, not paragraphs.
The audit subsystem already produces exactly this shape for capabilities (eight required checks
plus ready-to-invoke, surfaced as `pitwall_audit_capability` in MCP); `doctor` generalizes the
pattern from "is this capability ready to spend" to "is this Pitwall installation ready to use,"
and the guided-install prompt (proposal 3) ends every step by calling it. Agents get a
deterministic state machine instead of interpreting command exit codes.

**How it would work.**

- Checks grouped by phase, each yielding `{id, status: ok|warn|fail, detail, next_step}`:
  - **Install:** Python version pin, dependency sync, migrations applied, seed present.
  - **Services:** API reachable (and auth configured), Redis reachable, reconciler registered —
    skipped with `warn` in personal mode, which needs no database.
  - **Registry:** capabilities/providers present and healthy (reusing audit internals where the
    questions overlap).
  - **Spend controls:** budget configured, per-request cap set, kill-switch state, burn-rate
    sane.
  - **Dry-run end-to-end:** the same dry-run inference the README quickstart uses, as a
    canary.
- `--json` emits the full table; default output renders it as the CLI's familiar table style.
  Exit code follows the audit convention: non-zero when any check fails, with a `--strict`
  flavor treating warnings as failures.
- Exposed simultaneously as an MCP tool (`pitwall_doctor`) so a registered agent polls it
  directly.

**Design considerations.**

- **Reuse, don't duplicate:** the capability audit's check plumbing (`audit/checks.py`,
  `run_all_checks`) is the model; doctor composes existing probes (DB ping, Redis ping, API
  health) rather than growing parallel logic. Personal vs registry mode must be a first-class
  axis, or the personal path drowns in irrelevant failures.
- **Bounded and offline:** no check may make a paid call; the dry-run canary is dry-run by
  construction. Runtime must be seconds, so live provider probes are opt-in via a flag.

**Verification shape.** Unit tests with stubbed probes covering each status path and both modes;
an integration test against the testinfra compose stack (up → migrate → init → doctor all-green);
assertion that the MCP tool output is a strict subset/superset-consistent view of the JSON.

---

## Tier 3 — Harness-native presence

These proposals put Pitwall inside the harness's own UI vocabulary: commands, permission dialogs,
and shipped plugin packs.

### 6. Broker plugin pack

**Idea.** Ship curated per-harness packs for the *broker* (not just agent-routing): slash
commands, prompts, and skill-style guidance for the operations users actually perform —
`/pitwall onboard`, `/pitwall status`, `/pitwall cost`, `/pitwall serve`, `/pitwall stop`.

**Why it advances native feel.** Slash commands are the harness's native verb form. The repo has
already proven the delivery mechanism: `packages/agent-routing/plugins/` maintains three
harness-specific packs (Claude Code with `commands/`, `hooks/`, `agents/`, `skills/`; a Codex
skills pack; a Copilot pack) distributed through `.claude-plugin/marketplace.json`. Applying the
same machinery to broker operations means a user's first Pitwall experience can be typing
`/pitwall onboard` in the harness they already have open.

**How it would work.**

- A new pack (e.g. `plugins/pitwall-broker/`) with command files whose bodies are short, pinned
  workflows phrased against MCP tools where available and CLI-JSON otherwise: onboard (seed →
  mark healthy → dry-run → doctor), status (routes + pods + leases summary), cost (budget,
  burn rate, top capabilities), serve/stop (personal path wrapper honoring TTL and
  max-usd-per-hour).
- Each command's guardrail text mirrors agent-routing's tripwire doctrine: the command tells the
  agent which output constitutes success (dry-run `result.dry_run=true`, `doctor` all-green) and
  to stop and surface raw output on anything else, so silent delegation failures cannot enter
  context as usable results.
- Codex and OpenCode equivalents in their native forms (Codex prompt files; OpenCode command
  markdown under `.opencode/command/`), generated from a single source of truth so wording does
  not fork across harnesses.
- Marketplace/README wiring so `claude plugin` (or the equivalent) can install it without
  cloning the repo — this is the distribution native feel depends on.

**Design considerations.**

- **One source, many renderers:** the workflows live once (data or markdown), and a checked-in
  generator emits per-harness files; a drift test (the free-tier catalog already established this
  pattern) fails CI when rendered files go stale.
- **Scope discipline:** commands wrap *existing* surfaces only. The pack introduces no new
  runtime behavior — if a workflow needs a missing tool, that's a proposal-3/5 change, not a
  plugin change.
- **Trust and safety text:** every command file states that spend-y steps require explicit user
  authorization in-harness, matching the live-operation doctrine.

**Verification shape.** The drift/regeneration test; link checking over command markdown; a
content test asserting each command references its success criterion and its failure tripwire.

### 7. Committed agent-facing installation docs

**Idea.** A committed, agent-curated onboarding path in the repository — an in-repo agents
document or `docs/agents/<harness>.md` set — so that "clone and point an agent at it" works from
a fresh clone.

**Why it advances native feel.** The current `AGENTS.md` is deliberately local-only (excluded via
`.git/info/exclude`), so the public clone ships zero agent-specific guidance. Harnesses instruct
agents to look for exactly such files (Claude Code reads `CLAUDE.md`/`AGENTS.md` hierarchically;
Codex and OpenCode read `AGENTS.md`). A committed doc is the difference between an agent that
greps the tree hopefully and one that follows the canonical path: sync with the pinned
toolchain, choose personal vs registry mode, run the right init, verify with doctor, register
MCP, connect model backends.

**How it would work.**

- A concise committed `AGENTS.md` at repo root (public counterpart of the local file): repo map,
  command cheat-sheet (`make test-fast`, `make test-int` with `make up`), the
  smallest-complete-change discipline, and pointers into `docs/agents/`.
- `docs/agents/` per-harness pages: exact registration snippets for MCP (cross-linked with
  proposal 4's emitted shapes so docs and registrar cannot disagree — ideally the docs are
  rendered from the same source), the connect story for model backends, and troubleshooting.
- The guided-install prompt from proposal 3 references these pages rather than duplicating them
  (resources can serve them at `pitwall://docs/<page>`).

**Design considerations.**

- **Public/local split stays:** the local AGENTS.md keeps private workflow rules and remains
  uncommitted; the public file carries only what an outside agent needs. The two must be
  explicitly cross-referenced so future editors know both exist.
- **Docs-check compliance:** all markdown links and anchors must pass `make docs-check`;
  generated snippets share provenance with proposal 4/6 outputs to avoid three divergent copies
  of the same config example.
- **Versioning:** the pages state the Pitwall version they match; pre-1.0 drift is real and
  stale agent docs are worse than none.

**Verification shape.** `make docs-check` green; a content test asserting every snippet shown in
agent docs is byte-identical to the registrar/plugin-generator output for the same harness.

### 8. Tool annotations as the confirmation UX

**Idea.** Annotate MCP tools with standard hints (`readOnlyHint`, `destructiveHint`,
`idempotentHint`) so harness permission dialogs become the programmatic counterpart of the TUI's
confirmation modals.

**Why it advances native feel.** Pitwall's safety model already has a human confirmation layer —
`ConfirmLaunchModal`, `ConfirmRoutingJobModal`, `ConfirmVolumeFileModal`, `ConfirmOnboardingModal`
in the TUI — and a spend-gating layer in cost admission. But through MCP today, every tool looks
alike to the harness: an agent can fire a paid operation and the harness has no signal that this
one deserves an approve/deny dialog. Tool annotations are the MCP-standard channel for exactly
that signal, and Claude Code/OpenCode already render them as permission prompts. The result is
that the *harness's own dialog* becomes Pitwall's confirmation modal — zero new UI, correct
behavioral parity with the TUI, and per-session allow/deny rules users already know how to
manage.

**How it would work.**

- Audit the 76-tool catalog and classify: reads (`list_*`, `get_*`, cost summaries) get
  `readOnlyHint: true`; guarded mutations (provider health writes, lease stop/renew, resource
  mutations, kill-switch, seed/init) get `destructiveHint` where appropriate and stay
  non-read-only so harnesses prompt; pure idempotent retries get `idempotentHint`.
- The classification lives in the `ToolSpec` table next to the descriptions, reviewed like any
  schema change (this mirrors how the OpenAPI baseline gates REST semantics).
- The safe-boundary module (`src/pitwall/mcp/safe_boundary.py`) gains the complementary rule:
  guarded tools additionally accept an explicit confirmation parameter or re-confirmation token
  only if a harness lacks annotation support — otherwise annotations alone are the contract
  (smallest complete change; no parallel confirmation machinery unless a demonstrated harness
  gap requires it).

**Design considerations.**

- **Annotations are hints, not enforcement:** the server-side gates (cost admission, audit,
  kill-switch, admin secret) remain the actual enforcement; annotations only align harness UX
  with server-side classification. The mapping between "TUI shows a modal" and "tool is
  non-read-only" must be derived from one table so the two surfaces cannot disagree.
- **Baseline discipline:** add an exported annotation map to the same kind of committed baseline
  used for OpenAPI compatibility, so reclassifying a tool from read-only to guarded is a visible,
  reviewed diff.

**Verification shape.** A unit test asserting every registered tool carries an explicit
annotation (no defaults-by-omission); a parity test deriving the TUI-modal set and the
guarded-tool set from one classification table and asserting they match; baseline diff test.

---

## Tier 4 — Events and co-presence

These proposals keep the human in the loop with the surfaces already built, and give agents
reactive instead of polled awareness.

### 9. TUI bridges: `--tmux`, `--capture`, `--web`

**Idea.** Three opt-in transports for the existing Textual TUI so an agent-guided session and the
operator console can coexist: launch under tmux for interactive side-by-side, render screens
headlessly to images for preview, and serve the app to a browser tab.

**Why it advances native feel.** Harness tool calls are non-interactive and PTY-less, so a live
full-screen app can never render *inside* Claude Code/Codex/OpenCode — the harness owns the
terminal. But "native feel" does not have to mean "same pane": it can mean the agent can show
you the screen you're about to get, put the real console one keystroke away, and read the same
screen you're looking at. The TUI's architecture is unusually ready for this: every screen takes
a data source (`PostgresOverviewSource`, `ServiceProvidersSource`, ... installed in
`src/pitwall/tui/app.py`), so screens can be constructed against real state without a terminal.

**How it would work.**

- **`pitwall tui --tmux [session]`:** start the TUI in a named tmux session (creating the server
  if absent), print the attach command, and exit — or with `--attach` chain straight into it.
  Companion verbs: `pitwall tui --capture-pane` prints the current pane as text (agents read
  what the user sees; this is the same capability agent-routing's tripwires use for output
  discipline) and `--send-keys` drives the TUI for scripted walkthroughs, with guarded screens
  still requiring their own modals (the TUI's own confirmations remain the authority — scripted
  keys get no bypass).
- **`pitwall tui --capture <screen> --svg <path>`:** run headlessly via Textual's pilot/test
  harness, mount the requested screen with real sources, and export with `export_screenshot()`
  to SVG (PNG via the usual converters if needed). Use cases: preview in guided install
  ("here's the Cost screen you'll see next"), regression-golden rendering of every screen in
  CI, and doc generation.
- **`pitwall tui --web`:** serve the app through textual-web on loopback with a token, print the
  URL. The user gets the full interactive TUI in a browser tab adjacent to their harness —
  useful where tmux is unavailable (Windows hosts, web-based harness fronts).

**Design considerations.**

- **Opt-in only:** default `pitwall tui` behavior is unchanged; all three transports are
  explicit flags with loopback-binding rules matching the gateway's posture.
- **Headless mode must not mutate:** `--capture` runs against read-only sources where possible
  and never auto-confirms modals; a capture test that trips a confirmation modal fails rather
  than fakes a keypress, unless the scenario explicitly stages a confirm.
- **tmux dependency is soft:** absent tmux, `--tmux` fails with a one-line actionable error, not
  a traceback.

**Verification shape.** Unit/integration tests with a stubbed tmux binary asserting command
construction and session naming; a pilot-based test capturing every installed screen to SVG with
fixed data sources (doubling as a visual-regression artifact); a smoke test that `--web` binds
loopback and requires a token.

### 10. `pitwall watch --json`

**Idea.** A streaming event channel (`pod ready`, `lease expiring`, `budget threshold crossed`,
`kill-switch tripped`, `reconciler convergence`) as NDJSON on stdout, with an MCP-side
counterpart for subscribed agents.

**Why it advances native feel.** Agents currently learn about Pitwall state changes by polling
`status` commands. Polling is slow, noisy in context, and blind between polls — exactly the
failure mode that motivated webhook idempotency and the reconciler in the first place. A watch
stream lets a harness-resident agent sit reactive: it tails the stream (one background process,
bounded output), and forwards only meaningful transitions into the conversation. The same
events already exist as webhook traffic and reconciler state transitions; this is a *fan-out*,
not new detection logic.

**How it would work.**

- CLI: `pitwall watch --json [--filter pod,lease,budget,killswitch] [--since <ts>]` emits
  newline-delimited events `{ts, kind, severity, subject, detail}` and follows until stopped;
  `--exit-on-quiesce` variants support scripted waits ("stream until pod ornith is ready").
- Transport reuses what each mode has: in registry mode, the same Postgres logical state the
  reconciler converges (poll-with-backoff LISTEN/NOTIFY where available); in personal mode, the
  local state file and pod deadline scheduler. No new daemon — watch is a client of existing
  state.
- MCP side: notifications over the established session (agents that support it get pushed
  events), plus a polling `pitwall_events_since` tool as the portable fallback, so harnesses
  without notification support still get the semantics.

**Design considerations.**

- **Bounded output is a hard rule:** filters default narrowly, events are summarized (no payload
  dumps — webhook receiver's size discipline applies), and the CLI documents a
  `--max-events`/`--timeout` contract so a stuck stream can't flood an agent's context.
- **Read-only:** watch never mutates state and never triggers provider calls; it observes the
  reconciler rather than being one.
- **Event schema is a contract:** versioned kinds, additive-only changes, tested like the
  webhook payload schema.

**Verification shape.** Unit tests over event serialization and filter semantics; integration
test in registry mode driving a state transition through Postgres and asserting the streamed
event; a context-budget test asserting bounded output under event storms.

### 11. Harness hooks as guardrails

**Idea.** Ship small hook/plugin scripts for Claude Code (PreToolUse-style hooks) and OpenCode
(event plugins) that consult Pitwall's kill-switch and budget state before the *harness* runs
agent-initiated live operations, mirroring the pre-spend gate at the harness layer.

**Why it advances native feel.** Pitwall's server-side gates stop Pitwall operations. But an
agent in a harness can also cause spend *through the harness itself* — most importantly once
proposal 1 lands and the harness's own model traffic is Pitwall-brokered, and secondarily when
the agent runs CLI commands directly. A harness hook gives the kill-switch a presence inside the
agent loop: when the switch is tripped or the budget is exhausted, the next guarded action
doesn't fail mysteriously at the network layer — it is blocked at the harness with a human-
readable explanation the agent must surface. The repo already ships exactly this pattern:
`plugins/claude/hooks/` (dag-tripwire, ledger-tripwire) demonstrates
hook scripts with a `hooks.json` manifest, exit-code contracts, and failure-loud behavior.

**How it would work.**

- **Claude Code:** a PreToolUse hook matching Bash/command invocations that contain
  `pitwall`-sponsored live operations (or MCP tool names once proposals 4/8 make those the
  dominant path). The hook is a small script that checks local kill-switch/budget state (a
  fast, offline read — a state file or localhost probe, never a provider call) and exits with
  the harness's block code plus a reason when the switch is tripped.
- **OpenCode:** the same logic as an event plugin in the pack from proposal 6, using OpenCode's
  permission/event surface.
- **Codex:** where per-command hook support is absent, the guardrail degrades to the CLI/MCP
  layer (proposals 5/8) — documented honestly rather than approximated with fragile wrappers.
- **Failure posture mirrors the product:** fail-closed. If the hook cannot determine state, it
  blocks live operations and says why — the same posture as service boot (`os.EX_CONFIG`) and
  the webhook receiver. A guardrail that fails open on a missing state file is decoration.

**Design considerations.**

- **This is defense-in-depth, not enforcement:** the authoritative gates remain server-side
  (cost admission, kill-switch service, harness-level blocks cannot be assumed present). The
  hook's contract is "block-early, explain clearly," and its absence must never be load-bearing
  for safety.
- **Performance budget:** hooks run in the agent's hot path; the state read must be
  single-digit milliseconds (local file or loopback GET with a hard timeout), or the harness
  feels sluggish — a slow guardrail gets disabled by users, which is worse than none.
- **No secret material in hooks:** the hook reads public-by-construction local state (switch
  boolean, budget summary) and never embeds tokens.

**Verification shape.** Unit tests of the hook script's decision table (switch tripped, budget
exhausted, state unreadable, all-clear) and exit codes; an integration test running the hook
binary against staged state files; a latency test asserting the bounded-read property.

---

## Sequencing recommendation

Ordered by value-to-effort and by dependency:

1. **4 → 7 → 5** (MCP registrar, committed agent docs, doctor) — the guided-install story.
   Each is small, hermetic, and independently shippable; together they make "clone and point an
   agent at it" real.
2. **1 + 2** (connect + unified routes) — the differentiating native story: brokered model
   traffic. Highest design care, highest payoff.
3. **3** (prompts/resources) — turns the registrar's output into guided workflows.
4. **6 + 8** (plugin pack + annotations) — presence and confirmation UX; best built after the
   surfaces they wrap stabilize.
5. **9, 10, 11** (TUI bridges, watch, hooks) — co-presence and reactive polish.

## What this document is not

- Not an authorized plan. Each proposal requires explicit selection as current work before
  implementation, per workspace change discipline.
- Not a commitment to the illustrative harness config shapes; those are pinned against current
  harness documentation at implementation time and covered by tests, not trust.
- Not a change to Pitwall's enforcement model: server-side gates stay authoritative in every
  proposal; harness-side UX only mirrors them.
