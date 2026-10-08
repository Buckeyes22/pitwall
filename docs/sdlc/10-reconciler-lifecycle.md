# Reconciler & Workload Lifecycle

## 1. Purpose & Scope

The Reconciler & Workload Lifecycle subsystem is the operational heart of Pitwall's background processing. It:

- **Tracks workload state** from queued through terminal outcomes (completed, failed, cancelled, timed_out) via RunPod polling and webhook ingestion.
- **Reconciles cost** by computing actual spend from RunPod runtime and writing it back to workload rows and the `cost_daily` summary table.
- **Manages lease lifecycle** by emitting advance-warning events and tearing down expired leases.
- **Monitors provider health** via periodic LB probe runs whose results feed the cooldown state machine.
- **Drives daily cost rollup**, idempotency GC, hibernate-sweep alerting, backup drills, and workload archiving — all scheduled via Arq cron.
- **Fails closed for the removed legacy GPU worker CLI** (`pitwall.worker`); the only supported background worker is the reconciler Arq process.

The subsystem sits between the API layer (which creates workloads) and the RunPod API (which executes them). It is the authoritative source of truth for workload terminal states and actual cost.

---

## 2. Components

### `src/pitwall/reconciler/__init__.py` — Main Reconciler Worker

The Arq worker entrypoint. Handles RunPod status polling, webhook processing, lease expiry, health probing, free-pool quota windows, and all scheduled maintenance jobs.

#### Key functions

`validate_redis_dsn(dsn: str) -> bool`
: Returns True only if `dsn` is a parseable `redis://` URL with a non-empty netloc.

`check_redis_config() -> int`
: CLI check entrypoint. Prints `REDIS_URL is valid` on success, descriptive error on failure. Exits 0 or 1.

`map_runpod_status(status: str, *, cost_per_hr: Decimal|None = None, worker_time_ms: int|None = None, completed_at: datetime|None = None) -> RunPodJobStatus`
: Maps a RunPod queue status string to a `RunPodJobStatus`. Terminal statuses `COMPLETED`, `FAILED`, `CANCELLED`, `TIMED_OUT`, `TIMEOUT`, `TIME_OUT` map to corresponding `WorkloadState` values via `_RUNPOD_TERMINAL_MAP`. Active statuses `IN_QUEUE`, `IN_PROGRESS` and unknown strings return `terminal=False`. Actual cost is computed from `cost_per_hr * worker_time_ms / 3_600_000` when both args are provided.

`_compute_actual_cost(cost_per_hr: Decimal|None, worker_time_ms: int|None) -> Decimal|None`
: Internal. Returns `cost_per_ms * worker_time_ms` quantized to 6 decimal places, or `None` if either input is missing/zero.

`fetch_active_workloads(pool: asyncpg.Pool) -> list[dict[str, Any]]`
: Returns workloads in `queued` or `running` state that have a non-null `runpod_job_id`. Columns returned: `id`, `runpod_job_id`.

`apply_terminal_state(pool: asyncpg.Pool, *, workload_id: str, state: WorkloadState, actual_cost: Decimal|None, completed_at: datetime) -> bool`
: Updates `pitwall.workloads` with resolved state, cost, and completion time. Guards against re-applying to already-terminal rows via the `WHERE state NOT IN ('completed', 'failed', 'cancelled', 'timed_out')` clause. Returns `True` if a row was updated.

`fetch_workload_by_id(pool: asyncpg.Pool, workload_id: str) -> dict[str, Any]|None`
: Fetches a single workload row by its `id`. Returns `None` if not found.

`fetch_workload_by_runpod_job_id(pool: asyncpg.Pool, runpod_job_id: str) -> dict[str, Any]|None`
: Fetches a workload row by its `runpod_job_id`. Used by the webhook processing path.

`build_workload_completed_event(workload: dict[str, Any]) -> dict[str, Any]`
: Serializes a workload row into a Redis pub/sub event payload. Keys: `event="workload.completed"`, `workload_id`, `capability_id`, `provider_id`, `state`, `completed_at`, `execution_ms`, `output_bytes`, `cost_actual_usd`, optional `error`, `result`, `fallback_chain`. Strips `Decimal` from `cost_actual_usd` via `str()`.

