# Research: Bidirectional Orchestrator ↔ Subagent Channel for Agent Routing

- **Date:** 2026-09-08
- **Status:** Design decisions D1–D6 (§14) operator-approved 2026-09-08. **Phase 0 and
  Phase A are implemented and merged to `main`**. The single plan for that batch is
  `docs/superpowers/plans/2026-09-08-orchestrator-channel-phase-0-a.md` (implementation
  Tasks 0–11, review fix pass Tasks 12–17, docs corrections Tasks 18–22, audit fixes
  Task 23); exit
  checks green as of 2026-09-09 (root lane 5046 passed / 76 skipped / 143 deselected;
  component 587 passed / 4 skipped; ruff + mypy clean). D6 refined per the fix pass to
  the TTL-bounded contract (see §14). **Phases B–D are implemented on
  `feat/orchestrator-channel-b-d`** (Tasks 0–30 of
  `docs/superpowers/plans/2026-09-10-orchestrator-channel-phases-b-d.md`), which also
  closed the Phase A spec items the first batch skipped (§10 default expiry, D1 derived
  deadlines and per-dispatch cap, D2 pointers, §9.3 ledger fields, §11 property and chaos
  tests) and the concurrent-writer defect found while grounding it. Scripted exit checks
  green as of 2026-09-11: component suite `Ran 707 tests … OK (skipped=4)`; ruff clean;
  mypy `Success: no issues found in 66 source files`; `validate_plugins`, `check_generated`,
  `check_markdown_links` (186 links), and `sync_routes --check` all pass; the scripted Phase
  B/C/D exits (`test_channel_tier1_integration`, `test_channel_steer_integration`,
  `test_workflow_channel_e2e`) pass. The §13 **live** exit checks (Phase B per installed
  harness, Phase C scope-change, Phase D unattended fan-out) spend real model calls and are
  **operator-run steps still pending** — they were not executed in this implementation pass.
  Touches the Agent
  Routing component — keep the standard-library-only runtime contract unless the
  operator explicitly approves otherwise (nothing in this design requires breaking it).
