# Orchestrator Channel — Phase 0 + Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the two decided, immediately-implementable tranches of the orchestrator channel: **Phase 0** (broker MCP server hygiene: registry fold-in, error-code partitioning, count-sync gate, D6 spend-safety for the raw RunPod resource tools) and **Phase A** (agent-routing mailbox + coarse tier-4 ask/resume loop + inbox CLI + broker endpoints).

**Architecture:** Phase 0 is confined to the broker (`src/pitwall/mcp/`, one optional migration, docs). Phase A is confined to the Agent Routing component (`packages/agent-routing/runtime/model_routing/`) — mailbox files are the source of truth, the existing loopback receiver (`pitwall_sync.py`) grows channel endpoints, tier-4 resume reuses the dispatch id with `attempt++`, and pauses are lifecycle events, not result-schema changes.

**Tech Stack:** Broker — Python 3.14, FastMCP, asyncpg, pytest (root project, `uv run --frozen`). Component — Python 3.14 stdlib only (`dependencies = []`), pytest via the component venv, RunPod REST v2 through the existing client.

**Spec:** `docs/research/2026-09-08-orchestrator-channel.md` (grounded design; decisions D1–D6 approved 2026-09-08). This plan implements its §13 Phase 0 and Phase A. Phases B–D, and the Phase A spec items this plan did not cover (deadline-default expiry, D1 derived deadlines and per-dispatch cap, D2 pointers, §9.3 ledger fields, §10 steering-ignored signal and sequence-gap logging, §11 property and chaos tests, operator docs), are planned in `docs/superpowers/plans/2026-09-10-orchestrator-channel-phases-b-d.md`.

**History and status:** this is the only plan for the batch. Tasks 0–11 are the
implementation (2026-09-08). Tasks 12–17 are the review fix pass and Tasks 18–22 the
post-review docs corrections (both 2026-09-09); each was first written as its own plan file
and folded in here on 2026-09-10. Task 23 records the 2026-09-10 audit fixes. Every task is
complete and the work is merged to `main`.

## Global Constraints

- Root repo commands run through `uv run --frozen`; component commands run from
  `packages/agent-routing` with its pinned uv tool (see its `CONTRIBUTING.md`).
- Tests are hermetic; broker `tests/conftest.py` blocks provider DNS. No live RunPod
  calls anywhere in this plan.
- Agent Routing runtime stays standard-library-only (D-decisions add no dependency).
- Credentials never appear in output, logs, argv previews, or mailbox files.
- Commit after every task with `git commit -s`. Work on a focused branch off up-to-date
  `main`; keep `main` clean; do not push unless asked.
- Every behavior change lands with its test in the same task (repo PR rule).
- The completion contract is sacred: nothing in this plan may cause a dispatch to end
  without either a `SHIM-DONE` sentinel or a recorded `paused` classification.

## File Map

| File | Responsibility |
| --- | --- |
| `src/pitwall/mcp/registry.py` | fold `pitwall_health` into `TOOL_REGISTRY`; count asserts 75→76 |
| `src/pitwall/mcp/__init__.py` | remove the direct `@mcp.tool()` health registration |
| `src/pitwall/mcp/error_codes.py` *(new)* | bootstrap population of `register_error_code` |
| `src/pitwall/mcp/error_adapter.py` | unchanged except import of the bootstrap |
| `src/pitwall/mcp/tools/runpod_resources.py` | D6: `ttl_minutes` + lease wrapping for pod creates; gating for non-pod mutations |
| `db/migrations/0033_resource_mutations.sql` *(new, if needed)* | mutation recording if `config_audit` proves unsuitable |
| `docs/sdlc/03-mcp-server.md`, `docs/support-matrix.md`, `docs/operator/user-journey-catalog.md` | count corrections (76), J10 refresh |
| `packages/agent-routing/runtime/model_routing/mailbox.py` *(new)* | schemas, atomic writes, validation, dead-letter, caps |
| `packages/agent-routing/runtime/model_routing/run_store.py` | mailbox dir + `mailbox.json` artifact |
| `packages/agent-routing/runtime/model_routing/events.py` | `dispatch.paused` / `ask.resolved` / `steer.acked` envelopes |
| `packages/agent-routing/runtime/model_routing/dispatch.py` | exit-75 classification; tier-4 prompt contract; resume prompt assembly |
| `packages/agent-routing/runtime/model_routing/cli.py` | `runs resume`, `inbox` subcommands |
| `packages/agent-routing/runtime/model_routing/pitwall_sync.py` | channel endpoints on `run_receiver` |
| `packages/agent-routing/tests/test_mailbox.py`, `test_channel_broker.py`, `test_runs_resume.py`, `test_inbox_cli.py` *(new)* | component tests |
| `tests/mcp/**` (broker; colocate with existing MCP tests — discover exact dir at Task 0) | broker tests |

---

## Phase 0: Broker MCP hygiene

### Task 0: Orient

- [x] Confirm the broker MCP test directory name and the lease-creation service seam
      (do not modify anything):

```bash
ls tests/ | grep -i mcp
git grep -n "def .*lease" src/pitwall/leases/ src/pitwall/api/ | head
git grep -n "error_code" src/pitwall/api/errors.py | head -40
```

Record the actual lease-creation entry point and the error-code vocabulary; Tasks 2
and 4 reference them.

### Task 1: Fold `pitwall_health` into `TOOL_REGISTRY` (§12.1)

**Files:**
- Modify: `src/pitwall/mcp/registry.py`, `src/pitwall/mcp/__init__.py`,
  `docs/sdlc/03-mcp-server.md`, `docs/support-matrix.md`

**Interfaces:**
- Produces: `TOOL_REGISTRY` contains `pitwall_health` as a `ToolSpec`; served count,
  registry count, and documented count are all 76.