`publish_workload_completed(redis: Any, event: dict[str, Any]) -> int`
: JSON-serializes `event` and publishes to channel `pitwall:workload:completed`. Returns subscriber count or 0 on error. No-op if `redis is None`.

`apply_terminal_status_and_publish(pool: asyncpg.Pool, redis: Any, runpod_job_id: str, status: str, completed_at: datetime|None = None) -> bool`
: Core webhook-to-state function. Fetches workload by `runpod_job_id`, maps the status, applies terminal state, fetches the updated row, builds the event, and publishes it. Returns `True` if the workload was updated. Both `_poll_and_reconcile` and `_process_webhook_terminal_status` call this.

`aggregate_daily_cost(pool: asyncpg.Pool) -> None`
: Runs `_AGGREGATE_DAILY_SQL` which upserts into `pitwall.cost_daily` from completed workload rows, joined through `capabilities` and `providers`. Groups by UTC day, capability class, and provider type.

`fetch_providers_for_health_probe(pool: asyncpg.Pool) -> list[dict[str, Any]]`
: Returns enabled `serverless_lb` providers with a `runpod_endpoint_id`. Columns: `id`, `name`, `provider_type`, `runpod_endpoint_id`, `health_status`, `consecutive_failures`, `cooldown_trips`, `cooldown_until`.

`fetch_lb_providers_for_hibernate_sweep(pool: asyncpg.Pool) -> list[dict[str, Any]]`
: Same filter as above but includes `config` column for the hibernate sweep logic.

`update_provider_health(pool: asyncpg.Pool, *, provider_id: str, health_status: str, consecutive_failures: int, cooldown_trips: int, cooldown_until: datetime|None, self_hosted_state: Mapping|None = None, expected_updated_at: datetime|None = None) -> bool`
: Persists probe results back to the `pitwall.providers` row. With `expected_updated_at` the write is a compare-and-set: it applies only if the row is unchanged since it was read. Returns whether a row was written.

`persist_provider_health(pool, provider, compute) -> bool`
: Writes one probe result computed by `compute(row)`. On a compare-and-set miss (another probe tick or the OpenAI proxy wrote first) it re-reads the row and recomputes, so overlapping writers never lose a failure count.

`reap_orphaned_workloads(pool: asyncpg.Pool) -> int` — via `_reap_orphaned_workloads`, **cron every 5 min**
: Closes workloads still `queued`/`running` with no provider job id and no linked lease once they are an hour past their capability's `execution_timeout_ms` (default 60 s). They become `timed_out`, charged their admitted ceiling with provenance `reaped_unfinished`, so the daily rollup (terminal states only) agrees with the budget gate.
: First, `resolve_ambiguous_raw_pod_creates` takes the `runpod_direct` workloads with no lease whose newest control-plane journal row is a `pod.create` in one of two states:
  - `started`: an unknown outcome with no pod id.
  - `completed` with a pod id: the process stopped before its lease insert. That pod is leased by its recorded id, unless its live record carries another attempt's marker, in which case it is held.

  It acts only when the key's advisory lock is free. A running create holds that lock until it returns (`pod.create` has no call ceiling), and a cancelled one holds it until its create thread ends. It also waits until the newest journal row is older than `UNKNOWN_OUTCOME_GRACE_S` (the default 300 s ceiling plus 60 s, timed from the row's `created_at`). That wait is a settling margin, checked on the first read and again on the re-read under the lock, so a create that just completed is left to the tool's own lease insert, and RunPod's list has time to show a new pod. A same-key replay can still insert its lease late. If the reaper's lease lands first, the tool returns it instead of rolling back. For a `started` create it then lists pods (`get_pods`) and matches the `PITWALL_CREATE_ATTEMPT` marker the create put in the pod's env, which the journal row recorded. A marked pod counts in any state, because a `TERMINATED` one still existed and billed. The marker is per key, not per attempt, so a `TERMINATED` pod that an earlier `completed` create with the same key recorded (a rollback terminated it and freed the key) belongs to that attempt and is not matched:
  - **One pod whose env carries the matching marker, live or `TERMINATED`:** its lease is recorded through `raw_pod_lease`, with the TTL counted from the attempt. The lease sweep then settles the workload at accrued cost, never $0.
  - **No pod with the marker and no live pod with the name:** the workload closes `failed` at $0 (`raw_pod_create_absent`) and its idempotency key is freed.
  - **A lookup error, several marked pods, or a pod with the name but no matching marker:** the workload is held and the pod ids are logged; such a pod is never adopted or terminated. The next pass retries. The ceiling charge above applies only once the workload passes the orphan age threshold.