- **Related:** `docs/research/2026-09-08-omniroute-free-tier-integration.md` (free-tier
  context for the §9.2 auto-answer policy; that doc's §14 Phase D),
  `docs/sdlc/23-agent-routing.md`, `docs/agents/routing-readme.md`.
- **Grounding:** three review passes against code on 2026-09-08; factual corrections
  applied — see §15 (pass 1), §16 (pass 2), §17 (pass 3).

---

## 1. Problem

Agent Routing dispatches whole units of agentic work to foreign CLI harnesses
(`src/pitwall/agents/providers/`: claude, codex, agy, kimi, grok, qwen, opencode, pi,
hermes, cline, muse, goose, dsh) and verifies completion via the `SHIM-DONE exit=<n>`
sentinel + optional `SHIM_RESULT` JSON receipt (`result.py`, `dispatch.py`). The contract
is **fire-and-forget**: once a dispatch starts, the subagent cannot ask the orchestrating
model a clarifying question, and the orchestrator cannot steer the subagent mid-task.
Two failure classes follow:

- Subagents guess on ambiguity (wrong assumption → wasted run, wrong worktree diff).
- Orchestrators discover mid-flight that scope/priority changed and can only wait or
  kill.

We want a **bidirectional channel**: `ASK` (sub → orch clarifications) and `STEER`
(orch → sub mid-task directives), with `ANSWER` closing the loop and `REPORT` already
existing as events.

## 2. Constraints (these shape everything)

1. **Subagents are foreign harnesses.** We do not control their loops; we only get the
   affordances each harness ships: files, argv/stdin prompts, hooks, exit codes, and —
   decisively — **MCP client support** in the modern ones (Claude Code, Codex, Copilot
   CLI via the existing plugin bundles).
2. **The orchestrator is usually turn-based.** An interactive orchestrating harness only
   *acts* at turn boundaries; a channel that assumes a live orchestrator thread will
   deadlock. The design must work with polling/inbox semantics, not push-into-context.
3. **Unattended dispatch is the norm.** Shims default to unattended runs; any blocking
   wait must be bounded and must degrade to a default or a clean resumable exit.
4. **Stdlib-only runtime.** Mailbox = files (atomic tmp+rename, same pattern as the
   personal-mode `leases.json`); broker = stdlib `http.server`; MCP server = stdlib JSON
   plumbing. No new runtime dependencies.
5. **Security posture.** Everything loopback/local; subagent-written data is untrusted
   input (size-capped, schema-validated, never executed); mailbox inherits the private
   run-record permission discipline (0700 dirs / 0600 files).

## 3. Design principles

- **A contract around the harnesses, not inside them.** The channel is four message
  types, one directory layout, one exit code, one MCP server (§6.1). Anything needing
  more is a per-harness adapter concern, not a protocol change.
- **Files are the source of truth; processes are facades.** HTTP/MCP/inbox are views
  over the same mailbox. Crash-survivable, auditable, zero-dep by construction.
- **No deadlock is a protocol invariant.** Every `ASK` carries `default` + `deadline`;
  every wait is bounded; escalation (to workflow policy, then to the operator) is
  explicit.
- **Resume-first.** A dispatch that pauses on a question is a *paused run*, not a
  failure. The worktree isolation (`workspace.py`, `runs diff/apply/discard`) already
  gives paused runs a durable home.

---

## 4. Protocol primitives

### 4.1 `ASK` — subagent → orchestrator

`mailbox/asks/NNN.json`:

```json
{
  "version": 1,
  "dispatch_id": "…",
  "ask_id": "0001",
  "created_at": "2026-09-08T17:00:00Z",
  "blocked_on": "choice",
  "question": "Migrations 0031 and 0032 both touch rate_buckets — apply 0032 first?",
  "context": {
    "worktree": "…",
    "files_touched": ["db/migrations/0032_….sql"],
    "options": [
      {"id": "a", "text": "Apply 0032 then 0031"},
      {"id": "b", "text": "Rebase 0031 onto 0032"}
    ]
  },
  "default": "b",
  "default_rationale": "0031 is additive; 0032 renames a column 0031 reads",
  "deadline_s": 600,
  "severity": "normal"
}
```

Rules: `default` is mandatory (may be `"abort"`); body ≤ 64 KiB; `options` ≤ 8.
Schema-validated on write *and* read (subagent output is untrusted). Two approved
decisions shape these fields (§14):

- **D1 — deadlines are derived, not fixed:** `deadline_s = min(3600, 15% of the
  dispatch's remaining timeout)`; global cap 5 asks per run, overridable per dispatch.
- **D2 — pointer-based context:** asks carry `files_touched` + option ids only, never
  embedded diffs; answerers get context on demand via
  `pitwall-agent-routing runs diff <dispatch_id>`.

### 4.2 `ANSWER` — orchestrator → subagent

`mailbox/answers/NNN.json`: `{"version": 1, "ask_id": "0001", "answered_by":
"operator|orchestrator|policy", "choice": "b", "note": "…", "answered_at": "…"}`.
Append-only; the ask is resolved by exactly one answer (broker enforces via atomic
rename into `answers/`).

### 4.3 `STEER` — orchestrator → subagent

`mailbox/steer/NNN.json`:

```json
{
  "version": 1,
  "dispatch_id": "…",
  "kind": "note | scope | budget | priority | stop",
  "message": "Scope narrowed: skip the Redis driver, SQLite only.",
  "requires_ack": true,
  "deadline_s": 300,
  "created_at": "…"
}
```

`kind=stop` is the graceful abort: the subagent should wrap up, emit its `SHIM-DONE`
receipt (contract unchanged), and exit 0/its natural code — steering must never bypass
the completion sentinel or the ledger loses its unit of accounting.

### 4.4 `REPORT`

Existing event stream unchanged: `events.py` (`EventEmitter`) appends a per-run
`events.jsonl` artifact plus a **global** `state_root/events.jsonl` — the global tail is
the broker's natural watch feed. The loopback HTTP surface is separate: see §5.2.

---

## 5. Transport layer

### 5.1 Mailbox layout (source of truth)

```
$XDG_STATE_HOME/subagent-model-routing/runs/<dispatch_id>/mailbox/   # default layout
  asks/0001.json        # written by subagent (or its MCP tool)
  answers/0001.json     # written by broker on behalf of answerer
  steer/0001.json       # written by orchestrator
  acks/0001.json        # subagent acknowledgement of steering
  dead-letter/…         # schema-invalid or oversized writes, quarantined not dropped
```

State root and run naming follow `run_store.py` exactly (verified): override via
`SUBAGENT_MODEL_ROUTING_STATE_HOME`, else `$XDG_STATE_HOME/subagent-model-routing`
(default `~/.local/state/subagent-model-routing`); run directories are
`runs/<dispatch_id>` keyed by the dispatch UUID — the channel uses `dispatch_id`
terminology throughout to match the result schema's `dispatchId`.

- Atomic writes: write tmp in same dir, `os.replace`. Directory 0700, files 0600
  (matches `run_store.py` discipline).
- Sequence numbers are zero-padded, monotonic per box; readers glob-sorted.
- The run store is **artifact-based** (verified: `RunStore.write_json` artifacts,
  `prompt.deliver.md` delivery prompt, `record_request` request records) — the run
  directory gains `mailbox/` plus a small `mailbox.json` summary artifact (counts,
  unresolved ask ids, last steer id acked) that `runs show` renders, so it answers
  "is anything waiting on me?" without globbing.

### 5.2 Broker

The component already ships a loopback HTTP receiver: `pitwall_sync.py::run_receiver`
(stdlib `ThreadingHTTPServer`, loopback-only incl. an IPv6 `::1` variant), exposed as
`pitwall-agent-routing pitwall receiver` with companion `pitwall subscribe` / `pitwall
watch` commands and a `doctor` check (`_check_pitwall_receiver`). **The channel broker is
that receiver extended**, not a new server (still loopback-only, stdlib): it tails the
global `state_root/events.jsonl` (poll ~1 s, inotify-free for portability) plus mailbox
directories, and exposes

- `POST /asks` (subagent-side, optional convenience), long-poll `GET /asks?wait=`
- `POST /answers/{id}`, `POST /steer`
- `GET /inbox?dispatch_id=` — orchestrator polling surface (unresolved asks + unacked steers)

Files remain authoritative; HTTP is a facade. The broker never interprets content beyond
schema validation, size caps, and sequencing. **The channel endpoints inherit the
receiver's existing discipline verbatim** (verified in `run_receiver`): mandatory HMAC
(`X-Pitwall-Signature`; 503 while the secret is unset), 1 MiB body cap,
replay-dedup by delivery id (409 on replay) — the channel's `POST /asks`, `/answers`,
`/steer` get the same guards, and `/inbox` long-poll stays read-only + unauthenticated
loopback like `/health` only if the operator opts in; default is signed.

---

## 6. The MCP path (primary question mechanism)

The cleanest `ASK` primitive is an **MCP tool call**: it is the one affordance modern
harnesses expose that natively supports *blocking mid-task* semantics. The plugin
bundles (`plugins/pitwall`, `pitwall-codex`, `pitwall-copilot`) exist as the
per-harness installation surface for the three major harnesses — but they ship skills
and agent definitions only today, so MCP registration is new surface they grow (§6.3).

### 6.1 New stdio MCP server in Agent Routing

`pitwall-agent-routing mcp` — a minimal stdio-only MCP server inside the component
(keeping the channel owned by Agent Routing, avoiding any broker-DB env requirements in
subagent processes; the main `pitwall-mcp` server stays out of it). Tools:

| Tool | Caller | Behavior |
|---|---|---|
| `ask_orchestrator` | subagent | Writes `asks/NNN.json`, then **blocks** polling `answers/NNN.json` until answer or deadline; returns the answer, or `{"resolved_by": "default", …}` on timeout. Caps: one open ask per run at a time (configurable), total asks per run (default 5) — forces subagents to batch real questions. |
| `read_steering` | subagent | Non-blocking: returns unacked steers. Called opportunistically at natural boundaries (the routing skill teaches: before risky tool calls, after each milestone). |
| `ack_steer` | subagent | Acknowledges; `kind=stop` instructs graceful wrap-up. |
| `inbox` | orchestrator | Lists unresolved asks / unacked steers across runs (the orchestrator-side poll surface for interactive topologies). |
| `answer_ask` | orchestrator | Writes the answer via broker semantics. |

The routing skill gains the questioning discipline: *when to ask* (only when the default
is dangerous and irreversible), *how to ask* (options + a real default + rationale),
*when not to* (anything answerable from the worktree). Location note (verified): the
skill lives in the plugin bundles — `plugins/*/skills/subagent-model-routing/SKILL.md`
(with the `LEDGER:RANKINGS` block) — **not** in `prompting/`, which holds per-model
prompting references (`00-prompt-reference-index.md`, one file per model family); those
references gain a short per-family "asking through the channel" note as a secondary
teaching surface.

### 6.2 Why MCP-first

- Blocking tool call = native ask-and-wait inside the harness loop; no polling code in
  the subagent, no prompt-contract reliance.
- One protocol surface across every MCP-capable harness — one adapter instead of
  thirteen.
- Fails safe: if MCP isn't configured, the tool is absent and the subagent falls back to
  defaults (or the coarse ladder in §7) — the dispatch still completes.

### 6.3 Installation and registration (the honest cost)

MCP tools only exist in a harness whose config registers the server. Verified: the
plugin bundles today ship **skills and agent definitions only**
(`plugins/pitwall/agents/*-shim.md`, `plugins/pitwall-copilot/skills/…`) — no MCP
configuration anywhere in `plugins/`. Registration is therefore new per-harness
installation surface:

| Harness | Registration surface (verified against `capability_inventory.py` `HarnessProfile`) | Notes |
| --- | --- | --- |
| Claude Code | `~/.claude.json` (user scope) or project `.mcp.json` | stdio server entry: command `pitwall-agent-routing`, args `["mcp"]`; profile also reads `${CLAUDE_CONFIG_DIR}` overrides |
| Codex | `${CODEX_HOME:-~/.codex}/config.toml` → `[mcp_servers.…]` | same command shape |
| Copilot CLI | `~/.copilot/mcp-config.json` (+ `~/.copilot/config.json`) | dedicated MCP config file |
| OpenCode | `${XDG_CONFIG_HOME}/opencode/opencode.json` → `mcp` section | |
| Kimi | `${KIMI_CODE_HOME:-~/.kimi-code}/mcp.json` | dedicated MCP config file |
| Cline | `${CLINE_MCP_SETTINGS_PATH}` else `${CLINE_DATA_DIR:-${CLINE_DIR:-~/.cline}/data}/settings/cline_mcp_settings.json` | Cline 3.x reads `cline_mcp_settings.json` (function `nC()` in the installed 3.0.61 binary), not `mcp_settings.json` (corrected in §19) |

The `HarnessProfile` file lists are the source of truth for these paths — registration
tooling reads the same profiles, so the table cannot drift from the inventory.

Decisions:

1. **User scope by default.** Subagent dispatches run with injected `SUBAGENT_MODEL_ROUTING_*`
   environment and workspace modes `shared | isolated | auto` (`workspace.py`
   `WORKSPACE_MODES` — isolation is a *mode*, not unconditional); project-scope discovery
   is unreliable across those modes, so registration defaults to user scope and the shim
   may additionally inject a worktree-local config where a harness prefers project scope.
   Tier-4 resume (§7) presumes `isolated`/`auto`; the routing skill should bias
   ask-prone dispatches to isolated mode.
2. **Registration is idempotent installer work**: the existing plugin
   install/`provider_setup.py` machinery gains a `--with-mcp` step that writes/merges the
   entry per harness; uninstall removes it. Never a manual step for the operator. Note
   (verified): the plugin manifests today are description-only
   (`plugins/pitwall/.claude-plugin/plugin.json` = name/version/author/description/
   keywords; Copilot's likewise) — MCP registration is a *new manifest surface* shipped
   by each bundle per its harness's plugin schema, not a field that already exists.
3. **`doctor.py` verifies end-to-end**: spawn `pitwall-agent-routing mcp`, perform the
   MCP initialize + `tools/list` handshake, confirm `ask_orchestrator` is present, and
   confirm the target harness's config file references it. Doctor failure = tier-1
   unavailable, tier-4 still functional (reported, not fatal).
4. **Capability inventory** (`capability_inventory.py`) gains an `mcp_channel` flag per
   harness so the dispatcher and routing skill know the tier before dispatch — steering
   the skill to teach tier-1 questioning to MCP-capable harnesses and tier-4 discipline
   to the rest. This is a small extension, not a new inventory pass: the inventory
   already tracks an `mcps` capability kind (secret-safe — redacts
   authorization/password/secret/token fields, maps `mcp`/`mcpservers` config sections,
   2 MiB per-config cap), so "does this harness have MCP configured" is already
   answerable today.

---

## 7. Fidelity ladder (per harness)

| Tier | Mechanism | `ASK` | `STEER` | Applies to |
|---|---|---|---|---|
| 1 | MCP tools (§6) | blocking tool call | `read_steering` at boundaries | every harness whose profile carries MCP config (§6.3): Claude Code, Codex, Copilot CLI, OpenCode, Kimi, Cline, … |
| 2 | Harness hooks (new) | n/a | hook config shipped by the plugin bundles reads the mailbox at harness boundaries (`UserPromptSubmit`/tool gates); `kind=stop` can deny further tool calls until ack | harnesses with lifecycle-hook config (Claude Code, Codex) |
| 3 | Native bidirectional sessions (stream-json/proto) | true mid-session turns | true injection | any single harness we elect to invest in; **defer** |
| 4 | Coarse contract (universal) | prompt contract: "write `asks/NNN.json`, exit 75" → orchestrator answers → **run resumes** with answer + prior transcript appended; worktree state persists (`workspace.py`) | advisory prompt-prefix on resume; `stop` via SIGTERM + graceful sentinel | every remaining harness, including dumb CLIs |

Tier 4 is the guarantee that no harness is second-class: asking costs one exit and one
resume, never a lost run. Exit-code facts (verified in `process.py:279`): **124 is
already reserved for timeout and 130 for cancellation**; 75 (`EX_TEMPFAIL`) is free and
becomes "paused on question". Two contract notes from the review:

- **Do not extend the result schema in v1.** `result.py::validate_result` enforces an
  exact field set and `status ∈ {succeeded, failed, timed_out, cancelled,
  preflight_failed}` — there is no `paused`. A paused dispatch records a *lifecycle
  event* (global `events.jsonl`) + the exit-75 classification, and emits **no result
  document**; the resumed run's final result covers the whole dispatch with ask counts
  in the run record. Schema extension (adding `paused`) stays a deliberate v2 option.
- **Resume reuses the dispatch id** (verified constraint): worktrees are keyed by
  dispatch id (`workspace.py::worktree_path(env, dispatch_id)`) and attempts already
  flow through `SUBAGENT_MODEL_ROUTING_ATTEMPT` (`_dispatch_context`), so tier-4
  resume = same dispatch id + `attempt++` + a new prompt built from the original plus
  the answered Q&A. Minting a fresh dispatch id would orphan the worktree and break
  ledger linkage.
- **The signal-handling surface is the Python supervisor, not bash traps.** The shell
  shims are thin `exec` wrappers over the shared runtime (`claude-shim.sh` →
  `pitwall-agent-routing _shim claude`), and the sentinel protocol is stdout-based
  (`_emit_sentinel` writes `SHIM-RESULT {json}` then `SHIM-DONE exit=<n>`). A
  graceful-abort SIGTERM handler belongs in the Python `_shim` supervisor calling
  `_emit_sentinel` before tearing down the child — cleaner than shell `trap`s, and
  pass-1's "shims need traps" note is superseded by this. Until it lands, the
  dispatcher's SIGTERM→SIGKILL escalation (`process.py:119–146`) still means a
  receipt-less kill.

## 8. STEER delivery ladder

1. **Advisory note** — present on next `read_steering`/resume boundary; subagent told
   (skill + prompt contract) to check before destructive actions.
2. **Blocking gate** — where hooks exist, deny tool calls until `ack_steer` (use for
   `scope`/`priority` with `requires_ack`).
3. **Graceful abort** — `kind=stop`: SIGTERM handler in the Python `_shim` supervisor
   (the shell shims `exec` into it) emits the receipt via `_emit_sentinel` then exits;
   never SIGKILL first (ledger integrity), SIGKILL only after a grace period with the
   run marked `killed`. (See the supervisor note in §7 — this is Python-side work, not
   bash `trap`s.)

---

## 9. Orchestrator-side topologies

### 9.1 Interactive harness as orchestrator (today's model)

The orchestrating model is itself turn-based. Surfaces: `inbox` MCP tool /
`pitwall-agent-routing inbox` CLI (unresolved asks table); routing skill instructs the
orchestrator to poll between dispatch batches; unanswered asks past a soft threshold
escalate to the operator (TUI notification / terminal bell — the human is the final
orchestrator in a single-operator world). `ASK`s awaiting the operator are *not*
deadlocked: the subagent's `deadline_s` keeps running and defaults fire on expiry.

### 9.2 Pitwall-hosted loop (durable workflows)

`workflow.py` gains a `wait_for_answer` step type: a paused dispatch, resumed by the
workflow engine when the answer lands. The scheduler already gives this a home with the
right primitives (verified): per-run `_WorkflowFileLock` file locking, a
`_StateController`, PID-liveness checks (`_runner_active`), and an `AttemptOutcome`
record — `wait_for_answer` slots in as a scheduler-level step using the same lock
discipline rather than a new concurrency mechanism. Full bidirectionality becomes
native, and this enables the **auto-answer policy**:

```
ASK arrives
  → policy check (severity, blocked_on class)
  → routine (choice of files/flags/naming) → answer via a cheap/free model
    (free-tier gateway prong; clearly logged answered_by="policy:<model>")
  → consequential (schema, destructive ops, spend) → orchestrator inbox → operator
```

Synergy with the free-tier gateway plan (see the Related pointer above): routine
clarification traffic is exactly the workload a free pool absorbs well;
`answered_by` provenance keeps audit honest.

Eligibility is fixed by **D3 (§14)**: policy-eligible asks are
`blocked_on ∈ {choice, naming, file-selection}` × `severity: normal` × non-abort
default present, and policy responses may only select among the ask's provided
options — never free-text directives. `schema`, `destructive`, `spend`, and anything
`blocking` escalate to the operator unconditionally.

### 9.3 Ledger extensions (`run_store.py`)

Record Q&A pairs on the run: ask count, ask rate per hour, resolution source
(operator/orchestrator/policy/default), steer count/ack latency. Two payoffs:

- **Routing-quality signal**: ask-rate and steer-ack latency per harness/model become
  dispatcher scoring inputs (a harness that asks fewer, sharper questions is finishing
  work, not stalling) and `/pitwall:distill` training data.
- **Audit**: every mid-flight scope change is attributable. (Ledger location verified:
  `SUBAGENT_MODEL_ROUTING_LEDGER` override, else
  `~/.claude/subagent-model-routing/ledger/observations.jsonl` — `dispatch.py:53–57`.)

Retention is fixed by **D5 (§14)**: raw Q&A pairs expire with the run record under the
existing retention discipline; before expiry a distill step appends aggregates (counts,
`blocked_on` classes, resolution sources, latency-to-answer) to that ledger — the
routing signal survives, the code content does not.

---

## 10. Deadlock & abuse policy (protocol invariants)

- One open `ASK` per run; N asks per run cap (default 5); each with mandatory
  `deadline_s`; expiry applies the default and logs `resolved_by: "default"`.
- Broker rejects oversized/malformed writes to `dead-letter/` (quarantine, never
  interpret; mirrors the webhook size/rate discipline).
- `STEER` `deadline_s` + unacked-expiry → orchestrator sees "steering ignored" signal
  in inbox (informs kill decisions).
- MCP server: loopback stdio only, no network transport — consistent with the component
  and with the broker's MCP posture (SDLC 03).
- Rate caps on `POST /asks` per run (shims can't spam), sequence gaps tolerated
  (crash-safe) but logged.

## 11. Testing sketch (component lanes)

- Unit: mailbox atomicity (concurrent writer/reader), schema validation truth table,
  broker sequencing, deadline/default expiry, steer ack state machine.
- Property: no interleaving of writes loses messages; resume-with-answer replay is
  deterministic; caps hold under adversarial ask floods.
- Integration: fake harness (script) exercising tiers 4 → 1; MCP server against a
  scripted MCP client; workflow `wait_for_answer` round-trip; SIGTERM graceful-abort
  receipt emission.
- Chaos: kill broker mid-answer (mailbox survives, retry idempotent); kill subagent
  mid-ask (run resumable, ask orphaned→defaulted).

## 12. Folded scope: broker MCP server hygiene

MCP work in this plan touches MCP plumbing across the ecosystem (new channel server,
plugin registration, doctor verification). The broker's existing MCP implementation has
known hygiene gaps — verified in code 2026-09-08 — that should be corrected on the same
PR train so the ecosystem's MCP story is coherent before the channel lands on top of it.

### 12.1 `pitwall_health` is registered outside the registry (verified)

`src/pitwall/mcp/__init__.py:40-44` defines `pitwall_health()` decorated with
`@mcp.tool()` on the module-level `FastMCP("pitwall")` instance, registered just before
`register_all(mcp)` at `:46` — adjacent to but outside `TOOL_REGISTRY`;
`src/pitwall/mcp/registry.py:113` and `:322` assert
`len(TOOL_NAMES) == 75` / `len(_REGISTRY_BY_NAME) == 75`. The served surface is
therefore **76 tools while every count and doc says 75**.

Correction: fold `pitwall_health` into `TOOL_REGISTRY` as a `ToolSpec` (count becomes
76), assign it an explicit scope class, and update the count assertions, SDLC 03, and
the support matrix in the same change.

### 12.2 Error codes: the partitioning mechanism exists but is unpopulated (verified,
and smaller than first written)

`src/pitwall/mcp/error_adapter.py` already ships the extension point:
`register_error_code(error_code, mcp_code)` populates `_API_ERROR_CODE_TO_MCP_CODE`,
and `adapt_error` falls back to `PITWALL_ERROR_CODE_BASE = -32000` for unmapped codes
(the structured `ErrorData.data` payload — `to_response_body()`/`to_dict()` shape — is
preserved either way). The gap is that **no code registers any mapping**: the registry
is empty at boot, so every error lands on `-32000`.

Correction (revised): add a bootstrap that registers error-class codes through the
existing `register_error_code` — e.g. authn/z `-32001`, budget/spend-gate `-32002`,
validation `-32003`, upstream/provider `-32004`, conflict/state `-32005` — keeping
`-32000` as the unmapped fallback. No new machinery. The channel MCP server (§6.1)
adopts the same scheme from day one (ask-deadline expiry and steering conflicts get
their own codes), so future orchestrator tooling branches on codes uniformly across
both servers.

### 12.3 Raw RunPod resource tools bypass lease/cost rails (verified)

`src/pitwall/mcp/tools/runpod_resources.py` — the 29 raw resource tools (count
verified: 29 `pitwall_runpod_*` functions) create billable resources **outside lease
tracking with no self-termination deadline**; SDLC 03 warns about this twice. This is
the one gap that is a spend-safety issue rather than cosmetic. One helpful fact from
the review: mutations are already flagged at the adapter boundary (`_service(mutation=
True)` / `_call(..., mutation)`), so correction (a) has an existing seam to key off —
no new classification pass over tool definitions is needed.

Correction ladder — **decided 2026-09-08, split by resource kind (D6, §14)**:

- **Pod-creating tools → full lease wrapping (b):** `create_pod` returns a lease id and
  the created pod inherits the existing lease lifecycle end-to-end (readiness, TTL,
  teardown, cost close). No second tracking system for the expensive always-billing
  resource.
- **Non-pod mutations (endpoint updates, actions) → minimum gating (a):** BudgetGate
  admission + recording, without forcing lease semantics onto resources that do not
  bill continuously.

### 12.4 Count-assertion drift (verified)

User journey J10 asserts ≥ 20 MCP tools (stale); the support matrix claims 75; the
served surface is 76. Correction: generate journey assertions from `TOOL_REGISTRY`
(single source of truth) and add a docs-sync check that fails when the registry count
diverges from the documented count — same pattern as the broker's other count-sync
gates.

## 13. Phased plan (acceptance-gated, operator-selected)

**Phase 0 — broker MCP hygiene (independent of the channel; prerequisite for coherence,
not for tier-4).** Items §12.1, §12.2, §12.4 in one change-set; §12.3 implemented per
the approved D6 split (pod tools → lease wrapping; non-pod mutations → gating).
*Exit:* served tool count == registry count == documented count;
error codes partitioned with `-32000` fallback retained; `create_pod` returns a lease
id with TTL/teardown inherited; remaining mutating RunPod tools spend-gated and
recorded.

1. **Phase A — mailbox + coarse tier (4).** Layout, schemas, exit 75, resume-with-answer,
   run-record extensions, `inbox` CLI. *Exit:* a scripted dispatch pauses on a question,
   resumes with the answer, ledger shows the Q&A pair. (Verb precedent: the CLI already
   ships `workflow resume` / `workflow cancel` — run-level resume follows the same
   convention rather than inventing new verbs.)
2. **Phase B — MCP server + tier 1.** `pitwall-agent-routing mcp` with the five tools;
   registration tooling covers the full §6.3 table (the three existing bundles plus
   direct registration for Kimi/Cline/OpenCode); skill gains questioning discipline.
   *Exit:* live Claude Code subagent asks mid-task via tool call and receives
   an operator answer; default-expiry path proven.
3. **Phase C — steering.** STEER schema + tiers 1–2 delivery + SIGTERM graceful abort;
   inbox surfacing of unacked steers. *Exit:* mid-task scope change lands in a live run
   without killing it.
4. **Phase D — workflow wait + auto-answer policy.** `wait_for_answer` step; policy
   routing routine asks to a configured (possibly free-tier) model with provenance.
   *Exit:* end-to-end unattended fan-out where ≥1 routine clarification is policy-answered
   and ≥1 escalates to the operator.

## 14. Decisions (operator-approved 2026-09-08)

All six former open questions were resolved on 2026-09-08; recommendations approved as
proposed. Decision approval is not work authorization — phases still require explicit
selection per the workspace change discipline.

- **D1 — Ask caps and deadlines.** Global cap 5 asks per run (per-dispatch override
  via dispatch options; per-harness tuning only if Phase A ledger data shows
  bimodality). `deadline_s` is derived per ask: `min(3600, 15% of the dispatch's
  remaining timeout)`. A fixed deadline ignores dispatch length; the cap keeps the
  ask-rate routing signal honest.
- **D2 — Pointer-based ask context.** v1 asks carry `files_touched` + option ids
  only — no embedded diffs (subagent-written untrusted input: exfiltration and size
  risk). The `inbox`/`answer_ask` surfaces advertise
  `pitwall-agent-routing runs diff <dispatch_id>` for on-demand context. Revisit only
  if ledger data shows answerers mis-answering for lack of context.
- **D3 — Auto-answer eligibility.** Policy-eligible =
  `blocked_on ∈ {choice, naming, file-selection}` × `severity: normal` × non-abort
  default present; policy responses are restricted to selecting among the ask's
  provided options — never free-text directives. `schema`, `destructive`, `spend`,
  and anything `blocking` escalate to the operator unconditionally.
  `answered_by: policy:<model>` provenance is mandatory.
- **D4 — Broker inbox deferred.** v1 surfaces are the agent-routing CLI and the
  orchestrator `inbox` MCP tool. No broker endpoint, no component import. A future TUI
  panel may read the documented mailbox layout read-only; that coupling is a separate
  explicit decision.
- **D5 — Two-tier Q&A retention.** Raw Q&A pairs expire with the run record under the
  existing retention discipline; before expiry a distill step appends aggregates
  (counts, `blocked_on` classes, resolution sources, latency-to-answer) to the existing
  ledger (`observations.jsonl` / `/pitwall:distill`) — the routing signal survives,
  the code content does not.
- **D6 — RunPod resource gating, split by kind.** Pod-creating tools get full lease
  wrapping: `create_pod` returns a lease id. *As-built (fix pass 2026-09-09):* the
  contract is TTL-bounded — expired raw-pod leases are terminated by the existing
  lease expiry reconciler and cost-closed from `max_cost_per_hour` when supplied;
  raw pods expose no probe surface, so no readiness lifecycle applies (the original
  "inherits readiness/TTL/teardown/cost-close" phrasing overstated readiness).
  Non-pod mutations get BudgetGate admission + recording only. Avoids a
  second parallel tracking system for the expensive always-billing resource without
  forcing lease semantics onto non-continuously-billing resources.

## 15. Review correction log (2026-09-08 grounding pass)

Claims corrected against code during the comprehensive review:

1. **Receiver misattribution (§5.2).** The loopback receiver is
   `pitwall_sync.py::run_receiver` (CLI `pitwall receiver`, with `subscribe`/`watch`
   companions), not `events.py` — which is a JSONL `EventEmitter` appending per-run and
   global `events.jsonl`. The broker design now extends the real receiver and tails the
   global event log.
2. **Mailbox path (§5.1).** Corrected to `run_store.py`'s actual layout
   (`$XDG_STATE_HOME/subagent-model-routing/runs/<dispatch_id>/…`, with the
   `SUBAGENT_MODEL_ROUTING_STATE_HOME` override) and `dispatch_id` terminology.
3. **Tier-2 hooks misattribution (§7).** `hooks.py` is agent-routing's own fail-open
   operator hook runner (`~/.config/subagent-model-routing/hooks.json`, depth-capped) —
   a dispatcher-side *reaction* surface, not a harness-side injection mechanism.
   Harness hooks are new plugin-shipped config (tier 2 rewritten). Bonus: `HookRunner`
   is the natural place for operator-defined reactions to channel events
   (`dispatch.paused`, `steer.unacked`).
4. **Env/isolation wording (§6.3).** "Sanitized environments" → injected
   `SUBAGENT_MODEL_ROUTING_*` env + workspace *modes* (`shared|isolated|auto`);
   isolation is not unconditional, so tier-4 resume needs the skill to bias ask-prone
   dispatches to isolated mode.
5. **Cross-reference (header).** The "synergy in §10" pointer was ambiguous/wrong;
   now points at §9.2 here and the gateway doc's §14 Phase D.

Newly surfaced constraints incorporated (not corrections): exit codes 124/130 already
reserved (`process.py:279`); strict result schema argues for event-not-schema pause
recording in v1; shims lack signal traps today; `runpod_resources` mutations already
flagged at the adapter boundary; `capability_inventory.py` already inventories `mcps`
(secret-safe) — verified along with: 13 provider modules, `SHIM-DONE` sentinel
(`dispatch.py:125`), `SHIM_RESULT` opt-in (`:112`), 0700/0600 run-store modes, zero
runtime dependencies (`dependencies = []`), `runs diff/apply/discard` CLI, J10's
stale `≥ 20 tools` assertion (`docs/operator/user-journey-catalog.md:33`), and the 75
vs 76 broker MCP count.

## 16. Second-pass findings (same date, deeper flows)

1. **§12.2 revised — the fix is smaller than written.** `error_adapter.py` already
   exposes `register_error_code()` + `_API_ERROR_CODE_TO_MCP_CODE`; the map is simply
   never populated. Population-at-bootstrap, not new machinery.
2. **Broker auth/caps inherited, not invented.** `run_receiver` already enforces
   loopback-only bind, mandatory HMAC (`X-Pitwall-Signature`, 503 when unset), 1 MiB
   body cap, and replay-dedup (409). The channel endpoints adopt this discipline
   verbatim (§5.2 updated).
3. **Tier-4 resume has a concrete mechanism.** Worktrees are keyed by dispatch id and
   attempts already flow via `SUBAGENT_MODEL_ROUTING_ATTEMPT` — resume = same dispatch
   id, `attempt++`, prompt rebuilt with the answered Q&A. A fresh dispatch id would
   orphan the worktree (§7 updated).
4. **Signal handling lands in the Python supervisor.** Shell shims are `exec` wrappers
   over `pitwall-agent-routing _shim`; the sentinel protocol is stdout-based
   (`_emit_sentinel`). Graceful abort = SIGTERM handler in the supervisor, superseding
   the pass-1 "bash traps" framing (§7, §8.3 updated).
5. **Plugin manifests are description-only.** Phase B's "plugin bundles configure it"
   means adding a *new* manifest surface per harness plugin schema (§6.3 updated) —
   scope it as such, not as filling in an existing field.
6. **Workspace default is `shared`** (`RoutingOptions.workspace = "shared"`), which
   strengthens the pass-1 caveat: isolated mode is opt-in, so the skill bias for
   ask-prone dispatches matters for tier-4's resume guarantee.

## 17. Third-pass findings (same date, remaining unverified claims)

1. **`register_error_code` has zero callsites** (verified by grep) — §12.2's "registry
   is empty at boot" claim is correct as written; no change.
2. **`pitwall_health` registration mechanism pinned precisely** (§12.1): it is a
   `@mcp.tool()` decorator on the module-level `FastMCP("pitwall")` instance in
   `src/pitwall/mcp/__init__.py:40-44`, registered *before* `register_all(mcp)` at
   `:46` — i.e., adjacent to but outside `TOOL_REGISTRY`. Wording in §12.1 was
   accurate; now cites the decorator.
3. **Routing-skill location was wrong in §6.1** (corrected): the skill lives in
   `plugins/*/skills/subagent-model-routing/SKILL.md`; `prompting/` holds per-model
   prompting references and becomes a secondary teaching surface only.
4. **Run-record shape imprecise in §5.1** (corrected): the run store is
   artifact-based (`RunStore.write_json`, `prompt.deliver.md`, `record_request`), so
   the mailbox summary is a `mailbox.json` artifact, not a "section" of a record.
5. **Registration table upgraded from recollection to verified**
   (§6.3): paths now come from `capability_inventory.py` `HarnessProfile` file lists,
   which adds **Kimi** (`~/.kimi-code/mcp.json`) and **Cline**
   (`~/.cline/…/mcp_settings.json`) to the MCP-capable set and pins Copilot's exact
   user-scope path. The profiles become the tooling's source of truth so the table
   cannot drift.
5a. **Cline's MCP path corrected** (§6.3, 2026-09-10): the §6.3 row and
   `capability_inventory.py` originally listed `mcp_settings.json`, but Cline
   3.0.61 reads `$CLINE_MCP_SETTINGS_PATH`, else
   `${CLINE_DATA_DIR:-${CLINE_DIR:-~/.cline}/data}/settings/cline_mcp_settings.json`
   (function `nC()` in the installed binary). Both the profile and the table now
   resolve `cline_mcp_settings.json` with those environment overrides.
6. **`workflow resume` / `workflow cancel` subcommands already exist** (missed by the
   pass-1 grep window) — noted in Phase A as the verb precedent for run-level resume.
7. **Seats verified as a role enum** (`SEATS = default-author | critical | review |
   burst | throughput | local`) — relevant context for any future "orchestrator" or
   "gateway" seat values; no change to this design.

Pass-3 yield: three corrections (one attribution error, one shape imprecision, one
unverified-becomes-verified table) and four confirmations. Diminishing — remaining
unverified surface is confined to design choices (schemas, caps, policy boundaries)
rather than claims about existing code.

## 18. Fourth-pass findings (internal consistency; convergence)

Pass 4 re-read the assembled document end-to-end after three edit rounds and found six
issues — one factual contradiction and five consistency defects, all introduced or
survived by editing, none from misreading code:

1. **§6 intro contradicted §6.3** (corrected): it still claimed the plugin bundles
   "already install per-harness MCP configuration" — the exact claim §6.3 disproves.
   Rewritten to point at §6.3.
2. **Stale cross-reference in §9.2** (corrected): "the free-tier gateway doc's §10
   synergy" pointed at this doc's own Deadlock section; replaced with the Related
   pointer.
3. **Terminology drift** (corrected): §4.1/§4.3 JSON examples and §5.2's query param
   used `run_id` after §5.1 standardized on `dispatch_id`; all three fixed.
4. **§3 said "one MCP tool"** while §6.1 defines five; now "one MCP server".
5. **§12.1 now cites the decorator** (`@mcp.tool()` at `__init__.py:40-44`, before
   `register_all(mcp)` at `:46`), matching what §17.2 already claimed.
6. **Phase B scope clarified**: three bundles exist, six tier-1 harnesses —
   registration tooling covers the full §6.3 table.

**Convergence assessment:** pass 4's yield contains zero new code-claim errors — every
finding was self-inflicted edit drift or an already-verified fact expressed
inconsistently. The code-grounded surface is fully verified (§15–§17); the design
surface that remained open (§14) was resolved by operator decision on 2026-09-08.
Further passes would re-read prose, not claims. Stopping here.