- [x] **Step 1: Write the failing test** (broker MCP test dir from Task 0):

```python
def test_health_tool_is_in_registry() -> None:
    from pitwall.mcp.registry import TOOL_NAMES, TOOL_REGISTRY

    assert "pitwall_health" in TOOL_NAMES
    assert len(TOOL_REGISTRY) == 76
    spec = next(s for s in TOOL_REGISTRY if s.name == "pitwall_health")
    assert spec.scope  # explicit scope class assigned
```

- [x] **Step 2: Run to verify failure**

Run: `uv run --frozen pytest tests/mcp/test_registry_health.py -q`
Expected: FAIL (`pitwall_health` not in `TOOL_NAMES`).

- [x] **Step 3: Implement.** Add the `ToolSpec` to `TOOL_REGISTRY` (following the
      literal-spec pattern the registry already uses), update both
      `assert len(...) == 75` sites (registry.py:113, :322) to 76, and delete the
      `@mcp.tool()` block in `src/pitwall/mcp/__init__.py:40-44` (keep the function
      body only if the registry spec imports it; otherwise move it wholesale).
- [x] **Step 4: Verify.** Same pytest command — PASS. Then
      `uv run --frozen pytest tests/mcp -q` (whole MCP suite green).
- [x] **Step 5: Docs.** SDLC 03: fix the intro (75→76), the §"Invariant" lines, and
      the §2 note that currently documents `pitwall_health` as registered separately
      (the sentence at "plus `pitwall_health` (registered separately…)"). Support
      matrix: "75 statically registered tools" → 76.
- [x] **Step 6: Commit.** `git commit -s -m "feat(mcp): fold pitwall_health into TOOL_REGISTRY (76 tools)"`

### Task 2: Error-code partition bootstrap (§12.2)

**Files:**
- Create: `src/pitwall/mcp/error_codes.py`
- Modify: `src/pitwall/mcp/error_adapter.py` (import bootstrap), `docs/sdlc/03-mcp-server.md`

**Interfaces:**
- Produces: `register_error_codes()` populating the existing
  `register_error_code()` map with class codes — authn/z `-32001`,
  budget/spend `-32002`, validation `-32003`, upstream/provider `-32004`,
  conflict/state `-32005`; `-32000` remains the unmapped fallback; structured
  `ErrorData.data` unchanged.

- [x] **Step 1: Write the failing test.** Using two real exception classes from the
      Task 0 error-code inventory (one authz, one budget):

```python
def test_error_codes_are_partitioned() -> None:
    from pitwall.mcp.error_codes import register_error_codes

    register_error_codes()
    from pitwall.mcp.error_adapter import adapt_error

    # substitute the actual classes found in Task 0
    assert adapt_error(SampleAuthzError()).error.data is not None
```

Assert the `ErrorData.code` equals the class code (via `adapt_error(...).error.code`
following the McpError shape).

- [x] **Step 2: Run to verify failure.** Expected: FAIL (no `error_codes` module).
- [x] **Step 3: Implement.** `error_codes.py` maps the existing string error-code
      vocabulary (enumerated in Task 0) to the five class codes via
      `register_error_code`; import its `register_error_codes()` from
      `error_adapter` module scope so any `adapt_error` user gets the populated map.
      Do not invent new error-code strings — map existing ones only; unmapped stays
      `-32000`.
- [x] **Step 4: Verify + document the code table in SDLC 03. Commit.**

### Task 3: Count-sync gate + J10 refresh (§12.4)

**Files:**
- Create: `tests/mcp/test_doc_count_sync.py`
- Modify: `docs/operator/user-journey-catalog.md` (J10 row)

- [x] **Step 1: Write the failing test.** A hermetic test asserting
      (a) the count stated in `docs/sdlc/03-mcp-server.md` and
      `docs/support-matrix.md` equals `len(TOOL_NAMES)`, and (b) the J10 row's
      tool-count claim equals the same number (no more hard-coded `≥ 20`).
- [x] **Step 2: Verify failure → fix the J10 row (and any other stale count the test
      catches) → verify pass → commit.**

### Task 4: D6(a) — `create_pod` lease wrapping

**Files:**
- Modify: `src/pitwall/mcp/tools/runpod_resources.py`, the runpod resources spec
  manifest it contributes, and the lease service entry point found in Task 0.

**Interfaces:**
- Produces: `pitwall_runpod_create_pod` requires `ttl_minutes`, passes through
  `BudgetGate.try_launch_admission` (or the lease path that does), creates a lease
  row, and returns `{"lease_id": …, …}` under a TTL-bounded contract: expired
  raw-pod leases are terminated by the existing lease expiry reconciler and
  cost-closed from `max_cost_per_hour` when supplied; raw pods expose no probe
  surface, so no readiness lifecycle applies. *Refined by the Phase 0/A fix pass
  (Tasks 12–17 below): readiness language
  removed — raw pods have no probe surface.*

- [x] **Step 1: Write the failing tests.** Three behaviors: (1) create without
      `ttl_minutes` → tool error, no service call; (2) create over budget → budget
      rejection surfaces, no pod; (3) successful create (fake service) returns a
      lease id and a lease row exists. Use the existing MCP tool test fixtures.
- [x] **Step 2: Verify failure.**
- [x] **Step 3: Implement.** Thread `ttl_minutes` through the pod-create spec;
      wrap the existing control-plane `_call(..., mutation=True)` path with the lease
      creation service located in Task 0. The reconciler must already converge the
      lease (existing machinery) — do not add a second sweeper.
- [x] **Step 4: Verify pass; update SDLC 03's raw-resource warning paragraphs (the
      two "billable resources outside lease tracking" warnings) to describe the new
      contract. Commit.**