`_cost_reconcile(ctx: dict[str, Any]) -> None` — **cron every 5 min**
: For each active workload, unconditionally maps `IN_PROGRESS` to a terminal state and applies it. This catches jobs RunPod marked IN_PROGRESS that silently completed without a webhook. Idempotent: skip-already-terminal guard on the UPDATE.

`_poll_and_reconcile(ctx: dict[str, Any]) -> None` — **cron every 2 min**
: Polls all active workloads against RunPod. For `serverless_queue` providers uses `QueueClient.status()`; for `pod_lease` providers uses `get_pod()` and reads `runtime.podStatus`. Maps the returned status string; applies terminal state if terminal. Publishes `workload.completed` event on update. Silently continues on errors (inner try/except).

`_idempotency_gc(ctx: dict[str, Any]) -> None` — **cron nightly at 03:00 UTC**
: Deletes workload rows with a non-null `idempotency_key` older than 24 hours.

`_lb_endpoint_hibernate_sweep(ctx: dict[str, Any]) -> None` — **cron daily at 12:00 UTC**
: Per L14 invariant: endpoints with `workersMin > 0` that have been warm for > 24h trigger a `HibernateSweepAlert`. Tracks state in Redis (`pitwall:hibernate_sweep:workers_min:{provider_id}`) with 7-day TTL. Does NOT auto-hibernate — only alerts. Imports `L14_DAILY_BURN_PER_WORKER_USD` from `pitwall.cost.hibernate_alerts`.

`_backup_drill(ctx: dict[str, Any]) -> None` — **cron weekly Sun 04:00 UTC**
: Delegates to `run_pit_restore_drill` in `pitwall.ops.backup_drill`. Silently suppresses all exceptions.

`_archive_old_workloads(ctx: dict[str, Any]) -> None` — **cron weekly Sun 05:00 UTC**
: Archives completed workloads older than the retention threshold to JSONL files under `PITWALL_ARCHIVE_DIR`. Delegates to `archive_workloads_to_jsonl` in `pitwall.retention`.

`_rollup_job(ctx: dict[str, Any]) -> None` — **cron daily at 01:00 UTC**
: Calls `run_rollup(pool)`. After rollup completes, invokes `after_rollup_hook`, which runs `check_and_send_budget_alert` and the burn-rate forecast alert. A failing alert is logged as a redacted warning and the next alert still runs.

`_health_probe(ctx: dict[str, Any]) -> None` — **cron every minute**
: Probes all non-cooldown `serverless_lb` providers via `LBClient.probe()`. Feeds results into `apply_probe_result` (from `pitwall.routing.cooldown`) to compute the next health state, then persists with `update_provider_health`.

`_lease_expiry_reconcile(ctx: dict[str, Any]) -> None` — **cron every minute**
: Fetches active leases with `auto_teardown_on_expiry=True` expiring within 60 minutes. Publishes `lease.expiring` warning events at configurable intervals (default 15min and 5min before expiry via `PITWALL_LEASE_ADVANCE_WARNING_MIN`). On expiry (`minutes_until_expiry <= 0`), calls `run_teardown` with `LeaseState.EXPIRED`. One lease's unexpected error is logged (`lease expiry sweep failed for lease …`) and the sweep goes on to the next lease, so no single lease stalls every teardown.

