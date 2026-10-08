# Single cohesive project: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to
> implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan
> specifies behaviour, ownership, tests, and validation; it deliberately contains no implementation
> code. Each lane writes its own code test-first against the requirements below.

**Goal:** Turn the four build units in this repository into one Python package, one version, one
release, and one product identity, after first fixing every defect found by the 2026-09-28
evaluation.

**Architecture:** Phase 0 fixes defects on the current structure. Phase 1 moves Agent Routing into
`pitwall.agents` and renames it. Phase 2 removes duplication and approved dead code. Phases 3 and 4
port the gateway and the Pi workbench to Python. Phase 5 unifies version, CI, release, and docs.
Work runs as parallel Sonnet lanes with exclusive file ownership, merged by an integrator onto one
branch.

**Tech Stack:** Python 3.14.7, uv, pytest (Agent Routing's unittest suites run under pytest),
FastAPI and uvicorn, httpx, asyncpg, Arq, Textual, PyYAML, bubblewrap and seccomp on Linux, GitHub
Actions.

**Spec:** [`docs/superpowers/specs/2026-09-28-single-project-design.md`](../specs/2026-09-28-single-project-design.md).
**Findings input:** [`docs/evidence/2026-09-28-repo-systems-evaluation.md`](../../evidence/2026-09-28-repo-systems-evaluation.md).

## Global Constraints

- Python 3.14.7 only. Run Python through `uv run` or `.venv/bin/python`; never bare `python`.
- One `pyproject.toml`, one `uv.lock`, one runtime dependency set (the current root list). No new
  runtime dependencies without a line in the lane report justifying it.
- Clean break: no aliases for `pitwall-agent-routing`, `pitwall-gateway`, `pi-workbench`,
  `pitwall-mcp`, `SUBAGENT_MODEL_ROUTING_*`, `PITWALL_AGENT_ROUTING_*`, `PITWALL_API_TOKEN`,
  `SHIM_TIMEOUT_SECS`, or the `subagent-model-routing-local` marketplace.
- Terms: **provider** is a compute or inference backend; **harness** is an agent CLI;
  **agent profile** is a `name@harness` binding; **dispatch** is one harness run; **workflow** is a
  dependency-ordered set of dispatches.
- `pitwall agents _shim`, `pitwall agents _steer-gate`, and hook entry points must not import
  `fastapi`, `uvicorn`, `asyncpg`, `redis`, `arq`, `textual`, `runpod`, `mcp`, or
  `prometheus_client`.
- `pitwall.agents` never imports broker services, repositories, or `pitwall.db`. It may import
  Pydantic models from `pitwall.api`.
- Tests are hermetic: no live credentials, paid providers, user routing state, or production
  resources. Live checks stay behind the existing `live` marker.
- Lanes never run `make test-int`, `make test-db`, coverage-combined, or the journey harness. The
  shared `pitwall_test` database on port 5444 is used only by the integrator, one suite at a time.
- Pipe test output through `| tail -40`. Never paste a full test log into a report.
- Never weaken a test or a policy gate to make a change pass. Tests that pin old names are updated to
  the new names in the same commit as the rename.
- First unified version: `0.2.0a1`. Tags: `v*` only.

## Review Focus

These failure modes are implied by the spec but not exercised by the parity and fix tests. Each line
names the task that adds its test.

1. **A legacy environment variable is still set after the upgrade**, for example
   `SUBAGENT_MODEL_ROUTING_UNRESTRICTED=0`. With no aliases the old value would be silently ignored
   and the default (unrestricted) applied. Expected: the shim refuses to dispatch, exits non-zero,
   and names the replacement variable. Test in Task 1.2.
2. **`pitwall agents migrate` runs while a dispatch is in flight, or against an existing
   `pitwall.toml` that the user edited.** Expected: it refuses while runs are active, never
   overwrites differing values, and reports every conflict. Tests in Task 1.7.
3. **The old Node gateway is still listening on port 20130** when personal mode starts the Python
   gateway. Expected: a clear port-in-use error that names the port and the stale process, not a
   silent start failure. Test in Task 3.4.
4. **A client disconnects in the middle of a streamed completion.** Expected: the upstream request is
   cancelled promptly and no connection or task leaks. Test in Task 3.2.
5. **The `pi` binary is missing or the wrong version** when a workbench command runs. Expected: the
   command exits with an actionable error before it creates a worktree or writes any state. Test in
   Task 4.6.

---

## Execution model

### Branch, worktrees, and integration

- Integration branch: `feat/single-project`, cut from local `main` at `a04acd62`.
- Each lane is a Sonnet subagent started with `isolation: "worktree"` from the integration branch
  head at the start of its wave. It commits only on its own worktree branch.
- The integrator (the orchestrating session) merges each lane branch into `feat/single-project`
  after the lane reports `STATUS: DONE` and the integrator has re-run the lane's validation
  commands on the merge result.
- At most five lanes run at once. A wave starts only when every task it depends on is merged.
- After each wave merges, the integrator runs, in order and never in parallel: `make test-fast`,
  `make up`, `make test-int`, and the release-acceptance rebind (see below). A failure is
  root-caused and fixed before the next wave starts.

### Lane prompts

Every lane prompt is built from `~/.claude/templates/lane-prompt.md`:

- **You own** is the task's **Owns** list, verbatim.
- **Do not touch** is the template default plus the integrator-owned files below and every file
  another lane in the same wave owns.
- **Task** is the task's section of this plan, verbatim, plus the Global Constraints section.
- **Validation** is the task's **Validation** list.
- **Report** returns the ledger rows for the task's findings (see below) and ends with
  `STATUS: DONE` or `STATUS: INCOMPLETE`.
- Each lane runs `lane-checkpoint <task-id>` before any step expected to take more than ten
  minutes, and again when done.

### Integrator-owned files

No lane edits these unless the task's **Owns** list names them explicitly:

- `uv.lock`, `.secrets.baseline`, `CHANGELOG.md`
- `release_acceptance/` (all data files)
- `docs/superpowers/plans/2026-09-28-single-project-ledger.md`
- the local `AGENTS.md`

`pyproject.toml` is owned by a lane only in Tasks 1.1, 1.3, and 5.3.

### Release-acceptance rebind

`release_acceptance/reviewed-bindings*.json` pin source hashes, so they go stale on every source
edit. After each wave merges, the integrator regenerates the declarations and bindings with
`tools.release_acceptance.bind_surfaces` and `tools.release_acceptance.matrix`, reviews the diff
(new, removed, and renamed surfaces only), and commits the result as
`chore(release-acceptance): rebind after wave <n>`.

### Finding ledger

- **Task I0** creates `docs/superpowers/plans/2026-09-28-single-project-ledger.md` with one row
  per finding in the evaluation. Each row has an ID (`A-01`, `B-07`, and so on, by evaluation lane
  and order), severity, the cited file, a one-line summary, the owning task, and the state `open`.
- **Ownership rule:** a task owns every finding whose cited file is in its **Owns** list at the
  time of its wave. A finding in a file that a later task deletes is owned by the deleting task.
- Each lane reports one row per owned finding, with one of these states:
  - `fixed`: the commit and the regression test that failed before the fix.
  - `already fixed on main`: the commit that fixed it, and the test the lane added to pin it.
  - `closed by removal`: the task that deleted the code.
  - `closed by port`: the port task and the parity or fix test.
  - `intended behaviour`: allowed only for the rows listed under
    [Findings recorded as intended behaviour](#findings-recorded-as-intended-behaviour).
- The integrator copies the reported rows into the ledger when merging. The branch lands only
  when every row has a closing state.

### Wave map

| Wave | Tasks (parallel) | Depends on |
|---|---|---|
| 0 | I0 (integrator) | none |
| 1 | 0.1, 0.2, 0.3, 0.4, 0.5 | I0 |
| 2 | 0.6, 0.7, 0.8, 0.9 | I0 (independent of wave 1; files are disjoint) |
| 3 | 1.1 | waves 1 and 2 |
| 4 | 1.2 | 1.1 |
| 5 | 1.3, 1.4, 1.5, 1.6 | 1.2 |
| 6 | 1.7, 2.1, 2.2, 2.3 | 1.7 needs 1.4 and 1.5; 2.x need 1.3 |
| 7 | 2.4, 2.5, 2.6, 3.1, 3.2 | 2.4 and 2.5 need 2.2 (re-export modules); 2.6 needs 1.2; 3.x need wave 6 |
| 8 | 3.3, 4.1, 4.2 | 3.3 needs 3.2; 4.x need wave 7 |
| 9 | 3.4, 4.3, 4.4 | 3.4 needs 3.3; 4.3 needs 4.1 and 4.2; 4.4 needs 4.2 |
| 10 | 4.5 | 4.3 (launcher) |
| 11 | 4.6 | 4.1 to 4.5 |
| 12 | 5.1, 5.2, 5.3, 5.4 | every earlier task |
| 13 | I-final (integrator) | wave 12 |

Waves 1 and 2 are separated only by the five-lane limit.

**Package scaffolds.** Before wave 7 the integrator commits an empty `src/pitwall/gateway/__init__.py`
and `tests/gateway/__init__.py`; before wave 8, an empty `src/pitwall/workbench/__init__.py` and
`tests/workbench/__init__.py`. Parallel lanes then add modules to existing packages, and no lane owns
the package `__init__` file.

---

## Removals

Approving the spec approved exactly the list in its "Removals requiring approval" section. The
tasks below execute that list and cite the item numbers. One further removal was approved on
2026-09-28 (see [Decisions](#decisions-2026-09-28)):

- **R-extra-1:** `src/pitwall/cost_exporter/` (a 79-line deprecated re-export of
  `pitwall.cost.exporter`). Task 0.4 deletes it. Task 0.2 points `docker/Dockerfile.cost-exporter` at
  `pitwall.cost.exporter`, and the integrator points the `pitwall-cost-exporter` console script in
  `pyproject.toml` at it when merging wave 1.

## Findings recorded as intended behaviour

These evaluation rows describe deliberate design choices, not defects, confirmed by the maintainer
on 2026-09-28. They close as `intended behaviour` with the stated reason. The other three rows
originally proposed here (workflow support, argv prompts, steer-gate fail-open) became fix
requirements in Tasks 2.6, 0.8, and 1.5.

| Finding | Owning task | Reason |
|---|---|---|
| Audit CHECK constraints require a migration for every new action (lane A, db) | 0.7 | The constraint is the control that stops unreviewed audit actions. |
| Harness children inherit the parent environment (lane D) | 0.8 | Each harness reads its own credentials from the environment. |

---

## Task I0: finding ledger skeleton (integrator)

**Owns:** `docs/superpowers/plans/2026-09-28-single-project-ledger.md`

- [ ] Read the evaluation doc section by section and write one ledger row per finding with ID,
      severity, cited file, summary, and owning task, using the ownership rule and the task Owns
      lists in this plan.
- [ ] Confirm every row has an owning task by checking the table for an empty owner column.
- [ ] Commit: `docs(plan): single-project finding ledger`.

**Validation:**
- `uv run python tools/ci/check_markdown_links.py 2>&1 | tail -1` → `markdown links passed`.

---

## Phase 0: defect fixes on the current structure

Each Phase 0 task follows the same cycle for every finding it owns:

1. Write a test that exercises the reported behaviour.
2. Run it. If it passes on the branch head, the finding is already fixed: record
   `already fixed on main` with the fixing commit (`git log -S` or `git log -L`), and keep the test.
3. If it fails, fix the code, rerun it, and record `fixed`.

Size findings ("function is N lines") close when the named function is split into named steps of at
most 120 lines each, with its existing tests passing unchanged.

### Task 0.1: reconciler and webhooks

**Owns:**
- `src/pitwall/reconciler/` (all files)
- `src/pitwall/webhook_receiver/`
- `src/pitwall/webhook_dispatcher/`
- `src/pitwall/db/repository.py`
- `config/prometheus/`
- `tests/reconciler/`, `tests/webhook_dispatcher/`
- new `tests/webhook_receiver/`
- `tests/ops/test_backup_drill.py`

**Findings:** lane A reconciler, webhook_dispatcher, and webhook_receiver sections, plus any
finding citing `db/repository.py`.

**Requirements:**
- The receiver enqueues the terminal-status job under the name the worker registers for it
  (`_process_webhook_terminal_status` in `reconciler/__init__.py`, `WorkerSettings.functions`).
- `_lease_expiry_reconcile`:
  - A `run_teardown` failure for one lease is logged with the lease ID and counted, and the sweep
    continues with the remaining leases.
  - The failed lease is left for `_retry_stuck_teardowns`.
  - The duplicated `decide_renewal` and teardown blocks become one helper.
  - The dead `expires_at is None` guard is removed.
  - The function is split to at most 120 lines per step.
- `_write_through_lease_traffic` fetches providers and Redis stamps in one batched query and one
  pipelined read per tick, not one per lease.
- The budget circuit breaker's state persists across jobs. Store it in the worker's startup context,
  not in `ctx.setdefault` inside the job.
- The per-minute cron `minute` set uses `set(range(60))`. The `WorkerSettings` docstring lists every
  registered job.
- The backup drill and archive jobs run on Sunday: change the cron `weekday` to 6 and fix the
  docstring and the pinning test in `tests/ops/test_backup_drill.py`.
- Webhook retry scheduling:
  - `WebhookDispatcher` sets `next_retry_at` on retryable failures using its delay schedule.
  - The reconciler records it, and a new cron job calls
    `WebhookDeliveryFailureRepository.list_pending_retries` and redelivers.
  - Successful redelivery clears the row, and a final failure marks it exhausted.
  - `PitwallWebhookRetriesDue` in `config/prometheus/` then has a live source.
- Dispatcher details:
  - An egress-policy rejection is recorded as a non-retryable failure, not `retry_scheduled`.
  - Every resolved address is tried in order before a delivery counts as failed.
  - `http.client.HTTPException` subclasses are caught alongside `TimeoutError` and `OSError`.
  - Backoff sleeps no longer run inline inside a reconciler job: each delivery attempt is one job
    execution, and later attempts are scheduled through `next_retry_at`.
- Receiver details:
  - The job is enqueued before the delivery is marked seen. If enqueueing fails, the delivery is not
    recorded, so RunPod's retry is processed.
  - The Arq pool is created once per process and reused.
  - The receiver refuses to start when no webhook secret is configured, with an error naming the
    setting.
- `workload_lifecycle.py:205` (`submit_runpod_job`) is closed by removal in Task 2.5. Record it as
  `closed by removal` pending 2.5.

**Tests:**
- `tests/reconciler/test_job_names.py::test_every_enqueued_job_name_is_registered`: collects every
  `enqueue_job` name literal under `src/pitwall` via AST and asserts each is the registered name of a
  function in `WorkerSettings.functions` (resolved with `arq.worker.func`).
- `tests/reconciler/test_lease_sweep_isolation.py::test_teardown_failure_does_not_stop_sweep`: three
  expired leases, the first one's teardown raises `TeardownFailed`, and leases two and three are
  still torn down.
- `tests/reconciler/test_lease_sweep_isolation.py::test_failed_lease_left_for_retry`.
- `tests/reconciler/test_breaker_state.py::test_breaker_cooldown_survives_between_jobs`.
- `tests/reconciler/test_traffic_write_through.py::test_single_batched_read_per_tick`.
- `tests/webhook_dispatcher/test_retry_scheduling.py`:
  - `::test_retryable_failure_sets_next_retry_at`
  - `::test_egress_rejection_is_not_retryable`
  - `::test_all_resolved_addresses_attempted`
  - `::test_http_exception_is_caught`
- `tests/reconciler/test_webhook_retry_job.py`:
  - `::test_due_retries_are_redelivered_and_cleared`
  - `::test_final_failure_marked_exhausted`
- `tests/webhook_receiver/test_enqueue_order.py`:
  - `::test_enqueue_failure_does_not_mark_delivery_seen`
  - `::test_pool_reused_across_requests`
- `tests/webhook_receiver/test_startup.py::test_refuses_to_start_without_secret`.
- `tests/ops/test_backup_drill.py`: the schedule assertion now expects Sunday.

**Validation:**
- `uv run pytest tests/reconciler tests/webhook_dispatcher tests/webhook_receiver tests/ops -q 2>&1 | tail -5`
  → no failures, and every test listed above passes.
- `make test-fast 2>&1 | tail -5` → no failures.

**Commit messages:** one per finding group, `fix(reconciler): …`, `fix(webhooks): …`.

### Task 0.2: ops drills, retention, and images

**Owns:**
- `src/pitwall/ops/`, `src/pitwall/retention/`
- `docker/`, `docker-compose.yml`, `docker-compose.prod.yml`
- `tests/ops/` except `tests/ops/test_backup_drill.py`
- `tests/retention/`, `tests/chaos/`, `tests/test_dockerfiles.py`

**Findings:** lane A ops, retention, and docker/compose findings.

**Requirements:**
- `docker/Dockerfile.reconciler`:
  - Installs a `postgresql-client` whose major version matches the compose Postgres image.
  - Creates `/var/lib/pitwall/archive` owned by uid 10001 with mode 0700.
  - Its healthcheck probes the worker itself, not PID 1: it runs Arq's own health check
    (`arq.worker.check_health` against `pitwall.reconciler.WorkerSettings`), which reads the
    health-check key the Arq worker refreshes on every poll.
- `ops/backup_drill.py` runs `pg_dump` and `pg_restore` through `asyncio.create_subprocess_exec`
  with the same timeouts, so the event loop is never blocked.
- `retention/archive.py`:
  - Rows with object-storage keys are skipped when no `object_delete` adapter is supplied. They are
    counted and logged, and never make the batch fail.
  - The archive directory and manifest are written only when at least one row is archived.
  - Object deletes run after the database transaction commits, and a failed delete is recorded for
    the next run.
  - Rows archived in non-purge mode are marked so they are not re-archived.
  - Row locks are not held across file I/O: select, release, encrypt and write, then re-lock and
    purge by ID.
- The compose default `PITWALL_RETENTION_MODE=archive-purge` stays. It now works.
- R-extra-1: `docker/Dockerfile.cost-exporter` runs `pitwall.cost.exporter` instead of
  `pitwall.cost_exporter`.

**Tests:**
- `tests/test_dockerfiles.py`:
  - `::test_reconciler_image_has_pg_dump`
  - `::test_reconciler_archive_dir_owned_by_service_user`
  - `::test_reconciler_healthcheck_is_not_pid1`
- `tests/ops/test_backup_drill_async.py::test_drill_does_not_block_event_loop`: a concurrent task
  keeps ticking while a fake slow `pg_dump` runs.
- `tests/retention/test_archive_object_keys.py`:
  - `::test_rows_with_object_keys_skipped_without_adapter`
  - `::test_no_orphan_directory_when_nothing_archived`
  - `::test_object_delete_after_commit`
  - `::test_non_purge_rows_not_rearchived`
  - `::test_locks_released_during_encryption`

**Validation:**
- `uv run pytest tests/ops tests/retention tests/chaos tests/test_dockerfiles.py -q 2>&1 | tail -5`
  → no failures.
- `docker compose config -q` → exit 0.
- `make test-fast 2>&1 | tail -5` → no failures.

### Task 0.3: paid-resource safety (serve, RunPod clients, control plane)

**Owns:**
- `src/pitwall/serve.py`, `src/pitwall/runpod_client/`
- `src/pitwall/runpod_control_plane.py`, `src/pitwall/runpod_files.py`, `src/pitwall/runpod_credentials.py`
- `src/pitwall/providers/runpod.py`
- `tests/serve/`, `tests/runpod_client/`, `tests/runpod_control_plane/`
- test files importing `runpod_files` or `runpod_credentials`

**Findings:** lane B serve.py, runpod_client, runpod_control_plane, runpod_market/runpod_files, and
the `providers/runpod.py` findings.

**Requirements:**
- `serve_model`:
  - After `lease_repo.get` returns a lease, any exception (including `CancelledError`) runs
    `run_teardown` with a reason naming the failure step, then re-raises.
  - `ServeVerificationFailed` keeps its `served_model_mismatch` reason.
  - `serve_model` is split into catalogue resolution, launch, verification, and warm-cache
    recording steps of at most 120 lines each.
  - RunPod SDK errors are classified by exception type or status code, not string matching.
- One retry helper in `runpod_client/` serves every retry loop in `queue.py`, `lb.py`,
  `serverless.py`, and `serverless_lb.py`. It takes an explicit safe-to-retry predicate, and the
  `/run` POST predicate allows only `httpx.ConnectError`, `httpx.ConnectTimeout`, and 429. Read
  timeouts and 5xx responses after the request is sent are returned as ambiguous failures.
- The unwired `on_429` hook is removed (spec removal item 5).
- `RunPodControlPlaneService._call` does not wrap `create_pod` in a cancelling `asyncio.wait_for`.
  The backend's per-attempt timeouts bound the call, and `_audit` records the result whether it
  succeeded, failed, or timed out, including the pod ID when one exists. The other operations keep
  their ceilings, and the 300 s ceiling becomes a documented per-operation value.
- Every RunPod API key read goes through `resolve_runpod_api_key` (`mounts.py`, `templates.py`,
  `graphql.py`, `runpod_files.py`). REST base URL defaults come from one constant.
- `create_pod_with_fallback`'s three parameter lists become one parameter object.
- `RunPodProvider.infer` raises a typed unsupported-operation error for non-embedding requests, and
  its capability description says it supports embeddings only.
- `VolumeFileService` is split by responsibility (listing, reading, mutation journal, publishing)
  into classes of at most 400 lines.

**Tests:**
- `tests/serve/test_post_launch_teardown.py`: one test per failure point, each asserting teardown ran
  with the right reason and the original exception propagated.
  - `::test_lease_not_persisted_tears_down`
  - `::test_no_proxy_url_tears_down`
  - `::test_warm_cache_patch_failure_tears_down`
  - `::test_cancelled_during_verify_tears_down`
  - `::test_unexpected_verify_error_tears_down`
- `tests/runpod_client/test_retry_policy.py`:
  - `::test_run_post_not_retried_after_read_timeout`
  - `::test_run_post_not_retried_on_5xx`
  - `::test_run_post_retried_on_connect_error`
  - `::test_run_post_retried_on_429_with_retry_after`
  - `::test_all_clients_use_shared_helper`: an AST check that no retry loop remains outside the
    helper.
- `tests/runpod_control_plane/test_create_pod_timeout.py`:
  - `::test_slow_create_is_audited_with_pod_id`
  - `::test_no_outer_cancellation_for_create_pod`
- `tests/runpod_client/test_credential_resolution.py::test_saved_runpodctl_key_used_by_every_client`
  (parametrised over mounts, templates, graphql, files, pods, serverless, registry).
- `tests/providers/test_runpod_infer_contract.py::test_non_embedding_request_rejected`.

**Validation:**
- `uv run pytest tests/serve tests/runpod_client tests/runpod_control_plane tests/providers/test_runpod_infer_contract.py -q 2>&1 | tail -5`
  → no failures.
- `make test-fast 2>&1 | tail -5` → no failures.

### Task 0.4: budget, cost, TUI cost, personal mode

**Owns:**
- `src/pitwall/cost/` (all files)
- `src/pitwall/cost_exporter/`
- `src/pitwall/tui/cost.py`, `src/pitwall/tui/hardware_fit.py`
- `src/pitwall/audit/capability.py`
- `src/pitwall/personal/`
- `src/pitwall/doctor.py` (month-to-date call only), `src/pitwall/config.py` (the `[personal]` table only)
- `tests/cost/`, `tests/unit/cost/`, `tests/finops/`, `tests/personal/`, `tests/tui/test_cost*.py`

**Findings:** lane C cost/FinOps, TUI, and personal findings, and the `audit/capability.py`
month-boundary finding.

**Requirements:**
- One month-to-date spend function in `cost/` (built on `MONTH_TO_DATE_SPEND_SQL` in
  `cost/budget_gate.py`) with a UTC-safe month boundary (`submitted_at AT TIME ZONE 'UTC'` compared
  to the UTC month start). The gate, `cost/alerts.py` (`_compute_mtd_spend`), `cost/exporter.py`,
  `tui/cost.py`, `audit/capability.py`, `doctor.py`, and `cost/budget_limits.py` all call it.
  `main` already moved some readers; the lane finishes the rest.
- `cost/exporter.py`:
  - Refuses to start without a configured monthly budget, reading it the way the gate reads runtime
    limits (`cost/budget_limits.py`) rather than `os.environ` with a default.
  - Uses `Decimal` for the budget.
  - Creates its pool through `pitwall.db.get_pool` settings (`statement_cache_size=0`).
- `tui/hardware_fit.py` uses the shared pool factory instead of one pool per lookup.
- `cost/read_models.py` serialises money as decimal strings. REST, MCP, and CLI outputs change
  accordingly, and the OpenAPI baseline is updated by the integrator.
- `budget_gate.py`:
  - Rejects float inputs the way `estimator.py` does.
  - A `per_request_cap` rejection snapshot reports the real month-to-date spend and remaining
    budget.
- `cost/cli.py`:
  - Prints a fixed error message without exception text, like `cli_burn_rate.py`.
  - Has an accurate module docstring.
- Personal mode:
  - Backend selection is explicit: `[personal] backend = "personal" | "registry"` in `pitwall.toml`,
    defaulting to personal. `DATABASE_URL` alone no longer switches it, and the chosen backend is
    printed by `pitwall status`.
  - `serve` refuses a new lease when the month's committed ceilings (sum of
    `max_usd_per_hour × ttl` for this month's leases in state) plus the new one exceed the configured
    monthly budget.
  - `StateStore.upsert` takes an exclusive `fcntl` lock around read-modify-write.
  - `max_usd_per_hour` stays `Decimal` through personal mode and is converted to the RunPod SDK's
    float only at the single call site, rounded half-up to cents first.
- Closed by removal in Task 2.5: `cost/threshold_alerts.py`, `cost/slo_governor.py`,
  `finops/bidding.py`, `finops/time_machine.py` (float math and duplicated helpers go with them).
- R-extra-1: delete `src/pitwall/cost_exporter/` and its tests, and move any test that imported it
  to import `pitwall.cost.exporter`. Task 0.2 changes the Dockerfile, and the integrator changes the
  console script entry in `pyproject.toml`.

**Tests:**
- `tests/cost/test_mtd_single_source.py`:
  - `::test_all_readers_use_shared_function`: an AST scan for other month-to-date SQL.
  - `::test_month_boundary_utc_under_non_utc_session`: runs the SQL builder against a fake
    connection that records the session time zone setting.
- `tests/cost/test_exporter_budget.py`:
  - `::test_refuses_without_budget`
  - `::test_budget_is_decimal`
- `tests/cost/test_money_wire_format.py::test_cost_read_money_is_string`.
- `tests/cost/test_budget_gate_inputs.py`:
  - `::test_float_rejected`
  - `::test_per_request_rejection_snapshot_reports_real_spend`
- `tests/personal/test_backend_selection.py::test_database_url_alone_does_not_switch_backend`.
- `tests/personal/test_monthly_cap.py::test_serve_refused_over_monthly_budget`.
- `tests/personal/test_state_lock.py::test_concurrent_upserts_keep_both_records`.
- `tests/personal/test_price_boundary.py::test_hourly_cap_converted_only_at_sdk_call`.
- `tests/cost/test_exporter_module.py::test_cost_exporter_package_removed`.

**Validation:**
- `uv run pytest tests/cost tests/unit/cost tests/finops tests/personal tests/tui -q 2>&1 | tail -5`
  → no failures.
- `make test-fast 2>&1 | tail -5` → no failures.

### Task 0.5: MCP, CLI, and lease renewal surfaces

**Owns:**
- `src/pitwall/mcp/` (all files), `src/pitwall/mcp_install.py`, `src/pitwall/cli_mcp_install.py`
- `src/pitwall/cli.py`, `src/pitwall/cli_burn_rate.py`
- `src/pitwall/leases/`
- `tests/mcp/`, `tests/cli/`, `tests/leases/`

**Findings:** lane C MCP and CLI findings except the serialiser duplication (Task 2.3) and the
`cli.py` god-module split (Task 1.3). Also the lane A leases alias finding.

**Requirements:**
- `ToolSpec.scope` is removed. The six "Admin-only" tool descriptions state the actual trust model:
  any local process that can start the stdio server has full access.
- The hardcoded `actor="mcp:admin"` strings stay, and are documented as surface labels, not
  identities.
- Lease renewal from the CLI (`cli.py` lease renew path) and MCP (`mcp/tools/leases.py`) passes
  `pool` and `capability_name` to `leases/mutations.renew_lease`, so the renewal event is published
  as it is from REST.
- `mcp/tools/cost.py` rejects naive datetimes with the same error as `cost/cli.py`, and its docstring
  cites real module paths.
- `pitwall_health` reports real checks (database reachable, Redis reachable, provider registry
  loaded) as booleans.
- The import-time `assert len(...) == 79` checks in `mcp/registry.py` become a test.
- `mcp_install.py` forwards every environment variable that `check_domain_config("mcp")` requires.
  Verify against `config.py` `_REQUIRED_ENV_BY_SERVICE` and `_CORE_RUNTIME_ENV`.
- `cli.py`'s one-off asyncpg pool uses `pitwall.db.get_pool`.
- `cli.py` stops importing private RunPod symbols (`_TEMPLATE_ENV_KEYS`, `_safe_json`). Task 0.3
  exposes public equivalents; until it merges, this lane records the dependency in its report and
  the integrator applies the import change after both merge.
- The alias names in `leases/state.py` (`VALID_LEASE_STATE_TRANSITIONS`,
  `InvalidLeaseTransitionError`, `transition`, `can_transition`) are removed if they have no
  callers; any caller moves to the primary names. The compatibility alias
  `list_active_with_idle_timeout` in `db/repository.py` is reported to Task 0.1's owner through the
  integrator.

**Tests:**
- `tests/mcp/test_admin_descriptions.py::test_no_tool_claims_enforcement`.
- `tests/mcp/test_tool_count.py::test_registry_has_documented_tool_count`.
- `tests/leases/test_renewal_event.py::test_renewal_event_published_from_every_surface`
  (parametrised over REST, CLI, MCP).
- `tests/mcp/test_cost_datetimes.py::test_naive_datetime_rejected`.
- `tests/mcp/test_health.py::test_health_reports_real_checks`.
- `tests/mcp/test_install_env.py::test_forwards_every_required_mcp_variable`.

**Validation:**
- `uv run pytest tests/mcp tests/cli tests/leases -q 2>&1 | tail -5` → no failures.
- `make test-fast 2>&1 | tail -5` → no failures.

### Task 0.6: routing, non-RunPod providers, Model Studio, resolver, gateway catalog

**Owns:**
- `src/pitwall/routing/fallback.py`, `openai.py`, `lockout.py`, `production.py`
- `src/pitwall/api/routes/openai.py`
- `src/pitwall/providers/` except `runpod.py`
- `src/pitwall/resolver/`, `src/pitwall/gateway_catalog/`, `src/pitwall/cli_gateway.py`
- `src/pitwall/recommendations/engine.py`
- `tests/routing/` (non-property), `tests/providers/` except `test_runpod_infer_contract.py`
- `tests/resolver/`, `tests/gateway_catalog*`

**Findings:** lane B providers (non-RunPod), routing (except the parallel planners, the unused
primitives, and provider-specific leakage, which are Tasks 2.2, 2.5, and 2.4), resolver, and
gateway_catalog findings. Also the lane B recommendations stale-comment finding and the lane A
`openai_proxy` size finding.

**Requirements:**
- OpenAI proxy path:
  - `routing/fallback.py` treats 429 as failover-eligible.
  - `api/routes/openai.py` records a lockout on 429 through the same `routing/lockout.py` call the
    adapter path uses.
  - `openai_proxy` is split into steps of at most 120 lines.
- `build_production_plan` takes the lockout snapshot as an explicit argument. The caller reads the
  process-global table, so the function stays I/O-free as documented.
- `execute_sync_prepared`:
  - Records a cooldown for timeout and 5xx failures through `routing/cooldown.py` before moving to the
    next attempt.
  - Has a bounded backoff between attempts.
  - Is split into steps of at most 120 lines.
- `is_openai_compatible_provider(None)` returns False.
- Model Studio:
  - `availability()` calls `catalog.require_automation` like `infer`.
  - `_collect_stream` raises a typed provider error on malformed JSON, on an SSE error chunk, and on
    a non-SSE 200 body.
  - `availability()` guards `response.json()`.
  - `Retry-After` is honoured when present.
  - Model Studio has its own `ModelStudioQuotaExhausted`, and both it and the gateway's
    `QuotaExhausted` subclass a shared `providers/errors.py` `ProviderQuotaExhausted` that lockout
    code catches.
- `gateway.classify_429` treats naive body timestamps as UTC, like the header path.
- The legacy kwargs on `infer` in `together.py` and `model_studio/adapter.py` are removed and their
  callers updated. The gateway adapter uses `resolve_adapter_credentials`.
- `providers/service.py`:
  - Availability does not require a credential for providers that declare none.
  - The credentials mapping comes from the adapter's declared credential shape.
  - `_provider_error_code` maps exception types, not class-name strings.
- `vast.py` and `lambda_cloud.py` share their cleanup and polling logic through one helper module.
  Both keep their `except BaseException` cleanup semantics.
- `resolver/provider_urls.py`:
  - `validate_openai_base_url` rejects private, link-local, loopback, and metadata addresses for
    HTTPS unless the provider is explicitly local.
  - Its loopback rule matches `gateway.is_loopback_base_url`.
  - Its docstring states exactly what it guards.
- The `providers/gateway.py` import of `resolver.provider_urls` no longer loads `pitwall.routing`:
  `resolver/__init__.py` stops importing `resolver.service` eagerly, and the local-import workaround
  in `routing/fallback.py` is removed.
- `gateway_catalog/sync.py` computes `REPO_ROOT` lazily. The dead `gateway_catalog/data` fallback in
  `cli_gateway.py` is removed.
- The `recommendations/engine.py` `ScorecardMetric` comment is corrected, and the type is replaced by
  `observability.scorecards.EntityScorecard` where the fields match. Otherwise it is renamed so the
  two types are not confused.

**Tests:**
- `tests/routing/test_proxy_quota.py`:
  - `::test_429_fails_over_to_next_provider`
  - `::test_429_records_lockout`
- `tests/routing/test_production_plan_purity.py::test_plan_reads_only_injected_lockout`.
- `tests/routing/test_execute_attempts.py`:
  - `::test_timeout_records_cooldown`
  - `::test_backoff_between_attempts`
- `tests/providers/test_model_studio_provider.py` additions:
  - `::test_availability_respects_automation_gate`
  - `::test_malformed_chunk_raises_typed_error`
  - `::test_sse_error_chunk_raises`
  - `::test_non_sse_200_raises`
  - `::test_retry_after_honoured`
  - `::test_quota_error_is_model_studio_type`
- `tests/providers/test_gateway_classify_429.py::test_naive_body_timestamp_is_utc`.
- `tests/providers/test_service_availability.py`:
  - `::test_keyless_provider_available`
  - `::test_error_code_by_type`
- `tests/resolver/test_provider_urls.py`:
  - `::test_https_private_address_rejected`
  - `::test_localhost_consistent_with_gateway`
- `tests/providers/test_import_weight.py::test_gateway_adapter_does_not_import_routing`: a subprocess
  that checks `sys.modules`.

**Validation:**
- `uv run pytest tests/routing tests/providers tests/resolver tests/gateway_catalog* -q 2>&1 | tail -5`
  → no failures.
- `make test-fast 2>&1 | tail -5` → no failures.

### Task 0.7: API core, database tooling, policy, seed, audit, config

**Owns:**
- `src/pitwall/api/app.py`, `src/pitwall/api/leases/`, `src/pitwall/api/routes/jobs.py`, `src/pitwall/api/scopes.py`
- `src/pitwall/db/__init__.py`, `src/pitwall/policy/`, `src/pitwall/seed.py`, `src/pitwall/audit/` except `capability.py`
- `src/pitwall/config.py`, `src/pitwall/onboarding.py`, `src/pitwall/core/cost_reporting.py`
- `tests/api/`, `tests/db/`, `tests/audit/`, `tests/policy/`, `tests/config/`, `tests/security/`
- root test files for these modules: `tests/test_audit_checks.py`, `tests/test_onboarding*.py`, `tests/test_seed*.py`

**Findings:** lane A api, db and migrations, policy, audit, security, root-module, and core
findings, except the audit constraint row (intended behaviour) and doc drift (Task 5.2).

**Requirements:**
- `api/app.py`:
  - `InboundRateLimitMiddleware` evicts idle buckets (LRU with a fixed maximum and idle expiry).
  - Authentication runs before the body is buffered: `RequestBodyLimitMiddleware` moves inside the
    auth check for non-public paths.
  - `_PUBLIC_HEALTH_PATHS` lists only routes that exist.
  - `/healthz` and `/health` stop reporting `"backend": "runpod"`.
  - The unused `_RUNPOD_API_KEY` and `_DATABASE_URL` globals are removed.
- `config.py`:
  - The API boots without `RUNPOD_API_KEY` when no RunPod provider is configured. The key is required
    when the first RunPod operation runs, with a typed error.
  - `_explicit_env_settings_data` is split into steps of at most 120 lines.
- `api/leases/teardown.py`:
  - `run_teardown` reports success when the pod is terminated and the lease closed, and records audit
    or disarm errors separately without re-raising.
  - The missing-provider fallback uses the provider type recorded on the lease, not RunPod.
- `api/leases/launch.py` `_run_launch_runpod` and `onboarding.py` `_apply_locked` are split into
  steps of at most 120 lines.
- `GET /v1/jobs` returns stored `input` and `result` only to tokens that hold the scope required to
  create jobs. Read-only tokens get metadata. `api/scopes.py` documents this.
- `db/__init__.py`:
  - `_docker_psql` passes host, port, user, database, and password from `DATABASE_URL`, and refuses
    when the URL does not point at the container.
  - `cmd_status` is read-only: no `CREATE TABLE`. It reports connection errors as errors, not "all
    pending", and reports checksum drift through `detect_drift`.
  - `status` and `reset` use asyncpg like `migrate`.
  - `_applied_migrations_async` is deleted if it has no callers.
  - `db_lifespan` closes the pool in `finally`, and `get_pool` raises a typed error instead of
    `assert`.
- `policy/loader.py` uses PyYAML (`yaml.safe_load`). `seed.py`'s `_parse_simple_yaml` and its
  `ModuleNotFoundError` fallback are deleted.
- `audit/_runtime_config.py` and `audit/checks.py`:
  - Every hardcoded `True` input is derived from real configuration or code, or the check's pass
    message states only what it verified.
  - Check 07 verifies webhook idempotency through the receiver's dedupe path.
  - Source-text checks use AST.
  - `check_15` uses dependency injection instead of monkeypatching `pods._rest_request`.
  - Check 07 imports the receiver without triggering `require_runtime_env` (lazy import).
  - `checks.py` is split into one module per check family, each at most 500 lines.
- `core/cost_reporting.recent_workloads_read` is split into steps of at most 120 lines.

**Tests:**
- `tests/api/test_rate_limit_eviction.py::test_idle_buckets_evicted`.
- `tests/api/test_auth_before_body.py::test_unauthenticated_large_body_rejected_without_buffering`.
- `tests/api/test_health.py::test_health_is_provider_neutral`.
- `tests/config/test_runpod_key_optional.py::test_api_boots_without_runpod_key`.
- `tests/api/test_teardown_result.py`:
  - `::test_audit_error_does_not_fail_completed_teardown`
  - `::test_missing_provider_uses_lease_provider_type`
- `tests/api/test_jobs_visibility.py::test_read_only_token_sees_metadata_only`.
- `tests/db/test_docker_psql_target.py::test_uses_database_url_components`.
- `tests/db/test_status_readonly.py`:
  - `::test_status_creates_nothing`
  - `::test_connection_error_reported`
  - `::test_drift_reported`
- `tests/policy/test_loader_yaml.py`:
  - `::test_anchor_and_flow_and_block_scalars_parse`
  - `::test_matches_pyyaml`
- `tests/audit/test_no_hardcoded_attestations.py::test_runtime_config_inputs_are_derived`.
- `tests/audit/test_check_07.py::test_verifies_receiver_dedupe`.

**Validation:**
- `uv run pytest tests/api tests/db tests/audit tests/policy tests/config tests/security -q -m "not integration" 2>&1 | tail -5`
  → no failures.
- `make openapi-check 2>&1 | tail -3` → the diff contains only the intended changes, and the
  integrator updates the baseline.
- `make test-fast 2>&1 | tail -5` → no failures.

### Task 0.8: Agent Routing defects (before the move)

**Owns:**
- `packages/agent-routing/runtime/model_routing/dispatch.py`, `process.py`, `route_sync.py`, `doctor.py`, `provider_setup.py`
- `packages/agent-routing/runtime/model_routing/providers/`
- `packages/agent-routing/runtime/model_routing/resources/config/provider-installers.json`
- `packages/agent-routing/tests/`

**Findings:** lane D findings, except those under
[Findings recorded as intended behaviour](#findings-recorded-as-intended-behaviour), the legacy names
(Task 1.2), the hooks and steer-gate rows (Task 1.5), and workflow support (Task 2.6).

**Requirements:**
- `kimi.py` and `dsh.py` honour the unrestricted setting: they map it to their CLI's
  approval-bypass flag, or refuse to dispatch in restricted mode when the CLI has no such flag.
- An invalid, zero, or negative `SHIM_TIMEOUT_SECS` is a usage error (non-zero exit with a message),
  not a 1 ms timeout.
- The GNU `timeout`/`gtimeout` prerequisite check and `_gnu_timeout_available` are removed (spec
  removal item 14). Docs referencing it are updated in Task 5.2.
- `_dispatch_legacy`, `doctor._check_provider_auth_probe`, and `cli.build_parser` are split into
  steps of at most 120 lines.
- `doctor._check_generated_routes` imports its dependency without a `sys.path` hack. Move the needed
  function into the runtime package.
- `route_sync.py`'s subprocess call passes an explicit environment and stdio handling.
- One shared model-flag parser in `providers/base.py` handles `-m`, `--model`, and `--model=`, and
  all 13 adapters use it. muse gains `-m`.
- `sanitize_args` redacts values that look like secrets (by pattern, not only by flag name), for
  positional arguments too.
- Prompt delivery (decision 4):
  - `pi.py` switches from argv to file delivery. It writes the prompt to a mode-0600 file in the
    dispatch's private run directory and passes it as `@<path>` (documented in
    `pi [options] [--] [@files...]`).
  - `kimi.py` (`-p, --prompt <prompt>`), `grok.py` (`-p, --single <PROMPT>`), and `dsh.py` (argument
    pass-through to the booted profile) keep argv, because their `--help` documents no stdin or file
    input.
  - Before changing each adapter, the lane re-runs `<cli> --help` and records the relevant lines in
    its report. If a CLI now documents stdin or file input, that adapter switches too.
  - Adapters that keep argv refuse a prompt larger than a shared `ARGV_PROMPT_LIMIT_BYTES` (120 KiB,
    leaving headroom below Linux's 128 KiB single-argument limit `MAX_ARG_STRLEN`). They exit with a
    usage error naming the limit, instead of failing inside `exec`.
- All 20 installer recipes in `provider-installers.json` carry a pinned SHA-256. `download_installer`
  refuses a recipe with `sha256: null`. The lane downloads each pinned installer once to compute its
  hash and records the source URL and date in its report.

**Tests:**
- `tests/test_provider_adapters.py`:
  - `::test_unrestricted_honoured_by_every_adapter` (parametrised over all 13).
  - `::test_model_flag_forms` (parametrised).
- `tests/test_dispatch.py`:
  - `::test_invalid_timeout_is_usage_error`
  - `::test_dispatch_runs_without_gnu_timeout`
- `tests/test_provider_setup.py::test_unpinned_recipe_refused`.
- `tests/test_sanitize_args.py::test_positional_secret_redacted`.
- `tests/test_doctor.py::test_generated_routes_check_without_sys_path`.
- `tests/test_provider_adapters.py::test_pi_prompt_delivered_as_private_file`: asserts the `@<path>`
  argument, the file mode 0600, and that the prompt text is absent from argv.
- `tests/test_provider_adapters.py::test_argv_prompt_over_limit_refused` (parametrised over kimi,
  grok, dsh).

**Validation:**
- `cd packages/agent-routing && .venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -5`
  → `OK`, with the skip count unchanged from the pre-change run recorded in the report.

### Task 0.9: CI gates, guards, and release process

**Owns:**
- `.github/workflows/ci.yml`, `.github/workflows/release-readiness.yml`
- `Makefile`, `tools/guards/`, `tools/smoke_*`, `tools/benchmark_embedding_latency/`
- `.pre-commit-config.yaml`, `scripts/mutmut_score_gate.py`, `scripts/release/run-user-journeys.sh`
- `tests/test_release_scripts.py`, `tests/tools/`, `RELEASING.md`
- new `tests/test_mutmut_score_gate.py`

**Findings:** lane F findings about CI, guards, the smoke and benchmark tools, the Makefile, and
`RELEASING.md`. The release-readiness duplication and coverage re-runs are Task 5.1.

**Requirements:**
- A `journeys` job in `ci.yml` runs `scripts/release/run-user-journeys.sh` against the job's own
  Postgres and Redis service containers with `PITWALL_JOURNEY_HARNESS=1`. It is listed in the
  `required` job's `needs`.
- `gateway-catalog-drift` is added to the `required` job's `needs`.
- The lint job runs `tools/guards/python_policy.py` over the tracked Python files, so the guard no
  longer depends on pre-commit.
- `tools/guards/forbidden_imports.py` and its pre-commit hook are deleted (removal item 11).
- `tools/smoke_*` and `tools/benchmark_embedding_latency` are deleted (removal item 10).
- `scripts/mutmut_score_gate.py` gets a test for its threshold logic.
- The duplicate `mut` target is removed, and `mutation` remains.
- `RELEASING.md` requires the journey harness result for a release and names the command.
- `tests/test_release_scripts.py`'s pin on harness deselection is updated to expect the CI job.

**Tests:**
- `tests/tools/test_ci_required_gate.py`:
  - `::test_required_needs_journeys_and_catalog_drift`: parses `ci.yml`.
  - `::test_python_policy_runs_in_ci`.
- `tests/test_mutmut_score_gate.py::test_below_floor_fails` and `::test_at_floor_passes`.

**Validation:**
- `make ci-tools 2>&1 | tail -5` → actionlint and the workflow policy pass.
- `uv run pytest tests/tools tests/test_release_scripts.py tests/test_mutmut_score_gate.py -q 2>&1 | tail -5`
  → no failures.
- After merge, the integrator confirms the new `journeys` job passes on the branch's CI run and
  cites the run.

---

## Phase 1: Agent Routing becomes `pitwall.agents`

### Task 1.1: mechanical move

**Owns:**
- `packages/agent-routing/` (all of it, for deletion)
- `src/pitwall/agents/`, `tests/agents/`
- `plugins/`, `docs/agents/`, `docs/prompting/`, `examples/agents/`
- `tools/agents/`
- `pyproject.toml`, `ruff.toml` (root, if present)
- `.github/workflows/agent-routing-ci.yml`, `.github/workflows/ci.yml`

**Requirements:**
- Move files with `git mv`, preserving history, following this map:
  - `packages/agent-routing/runtime/model_routing/` → `src/pitwall/agents/`
  - `packages/agent-routing/tests/` → `tests/agents/`
  - `plugins/pitwall` → `plugins/claude`, `plugins/pitwall-codex` → `plugins/codex`,
    `plugins/pitwall-copilot` → `plugins/copilot`
  - `docs/` → `docs/agents/`, `prompting/` → `docs/prompting/`, `examples/` → `examples/agents/`
  - `tools/` → `tools/agents/`, `references/README.md` → `docs/agents/references.md`
  - `scripts/` → `src/pitwall/agents/resources/scripts/` (deleted in Task 1.5)
  - `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` → `docs/agents/` (folded in Task 5.3)
- Rewrite imports from `model_routing` to `pitwall.agents`. Internal module names are unchanged in
  this task.
- `_hatch_build.py`: keep its behaviour (package data inclusion) in the root build config, then
  delete the hook file.
- Merge the Agent Routing ruff settings into the root config. Where rules conflict, the root wins,
  and the lane lists each overridden rule in its report.
- Agent Routing's dev dependency group entries that the root `dev` extra lacks are added to the root
  `dev` extra.
- Paths computed from the component root (for example `parents[2]` in `tools/release/*` and in
  tests) are updated to the new locations.
- `agent-routing-ci.yml` is deleted. Any check it ran that `ci.yml` lacks (for example
  `check_generated.py` and the validators) is added to `ci.yml`'s lint or test job.
- `agent-routing-model-facts.yml` path filters and commands are updated in place, and folded in
  Task 5.1.
- No behaviour, name, or environment variable changes.

**Tests:**
- The moved suite is the test. Every Agent Routing test runs under the root project's pytest.

**Validation:**
- Before the move, record `cd packages/agent-routing && .venv/bin/python -m unittest discover -s tests -q 2>&1 | tail -3`.
- After the move, `uv run pytest tests/agents -q 2>&1 | tail -3` → the same number of passing tests
  and the same skip count as recorded.
- `make test-fast 2>&1 | tail -5` → no failures.
- `test ! -e packages/agent-routing && echo gone` → `gone`.

### Task 1.2: terms and names

**Owns:** `src/pitwall/agents/`, `tests/agents/`, `plugins/`, `docs/agents/`, `examples/agents/`,
`tools/agents/`.

**Requirements:**
- `agents/providers/` becomes `agents/harnesses/`, and "provider" in identifiers, messages, and help
  text within `pitwall.agents` becomes "harness" where it means an agent CLI.
- `routes.py`, `route_sync.py`, `route_probe.py`, and `routes_setup.py` become `profiles.py` (split
  into submodules if it would exceed 800 lines). "route" becomes "profile" where it means a
  `name@harness` binding.
- `endpoint_slots.py` and `endpoint_discovery.py` become `endpoints.py`.
- `provider_setup.py` becomes `setup.py`.
- `pitwall.py` and `pitwall_sync.py` become `broker.py`.
- Environment variables are renamed:
  - `SUBAGENT_MODEL_ROUTING_*` and `PITWALL_AGENT_ROUTING_*` become `PITWALL_AGENTS_*`.
  - `SHIM_TIMEOUT_SECS` becomes `PITWALL_AGENTS_TIMEOUT_SECS`.
  - `PITWALL_API_TOKEN` is removed, and the broker client reads `PITWALL_AGENTS_API_TOKEN`.
- Review Focus 1: at startup, `pitwall agents` (every subcommand, including `_shim`) refuses to run
  when any legacy variable is set. It exits 2 and prints each legacy name with its replacement.
- Tests that pin old names are updated in the same commit (see the "renames are guarded by tests"
  note: grep `tests/` for every old name before finishing).

**Tests:**
- `tests/agents/test_legacy_env_guard.py`:
  - `::test_shim_refuses_when_legacy_variable_set` (parametrised over every legacy name).
  - `::test_message_names_replacement`
- `tests/agents/test_no_legacy_names.py::test_no_legacy_identifiers_remain`: greps the package for
  old variable names, `model_routing`, and `routes.json`, excluding the migration module's
  legacy-name table.

**Validation:**
- `uv run pytest tests/agents -q 2>&1 | tail -3` → no failures, and the test count is at least the
  count recorded in Task 1.1.
- `grep -rn "SUBAGENT_MODEL_ROUTING\|PITWALL_AGENT_ROUTING\|SHIM_TIMEOUT_SECS" src tests plugins docs/agents | grep -v migrate | wc -l`
  → `0`.

### Task 1.3: CLI integration, doctor, and the startup guard

**Owns:**
- `src/pitwall/cli.py` and new `src/pitwall/cli/` modules it is split into
- `src/pitwall/agents/cli.py`, `src/pitwall/doctor.py`, `src/pitwall/agents/doctor.py`
- `src/pitwall/mcp/__main__.py`, `src/pitwall/service_args.py`
- `pyproject.toml` (`[project.scripts]` only)
- `tests/cli/`, new `tests/test_startup_imports.py`

**Findings:** the lane C `cli.py` god-module finding.

**Requirements:**
- `pitwall.cli:main` dispatches command groups through a table of `(group, module, function)` with
  lazy imports. Each group lives in its own module under `src/pitwall/cli/`, and no module exceeds
  800 lines. The existing `cli_*.py` modules move into it.
- New groups:
  - `pitwall agents …` (the Agent Routing CLI).
  - `pitwall usage …` (moved from `agents usage` to top level).
  - `pitwall mcp serve broker`, `pitwall mcp serve channel`, and `pitwall mcp install`, which
    registers both servers.
- `pitwall doctor` runs one report with sections for broker, agents, gateway, and workbench. The
  gateway and workbench sections are added by Tasks 3.4 and 4.6 through a section registry this task
  defines: `register_doctor_section(name: str, check: Callable[[], DoctorSection])`.
- `[project.scripts]` removes `pitwall-agent-routing` and `pitwall-mcp`. `pitwall`, `pitwall-api`,
  `pitwall-reconciler`, `pitwall-webhook`, and `pitwall-cost-exporter` remain.
- A CLI boundary guard, like the TUI and MCP guards, forbids CLI modules from importing repositories
  or DB internals directly.

**Tests:**
- `tests/test_startup_imports.py::test_shim_path_imports_no_heavy_modules`: runs
  `pitwall agents _shim --help` and `pitwall agents _steer-gate --help` in a subprocess with
  `-X importtime`, and asserts none of the forbidden modules in the Global Constraints loads.
- `tests/cli/test_group_table.py::test_every_group_lazy`.
- `tests/cli/test_no_business_logic_guard.py`.
- `tests/cli/test_mcp_serve.py`:
  - `::test_serve_broker`
  - `::test_serve_channel_without_broker_config`
- `tests/test_doctor_sections.py::test_unified_report_has_agents_section`.

**Validation:**
- `uv run pytest tests/cli tests/test_startup_imports.py tests/test_doctor_sections.py tests/agents -q 2>&1 | tail -5`
  → no failures.
- `uv run pitwall agents --help 2>&1 | head -3` → the agents usage text.

### Task 1.4: one config file and one state root

**Owns:**
- `src/pitwall/config.py`, `src/pitwall/agents/profiles*.py`
- `src/pitwall/agents/run_store.py`, `mailbox.py`, `resources.py`
- `src/pitwall/agents/usage/cache.py`
- new `src/pitwall/agents/paths.py`
- `tests/config/`, `tests/agents/test_profiles*.py`, `tests/agents/test_paths.py`

**Requirements:**
- `pitwall.toml` gains an `[agents]` table (settings Agent Routing kept in its own config) and an
  `[agents.profiles]` table (replacing `routes.json`), both validated by `config.py`'s settings
  models.
- `agents/paths.py` is the only place that resolves agent state paths. The root is
  `~/.local/state/pitwall/agents/`, honouring `XDG_STATE_HOME`. The run store, mailbox, receipts, and
  usage cache resolve through it.
- `PITWALL_AGENTS_PROFILES` (the replacement for the old routes-file override) points at an
  alternative TOML file, for tests and isolated runs.

**Tests:**
- `tests/agents/test_paths.py`:
  - `::test_state_root_under_pitwall`
  - `::test_xdg_override`
- `tests/agents/test_profiles_toml.py`:
  - `::test_profiles_load_from_pitwall_toml`
  - `::test_invalid_profile_rejected_with_path`
- `tests/config/test_agents_table.py::test_agents_table_validated`.

**Validation:**
- `uv run pytest tests/agents tests/config -q 2>&1 | tail -5` → no failures.

### Task 1.5: plugins, shims, hooks, and install

**Owns:**
- `plugins/`
- `src/pitwall/agents/installation.py`, `hooks.py`, `steer_gate.py`, `mcp_registration.py`
- `src/pitwall/agents/resources/`
- `tools/agents/check_generated.py`
- `tests/agents/test_install*.py`, `tests/agents/test_claude_tripwires.py`, `test_plugin_identity.py`,
  `test_marketplaces.py`, `test_shim_contract.py`, `test_shim_receipt.py`

**Findings:** lane D hooks and steer-gate rows.

**Requirements:**
- `pitwall agents install`:
  - Generates `~/.claude/scripts/<harness>-shim.sh` for all 13 harnesses and `route-shim.sh` as
    two-line wrappers (`exec pitwall agents _shim <harness> "$@"`), mode 0755.
  - Installs `plugins/claude`, `plugins/codex`, and `plugins/copilot` from package data.
  - Registers the channel MCP server as `pitwall mcp serve channel`.
- `pitwall agents uninstall` removes exactly what `install` wrote.
- The committed shim scripts, `bootstrap.sh`, `install.sh`, the `pitwall-agent-routing` launcher, and
  `parse-shim-result.py` are deleted (removal item 13). The result parsing moves into
  `pitwall.agents.result`.
- The shim output contract (`SHIM-DONE exit=N`, last-two-line result) is unchanged.
- Generated references (`routes.generated.md`, `provider-registry.generated.json`) are generated once
  into `src/pitwall/agents/resources/generated/` and copied into each plugin at install.
  `check_generated.py` checks only the single copy.
- Plugin manifests keep the `pitwall:*` identity. The marketplace name becomes `pitwall-local`.
- The steer-gate hook fails closed (decision 5). When the gate cannot be evaluated (CLI missing,
  timeout, or error), the hook blocks the tool call. Its block message states:
  - the cause;
  - that recovery must not go through a tool call: type `! uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` at the prompt, or
    disable the plugin with `/plugin`;
  - how to check the install with `pitwall doctor`.
- `pitwall agents uninstall` removes the hook registrations first, before any other file, so an
  uninstall never leaves a hook pointing at a missing CLI.
- `pitwall doctor`'s agents section fails when a registered hook points at a CLI that cannot be run.
- The hook's timeout stays 10 s. The startup import guard (Task 1.3) keeps `_steer-gate` fast enough
  that a healthy install never reaches it.
- `launch-guard.py`, `dag-tripwire.py`, and `ledger-tripwire.py` are split into modules of at most
  400 lines, with their tests unchanged.

**Tests:**
- `tests/agents/test_install.py`:
  - `::test_install_writes_all_shims_executable`
  - `::test_shim_invokes_pitwall_agents`
  - `::test_uninstall_removes_exactly_installed_files`
  - `::test_channel_server_registered_as_pitwall_mcp`
- `tests/agents/test_shim_contract.py`: the existing contract tests run against generated shims.
- `tests/agents/test_steer_gate_fail_closed.py`:
  - `::test_missing_cli_blocks_with_recovery_message`
  - `::test_timeout_blocks`
  - `::test_gate_error_blocks`
  - `::test_uninstall_removes_hooks_first`
- `tests/agents/test_doctor_hooks.py::test_doctor_fails_on_hook_with_unrunnable_cli`.

**Validation:**
- `uv run pytest tests/agents -q 2>&1 | tail -5` → no failures.
- `uv run python tools/agents/check_generated.py; echo exit=$?` → `exit=0`.

### Task 1.6: broker client and the import-direction guard

**Owns:** `src/pitwall/agents/broker.py`, `tests/agents/test_broker*.py`, new
`tests/test_agents_import_direction.py`.

**Requirements:**
- `agents/broker.py` builds requests and parses responses with the Pydantic models from
  `pitwall.api` that the broker's routes use, over `httpx`.
- `broker.py` reads `PITWALL_API_URL` and `PITWALL_AGENTS_API_TOKEN`.
- No module under `pitwall.agents` imports `pitwall.db`, `pitwall.api.leases`, repositories, or
  service modules.

**Tests:**
- `tests/test_agents_import_direction.py::test_agents_never_imports_broker_internals`: AST over
  `src/pitwall/agents`.
- `tests/agents/test_broker_models.py::test_request_matches_api_schema`: validates the client's
  payload against the route's request model.
- `tests/agents/test_broker_models.py::test_response_parsed_with_api_model`.

**Validation:**
- `uv run pytest tests/agents tests/test_agents_import_direction.py -q 2>&1 | tail -5` → no failures.

### Task 1.7: `pitwall agents migrate`

**Owns:** new `src/pitwall/agents/migrate.py`, `tests/agents/test_migrate.py`,
`tests/agents/fixtures/legacy_layout/`.

**Consumes:**
- `agents/paths.py` (Task 1.4)
- the `pitwall.toml` agents tables (Task 1.4)
- `installation.install` and `installation.uninstall` (Task 1.5)

**Requirements:**
- The migration performs spec section "Migration" steps 1 to 5, idempotently.
- Review Focus 2:
  - It refuses (exit 2, listing the active runs) while the legacy run store has active dispatches.
  - It never overwrites a differing value in an existing `pitwall.toml`. It lists every conflict and
    exits non-zero without changing anything.
- It removes the old install only after every other step succeeded, and prints each removed path.
- The legacy-name table used for reporting old environment variables lives only in this module.

**Tests:**
- `tests/agents/test_migrate.py`:
  - `::test_migrates_fixture_layout`
  - `::test_second_run_is_noop`
  - `::test_refuses_with_active_runs`
  - `::test_conflicting_toml_value_not_overwritten`
  - `::test_reports_legacy_environment_variables`
  - `::test_old_install_removed_last`

**Validation:**
- `uv run pytest tests/agents/test_migrate.py -q 2>&1 | tail -5` → no failures.

---

## Phase 2: one implementation per concern, and approved removals

### Task 2.1: one Model Studio implementation

**Owns:**
- `src/pitwall/providers/model_studio/`
- `src/pitwall/agents/model_studio.py`, `src/pitwall/agents/model_studio_openapi.py`, `src/pitwall/agents/usage/model_studio.py`
- `tests/providers/test_model_studio*.py`, `tests/agents/test_model_studio*.py`, `tests/agents/test_usage*model_studio*.py`
- `tests/test_model_studio_catalog_parity.py`

**Requirements:**
- `pitwall.providers.model_studio` is the only Model Studio implementation: catalog, OpenAPI signing
  and billing reads, and the Token Plan usage read.
- `agents/model_studio*.py` are deleted, and agents code (usage, harness setup) imports the provider
  package.
- One `catalog.json`. The catalog-parity test is deleted because there is nothing left to compare.
- Every behaviour covered by the deleted agents tests is covered by a test against the provider
  package. The lane lists each deleted test and its replacement in its report.

**Validation:**
- `uv run pytest tests/providers tests/agents -q 2>&1 | tail -5` → no failures.
- `git ls-files | grep -c "model_studio.*catalog.json"` → `1`.

### Task 2.2: one route planner

**Owns:**
- `src/pitwall/routing/planner.py`, `src/pitwall/routing/production.py`
- `src/pitwall/cost/simulator.py`, `src/pitwall/finops/time_machine.py`
- `src/pitwall/resolver/service.py`, `src/pitwall/core/inference.py`
- `src/pitwall/routing/__init__.py`, `src/pitwall/cost/__init__.py`, `src/pitwall/finops/__init__.py`
  (re-exports of the modules this task deletes)
- `tests/routing/test_planner*.py`, `tests/cost/test_simulator*.py`, `tests/finops/test_time_machine*.py`
- the planner property tests in `tests/property/`

**Requirements:**
- `cost/simulator.py` plans with `build_production_plan`, passing observed state (health, quota,
  lockout, prices) as the explicit inputs that Task 0.6 made injectable.
- `resolver.select_stage12_provider` is folded into the production planner, and `core/inference.py`
  calls the production planner.
- `routing/planner.py` is deleted (removal item 2), and so is `finops/time_machine.py` (removal
  item 3).
- `docs/sdlc/04-routing.md`'s entry point is updated in Task 5.2. This lane reports the new entry
  points.
- Property tests that exercised `plan_route` are re-targeted at `build_production_plan` where they
  test real routing rules, and deleted where they tested only the removed planner. The report lists
  each.

**Tests:**
- `tests/cost/test_simulator_uses_production_planner.py::test_simulation_matches_live_plan`: the same
  inputs give the same provider order from the simulator and from the live planner.

**Validation:**
- `uv run pytest tests/routing tests/cost tests/property tests/resolver -q 2>&1 | tail -5` → no
  failures.

### Task 2.3: one serialiser per response

**Owns:**
- new `src/pitwall/api/serializers.py`
- `src/pitwall/api/capability_routes.py`, `src/pitwall/api/provider_routes.py`, `src/pitwall/api/routes/leases.py`
- `src/pitwall/mcp/tools/discovery.py`, `src/pitwall/mcp/tools/admin.py`, `src/pitwall/mcp/tools/leases.py`
- `tests/api/test_serializers.py`

**Requirements:**
- `capability_to_response`, `provider_to_response`, and `lease_to_response` exist once in
  `api/serializers.py`. REST and MCP call them.
- The private copies (`_capability_to_response` in three places, `_provider_to_response` in three,
  `_lease_to_response` in two) are deleted.

**Tests:**
- `tests/api/test_serializers.py::test_rest_and_mcp_outputs_identical` (parametrised over the three
  resources).
- `tests/api/test_serializers.py::test_no_private_copies`: an AST scan.

**Validation:**
- `uv run pytest tests/api tests/mcp -q -m "not integration" 2>&1 | tail -5` → no failures.

### Task 2.4: providers declare their own behaviour

**Owns:**
- `src/pitwall/routing/fallback.py`, `src/pitwall/routing/lockout.py`
- `src/pitwall/providers/interface.py`, `registry.py`, `__init__.py`, and each adapter module's
  declaration block
- `src/pitwall/core/enums.py`, `src/pitwall/api/provider_schemas.py`, `src/pitwall/seed.py`
- the provider-specific blocks of `src/pitwall/reconciler/__init__.py`
- `tests/providers/test_declarations.py`

- `src/pitwall/providers/drift.py`, `src/pitwall/providers/_wave2_feasibility.py` (deletion),
  `src/pitwall/recommendations/engine.py`, and their tests

**Findings:** the lane B "provider-specific logic leaks into routing" finding, and spec removal item 4.

**Requirements:**
- The adapter protocol gains a declaration describing:
  - its lockout configuration layout (read by `lockout.py`);
  - whether it is OpenAI-compatible and how fallback treats it (read by `fallback.py`);
  - its config schema (read by `provider_schemas.py`);
  - its seed rows (read by `seed.py`);
  - its reconcile hooks (read by the reconciler).
- `fallback.py`, `lockout.py`, `provider_schemas.py`, `seed.py`, and the reconciler contain no
  `ProviderType.<NAME>` branches.
- `core/enums.py` keeps a single provider-type enum.
- Removal item 4: delete `providers/drift.py` and `providers/_wave2_feasibility.py` with their tests
  and re-exports, and the drift-based recommendations in `recommendations/engine.py`.

**Tests:**
- `tests/providers/test_declarations.py::test_no_provider_type_branches_in_shared_layers`: an AST
  scan of the five files.
- `tests/providers/test_declarations.py::test_every_registered_adapter_declares_all_fields`.
- `tests/providers/test_declarations.py::test_new_provider_needs_only_adapter_module`: registers a
  fake adapter and exercises routing, lockout, and seed without editing shared files.

**Validation:**
- `uv run pytest tests/providers tests/routing tests/reconciler tests/test_seed*.py -q 2>&1 | tail -5`
  → no failures.

### Task 2.5: approved removals

**Owns:** every file deleted below, their tests, and the re-export modules
`src/pitwall/routing/__init__.py`, `src/pitwall/cost/__init__.py`, `src/pitwall/finops/__init__.py`,
`src/pitwall/rate_limits/`, `src/pitwall/autopilot/`, `src/pitwall/gitops/reconcile.py`,
`src/pitwall/workload_lifecycle.py`, `src/pitwall/workers/`, and `src/pitwall/live.py`.

**Requirements (spec removal items):**
1. Delete `routing/canary.py`, `prewarm.py`, `failover.py`, `semantic_cache.py`, `carbon.py`,
   `cascade.py`, `hedging.py`, `quality_routing.py`, `arbitrage.py`, with their unit and property
   tests, and the `prewarm` type reference in `autopilot`.
3. Delete `cost/threshold_alerts.py`, `cost/slo_governor.py`, and `finops/bidding.py`, with their
   tests.
5. Delete `rate_limits/store.py` (`RateBucketStore`, `halved_capacity`).
6. Delete `gitops.apply_plan` and its tests. `build_reconcile_plan` stays.
7. Delete `workers/vllm.py`, `workers/header_policy.py`, and `tests/workers/`. `worker.py` stays.
8. Delete `workload_lifecycle.enqueue_submit_runpod_job` and `insert_passthrough_workload`.
9. Move `src/pitwall/live.py` to `tests/support/live.py` and update its importers.
- Before deleting each symbol, grep `src/`, `tests/`, `tools/`, `scripts/`, and `docs/` for it. Any
  production caller found stops that item. The lane reports the caller instead of deleting.

**Tests:**
- `tests/test_removed_modules.py::test_removed_modules_not_importable`: parametrised over each
  removed module path.

**Validation:**
- `make test-fast 2>&1 | tail -5` → no failures.
- `uv run python -c "import pitwall.routing, pitwall.cost, pitwall.finops, pitwall.providers"; echo exit=$?`
  → `exit=0`.

### Task 2.6: workflow support for all 13 harnesses

**Owns:**
- `src/pitwall/agents/scheduler.py` (`_provider_args` and its call site)
- `src/pitwall/agents/workflow.py` (the adapter capability check)
- `src/pitwall/agents/harnesses/` (all adapters)
- `tests/agents/test_scheduler*.py`, `tests/agents/test_workflow*.py`
- new `tests/agents/test_workflow_all_harnesses.py`

**Findings:** the lane D `workflow_supported` finding (decision 3).

**Produces:**
- `HarnessAdapter.workflow_args(model: str, effort: str | None, prompt_path: Path) -> list[str]`,
  the shim arguments for one workflow task.
- `HarnessAdapter.supported_efforts: frozenset[str] | None`, where `None` means the harness has no
  effort control.

**Requirements:**
- The per-harness switch in `scheduler._provider_args` moves into `workflow_args` on each adapter.
  The six existing mappings (codex, claude, grok, kimi, opencode, pi) keep their exact argument
  order, and `_provider_args` is deleted.
- agy, cline, dsh, goose, hermes, muse, and qwen implement `workflow_args` using the model and
  prompt-file forms their one-shot shims already accept (see each adapter's `build_argv` and
  `endpoint_argv`).
- Workflow validation in `workflow.py`:
  - Rejects an `effort` on a harness whose `supported_efforts` is `None`, with an error naming the
    harness.
  - Rejects a model other than the bound model on model-bound harnesses (muse, agy).
- `workflow_supported` is removed. Every registered harness is workflow-capable, and the "does not
  support workflow execution" error path is deleted.
- `runs resume` re-emits the model for harnesses with `model_positional = True`, as it does for
  opencode today.
- The docs statements naming the six-harness set (`docs/agents/workflows.md` and the README
  workflow paragraph) are reported to Task 5.2 with the new wording.

**Tests:**
- `tests/agents/test_workflow_all_harnesses.py`:
  - `::test_every_harness_builds_workflow_args` (parametrised over all 13). It asserts the argv each
    produces for a model, an effort where supported, and a prompt path, against the harness's
    documented flags.
  - `::test_existing_six_argument_order_unchanged`: pinned against the pre-change `_provider_args`
    outputs, which the lane records before refactoring.
  - `::test_effort_rejected_where_unsupported`
  - `::test_model_bound_harness_rejects_other_model`
  - `::test_workflow_runs_with_fake_harness_binaries`: runs a two-node workflow for each of the seven
    newly supported harnesses, using the fake-binary fixtures the adapter tests already use, through
    `scheduler` to completion.

**Validation:**
- `uv run pytest tests/agents -q 2>&1 | tail -5` → no failures.
- `grep -rn "workflow_supported\|_provider_args" src/pitwall/agents | wc -l` → `0`.

---

## Phase 3: gateway port

### Task 3.1: catalog sync without Node

**Owns:**
- `src/pitwall/gateway_catalog/`
- `tools/gateway/extract_upstream.mjs` (deleted) and a new `src/pitwall/gateway_catalog/extract.py`
- `tools/gateway/sync_catalog.py`
- `tests/gateway_catalog/`, `tests/fixtures/omniroute/`

**Requirements:**
- `extract.py` reproduces `extract_upstream.mjs` in Python. It parses
  `open-sse/config/freeModelCatalog.data.ts`, `src/shared/constants/config.ts`, and
  `open-sse/config/providers/registry/<id>/index.ts` into the same JSON shape.
- `sync.py` downloads the pinned `omniroute` tarball from the npm registry over HTTPS, verifies it
  against the registry's `dist.integrity` value, and extracts it in memory. `npm` and `node` are not
  invoked.
- `--from-json` stays for offline runs.
- Before deleting the `.mjs`, the lane runs it once (Node is present on the development machine)
  against the fixture files to record the expected JSON.

**Tests:**
- `tests/gateway_catalog/test_extract_parity.py::test_python_extractor_matches_recorded_mjs_output`.
- `tests/gateway_catalog/test_sync_integrity.py::test_tarball_integrity_mismatch_rejected`.
- `tests/gateway_catalog/test_sync_integrity.py::test_no_npm_or_node_invoked`: patches
  `subprocess` and asserts no calls.

**Validation:**
- `uv run pytest tests/gateway_catalog -q 2>&1 | tail -5` → no failures.
- `uv run python tools/gateway/check_catalog_drift.py; echo exit=$?` → `exit=0`.

### Task 3.2: upstream relay, translation, and compression

**Owns:**
- new `src/pitwall/gateway/relay.py`, `translation.py`, `compression.py` (the package `__init__`
  is an integrator scaffold)
- new `tests/gateway/test_relay.py`, `test_translation.py`, `test_compression.py`

**Produces:**
- `async def relay_chat(request_body: dict, target: RouteTarget, deadlines: Deadlines, client: httpx.AsyncClient) -> RelayResult`
- `async def relay_embeddings(...)` with the same shape.
- `translate_inbound(body: dict, shape: InboundShape) -> dict` and `translate_outbound(...)`.
- `compress_request(body: dict, policy: CompressionPolicy) -> dict`.
- The dataclasses `RouteTarget`, `Deadlines(first_byte_s: float, idle_s: float)`, and `RelayResult`.

**Requirements:**
- Each `packages/gateway/tests/compression.test.ts` case and the translation and streaming cases in
  `routes.test.ts`, `shim.test.ts`, and `hardening.test.ts` is translated to a pytest case. Each
  pytest case names its source test in a comment. Cases that encode a spec-listed defect are
  inverted to assert the fix.
- Streaming uses `Deadlines`:
  - A stream exceeding `first_byte_s` before the first chunk, or `idle_s` between chunks, sends one
    terminal SSE error event and then aborts the connection.
  - A truncated stream never ends with a normal close.
- Review Focus 4: client disconnect cancels the upstream request, and no task or connection remains
  open afterwards.
- Non-SSE upstream bodies are capped at the configured size.
- Upstream request shaping reuses `pitwall.routing.openai` helpers wherever they match. The lane
  lists each reused helper in its report.

**Tests:**
- The translated cases, plus:
- `tests/gateway/test_relay.py`:
  - `::test_idle_deadline_sends_error_event_and_aborts`
  - `::test_first_byte_deadline`
  - `::test_long_healthy_stream_not_cut`: a stream longer than 30 s total with regular chunks.
  - `::test_client_disconnect_cancels_upstream`
  - `::test_non_sse_body_capped`

**Validation:**
- `uv run pytest tests/gateway -q 2>&1 | tail -5` → no failures.

### Task 3.3: gateway server and surfaces

**Owns:**
- new `src/pitwall/gateway/app.py`, `auth.py`, `config.py`, `routes_table.py`
- new `src/pitwall/cli/gateway.py` subcommand module (`pitwall gateway serve`) and its one row in
  the command-group table created by Task 1.3
- new `tests/gateway/test_app*.py`, `test_auth.py`, `test_config.py`, `test_models.py`, `test_surfaces.py`

**Consumes:** Task 3.2's relay, translation, and compression interfaces.

**Requirements:**
- An ASGI app with the five routes (chat completions, models, embeddings, health, telemetry), served
  by uvicorn from `pitwall gateway serve`, bound to loopback only. It refuses non-loopback binds.
- Bearer authentication uses `hmac.compare_digest` in one dependency applied to every non-health
  route. `rateLimit` (a `TokenBucket` per token) applies to every authenticated route.
- The route table comes from `PITWALL_GATEWAY_ROUTES`. `/v1/models` in route-table mode returns the
  route names without calling upstream, as the TypeScript version does.
- Error bodies go through `pitwall.security.redaction`.
- The 413 message states the configured cap.
- Each case in `config.test.ts`, `models.test.ts`, `models-lifecycle.test.ts`, `surfaces.test.ts`,
  `upstream-auth.test.ts`, `executor-dispatch.test.ts`, and the rest of `shim.test.ts` and
  `hardening.test.ts` is translated. Test-only executor registration is done through app-factory
  injection, not module globals.

**Tests:**
- The translated cases, plus:
- `tests/gateway/test_auth.py::test_constant_time_compare_used`.
- `tests/gateway/test_auth.py::test_rate_limit_applies_to_models_and_telemetry`.
- `tests/gateway/test_app.py`:
  - `::test_refuses_non_loopback_bind`
  - `::test_413_message_reports_configured_cap`

**Validation:**
- `uv run pytest tests/gateway -q 2>&1 | tail -5` → no failures, with at least 132 translated cases
  counted by a `@pytest.mark.parity` marker (`uv run pytest tests/gateway -m parity -q | tail -1`).

### Task 3.4: cutover to the Python gateway

**Owns:**
- `src/pitwall/personal/gateway.py`, `src/pitwall/providers/gateway.py` (launch references only)
- `packages/gateway/` (deletion)
- `.github/workflows/gateway-ci.yml`, `.github/workflows/gateway-release.yml`
- `tests/personal/test_gateway*.py`
- the gateway doctor section registration

**Requirements:**
- Personal mode starts `pitwall gateway serve` instead of `node dist/src/shim.js`, with the same
  port, environment contract, and supervision.
- Review Focus 3: if port 20130 is already bound, personal mode reports the port and the process
  holding it (via `/proc` on Linux, or a generic message elsewhere) and exits non-zero.
- `packages/gateway/`, `gateway-ci.yml`, and `gateway-release.yml` are deleted (removal items 15 and
  17).
- The doctor section `gateway` is registered through `register_doctor_section`.

**Tests:**
- `tests/personal/test_gateway_launch.py`:
  - `::test_launches_python_gateway`
  - `::test_port_in_use_reported`
- `tests/test_doctor_sections.py::test_gateway_section_present`.

**Validation:**
- `uv run pytest tests/personal tests/gateway tests/test_doctor_sections.py -q 2>&1 | tail -5` → no
  failures.
- `test ! -e packages/gateway && echo gone` → `gone`.

---

## Phase 4: workbench port

All Phase 4 tasks translate the listed `packages/pi-workbench/tests/*.test.ts` cases into pytest
cases, marked `@pytest.mark.parity` and naming their source test. Tests that need a real `pi` binary
(`native-runtime`, `native-writer-runtime`, `native-crash-runtime`, `hosted-compiled-rpc`,
`runtime-lifecycle`) carry the `live` marker.

### Task 4.1: Pi extensions as package data

**Owns:**
- new `src/pitwall/workbench/pi_extensions/` (`*.ts` sources and committed `*.js`)
- new `src/pitwall/workbench/pi_extensions/COMPILER` (records the TypeScript version)
- new `tests/workbench/pi_extensions/`
- a new `Makefile` target `pi-extensions-check`

**Requirements:**
- `provider-extension`, `native-extension`, `extension`, `restricted-extension`, and the comparison
  lifecycle observer are moved as TypeScript sources and compiled once to plain ES modules. Both are
  committed.
- The `.js` files are included as package data.
- Their behaviour tests (`provider-extension.test.ts`, `native-extension.test.ts`,
  `comparison-lifecycle-observer.test.ts`) are converted to Node's built-in test runner
  (`node --test`) as `.test.mjs` files with no npm dependencies. A pytest wrapper runs them and skips
  with a reason when `node` is absent.
- `make pi-extensions-check` compiles the sources with the recorded TypeScript version through a
  pinned `npx --yes typescript@<version> tsc` and fails if the output differs from the committed
  `.js`.

**Tests:**
- `tests/workbench/pi_extensions/test_node_suites.py::test_extension_suites_pass`.
- `tests/workbench/pi_extensions/test_packaged.py::test_js_files_in_wheel`: builds the wheel and
  lists its contents.

**Validation:**
- `uv run pytest tests/workbench/pi_extensions -q 2>&1 | tail -5` → no failures.
- `make pi-extensions-check; echo exit=$?` → `exit=0`.

### Task 4.2: restricted mode and seccomp

**Owns:**
- new `src/pitwall/workbench/restricted.py`, `src/pitwall/workbench/seccomp.py`
- new `tests/workbench/test_restricted*.py`, `tests/workbench/test_seccomp.py`

**Produces:**
- `build_restricted_command(argv: list[str], workdir: Path, policy: RestrictedPolicy) -> list[str]`
- `seccomp_program(arch: str) -> bytes`
- `restricted_tool_environment(env: Mapping[str, str]) -> dict[str, str]`

**Requirements:**
- Translate `restricted.test.ts`, `restricted-prerequisites.test.ts`, and
  `native-restricted-probe.test.ts`.
- The seccomp program implements `restricted.ts`'s policy:
  - kill on an unexpected architecture;
  - reject x32;
  - deny the x86_64 socket syscalls 41 to 55, `accept4`, `recvmmsg`, `sendmmsg`, and the io_uring
    syscalls;
  - allow the rest.
- Restricted mode refuses to run without bubblewrap and never falls back to unrestricted mode.
- Mounts hide home, root, run, and tmp behind tmpfs, and refuse `/`, `/home`, and `/root` as
  workdirs.

**Tests:**
- The translated cases, plus:
- `tests/workbench/test_seccomp.py`:
  - `::test_program_decodes_to_policy`: decodes each 8-byte `sock_filter` and asserts the
    instruction sequence.
  - `::test_x32_rejected`
  - `::test_unknown_arch_killed`

**Validation:**
- `uv run pytest tests/workbench -q -k "restricted or seccomp" 2>&1 | tail -5` → no failures.

### Task 4.3: profiles, launcher, settings, admission, accounting

**Owns:**
- new `src/pitwall/workbench/profile.py`, `native_profile.py`, `hosted_profiles.py`,
  `runtime_settings.py`, `launcher.py`, `admission.py`, `timeouts.py`, `accounting.py`,
  `account_budget.py`
- the matching `tests/workbench/test_*.py`

**Consumes:**
- `pi_extensions` paths (Task 4.1)
- `build_restricted_command` and `restricted_tool_environment` (Task 4.2)

**Produces:**
- `launch_pi(options: PiLaunchOptions) -> subprocess.Popen`
- `compile_profile(path: Path) -> CompiledProfile`
- `PiLaunchOptions`, a dataclass mirroring the TypeScript interface in `launcher.ts`

**Requirements:**
- Translate `profile`, `native-profile`, `hosted-profiles`, `runtime-settings`,
  `launcher-dependencies`, `launcher-environment`, `admission`, `shared-admission`,
  `runtime-admission`, `timeouts`, `accounting`, `account-budget`, and `usage` tests.
- The launcher passes `--no-extensions --no-skills --no-prompt-templates --no-themes` and each
  extension path from package data. Its argument order matches `launcher.ts:50`.
- Profiles are written with mode 0600 and exclusive creation, and are rechecked at load.

**Validation:**
- `uv run pytest tests/workbench -q -m "not live" 2>&1 | tail -5` → no failures.

### Task 4.4: handoff workspace and task records

**Owns:**
- new `src/pitwall/workbench/workspace.py`, `task_record.py`
- `tests/workbench/test_workspace.py`, `test_task_record.py`, `test_artifact_containment.py`

**Consumes:** `restricted_tool_environment` (Task 4.2).

**Requirements:**
- Translate `workspace.test.ts`, `task-record.test.ts`, and `artifact-containment.test.ts`.
- Approved checks run with `restricted_tool_environment(os.environ)`, not the full parent
  environment.
- The timeout-kill test waits for the pid file before asserting (the flaky test in the evaluation).
  It runs 20 consecutive times in the validation.

**Tests:**
- The translated cases, plus:
- `tests/workbench/test_workspace.py::test_approved_checks_get_scrubbed_environment`.

**Validation:**
- `for i in $(seq 20); do uv run pytest tests/workbench/test_workspace.py -q -k timeout 2>&1 | tail -1; done | grep -c " passed"`
  → `20`.
- `uv run pytest tests/workbench -q -m "not live" 2>&1 | tail -5` → no failures.

### Task 4.5: comparison and acceptance runners

**Owns:**
- new `src/pitwall/workbench/comparison/` (acceptance, child evidence, metrics, reasoning fixture,
  recovery, lifecycle)
- new `src/pitwall/workbench/hosted/` (evaluation, native evaluation, acceptance runners)
- `tests/workbench/test_comparison*.py`, `test_hosted*.py`, `test_baseline_runner.py`,
  `test_dynamic_contract_acceptance.py`, `test_validation_scenarios.py`
- `tests/workbench/fixtures/` (including `colors.png`)

**Requirements:**
- Port `src/comparison-*.ts`, `src/hosted-*.ts`, and `scripts/comparison-runner.ts`,
  `comparison-reevaluate.ts`, `comparison-child-reevaluate.ts`, `hosted-acceptance.ts`,
  `hosted-native-acceptance.ts`, `baseline-rpc.ts`, and `create-fixture.ts` into these modules. The
  runners become `pitwall workbench compare …` and `pitwall workbench acceptance …` subcommand
  functions, which Task 4.6 wires.
- `scripts/clean-build.mjs` is not ported: it existed for the npm build.
- `scripts/two-tui-native.mts` is not ported (removal item 12).
- Translate the tests listed in **Owns**.
- The 16 `any` uses become typed dataclasses or `TypedDict`s, checked by `mypy --strict`. The bare
  `catch {}` blocks become typed `except` clauses with a reason comment.

**Validation:**
- `uv run pytest tests/workbench -q -m "not live" 2>&1 | tail -5` → no failures.
- `uv run mypy --strict src/pitwall/workbench 2>&1 | tail -3` → `Success`.

### Task 4.6: workbench CLI, doctor, and cutover

**Owns:**
- new `src/pitwall/workbench/cli.py`, `src/pitwall/workbench/doctor.py`
- new `src/pitwall/cli/workbench.py` and its row in the command-group table
- `packages/pi-workbench/` (deletion)
- the `pi-workbench` job in `.github/workflows/ci.yml`
- `tests/workbench/test_cli.py`, `test_surfaces.py`

**Requirements:**
- `pitwall workbench …` exposes every command the TypeScript CLI had (translate `cli.test.ts` and
  `surfaces.test.ts`), plus the compare and acceptance commands from Task 4.5.
- Review Focus 5: every command that launches Pi first checks that `pi` exists and reports the pinned
  versions (`@earendil-works/pi-coding-agent` 0.84.4, `@tintinweb/pi-subagents` 0.19.0). On a
  mismatch it exits non-zero with the install command, before creating a worktree or writing state.
- `pitwall agents setup pi` installs the pinned versions through the harness installer recipes.
- The `workbench` doctor section is registered.
- `packages/pi-workbench/` is deleted (removal item 17). The CI `pi-workbench` job is replaced by the
  workbench tests in the main test job plus `make pi-extensions-check`.

**Tests:**
- The translated cases, plus:
- `tests/workbench/test_cli.py`:
  - `::test_missing_pi_fails_before_worktree`
  - `::test_wrong_pi_version_fails_before_state_written`
- `tests/test_doctor_sections.py::test_workbench_section_present`.

**Validation:**
- `uv run pytest tests/workbench tests/test_doctor_sections.py -q -m "not live" 2>&1 | tail -5` → no
  failures.
- `test ! -e packages/pi-workbench && echo gone` → `gone`.
- `git ls-files | grep -c "package.json"` → `0`.

---

## Phase 5: one version, CI, release, and docs

### Task 5.1: one CI workflow and one release workflow

**Owns:**
- `.github/workflows/` (all files)
- `scripts/release/`, `tools/agents/release/` (merged and deleted)
- `tests/test_release_scripts.py`, `tests/release/test_workflows*.py`

**Findings:** lane F release-readiness duplication, coverage re-runs, and the ubuntu-26.04 runner
note.

**Requirements:**
- `ci.yml` is the only CI workflow. Its jobs are lint, format, typecheck, docs, security, hermetic
  tests, integration, journeys, gateway catalog drift, and Pi extensions check, all in `required`.
- The model-facts schedule becomes a scheduled job in `ci.yml`, or a single `model-facts.yml`
  workflow of the one project with root paths.
- The hermetic suite runs once per CI run, with coverage collected there. `coverage-combined` reuses
  the `test` and `integration` jobs' coverage data files instead of re-running the suites.
- `release-readiness.yml` checks that the tagged commit's `ci.yml` run succeeded, via the GitHub API
  with the workflow token, and then runs only artifact build, inspection, smoke, and journeys.
- `release.yml` builds and publishes one wheel and one sdist, the container images, and the release
  notes, for `v*` tags only. `agent-routing-release.yml`, `gateway-release.yml`, and every
  `agent-routing/v*` and `gateway/v*` trigger are removed.
- `tools/agents/release/validate_candidate.py` and `inspect_artifacts.py` are merged into
  `scripts/release/` and deleted.
- The Pi extensions job pins its Node version and runner image with a comment explaining why.

**Tests:**
- `tests/release/test_workflows.py`:
  - `::test_single_ci_workflow`
  - `::test_required_lists_every_non_scheduled_job`
  - `::test_release_triggers_only_v_tags`
  - `::test_readiness_does_not_rerun_ci_jobs`

**Validation:**
- `make ci-tools 2>&1 | tail -5` → pass.
- `uv run pytest tests/release/test_workflows.py tests/test_release_scripts.py -q 2>&1 | tail -5` →
  no failures.

### Task 5.2: docs for one product

**Owns:**
- `README.md`, `CONTRIBUTING.md`, `RELEASING.md`, `SECURITY.md`, `SUPPORT.md`
- `docs/` except `docs/superpowers/` and `docs/evidence/`
- `qa/`

**Findings:** the workflow-set wording reported by Task 2.6, and every doc-drift finding (stale `file:line` references in `docs/sdlc/`, the
`docs/sdlc/05-cost-budget.md` `threshold_alerts` description, `docs/sdlc/04-routing.md` entry point,
`docs/sdlc/16-core-config.md` worker env claim, `docs/sdlc/03-mcp-server.md` bare path, gateway
README `/v1/models` text, `STRIP-NOTES.md`, Agent Routing GNU `timeout` docs).

**Requirements:**
- `README.md` describes one product: one install (the release wheel), `pitwall agents install`
  for hosts, and one command reference.
- `docs/sdlc/` references code as `module:symbol`. No `src/…py:N` references remain.
- `docs/agents/` holds the Agent Routing docs rewritten for `pitwall agents`, profiles, harnesses,
  and the new environment variables. `docs/prompting/` holds the prompting references.
- `docs/sdlc/25-pi-workbench.md` and the gateway docs describe the Python implementations.
- The workbench docs keep the note from `docs/source-lock.md` about which Pi version actually runs.
- `qa/` missions and handbook use the current command names. Check every command against
  `uv run pitwall --help` and each group's `--help`.
- `docs/operator/` includes the migration guide for `pitwall agents migrate`.

**Tests:**
- `tests/docs/test_no_line_references.py::test_sdlc_has_no_file_line_references`.
- `tests/docs/test_qa_commands_exist.py::test_every_qa_command_is_a_real_command`: parses fenced
  `pitwall …` commands in `qa/` and runs `--help` for each group and subcommand.

**Validation:**
- `make docs-check 2>&1 | tail -3` → `markdown links passed`.
- `uv run pytest tests/docs -q 2>&1 | tail -3` → no failures.

### Task 5.3: version, changelog, and package metadata

**Owns:**
- `pyproject.toml` (`[project]` metadata)
- `CHANGELOG.md`
- `docs/agents/CHANGELOG.md` (folded and deleted)
- `plugins/*/` manifest version fields
- `src/pitwall/__init__.py` version
- `tests/test_version.py`

**Requirements:**
- The version is `0.2.0a1` in `pyproject.toml`, the package `__version__`, and every plugin manifest.
- `CHANGELOG.md` gains the `0.2.0a1` entry describing the unification: commands, environment
  variables, paths, and removals. The Agent Routing and gateway changelogs are appended under
  "History before unification", with their last versions recorded (`agent-routing/v0.12.0`,
  `gateway/v0.2.0`).
- Package metadata (description, keywords, URLs, license files) describes the one product. The
  licence files from Agent Routing (Apache-2.0 for pi-workbench, MIT for the gateway's vendored code,
  which is now removed) are reconciled into `LICENSE` and `NOTICE`, with a line in the report
  explaining each licence decision.

**Tests:**
- `tests/test_version.py::test_versions_agree`: pyproject, `__version__`, and plugin manifests.

**Validation:**
- `uv run pytest tests/test_version.py -q 2>&1 | tail -3` → no failures.
- `uv build 2>&1 | tail -3` → one wheel and one sdist named `pitwall-0.2.0a1`.

### Task 5.4: release acceptance for the unified surfaces

**Owns:**
- `tools/release_acceptance/`
- `release_acceptance/` (lane-owned for this task only)
- `tests/release_acceptance/`
- `tests/release/test_*journey*.py`

**Requirements:**
- `node_inventory.py` and every reference to the npm packages are removed. The TypeScript surfaces
  are now Python surfaces, discovered by the CLI and MCP inventories.
- The CLI inventory discovers the `agents`, `usage`, `gateway`, `workbench`, and `mcp serve` groups.
  The MCP inventory discovers both servers.
- The journey tests (`test_cli_all_commands_journey.py`, `test_mcp_all_tools_journey.py`,
  `test_rest_all_operations_journey.py`, `test_matrix_complete.py`) cover the new commands and
  servers.
- Declarations and bindings are regenerated and reviewed. Every removed surface is removed from the
  declarations, not left unbound.

**Validation:**
- `uv run pytest tests/release_acceptance -q 2>&1 | tail -5` → no failures.
- The integrator runs the journey harness after merge. Its result is recorded in I-final.

---

## Task I-final: land the branch (integrator)

- [ ] Update the local `AGENTS.md`: replace the separate-projects and stdlib-only rules with the
      spec's rules (one project, the startup import guard, the agents-to-broker import direction), and
      remove the `packages/agent-routing` setup instructions.
- [ ] Confirm every ledger row has a closing state:
      `grep -c "| open |" docs/superpowers/plans/2026-09-28-single-project-ledger.md` → `0`.
- [ ] Run serially and record each command with its tail output in the ledger's completion section:
      - `make test-fast`
      - `make up && make test-int`
      - `scripts/release/run-user-journeys.sh`
      - `make docs-check`
      - `make ci-tools`
      - `uv run python tools/security/check_secrets.py`
      - `uv build`
- [ ] Run the migration against a copy of the maintainer's real Agent Routing state in a temporary
      `HOME`, and record the output.
- [ ] Open one pull request from `feat/single-project` and cite the green CI run.

## Continuation: CI runtime (2026-09-30)

Findings, measured on green run 36693015498 of PR #54 (`.github/workflows/ci.yml`): the `journeys`
job takes 44 min, `test` 41 min, `agents-macos` 25 min, and every other job 3 min or less. Causes:

- CR-1: J27 (`scripts/release/run-user-journeys.sh` `j27()`) re-runs the full ~8,500-test unit lane
  (`uv run pytest -q -m "not integration and not slow"`), which the `test` job already runs and gates.
- CR-2: no parallel test execution anywhere: `pytest-xdist` is not a dependency; `make test-fast`,
  `make test-cov`, the README testing commands, J27, and the `test` job all run one pytest process.
- CR-3: `agents-macos` runs all of `tests/agents` (110 files, 1,300+ tests) on macOS runners; only
  the 55 files that spawn processes, send signals, or touch permissions, symlinks, or shells
  exercise macOS-specific behaviour.

Landing (maintainer instruction, admin bypass approved): the head commit that lands these carries
`[skip ci]`, so no CI run is triggered. Local runs of every changed CI command are the evidence.

### Task C.1: dependencies and markers (integrator)

**Owns:** `pyproject.toml` (`[project.optional-dependencies] dev`, `[tool.pytest.ini_options] markers`), `uv.lock`.

**Requirements:**
- Add `pytest-xdist` to the `dev` extra; `uv lock` updates only it and its dependencies.
- Register the `macos` marker: "platform-sensitive: runs in the agents-macos job".

**Validation:**
- `uv lock --check` → exit 0; `uv run python -c "import xdist"` → exit 0.
- `uv run pytest --markers | grep -c "@pytest.mark.macos"` → `1`.

### Task C.2: the fast suite runs in parallel (lane XD)

**Owns:** every file under `tests/` except `tests/agents/conftest.py`, `tests/release/`, and
`scripts/release/`; `Makefile` (`test-fast`, `test-cov`); `README.md` testing commands (lines ~274-280).

**Consumes:** C.1 (`pytest-xdist` installed).

**Requirements:**
- `make test-fast` and `make test-cov` run `-n auto`; the README unit-lane command matches.
- The fast suite passes under `-n auto` with pytest-randomly on. Every failure found under xdist is
  root-caused and fixed in the test (shared fixed ports that are actually bound, shared file paths,
  module-global state that assumed one process, order assumptions). No retries, sleeps, skips, or
  `-p no:xdist` exemptions; no weakened assertions.
- Coverage under xdist: `--cov` with `-n auto` still reaches the hermetic floor (`--cov-fail-under=74`).

**Validation:**
- `uv run pytest -n auto -q -p no:cacheprovider -m "not integration and not slow and not live" -p randomly --randomly-seed=N 2>&1 | tail -1`
  for N in 1, 2, 3 → no failures each.
- `uv run pytest -n auto -m "not integration and not slow and not live" --cov=src/pitwall --cov-report= --cov-fail-under=74 -q 2>&1 | tail -2` → passes, coverage ≥ 74%.
- Whole-tree `ruff check .` and `ruff format --check .` clean; `python_policy.py` exit 0 on changed files.

### Task C.3: J27 in CI does not repeat the unit lane (lane J27)

**Owns:** `scripts/release/run-user-journeys.sh` (`j27()` only), `tests/release/` tests of the harness script.

**Requirements:**
- `j27()` runs the README unit lane unless `PITWALL_JOURNEYS_UNIT_LANE=covered`, in which case it
  records `README unit lane: covered by the test job` and still runs the README security lane.
  Unset (local, release, I-final runs) keeps today's behaviour.
- The unit-lane command J27 runs is the README's (with `-n auto` after C.2).

**Validation:**
- `bash -n scripts/release/run-user-journeys.sh` → exit 0.
- `JOURNEY_FILTER=J27 PITWALL_JOURNEYS_UNIT_LANE=covered scripts/release/run-user-journeys.sh 2>&1 | grep -E "J27|covered"`
  → J27 PASS with the "covered by the test job" line, in seconds.
- The harness-script tests in `tests/release/` pass.

### Task C.4: macOS runs the platform-sensitive tests (lane MACSUB)

**Owns:** `tests/agents/conftest.py`.

**Requirements:**
- `pytest_collection_modifyitems` in `tests/agents/conftest.py` adds the `macos` marker to every
  test in an explicit module list: the `tests/agents/test_*.py` files that spawn subprocesses or
  shells, send signals, or touch file permissions, symlinks, or process groups (currently 55; the
  list is written out, with the criterion in a comment).

**Validation:**
- `uv run pytest tests/agents -m macos --collect-only -q | tail -1` → the subset count, and
  `uv run pytest tests/agents -m macos -n auto -q -p no:cacheprovider | tail -1` → no failures on Linux.
- `uv run pytest tests/agents -m "not macos" --collect-only -q` lists no module that imports
  `subprocess`, `signal`, or calls `os.kill`/`os.killpg`/`chmod`/`symlink`.

### Task C.5: CI wiring and landing without a CI run (integrator)

**Owns:** `.github/workflows/ci.yml` (`test`, `journeys`, `agents-macos` jobs), the ledger rows CR-1..CR-3.

**Consumes:** C.1-C.4.

**Requirements:**
- `test` job: the fast-suite step runs `-n auto` (coverage and `--cov-fail-under=74` unchanged).
- `journeys` job: sets `PITWALL_JOURNEYS_UNIT_LANE=covered`.
- `agents-macos` job: runs `uv run --frozen pytest tests/agents -m macos -n auto -q -p no:randomly -p no:cov`.
- `tools/ci/check_workflows.py` and actionlint pass.
- Land: every local gate in `/tmp/lanes/prepush.sh` passes; the head commit message contains
  `[skip ci]`; push; confirm no workflow run starts for the new head
  (`gh run list --branch feat/single-project --limit 3`).

**Validation:**
- `uv run python tools/ci/check_workflows.py` → `workflow policy passed`.
- The `test` job's exact command, run locally, passes; the `journeys` job's exact command
  (`PITWALL_JOURNEY_HARNESS=1 PITWALL_JOURNEYS_UNIT_LANE=covered scripts/release/run-user-journeys.sh`)
  passes 42/42; the `agents-macos` command passes on Linux.
- `gh run list --repo Buckeyes22/pitwall --branch feat/single-project --limit 3` shows no run for the pushed head.

## Decisions (2026-09-28)

- Delete `src/pitwall/cost_exporter/` (R-extra-1)? -> Delete it (one fewer alias module; the console
  script and Dockerfile point at `pitwall.cost.exporter`). Tasks 0.2 and 0.4, plus the integrator's
  wave 1 merge.
- Audit CHECK constraints need a migration per new action? -> Keep as intended (the constraint is the
  review control for audit actions).
- Workflow support for only 6 of 13 harnesses? -> Extend to all 13 (every registered harness should be
  usable in a workflow). New Task 2.6.
- Prompts passed in argv for kimi, grok, dsh, pi? -> Use stdin or file where the CLI supports it (pi
  moves to `@file`; kimi, grok, and dsh document only argv, so they keep it with a size limit).
  Task 0.8.
- Steer-gate hook fails open? -> Fail closed (blocking steers are always enforced; the block message
  gives recovery steps that do not need a tool call). Task 1.5.
- Harness children inherit the parent environment? -> Keep as intended (each harness reads its own
  credentials from the environment).
- How to commit the evaluation, spec, and plan? -> One docs commit now on `feat/single-project`, so
  every lane worktree starts from a branch that contains them.

- Delete orphaned planner-only types, the constraint helpers only the removed planner used, and the `rate_buckets` table and `RateBucket` model (surfaced by Task 2.5, 2026-09-29)? -> Yes to all three (R-extra-2: `routing/types.py` RoutePlan, RouteAttempt, RouteCandidate, CapacityDecision, CapacityProbeKey, ProviderEliminated, parse_stream_from_bytes; R-extra-3: `routing/constraints.py` HardConstraintFilterResult, apply_hard_constraints, check_hard_constraints, evaluate_hard_constraint, hard_constraint_filter, hard_constraint_reasons, stage1_hard_constraint_filter, DEFAULT_LB_MAX_PAYLOAD_MB; R-extra-4: `rate_buckets` table via a new migration and the `RateBucket` model). Task 2.5.
- Delete the orphaned rate-bucket chain and the orphaned elimination chain (surfaced by Task 2.5, 2026-09-29)? -> Yes to both (R-extra-5: `RateBucket` model, `TokenBucketRateLimiter`, `RateBucketStoreProtocol`, `tests/rate_limits/test_bucket_algorithm.py`; R-extra-6: `routing/types.py` RouteElimination and ProviderEliminated, `routing/constraints.py` filter_hard_constraints and HardConstraintFilterResult). Task 2.5.
- Delete the `pitwall.rate_limits` helpers with no production caller (surfaced after Task 2.5, 2026-09-29)? -> Yes (R-extra-7: `RateLimitConfig`, `capacity_after_429`, `dynamic_capacity`, `effective_capacity`, `halved_capacity`, `RateLimitExceeded`, the `MonotonicClock`/`WallClock` aliases, and their test cases; `TokenBucket` and `retry_after` stay). Follow-up answer: also delete the then-unused `LOCAL_WAIT_LIMIT_S`, `CAPACITY_REFRESH_INTERVAL_S`, `CAPACITY_REBUILD_WINDOW_S` -> Yes. Lane R7.
- Keep the journey harness in pull-request CI? -> No (2026-09-30, maintainer): it is a local tool for confidence before releasing or open-sourcing, never meant for CI. The `journeys` job and its required-gate entry are removed from `ci.yml`; `scripts/release/run-user-journeys.sh`, every journey test, and release-readiness's `journeys` job stay. The macOS job stays: it carries `origin/main`'s required `macos-smoke` lane backing the support matrix's "Agent Routing on macOS" row.