### Task 5: D6(b) — non-pod mutation gating

**Files:**
- Modify: `src/pitwall/mcp/tools/runpod_resources.py`; `db/migrations/0033_resource_mutations.sql`
      only if Task 0 shows `config_audit` is unsuitable for the recording shape.

- [x] **Step 1: Failing tests:** every non-pod mutating tool (update/action/delete/
      terminate paths — enumerate from the 29 `pitwall_runpod_*` functions) performs
      a budget admission check and writes a mutation record; read-only tools do
      neither.
- [x] **Step 2: Verify failure → implement** (key off the existing
      `_service(mutation=True)` boundary — one gate, not per-tool copies) →
      verify pass → commit.
- [x] **Step 3: Phase 0 exit check** (from the spec §13): served count == registry
      count == documented count; codes partitioned; pod create lease-wrapped;
      non-pod mutations gated+recorded. Run the full broker lane:
      `uv run --frozen pytest -q -m "not integration and not slow"`.

---

## Phase A: Mailbox + coarse tier (component)

All work in `packages/agent-routing`; run tests with the component's pinned tooling
from that directory. Respect `DIRECTORY_MODE = 0o700` / `FILE_MODE = 0o600`.

### Task 6: Mailbox module (schemas + atomic writes + dead-letter)

**Files:**
- Create: `runtime/model_routing/mailbox.py`, `tests/test_mailbox.py`

**Interfaces:**
- `Mailbox` (per dispatch): `write_ask`, `write_steer`, `write_ack`, `write_answer`
  (broker-side), `pending_asks()`, `unacked_steers()`, `summary()`.
- Schemas v1 per spec §4: `ASK` (`dispatch_id`, `ask_id`, `blocked_on`, `question`,
  `context.files_touched`, `context.options[]` ≤ 8, `default` mandatory, D1-derived
  `deadline_s`, `severity`), `ANSWER`, `STEER` (`kind ∈ note|scope|budget|priority|
  stop`, `requires_ack`, `deadline_s`), `ACK`. Body cap 64 KiB. Zero-padded monotonic
  ids per box. Validation on write **and** read.

- [x] **Step 1: Write failing tests:** schema truth table (missing `default`
      rejected; >8 options rejected; 64 KiB cap; `blocked_on` enumeration), atomicity
      (concurrent writer/reader never sees partial JSON), dead-letter quarantine
      (invalid write lands in `dead-letter/`, never interpreted), permissions
      (0700/0600).
- [x] **Step 2: Verify failure → implement** (reuse `run_store.atomic_write_json`
      primitives) → verify pass → commit.

### Task 7: Run-store + event integration

**Files:**
- Modify: `runtime/model_routing/run_store.py`, `events.py`; tests.

- [x] **Step 1: Failing tests:** `RunStore` creates `mailbox/` lazily; `mailbox.json`
      summary artifact updates on write/resolve (counts, unresolved ask ids, last
      acked steer id); `EventEmitter` emits `dispatch.paused`, `ask.resolved`
      (with `resolved_by`), `steer.acked`.
- [x] **Step 2: Verify failure → implement → verify pass → commit.**

### Task 8: Exit-75 classification (paused, non-terminal)

**Files:**
- Modify: `runtime/model_routing/dispatch.py`; tests (`tests/test_shim_contract.py`
      grows cases).

- [x] **Step 1: Failing tests:** a fake harness exiting 75 with an unresolved ask in
      the mailbox → dispatcher classifies `paused` (not failed), emits
      `dispatch.paused`, writes no result document, ledger records the pause; exit 75
      with *no* ask present → classified as failure (contract abuse).
- [x] **Step 2: Verify failure → implement** (classification beside the existing
      124/130 mapping in `process.py`/`dispatch.py`; no result-schema change) →
      verify pass → commit.

### Task 9: Tier-4 prompt contract + `runs resume`

**Files:**
- Modify: `runtime/model_routing/dispatch.py`, `cli.py`; create
      `tests/test_runs_resume.py`.

**Interfaces:**
- `pitwall-agent-routing runs resume <dispatch_id>`: reads the retained prompt
  (`prompt.deliver.md`, or the retained request prompt) plus resolved Q&A, rebuilds
  the prompt with a clarifications appendix, re-dispatches with the **same**
  `SUBAGENT_MODEL_ROUTING_DISPATCH_ID` and `ATTEMPT+1` (worktree linkage preserved).
- Tier-4 prompt suffix (dispatches opted into ask support): instructions to write
  `asks/NNN.json` per schema and exit 75 when a question is blocking.

- [x] **Step 1: Failing tests:** resume of a paused run uses the same dispatch id and
      incremented attempt (assert via env capture fake), prompt contains original +
      answered Q&A in order, and resume of a run with unresolved asks fails closed
      with a clear error. Resume without a retained prompt fails closed (never
      re-dispatch a guessed prompt).
- [x] **Step 2: Verify failure → implement** (follow the `workflow resume` verb
      conventions) → verify pass → commit.**

### Task 10: `inbox` CLI

**Files:**
- Modify: `runtime/model_routing/cli.py`; create `tests/test_inbox_cli.py`.

- [x] **Step 1: Failing tests:** `inbox` lists unresolved asks + unacked steers across
      all runs (table + `--json`), empty output when nothing waits, D1 deadline
      remaining shown per ask.
- [x] **Step 2: Verify failure → implement → verify pass → commit.**

### Task 11: Broker channel endpoints

**Files:**
- Modify: `runtime/model_routing/pitwall_sync.py`; create
      `tests/test_channel_broker.py` (reuse `tests/http_test_support.py`).

**Interfaces:**
- `POST /asks`, `POST /answers/{id}`, `POST /steer` — inheriting the receiver's
  existing discipline verbatim: loopback-only bind, mandatory HMAC
  (`X-Pitwall-Signature`, 503 while unset), 1 MiB cap, replay-dedup (409).