`_budget_breach_escalation(ctx: dict[str, Any]) -> KillEscalationOutcome | None` — **cron every minute**
: Inert unless `PITWALL_BUDGET_BREACH_KILL_MODE` is `shadow` or `armed`. Evaluates the budget circuit breaker against month-to-date spend; `shadow` logs the would-be kill, `armed` fires the kill switch once per UTC month (actor `system:budget-breach`). See SDLC 05.

`_process_webhook_terminal_status(ctx: dict[str, Any], runpod_job_id: str, status: str) -> None` — **Arq job**
: Enqueued by the webhook receiver. Applies terminal state and publishes the event. Calls `apply_terminal_status_and_publish` directly.

`fetch_providers_of_types(pool: asyncpg.Pool, provider_types: Iterable[str]) -> list[dict[str, Any]]`
: Returns the enabled providers whose `provider_type` is in `provider_types`, ordered by `id`. Feeds `_quota_poll`.

`_quota_poll(ctx: dict[str, Any]) -> None` — **cron every 5 min**
: Samples free-pool quota windows through each provider declaration's `quota_tick` hook (`pitwall.providers.interface.ProviderDeclaration`). For every registered declaration that sets one, it loads that declaration's enabled providers and calls the hook with the `QuotaRepository`, the provider row, the tick time, and the existing records keyed by `(provider_id, pool_key)`. One provider's failure is logged (redacted) and never stops the tick. Two hooks exist. The gateway hook (`openai_gateway`) never calls upstream: it reads `config.gateway.catalog` (`free_type`, `pool_key`, `tos`, `monthly_tokens`/`credit_tokens`), seeds a `QuotaRecord` when `(provider_id, pool_key)` is new, rolls the window when `reset_at <= now` using `quota_window` (monthly and credit windows span the UTC calendar month, daily windows the UTC day, other free types `(None, None)`), and appends a `provider_quota_samples` row. The Model Studio hook refreshes the window from the Alibaba billing or subscription statistics, falling back to the configured tier when the statistics are unavailable.

#### Invariants

- Terminal states are **never** overwritten: the UPDATE guard `state NOT IN ('completed', 'failed', 'cancelled', 'timed_out')` ensures idempotent re-runs.
- Cost is computed from RunPod-reported `worker_time_ms` multiplied by the provider's `cost_per_hr` rate; no estimation occurs.
- The `_poll_and_reconcile` loop is safe to re-run after a worker restart — all paths check the terminal guard before applying.

---

### `src/pitwall/reconciler/__main__.py` — CLI Entry Point

`main(argv: Sequence[str] | None = None) -> None`
: Supports two modes: `python -m pitwall.reconciler` (run worker) and `python -m pitwall.reconciler check` (validate Redis DSN). Exits 0 on success, 1 on config error or missing `arq`.

---

### `src/pitwall/reconciler/cost_daily_rollup.py` — Daily Cost Aggregation

`run_rollup(pool: asyncpg.Pool, *, after_rollup: AlertHook|None = None) -> None`
: Runs two upsert statements:
1. `_AGGREGATE_DAILY_SQL`: joins `pitwall.workloads` → `pitwall.capabilities` → `pitwall.providers`, groups completed workloads by UTC day / capability class / provider type, upserts counts and sum of `cost_actual_usd` into `pitwall.cost_daily`.
2. `_VOLUME_STORAGE_DAILY_SQL`: accrues daily volume storage cost for volumes without a configured `monthly_cost_usd`. Tiered: $0.07/GB/mo ≤ 1 TB, $0.05/GB/mo > 1 TB. Upserts into `pitwall.volume_cost_daily`.

`AlertHook = Callable[[], Awaitable[Any]]`
: Callback type for side-effects after a successful rollup. Used by `_rollup_job` to trigger budget alerts.

**Invariant**: Both statements are idempotent via `ON CONFLICT (day, ...)` clauses. Safe to re-run for the same UTC day.

---

### `src/pitwall/workload_lifecycle.py` — Workload State Machine

Provides pure functions for generating workload ids and moving workload rows through their lifecycle.

`generate_workload_id() -> str`
: Returns `f"wkl_{ulid_new()}"` — a ULID-based workload identifier.

`transition_to_running(repo: WorkloadRepository, workload_id: str, *, provider_id: str|None = None, fallback_chain: list[str]|None = None) -> Workload|None`
: Calls `repo.guarded_transition` moving from `QUEUED` → `RUNNING`. Sets `started_at`. Optional `fallback_chain` is recorded on the row.

`transition_to_completed(repo, workload_id, *, execution_ms=None, output_bytes=None, result=None, provider_id=None, cost_ceiling_usd=None, cost_quote=None, cost_actual_usd=None, cost_actual_provenance=None, fallback_chain=None, langfuse_trace_id=None) -> Workload|None`
: Calls `repo.guarded_transition` moving from `RUNNING` → `COMPLETED`. In addition to terminal timing, result, fallback, and trace fields, it can persist the winning provider, an exact ceiling/quote pair, and a sourced actual/provenance pair. A ceiling requires its structured quote, and an actual requires provenance.

`transition_to_failed(repo, workload_id, *, execution_ms=None, output_bytes=None, provider_id=None, error=None, fallback_chain=None, langfuse_trace_id=None, cost_ceiling_usd=None, cost_quote=None, cost_actual_usd=None, cost_actual_provenance=None, allow_queued=False) -> Workload|None`
: Calls `repo.guarded_transition` moving from `RUNNING` → `FAILED`, or from `QUEUED|RUNNING` when `allow_queued=True` is explicitly requested for a pre-egress terminal failure. It persists the applicable terminal timing, partial output bytes, response-owning provider, error, fallback, trace, exact ceiling/quote, and sourced actual/provenance fields under the same pairwise validation rules as completion.

**Invariant**: `guarded_transition` only succeeds if the workload is in `from_states`. The normal
state machine is `QUEUED` → `RUNNING` → `COMPLETED|FAILED`; the sole direct `QUEUED` → `FAILED`
path requires explicit `allow_queued=True` for a terminal pre-egress failure. Retrograde
transitions are impossible.

---

### `src/pitwall/worker.py` — deferred-worker tombstone

The incomplete GPU worker is not an alpha feature. `main()` prints an
actionable ADR reference and returns `EX_UNAVAILABLE` (69), preventing stale
automation from treating a no-op process as healthy. See
[ADR 0002](../decisions/0002-worker-deferred.md).

---

## 3. Reconciliation Loop

### State Convergence Target

The reconciler converges all workloads to a terminal `WorkloadState` (`completed`, `failed`, `cancelled`, `timed_out`). The ground truth is the RunPod API response; Pitwall's DB is updated to match.

### Polling Mechanism

`_poll_and_reconcile` runs every 2 minutes. It queries all `queued`/`running` workloads with a `runpod_job_id`, then calls:
- `QueueClient.status(endpoint_id, job_id)` for `serverless_queue` providers.
- `get_pod(job_id)` for `pod_lease` providers (returns `TIMED_OUT` if the pod is `None`).

Results are mapped via `map_runpod_status`. Terminal statuses are applied via `apply_terminal_state`, then the updated workload is fetched and published to Redis.

### Webhook Path

RunPod sends webhooks on job completion. The webhook receiver enqueues `_process_webhook_terminal_status` as an Arq job. The job calls `apply_terminal_status_and_publish`, which follows the same fetch → map → apply → publish path as polling.

### Terminal State Application

`apply_terminal_state` writes `state`, `cost_actual_usd`, and `completed_at` in a single UPDATE with a guard against already-terminal rows. The guard ensures that even if RunPod sends duplicate webhook events or the polling loop races, the state is set exactly once.

### Worker Entrypoint

```
python -m pitwall.reconciler          # Arq worker with cron jobs
python -m pitwall.reconciler check    # Redis DSN validation
```

The reconciler is driven by Arq's `WorkerSettings` class. Arq reads `REDIS_URL`, connects, and dispatches cron jobs and enqueued jobs.

---

## 4. Public Interfaces

### `pitwall.workload_lifecycle`