- `GET /inbox?dispatch_id=` — signed read surface (unauthenticated only behind an
  explicit opt-in flag, default signed).
- Dead-letter on schema failure; per-run ask rate cap (D1: 5/run).

- [x] **Step 1: Failing tests:** HMAC rejection, replay 409, oversized body 400/413,
      invalid schema → dead-letter + 400, happy path writes mailbox files, inbox
      reflects state.
- [x] **Step 2: Verify failure → implement → verify pass.**
- [x] **Step 3: Phase A exit check** (spec §13): scripted dispatch pauses on a
      question, resumes with the answer, ledger shows the Q&A pair. Full component
      suite green. Commit.

---

## Review fix pass (2026-09-09)

**Goal:** Fix every finding from the review of branch `orchestrator-channel-phase-0-a` (Phase 0 + Phase A). Two
findings are spend-safety defects in Task 4 (D6a) that must be corrected before merge: the raw-pod lease is inert
(nothing converges it, so TTL/teardown never fire) and the admitted workload permanently consumes its cost ceiling
from the monthly budget. Three smaller findings (channel replay race, resume log truncation, tier-4 prompt
overpromise) and three nits complete the set.

**Decisions recorded in this pass:**

- Raw pods get no readiness probing. No probe surface exists, so the D6 contract is
  TTL-bounded teardown only.
- `pitwall_runpod_terminate_pod` is not linked to lease rows. Control-plane terminate is
  idempotent, so the expiry sweeper converges leases whose pod was already terminated by hand.
- Deadline expiry does not auto-apply ask defaults in this batch. The tier-4 prompt wording
  was corrected to stop promising it; spec §10 expiry handling belongs to tier 1 (Phase B).

**Grounding (verified in code 2026-09-09):**

- `run_teardown` (`src/pitwall/api/leases/teardown.py:85`) already handles a provider-less lease:
  `provider_repo.get("runpod_direct")` → `None` → plain `terminate_pod(pod_id)` via the global RunPod client
  (`teardown.py:123,132-153`). Termination of an already-terminated pod is idempotent (control-plane contract).
- The single existing sweeper is `_lease_expiry_reconcile` (`src/pitwall/reconciler/__init__.py:1297`). Its row loop
  already contains the correct call site for expired leases — `run_teardown(reason="ttl", terminal_state=EXPIRED)`
  at `__init__.py:1436-1444` — and the `controlled` map (`_write_through_lease_traffic` →
  `LeaseRepository.list_active_for_activity_control`, `repository.py:897-909`) **already includes** `creating` /
  `waiting_runtime` / `waiting_probe` via `ACTIVE_LEASE_STATES` (`src/pitwall/leases/state.py:62-71`).
- The exact three blockers that keep an expired raw-pod lease from being torn down:
  1. `_LEASE_EXPIRY_LEASES_SQL` filters `state = 'active'` (`reconciler/__init__.py:306-313`), so expired
     pre-active leases never enter the row loop (`decide_renewal` returns `"skip"` for them in the controlled
     loop — MANUAL policy, never idle per `leases/controller.py:34-42`).
  2. `_mark_stopping` raises `LeaseStateConflict` for any state other than ACTIVE/STOPPING
     (`teardown.py:350-356`).
  3. `LEASE_STATE_TRANSITIONS` forbids `CREATING/WAITING_* → STOPPING` (`leases/state.py:39-47`).
- `close_lease_cost` with `provider=None` returns `lease.cost_accrued_usd or 0` (`teardown.py:292-306`); the
  `Lease` model already carries `max_usd_per_hour: Decimal(gt=0, max_digits=12, decimal_places=4)`
  (`src/pitwall/core/models.py:244`) but the MCP tool never sets it. Note: `PodCreateRequest.max_cost_per_hour`
  allows 6 decimal places — it must be quantized to 4 before assignment or `Lease` validation raises.
- `MONTH_TO_DATE_SPEND_SQL` sums `COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)`
  (`src/pitwall/cost/budget_gate.py:18-22`), so the queued workload inserted by
  `admit_raw_pod_lease` (`src/pitwall/api/leases/launch.py:985-1011`) counts its **ceiling** against the monthly
  budget forever unless `cost_actual_usd` is eventually set. `WorkloadRepository.update_state`
  (`src/pitwall/db/repository.py:1504-1568`) cannot set cost fields today.