| Function | Signature |
|---|---|
| `generate_workload_id` | `() -> str` |
| `transition_to_running` | `(repo: WorkloadRepository, workload_id: str, *, provider_id: str\|None = None, fallback_chain: list[str]\|None = None) -> Workload\|None` |
| `transition_to_completed` | `(repo, workload_id, *, execution_ms=None, output_bytes=None, result=None, provider_id=None, cost_ceiling_usd=None, cost_quote=None, cost_actual_usd=None, cost_actual_provenance=None, fallback_chain=None, langfuse_trace_id=None) -> Workload\|None` |
| `transition_to_failed` | `(repo, workload_id, *, execution_ms=None, output_bytes=None, provider_id=None, error=None, fallback_chain=None, langfuse_trace_id=None, cost_ceiling_usd=None, cost_quote=None, cost_actual_usd=None, cost_actual_provenance=None, allow_queued=False) -> Workload\|None` |

### `pitwall.reconciler`

| Function | Signature |
|---|---|
| `validate_redis_dsn` | `(dsn: str) -> bool` |
| `check_redis_config` | `() -> int` |
| `map_runpod_status` | `(status: str, *, cost_per_hr: Decimal\|None = None, worker_time_ms: int\|None = None, completed_at: datetime\|None = None) -> RunPodJobStatus` |
| `apply_terminal_state` | `(pool: asyncpg.Pool, *, workload_id: str, state: WorkloadState, actual_cost: Decimal\|None, completed_at: datetime) -> bool` |
| `fetch_workload_by_id` | `(pool: asyncpg.Pool, workload_id: str) -> dict[str, Any]\|None` |
| `fetch_workload_by_runpod_job_id` | `(pool: asyncpg.Pool, runpod_job_id: str) -> dict[str, Any]\|None` |
| `apply_terminal_status_and_publish` | `(pool: asyncpg.Pool, redis: Any, runpod_job_id: str, status: str, completed_at: datetime\|None = None) -> bool` |
| `publish_workload_completed` | `(redis: Any, event: dict[str, Any]) -> int` |
| `aggregate_daily_cost` | `(pool: asyncpg.Pool) -> None` |
| `fetch_providers_for_health_probe` | `(pool: asyncpg.Pool) -> list[dict[str, Any]]` |
| `update_provider_health` | `(pool: asyncpg.Pool, *, provider_id: str, health_status: str, consecutive_failures: int, cooldown_trips: int, cooldown_until: datetime\|None) -> None` |

### `pitwall.reconciler.cost_daily_rollup`

| Function | Signature |
|---|---|
| `run_rollup` | `(pool: asyncpg.Pool, *, after_rollup: AlertHook\|None = None) -> None` |

---

## 5. Configuration

| Environment Variable | Default | Purpose |
|---|---|---|
| `REDIS_URL` | *(required)* | Arq Redis connection DSN, e.g. `redis://localhost:6379/0` |
| `RUNPOD_API_KEY` | *(required for polling/probes)* | RunPod REST API key |
| `RUNPOD_REST_API_URL` | `https://api.runpod.io/v2` | RunPod v2 API base URL for hibernate sweep |
| `PITWALL_LEASE_ADVANCE_WARNING_MIN` | `15,5` | Comma-separated minutes at which to fire lease expiry warnings |
| `PITWALL_ARCHIVE_DIR` | *(none)* | Directory path for JSONL workload archives |

The reconciler module calls `require_runtime_env("reconciler")` at import time (module level, ahead of the `arq` import guard), which validates that the reconciler runtime environment is present. `arq` must also be importable.

---

## 6. Failure Modes & Error Types

### Import errors
`reconciler/__init__.py` wraps its `arq` imports in `try/except ImportError` and records the result in `_ARQ_AVAILABLE`; `reconciler/__main__.py` guards `create_worker` the same way. When `arq` is unavailable, `main` prints `arq is not installed; cannot run worker.` to stderr and exits 1, while `check` still validates the DSN.

### Database connection errors
All async DB operations (`fetch`, `execute`, `fetchrow`) are wrapped in try/except at the call site inside each cron job function. Errors are suppressed and the function returns early. This prevents one failing job from crashing the worker loop.

### RunPod API errors
`_poll_and_reconcile` wraps the provider status lookup in a `try`/`except`. On any exception it `continue`s to the next workload. This means a RunPod outage causes polling to silently skip, not crash.

### Redis pub/sub errors
`publish_workload_completed` catches `Exception` and returns 0, ensuring a Redis failure does not propagate.

### Missing `runpod_job_id`
`_poll_and_reconcile` skips any row where `runpod_endpoint_id` or `runpod_job_id` is falsy.

### Lease teardown errors
A teardown whose provider call fails leaves its lease `stopping` with a live pod. Each `_lease_expiry_reconcile` tick first runs `_retry_stuck_teardowns`, which retries `run_teardown` for every `stopping` lease (an expired one closes `expired` with reason `ttl`, any other `stopped` with reason `operator`); one lease's failure is logged and retried next tick without stalling the others.

### Webhook job not found
`apply_terminal_status_and_publish` returns `False` when `fetch_workload_by_runpod_job_id` returns `None`, indicating the workload is not yet in the DB or the job ID is unknown. The webhook receiver handles this gracefully.

### Terminal guard re-application
`apply_terminal_state` returns `False` if the workload was already terminal. All callers treat `False` as a no-op and do not publish duplicate events.

---

## 7. Testing

| Test file | What it covers |
|---|---|
| `tests/test_workload_lifecycle.py` | Workload state transition functions: `generate_workload_id`, `transition_to_running/completed/failed` |
| `tests/reconciler/test_poll_and_reconcile.py` | `_poll_and_reconcile`, `map_runpod_status`, `apply_terminal_state`, `apply_terminal_status_and_publish` |
| `tests/reconciler/test_cost_daily_rollup.py` | `run_rollup`, daily aggregation SQL, volume storage accrual |
| `tests/reconciler/test_idempotency_gc.py` | `_idempotency_gc`, stale idempotency key deletion |
| `tests/reconciler/test_lease_expiry_reconcile.py` | `_lease_expiry_reconcile`, lease warning events, teardown trigger |
| `tests/reconciler/test_lb_endpoint_hibernate_sweep.py` | `_lb_endpoint_hibernate_sweep`, L14 warm-duration tracking, alert threshold |
| `tests/reconciler/test_init_coverage.py` | Surface API coverage check for the reconciler `__init__` module |
| `tests/api/test_e2e_lease_lifecycle.py` | End-to-end lease lifecycle (teardown path exercised via reconciler) |

---

## 8. Dependencies

### From `pitwall` itself

| Import | Source module |
|---|---|
| `WorkloadState`, `LeaseState` enums | `pitwall.core.enums` |
| `Workload` model | `pitwall.core.models` |
| `ulid_new` | `pitwall.core.ids` |
| `WorkloadRepository` | `pitwall.db.repository` |
| `require_runtime_env` | `pitwall.config` |
| `QueueClient` | `pitwall.runpod_client.queue` |
| `get_pod` | `pitwall.runpod_client.pods` |
| `LBClient` | `pitwall.runpod_client.lb` |
| `apply_probe_result`, `is_in_cooldown` | `pitwall.routing.cooldown` |
| `HibernateSweepAlert`, `send_hibernate_sweep_alert`, `L14_DAILY_BURN_PER_WORKER_USD` | `pitwall.cost.hibernate_alerts` |
| `check_and_send_budget_alert` | `pitwall.cost.alerts` |
| `run_teardown` | `pitwall.api.leases.teardown` |
| `run_pit_restore_drill` | `pitwall.ops.backup_drill` |
| `archive_workloads_to_jsonl` | `pitwall.retention` |

### External libraries

| Library | Used for |
|---|---|
| `arq` | Arq worker, cron scheduling, job enqueuing |
| `asyncpg` | PostgreSQL async driver for all DB operations |
| `pydantic` | `RunPodJobStatus` model |
| `httpx` | HTTP client for RunPod REST API (hibernate sweep, health probes) |
| `datetime`, `decimal` | Cost calculations and timestamping |
| `pathlib` | Archive directory path resolution |
| `json` | Redis pub/sub payload serialization |