- The webhook path holds `_DELIVERY_LOCK` across check → apply → remember (`pitwall_sync.py:155-170`, one `with`
  block wrapping `apply_event`'s body); the channel helpers split this into `_channel_claim_delivery` /
  `_channel_remember_delivery` around an unlocked apply — the race.
- `RunStore.touch_artifact` opens with `O_TRUNC` (`run_store.py:111-116`); the resume path re-touches
  `stdout.log`/`stderr.log`, destroying attempt-1 output.
- Hermetic test precedents to copy: `tests/reconciler/test_lease_expiry_reconcile.py` (mock pool +
  `_patch_run_teardown` monkeypatch, lines 29-50), `tests/mcp/test_runpod_create_pod_lease.py` (fake
  admission/repo), `packages/agent-routing/tests/test_channel_broker.py` (loopback receiver harness).

**Spec:** review findings F1–F5 + N1–N3 against
`docs/research/2026-09-08-orchestrator-channel.md` (§12.3 D6, §13 Phase 0 exit) and Tasks 0–11 above.

### Global constraints (fix pass)

- Root repo commands run through `uv run --frozen`; component commands run from `packages/agent-routing` with its
  pinned uv tool. Tests stay hermetic (broker `tests/conftest.py` blocks provider DNS; component tests use
  sandboxes/loopback fakes). No live RunPod calls.
- Agent Routing runtime stays standard-library-only.
- Commit after every task with `git commit -s`, on the existing `orchestrator-channel-phase-0-a` branch. Do not
  push unless asked. Every behavior change lands with its test in the same task.
- The completion contract stays sacred; nothing here may cause a dispatch to end without a `SHIM-DONE` sentinel
  or a recorded `paused` classification.
- Do not add a second sweeper: all lease convergence flows through the existing `_lease_expiry_reconcile` pass.

### File map (fix pass)

| File | Responsibility |
| --- | --- |
| `src/pitwall/leases/state.py` | F1: allow pre-active → STOPPING transitions |
| `src/pitwall/api/leases/teardown.py` | F1: `_mark_stopping` accepts pre-active; F2: `close_lease_cost` falls back to `lease.max_usd_per_hour` |
| `src/pitwall/reconciler/__init__.py` | F1: expiry SQL picks up expired pre-active auto-teardown leases; F2: close linked workload after raw-pod teardown |
| `src/pitwall/db/repository.py` | F2: `WorkloadRepository.update_state` gains `cost_actual_usd` (+ provenance/reconciled_at) |
| `src/pitwall/mcp/tools/runpod_resources.py` | F1/F2: set `max_usd_per_hour`; close workload on create failure; lease-insert-failure cleanup; honest tool description |
| `docs/sdlc/03-mcp-server.md` | F1: replace the overclaiming lifecycle sentences with the honest TTL-bounded contract |
| `packages/agent-routing/runtime/model_routing/pitwall_sync.py` | F3: claim/apply/remember under one `_DELIVERY_LOCK` |
| `packages/agent-routing/runtime/model_routing/dispatch.py` | F4: rotate attempt-1 logs on resume; F5: tier-4 wording; N2: ledger ask resolutions |
| `packages/agent-routing/runtime/model_routing/mailbox.py` | N3: dead-letter per-box cap |
| `packages/agent-routing/runtime/model_routing/run_store.py` | F4: `rotate_attempt_logs` helper |
| tests (see tasks) | one failing test per behavior, written first |

---

### Task 12 (F1): Raw-pod leases converge through the existing expiry sweeper

**Files:** `src/pitwall/leases/state.py`, `src/pitwall/api/leases/teardown.py`,
`src/pitwall/reconciler/__init__.py`; tests in `tests/leases/test_teardown.py`,
`tests/reconciler/test_lease_expiry_reconcile.py`.

- [x] **Step 1 — failing tests** (copy the mock-pool + `_patch_run_teardown` pattern from
      `tests/reconciler/test_lease_expiry_reconcile.py:29-50`):
  - `transition_lease_state(CREATING, STOPPING)` (and `WAITING_RUNTIME`/`WAITING_PROBE` → `STOPPING`) is legal;
    terminal map otherwise unchanged.
  - `_mark_stopping` on a `creating` lease yields a `stopping` lease instead of raising `LeaseStateConflict`.
  - `_lease_expiry_reconcile` tears down a lease row with `state='creating'`,
    `auto_teardown_on_expiry=true`, `expires_at <= now` — assert `run_teardown` called with
    `reason="ttl", terminal_state=EXPIRED`. Also assert a `creating` lease with a **future** expiry is *not*
    touched (no premature teardown, no warnings).
- [x] **Step 2 — implement:**
  - `state.py`: add `LeaseState.STOPPING` to the allowed sets of `CREATING`, `WAITING_RUNTIME`,
    `WAITING_PROBE` (keep `FAILED` entries). Terminal states unchanged.
  - `teardown.py:_mark_stopping`: replace the `state != LeaseState.ACTIVE` guard with membership in
    `ACTIVE_LEASE_STATES - {STOPPING}`; `transition_lease_state` remains the authority.
  - `reconciler/__init__.py`: widen the sweep — change `_LEASE_EXPIRY_LEASES_SQL`'s `state = 'active'` to
    `state = ANY(ARRAY['active','creating','waiting_runtime','waiting_probe'])`. The warning branch
    (T-15/T-5) must stay active-only: guard the warning publication on `state == 'active'` so pre-active
    leases get no readiness warnings. Expired rows flow to the existing T-0 `run_teardown` call site
    (`__init__.py:1436-1444`); `decide_renewal` returns `"skip"` for MANUAL non-idle leases, so no other
    behavior changes for them.
- [x] **Step 3 — verify:** new tests pass; `uv run --frozen pytest tests/leases tests/reconciler -q` green.
- [x] **Step 4 — commit:** `fix(leases): converge expired pre-active auto-teardown leases via the expiry sweeper`

### Task 13 (F1/F2): Honest cost-close + MCP failure-path cleanup

**Files:** `src/pitwall/api/leases/teardown.py`, `src/pitwall/mcp/tools/runpod_resources.py`,
`docs/sdlc/03-mcp-server.md`; tests in `tests/leases/test_teardown.py`, `tests/mcp/test_runpod_create_pod_lease.py`.

- [x] **Step 1 — failing tests:**
  - `close_lease_cost(lease, provider=None, terminated_at)` with `lease.max_usd_per_hour = Decimal("0.50")`
    returns `(rate/3600) * elapsed_seconds` (create a lease with `created_at` 30 minutes before `terminated_at`,
    expect `Decimal("0.25")` quantized per `_usd`). With neither provider rate nor `max_usd_per_hour`, still
    returns `cost_accrued or 0` (existing behavior).
  - MCP: successful create passes `max_usd_per_hour` on the `Lease` (assert on the fake repo's captured lease;
    use a 6-dp request value like `Decimal("0.123456")` and assert the lease holds `Decimal("0.1235")` —
    quantize to 4 dp before construction).
  - MCP: pod-create failure after admission → admitted workload closed
    (`update_state(..., state="failed", cost_actual_usd=Decimal("0"))` — via fake repo capture) and no lease row.
  - MCP: lease-insert failure after pod create → best-effort `terminate_pod` invoked on the fake service and the
    workload closed; the original exception still surfaces to the caller.
- [x] **Step 2 — implement:**
  - `teardown.py:close_lease_cost`: after `_provider_cost_rate_per_second(provider)` returns `None`, fall back to
    `lease.max_usd_per_hour / 3600` when set; prorate identically.
  - `runpod_resources.py:pitwall_runpod_create_pod`: set `max_usd_per_hour` on the `Lease` from
    `request.max_cost_per_hour.quantize(Decimal("0.0001"))` when provided. Wrap the
    `LeaseRepository.create` call: on failure, best-effort `service.terminate_pod(...)` (idempotent) and close
    the admitted workload (Task 14's helper), then re-raise. Wrap the create-pod `_call`: on
    `RunPodControlPlaneError`, close the admitted workload before adapting.
  - Rewrite the spec-manifest description and SDLC 03 (both warning paragraphs, §2 and §3.4) to the honest
    contract: *"TTL-bounded: expired raw-pod leases are terminated by the existing lease expiry reconciler and
    cost-closed from `max_cost_per_hour` when supplied; raw pods expose no probe surface, so no readiness
    lifecycle applies."* Remove every remaining "inherits readiness" claim.
- [x] **Step 3 — verify:** new tests + `uv run --frozen pytest tests/mcp tests/leases -q` green;
      `uv run --frozen pytest tests/mcp/test_doc_count_sync.py -q` still green.
- [x] **Step 4 — commit:** `fix(mcp): honest raw-pod lease contract — cost rate, failure cleanup, no readiness claim`

### Task 14 (F2): Close admitted workloads so ceilings stop counting

**Files:** `src/pitwall/db/repository.py`, `src/pitwall/reconciler/__init__.py`; tests near
`tests/reconciler/test_lease_expiry_reconcile.py`.

- [x] **Step 1 — failing tests:**
  - `WorkloadRepository.update_state(workload_id, "failed", cost_actual_usd=Decimal("0"))` persists
    `cost_actual_usd` (mock pool captures the SQL params; assert the SET list includes `cost_actual_usd` and
    the value). Optional `cost_actual_provenance` / `cost_reconciled_at` params follow the file's
    positional-SET style (`repository.py:1504-1568`).
  - `_lease_expiry_reconcile`, after tearing down an expired raw-pod lease whose `Lease.workload_id` is set,
    closes that workload with `state="completed"` and `cost_actual_usd` equal to the teardown's accrued cost.
- [x] **Step 2 — implement:**
  - Extend `update_state` with the three optional cost params (style: append to `sets`/`params` exactly like the
    existing optional fields; no signature break for callers).
  - In the reconciler's T-0 teardown call site for provider-less raw-pod leases (the branch Task 12
    feeds), capture the `LeaseTeardownResult.lease.cost_accrued_usd` and, when the lease has a `workload_id`,
    call `WorkloadRepository.update_state(workload_id, "completed", cost_actual_usd=accrued,
    cost_actual_provenance="lease_teardown")`. Guard with try/except so a workload-close failure logs but never
    blocks teardown (mirror the audit-failure pattern in `run_teardown`).
- [x] **Step 3 — verify:** tests green; `uv run --frozen pytest tests/reconciler tests/cost -q` green.
- [x] **Step 4 — commit:** `fix(cost): close admitted workloads at raw-pod teardown and on create failure`

### Task 15 (F3): Channel replay discipline under one lock

**Files:** `packages/agent-routing/runtime/model_routing/pitwall_sync.py`; test in
`packages/agent-routing/tests/test_channel_broker.py`.

- [x] **Step 1 — failing test:** against the loopback receiver, fire **8 concurrent** identical `POST /asks`
  (same `delivery_id`, valid body, distinct threads + signed requests). Assert exactly one `202`, the rest
  `409`, and exactly **one** file in `mailbox/asks/`. (Current code fails: claim and remember are separate lock
  scopes, so concurrent duplicates can both apply.)
- [x] **Step 2 — implement:** fold `_channel_claim_delivery` / `_channel_remember_delivery` away — each
  `apply_channel_ask` / `apply_channel_answer` / `apply_channel_steer` wraps its whole body
  (replay check → mailbox write → remember) in a single `with _DELIVERY_LOCK:` block, mirroring
  `apply_event`'s structure at `pitwall_sync.py:155-170`. The HTTP handler stays lock-free.
- [x] **Step 3 — verify:** `python3 -m unittest tests.test_channel_broker tests.test_pitwall_sync -v` green
      (from `packages/agent-routing`); `ruff check` + `mypy --python-version 3.14` clean.
- [x] **Step 4 — commit:** `fix(agent-routing): hold the delivery lock across channel apply, like webhooks`

### Task 16 (F4 + F5): Preserve attempt-1 logs; stop promising auto-defaults

**Files:** `packages/agent-routing/runtime/model_routing/run_store.py`,
`packages/agent-routing/runtime/model_routing/dispatch.py`; tests
`packages/agent-routing/tests/test_runs_resume.py`.

- [x] **Step 1 — failing tests:**
  - Resume preserves attempt-1 output: extend the fake provider's `ask` mode to `print("attempt-1 marker")`
    before exiting 75; after resume assert `stdout.attempt1.log` exists, contains the marker, and the fresh
    `stdout.log` holds only attempt-2 output.
  - Tier-4 suffix no longer claims automatic default application: assert the rendered suffix contains the new
    wording ("if the deadline passes, your stated default is applied by the orchestrator when the run is
    resumed") and does **not** contain "resumes without you".
- [x] **Step 2 — implement:**
  - `run_store.py`: add `rotate_attempt_logs(previous_attempt: int)` — for `stdout.log`/`stderr.log`, if present,
    `os.replace` to `stdout.attempt{N}.log` / `stderr.attempt{N}.log` (0600 preserved by rename).
  - `dispatch.py`: in `dispatch_legacy`, when `resume_mode`, call
    `store.rotate_attempt_logs(context.attempt - 1)` immediately before the two `touch_artifact` calls.
  - `dispatch.py:tier4_prompt_suffix`: rewrite the deadline sentence to the honest Phase-A semantics above.
- [x] **Step 3 — verify:** `python3 -m unittest tests.test_runs_resume tests.test_pause_contract -v` green.
- [x] **Step 4 — commit:** `fix(agent-routing): preserve paused-attempt logs on resume; honest tier-4 deadline wording`

### Task 17 (N1–N3): Review nits

**Files:** `tests/mcp/test_registry_health.py`,
`packages/agent-routing/runtime/model_routing/dispatch.py`,
`packages/agent-routing/runtime/model_routing/mailbox.py`; tests
`packages/agent-routing/tests/test_runs_resume.py`, `packages/agent-routing/tests/test_mailbox.py`.

- [x] **N1 — scope assertion has teeth:** in `test_registry_health.py`, strengthen to
      `assert spec.scope == "health"` for `pitwall_health` and add a loop asserting every spec's `scope` is a
      non-empty string (documents that `"general"` is the deliberate default, not the tested property).
- [x] **N2 — ledger shows the Q&A pair:** on the final `finished` ledger record, when `pause.json` exists,
      include `askResolutions` built from the mailbox — `[f"{ask_id}:{answered_by}" for answered asks in id
      order]` (extend `_ledger_record` with `ask_resolutions: list[str] | None`). Test: after resume, the
      attempt-2 `finished` row carries `["0001:operator"]`.
- [x] **N3 — dead-letter cap:** `Mailbox.quarantine` prunes to the newest 20 files per box after writing
      (glob-sort by name; unlink oldest, ignore OSError). Test: 25 invalid writes → at most 20 dead-letter
      files.
- [x] **Verify + commit:** broker + component suites green;
      `fix(agent-routing): review nits — scope assert, ledger resolutions, dead-letter cap`

---

### Exit checks (fix pass)

- [x] **F1 proof:** hermetic reconciler test shows an expired `creating` raw-pod lease reaching
      `run_teardown(reason="ttl", terminal_state=EXPIRED)`; `state.py` map and `_mark_stopping` accept pre-active
      → stopping; no second sweeper exists (grep: `_lease_expiry_reconcile` remains the only teardown driver).
- [x] **F2 proof:** workload-close test shows `cost_actual_usd` set on both the failure path (actual=0) and the
      teardown path (actual=accrued); `MONTH_TO_DATE_SPEND_SQL` therefore stops counting the ceiling once
      actual is set.
- [x] **F3 proof:** concurrent-duplicate test yields exactly one applied ask.
- [x] Full lanes: `uv run --frozen pytest -q -m "not integration and not slow"` (root) and
      `python3 -m unittest discover -s tests` (component) plus component
      `ruff check runtime tests tools scripts/pitwall-agent-routing` and
      `mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing`.
- [x] Docs contain no remaining "inherits readiness/TTL/teardown/cost-close" claim for raw pods; count-sync gate
      (`tests/mcp/test_doc_count_sync.py`) green.

---

## Post-review corrections (2026-09-09)

**Goal:** Resolve every actionable finding from the 2026-09-09 review of the `orchestrator-channel-phase-0-a` branch: correct stale D6 "inherits readiness" wording in the two planning docs, record the as-built implementation status in the spec, commit the four untracked docs with explicit paths, and verify the unrelated model-catalog working-tree changes remain untouched.

**Findings with no repo action (recorded for completeness):**

- *Report commit-count miscount ("8" vs actual 7):* the completion report was a message, not a repo artifact; all F1–F5/N1–N3 commits were verified present. Nothing to fix.
- *`tests/db` errors when running without markers:* operator error during review (integration-marked tests need testinfra Postgres); the documented lane reproduces green. Nothing to fix.
- *Garbled "§3.4 / newest 20 files" note:* verified absent from SDLC 03. Nothing to fix.

**Spec:** `docs/research/2026-09-08-orchestrator-channel.md` (§14 D6) and Tasks 12–17 above (as-built honest contract).

This pass changed documentation only; all 18 branch commits stood review.

### Global constraints (corrections pass)

- All work stays on the existing `orchestrator-channel-phase-0-a` branch (these docs
  describe that branch's work; they ship with it).
- **Explicit-path staging only.** Never `git add -A`, `git add .`, or `git add -u` —
  the working tree carries an unrelated, uncommitted model-catalog refresh (11 new
  `docs/models/` dossiers, `src/pitwall/models/schema.py`, `tests/models/*`,
  `tools/engines/smoke_launch_shape.py`, agent-routing plugin generated
  references/ledgers, registry JSONs) that belongs to another session and must be
  preserved exactly as found (workspace rule: preserve unrelated user work).
- Commits are `git commit -s`, docs-only; no code changes in this plan.
- Documentation accuracy rule applies: correct wording only to what the code verifiably
  does (the fix-plan contract), not aspirational phrasing.

---

### Task 18: Baseline verification

- [x] Confirm branch and that the working tree still matches the review state:

```bash
git branch --show-current          # orchestrator-channel-phase-0-a
git status --short | head -40      # unrelated modifications + 4 untracked docs present
git log --oneline -1               # e07df49 (or later, unchanged by this plan)
```

If the working tree differs materially from the review state (new commits, the catalog
refresh already committed elsewhere), stop and re-verify before proceeding.

### Task 19: Correct the stale D6 "inherits readiness" wording (2 files)

The as-built contract (from the fix plan, verified in SDLC 03 and the shipped tool) is:
*TTL-bounded — expired raw-pod leases are terminated by the existing lease expiry
reconciler and cost-closed from `max_cost_per_hour` when supplied; raw pods expose no
probe surface, so no readiness lifecycle applies.*

**Files:**
- `docs/superpowers/plans/2026-09-08-orchestrator-channel-phase-0-a.md` (Task 4
  Interfaces bullet: "the pod inherits readiness/TTL/teardown/cost-close")
- `docs/research/2026-09-08-orchestrator-channel.md` (§14 D6: "inherits readiness/TTL/
  teardown/cost-close")

- [x] **Step 1:** In the phase-0-a plan, replace the inheritance phrasing with the
      honest contract above and add one sentence: *"Refined by the Phase 0/A fix pass
      (Tasks 12–17 below): readiness language
      removed — raw pods have no probe surface."*
- [x] **Step 2:** In the research doc §14 D6, make the same replacement, phrased as a
      decision-record amendment: keep the original decision text readable, append
      "*As-built (fix pass 2026-09-09):*" followed by the honest contract. Decision
      logs record evolution, they don't rewrite history silently.
- [x] **Step 3:** Grep proves the fix:

```bash
git grep -n "inherits readiness" -- docs/superpowers docs/research \
  | grep -v fixes.md | grep -v "readiness language removed\|readiness.*removed"
```

Expected: no matches outside the fixes plan and the explicit amendment sentences.

### Task 20: Record as-built implementation status in the spec

**Files:**
- `docs/research/2026-09-08-orchestrator-channel.md` (Status bullet)

- [x] **Step 1:** Update the Status bullet from "nothing here is scheduled" to reflect
      reality: *Phase 0 and Phase A are implemented on branch
      `orchestrator-channel-phase-0-a` (18 commits incl. the 7-commit review-fix pass);
      exit checks green (root lane 5046 passed / 76 skipped / 143 deselected; component
      587 passed / 4 skipped; ruff + mypy clean); D6 refined per the fix plan
      (TTL-bounded contract). Phases B–D remain unscheduled and unplanned.*
- [x] **Step 2:** While in the file, spot-check the doc's other countable claims
      against as-built (76-tool registry, `-32001..-32005` codes, J10 refresh) — all
      were verified green in review; fix any stray mismatch found (none expected).

### Task 21: Commit the four untracked docs (explicit paths)

- [x] **Step 1:** Stage exactly these paths and nothing else:

```bash
git add docs/research/2026-09-08-omniroute-free-tier-integration.md \
        docs/research/2026-09-08-orchestrator-channel.md \
        docs/superpowers/plans/2026-09-08-orchestrator-channel-phase-0-a.md \
        docs/superpowers/plans/2026-09-09-orchestrator-channel-phase-0-a-fixes.md
git status --short          # exactly 4 staged entries (M/A), nothing else staged
```

(The fix-pass plan file staged here was later folded into this document as Tasks 12–17.)

- [x] **Step 2:** If Task 19/20 edits also touched the two docs being committed, the
  staged versions must include them — verify with `git diff --cached --stat` (expect
  the two edited docs to show changes beyond their original content).
- [x] **Step 3:** `git commit -s -m "docs: channel research + plans; D6 honest contract + as-built status"`
      — commit message may be adjusted, but keep it docs-scoped.

### Task 22: Working-tree preservation verification (no changes)

- [x] **Step 1:** After the docs commit:

```bash
git status --short
```

Expected: the unrelated model-catalog refresh is **still present and uncommitted**
(same modified/untracked set as Task 18, minus the four committed docs). No stashes
created, no resets, no checkouts that could disturb it.
- [x] **Step 2:** Confirm nothing in this plan's commits touched
      `src/pitwall/models/`, `tests/models/`, `tools/engines/`,
      `packages/agent-routing/plugins/`, `packages/agent-routing/prompting/`, or
      `packages/agent-routing/runtime/model_routing/resources/`:

```bash
git show --stat HEAD | tail -8
```

Expected: exactly 4 docs files in the commit.

---

### Exit checks (corrections pass)

- [x] `git grep -n "inherits readiness"` shows matches only in the fix-pass section of this plan and the
      two explicit amendment sentences (which quote the removed phrasing).
- [x] The spec's Status bullet matches the verified branch state; no stale
      "unscheduled" claim for Phases 0/A.
- [x] HEAD is a single docs-only commit containing exactly the four doc files.
- [x] The unrelated model-catalog refresh is byte-for-byte untouched (same `git status`
      set as baseline, minus the four docs).

---

## Audit fixes (2026-09-10)

### Task 23: Audit of the merged batch

A low-effort code review of the whole batch found four defects and some leftover lint.
All were fixed with regression tests.

- [x] Resume stopped appending the tier-4 contract a second time, and it rewrites the retained
      prompt only after every replay input validates (`b3fcebf`).
- [x] Dead-letter pruning orders by write time with a monotonic sequence tiebreak; the lexical
      sort had dropped `-10`…`-14` instead of the oldest files (`b3fcebf`).
- [x] The channel ask cap is owned by the mailbox (`MailboxCapError` → 429) instead of a
      duplicate pre-check that ignored the configured cap (`b3fcebf`).
- [x] The inbox CLI reuses the broker's inbox builder instead of a second copy (`b3fcebf`).
- [x] Ruff and `mypy --strict` findings left by the Phase 0 work were cleared (`bcca1c7`).

Verification: component suite `587 tests OK`; broker lanes `1164 passed`; ruff and mypy clean.
