# Repository systems evaluation (2026-09-28)

A read-only survey of every system in this repository: what each one is, how big it is, how it
connects to the rest, how well it is tested, and what is wrong with it. Run on branch
`feat/model-studio` at commit `f59e971e`.

## Method

The repository was split into six non-overlapping lanes along its build-system and concern
boundaries. Each lane was assigned to one Claude Sonnet 5.5 subagent with a read-only brief: no
file edits, no state-changing git commands, no live or paid provider calls, and no DB-backed test
suites (they share `pitwall_test` on port 5444). At most five lanes ran concurrently. Each lane
was required to cite a `file:line` it had opened for every concrete finding and to grade each
subsystem A to F.

| Lane | Scope |
|---|---|
| A | Control-plane core: `src/pitwall/` api, core, db, leases, reconciler, workers, webhook dispatcher and receiver, policy, audit, security, retention, gitops, autopilot, ops, observability, root modules; `db/migrations/`, `docker/`, compose files, `config/`, `seed/`, `dashboards/`, `Makefile` |
| B | Routing, providers (including Model Studio), `runpod_client/`, `runpod_control_plane.py`, `runpod_market.py`, `runpod_files.py`, `runpod_credentials.py`, resolver, models, gateway catalog, recommendations, rate limits, `serve.py` |
| C | Cost, FinOps, cost exporter; operator surfaces: MCP server and installer, CLI, TUI, personal mode |
| D | `packages/agent-routing` (separate uv project) |
| E | `packages/gateway` and `packages/pi-workbench` (TypeScript) |
| F | Engineering process: `tools/`, `release_acceptance/`, `scripts/`, `.github/workflows/`, `Makefile`, `qa/`, `docs/` structure |

After each lane reported, the highest-severity findings were re-checked directly against the code
by the orchestrating session (see [Independent verification](#independent-verification)).
Findings not listed there carry the lane's own citation and were not independently re-read.

### Test runs performed by the lanes

| Lane | Command (abridged) | Result |
|---|---|---|
| A | `uv run pytest -q -m "not integration and not slow"` over tests/api, leases, reconciler, webhook_dispatcher, security, audit, core, config, chaos, db, retention, policy, gitops, ops, observability, autopilot, workers | 1613 passed, 3 skipped, 57 deselected |
| B | `uv run pytest tests/routing tests/providers tests/resolver tests/models tests/serve tests/runpod_control_plane -q` | 952 passed in 19.29 s |
| C | `uv run pytest tests/cost tests/finops tests/mcp tests/cli tests/tui tests/personal tests/unit tests/tools -q` | 1731 passed in 216.72 s |
| D | `.venv/bin/python -m unittest discover -s tests -q` (from `packages/agent-routing`) | Ran 890 tests in 540.5 s, OK (skipped=4) |
| E | gateway: `tsc --noEmit`, `eslint --max-warnings=0`, `vitest run` | tsc and eslint clean; 132 passed in 4.8 s |
| E | pi-workbench: `tsc --noEmit`, `vitest run` (three full-suite runs) | tsc clean; 281 passed, 1 skipped, 1 to 2 failed per run (flaky, see E) |
| F | `uv run pytest tests/release tests/tools tests/release_acceptance tests/test_release_scripts.py` | 634 passed, 417 skipped, 341 deselected in 72 s |

Not run: the DB and integration suites, the `webhook_receiver` tests, and the release
user-journey harness (`scripts/release/run-user-journeys.sh`).

## Summary

| Lane | System | Approx. size | Grade |
|---|---|---|---|
| A | Control-plane core | about 35k LOC | C |
| B | Routing and providers | about 37k LOC | C+ |
| C | Cost and operator surfaces | about 31k LOC | B- |
| D | `packages/agent-routing` | about 20k LOC runtime, 22k LOC tests | B |
| E | `packages/gateway` | about 5k LOC (1.7k local, 3.3k vendored) | B |
| E | `packages/pi-workbench` | about 4.6k LOC src | B |
| F | Tooling, release acceptance, CI, QA, docs | about 62k LOC acceptance material alone | C |

### Highest-severity findings across the repository

1. **Webhook terminal-status jobs never run.** The receiver enqueues an Arq job under a name the
   worker does not register. Only the 2-minute poll applies terminal states. (A; verified)
2. **Paid resources can leak or be billed twice.** `serve_model` raises without teardown on three
   post-launch paths; the RunPod queue client retries an ambiguous `/run` POST; the control-plane
   `create_pod` timeout can orphan an unaudited pod; one failing teardown blocks the whole lease
   expiry sweep. (A, B; three of four verified)
3. **Budget arithmetic is not single-sourced.** Four definitions of month-to-date spend; the 80%
   alert lags the admission gate; the exporter invents a $1000 budget when the variable is unset;
   the gate's month boundary depends on the DB session time zone. (C; verified)
4. **Gateway SSE streams are truncated at 30 s and ended as if complete.** (E; verified)
5. **The release acceptance matrix and harness-only journeys run in no workflow**, and the
   required CI aggregate omits `gateway-catalog-drift`. (F; verified)
6. **"Admin-only" MCP tools have no enforcement**; the `scope` field is never read. (C; verified)

### Cross-cutting patterns

- **Safety mechanisms that do not work as advertised.** Backup drill (no `pg_dump` in the
  reconciler image), retention purge (destructive compose default that fails on object-storage
  keys), webhook retry scheduling (never implemented; its alert can never fire),
  `gitops.apply_plan` (violates audit constraints), and audit checks that attest hardcoded
  `True` values.
- **About 9 to 10k LOC of code with no production caller**: routing primitives (~3.7k), FinOps and
  cost modules (~1.9k), `providers/drift.py`, `_wave2_feasibility.py`, `rate_limits/store.py`,
  `gitops.apply_plan`, `workers/vllm.py`, `workload_lifecycle` enqueue helpers, and the
  `tools/smoke_*` scripts. Property and unit tests keep this code green, which hides that it is
  unwired.
- **Parallel implementations of one concern**: three route planners, four month-to-date spend
  queries, two alert engines, MCP and REST serializers copied up to three times, four RunPod
  retry loops, two YAML parsers.
- **Provider-specific logic leaking into shared layers**: adding a provider touches about eight
  modules.
- **Oversized modules and functions**: `cli.py` 2,736 LOC, `onboarding.py` 2,766,
  `reconciler/__init__.py` 2,281, `db/repository.py` 2,218, `routing/production.py` 2,473,
  `openai_proxy` 663 lines, `_dispatch_legacy` 573 lines, `serve_model` about 550 lines.
- **Release machinery out of proportion to the product and not enforced**: about 62k lines of
  acceptance tooling, data, and tests against about 106k lines of product source, with no workflow
  invoking it.
- **Documentation drift**: SDLC docs cite about 535 `src/pitwall/...py:N` line references; four
  spot checks all pointed at the wrong lines.

## Independent verification

The orchestrating session re-read the code for these findings. All were confirmed.

| Finding | Evidence checked |
|---|---|
| Webhook job name mismatch | `webhook_receiver/__init__.py:364` enqueues `"process_webhook_terminal_status"`; `reconciler/__init__.py:2256-2258` registers `_process_webhook_terminal_status`; `arq.worker.func(...)` on the registered function prints `['_process_webhook_terminal_status']` |
| Dead `submit_runpod_job` enqueue | `workload_lifecycle.py:205` enqueues `"submit_runpod_job"`; not present in `WorkerSettings.functions` (`reconciler/__init__.py:2256-2258`) |
| Unguarded teardown in expiry sweep | `reconciler/__init__.py:1420-1470` read: `run_teardown` awaited with no surrounding try/except |
| `serve_model` leaks after lease creation | `serve.py:1673-1721` read: `ServeLaunchFailed` raised at 1674, 1677, 1690, 1719 with no teardown; teardown only in the `except ServeVerificationFailed` block |
| RunPod `/run` POST retried after ambiguous failure | `runpod_client/queue.py:133-150`: `except httpx.HTTPError` (includes read timeouts) sets a delay and `continue`s the POST loop |
| Exporter default budget | `cost/exporter.py:126`: `BUDGET_USD = float(os.environ.get("PITWALL_MONTHLY_BUDGET_USD", "1000"))` |
| Gate vs alert MTD definitions | `cost/budget_gate.py:18-22` sums `COALESCE(actual, ceiling, estimate)` with `now() AT TIME ZONE 'UTC'`; `cost/alerts.py:152-160` sums `cost_actual_usd` only for queued/running/completed |
| MCP `scope` never read | `grep -rn "\.scope\b" src/pitwall/mcp` returns no matches |
| Gateway stream truncation | `packages/gateway/src/shim.ts:401-402` timer cleared only in `finally` (442); `relayUpstreamStream` loop exits on abort and calls `res.end()` at 588 |
| Journey harness not in CI | `grep -c "JOURNEY_HARNESS\|run-user-journeys\|run_bound_tests"` returns 0 for all eight workflow files and the `Makefile`; `tests/release/conftest.py:33-37` deselects `journey_harness` items unless `PITWALL_JOURNEY_HARNESS=1` |
| `gateway-catalog-drift` not in required gate | `.github/workflows/ci.yml:504-519` `needs:` list read in full |
| agent-routing `unrestricted` ignored by kimi and dsh | `grep -n unrestricted` over `runtime/model_routing/providers/{kimi,dsh,base}.py` matches only `base.py:142,152,156` |
| GNU `timeout` prerequisite and 1 ms timeout fallback | `runtime/model_routing/dispatch.py:802-806` exits early when `_gnu_timeout_available` is false; `dispatch.py:876-881` sets `timeout_seconds = 0.001` on invalid or non-positive input |

---

## Lane A: control-plane core

| Subsystem | src LOC | Tests (functions) | Grade |
|---|---|---|---|
| api (102 OpenAPI operations) | 12,471 | tests/api 372, tests/security 73, tests/integration 106 (shared) | C |
| core | 1,830 | tests/core 42 plus root model tests | B |
| db (plus 35 migrations, 1,236 SQL LOC) | 3,297 | tests/db 165, 6 SQL scripts | B- |
| leases | 714 | tests/leases 152 | B |
| reconciler | 2,433 | tests/reconciler 136 | D |
| workers, `worker.py` | 197 | 22 | C (orphaned) |
| webhook_dispatcher | 708 | 31 | C |
| webhook_receiver | 528 | scattered across 4 to 5 files | D |
| policy | 580 | 6 | C |
| audit | 2,456 | tests/audit 50, test_audit_checks 85 | C- |
| security (`pre_spend.py`, `redaction.py`) | 1,261 | 73 | A- |
| retention | 349 | 9 | D |
| gitops | 1,107 | 10 | D |
| autopilot | 1,067 | 20 | B |
| ops (`backup_drill.py`, `chaos_drill.py`) | 1,021 | 28, plus chaos 11 | D |
| observability | 789 | 44 | B |
| Root modules (`config.py` 1,663; `onboarding.py` 2,766; `doctor.py` 672; `seed.py` 805; `r2_*` 704; `workload_lifecycle.py` 217; others) | about 7.5k | onboarding 35, seed 23, r2 22, workload_lifecycle 15, live 11, staging_store 6 | C+ |
| docker, compose, config, seed, dashboards, Makefile | Makefile 60; compose 254 | tests/test_dockerfiles.py and similar | C |

### reconciler

Arq cron worker for lease expiry, teardown retries, health probes, cost and quota polling,
retention, the backup drill, and webhook terminal status. One 2,281-line `__init__.py`;
`WorkerSettings` at `:2055`. Depends on Postgres, Redis, Arq, the RunPod API, and
`api/leases/`.

- **High: webhook terminal-status jobs are never processed** (verified). The receiver enqueues
  `"process_webhook_terminal_status"` (`webhook_receiver/__init__.py:363-366`); the worker
  registers `_process_webhook_terminal_status` (`reconciler/__init__.py:2257`). Arq resolves jobs
  by the registered name, so the job is never found. Terminal states only land via the 2-minute
  `_poll_and_reconcile`. No test compares the names (`tests/reconciler/test_init_coverage.py:116`
  only checks the function object is present). `workload_lifecycle.py:205` has the same defect for
  `"submit_runpod_job"`.
- **High: a failing teardown blocks the lease sweep** (verified at `:1420-1470`).
  `_lease_expiry_reconcile` calls `run_teardown` without try/except at `:1455`, `:1464`, `:1522`,
  `:1531`, `:1548`, `:1618`. `run_teardown` raises `TeardownFailed` on provider failure
  (`api/leases/teardown.py:~184-188`). `list_active_for_activity_control` includes `stopping`
  leases ordered oldest-traffic first (`db/repository.py:919-933`), so a stuck lease raises first
  every tick and later leases are never torn down; their pods keep billing.
  `_retry_stuck_teardowns` (`:1370-1394`) does isolate per lease. No test injects a teardown
  failure into the expiry sweep.
- **Medium: the weekly backup drill cannot pass in the shipped image.** `docker/Dockerfile.reconciler`
  (lines 1-31) installs no `postgresql-client`; the drill shells out to `pg_dump` and
  `pg_restore` (`ops/backup_drill.py:69-92`, `:304-343`).
- **Medium: the drill blocks the worker event loop.** Synchronous `subprocess.run` with timeouts
  up to 300 s inside an async job (`ops/backup_drill.py:250,274,304,324`) stalls every other cron,
  including lease expiry.
- **Medium: retention default is destructive and unworkable.** Compose defaults
  `PITWALL_RETENTION_MODE` to `archive-purge` (`docker-compose.yml:~143`); the code default is
  `off` (`reconciler/__init__.py:1033`). See Retention.
- **Low:** `PitwallWebhookRetriesDue` (`config/prometheus/pitwall-cloud-alerts.yml:46-47`) can never
  fire because `next_retry_at` is never set.
- **Low:** `WorkerSettings` docstring says the drill and archive run "weekly on Sunday"
  (`:2068-2069`); `weekday={0}` (`:2253-2254`) is Monday in Arq. `tests/ops/test_backup_drill.py:287-296`
  pins the wrong claim. The docstring also omits `_budget_breach_escalation`.
- **Low:** the budget circuit breaker is stored with `ctx.setdefault(...)` (`:1811`) in a per-job
  context rebuilt every job (`arq/worker.py:582`), so its cooldown and hysteresis reset every
  minute.
- **Low:** duplicated `decide_renewal` plus teardown blocks (`:1447-1470`, `:1517-1540`); a dead
  `expires_at is None` guard (`:1512`; `Lease.expires_at` is non-null at `core/models.py:239`);
  N+1 provider and Redis reads per lease every minute (`:1953-1979`); a literal
  `minute={0..59}` set (`:2110`); `_lease_expiry_reconcile` is 271 lines.
- **Low:** the reconciler healthcheck `os.kill(1, 0)` (`Dockerfile.reconciler:30`) targets the
  init process under compose `init: true`, so it passes even when the worker is hung.

### api

FastAPI REST surface. `app.py` (679 LOC), `routes/openai.py` (1,682), `leases/launch.py` (2,041),
`leases/teardown.py` (590), `exceptions.py`, `scopes.py`. The scope map across all 102 operations
is sound: admin routes need `server:admin` plus `X-Pitwall-Secret`; RunPod GET reads need only
`read` and their outputs are fingerprinted and secret-stripped (`runpod_control_plane.py:1747-1766`).

- **Medium:** `openai_proxy` is 663 lines (`api/routes/openai.py:1017`) on the spend path, with 23
  annotated `except Exception` blocks. Also over 245 lines: `_run_launch_runpod`
  (`launch.py:1357`), `run_teardown` (`teardown.py:119`), `_apply_locked` (`onboarding.py`).
- **Medium:** `run_teardown` closes the lease and then re-raises audit or disarm errors
  (`teardown.py:~281-283`), so a completed teardown can be reported as a failure.
- **Low:** `InboundRateLimitMiddleware._buckets` (`app.py:373,417-424`) is keyed by client IP and
  token digest and never evicted.
- **Low:** `RequestBodyLimitMiddleware` buffers up to 8 MiB before authentication
  (`app.py:~396-401`).
- **Low:** `_PUBLIC_HEALTH_PATHS` (`app.py:~92`) lists `/metrics`, `/ready`, `/readiness`, which
  do not exist; `/healthz` and `/health` return `"backend": "runpod"` (`app.py:~482-489`);
  `_RUNPOD_API_KEY` and `_DATABASE_URL` (`app.py:85-86`) are never read.
- **Low:** the API refuses to boot without `RUNPOD_API_KEY` (`config.py:995-1003`), so a Model
  Studio or gateway-only deployment needs a dummy key.
- **Low:** teardown falls back to a RunPod terminate when the provider row is missing
  (`teardown.py:~190-192`).
- **Low:** `GET /v1/jobs` returns stored `input` and `result` to any read-scope token
  (`api/routes/jobs.py:~46-70`); acceptable only under a single-tenant model.

### db and migrations

asyncpg pool, seven repositories in `db/repository.py` (2,218 LOC), the migration runner, and reset
guards. The runner takes an advisory lock, runs each migration in a transaction, and detects
checksum drift (`db/__init__.py:359-433`). Pools use `statement_cache_size=0` for PgBouncer.

- **Medium:** `_docker_psql` (`db/__init__.py:275-301`) execs into `pitwall-test-postgres` and
  ignores the host, port, and credentials in `DATABASE_URL`. If `psql` is absent and that
  container is running, `db reset --force` can drop the test container's schema instead of the
  intended database.
- **Low:** `cmd_status` runs `CREATE TABLE IF NOT EXISTS schema_migrations` (`:~485`);
  `_applied_migrations` returns `{}` on any error (`:341`), so a broken connection reads as "all
  pending"; `status` never calls `detect_drift`.
- **Low:** `migrate` uses asyncpg while `status` and `reset` shell out to `psql`;
  `_applied_migrations_async` (`:351`) is unused; `db_lifespan` yields without try/finally
  (`:~112`); `get_pool` relies on `assert dsn`.
- **Low:** audit constraints are dropped and recreated in migrations 0016, 0019, 0020, 0021, 0027;
  every new audit action needs a migration (the root of the gitops defect below).

### leases

State machine, activity-based renewal, events, mutations. Redis traffic stamps fail safe (an
unreadable stamp means busy, `activity.py:47-66`). The cleanest subsystem in this lane. Minor
alias clutter at `state.py:64,127-128` and a compatibility alias at `db/repository.py:935-938`.

### webhook_dispatcher

Signed, SSRF-safe outbound delivery with DNS-pinned HTTPS and an HMAC signer.

- **Medium: retry scheduling was never implemented.** `DeliveryOutcome.next_retry_at` is always
  `None` (`dispatcher.py:~163`); the reconciler inserts failures without it
  (`reconciler/__init__.py:~573-585`); `list_pending_retries` and `update_next_retry`
  (`db/repository.py:1839,1857`) have no callers; the exporter's `retries_due`
  (`cost/exporter.py:190-197`) is always zero.
- **Low:** an egress-policy rejection is labelled `"retry_scheduled"` (`dispatcher.py:47-63`).
- **Low:** backoff sleeps of up to 13 s per subscription (`dispatcher.py:26`) run inline and
  sequentially inside reconciler jobs.
- **Low:** only `TimeoutError` and `OSError` are caught (`dispatcher.py:~155`);
  `http.client.HTTPException` subclasses escape; only the first resolved address is tried.

### webhook_receiver

- **High:** the job-name mismatch above.
- **Medium:** `insert_or_skip` records the delivery before `_enqueue_terminal_status_job`, and
  enqueue failure is swallowed (`webhook_receiver/__init__.py:~342-372`), so a RunPod retry is
  treated as a duplicate.
- **Low:** one Arq pool is created and closed per request (`:361`).
- **Low:** with no secret configured, no signature is checked; the bind guard
  (`config.py:964-992`) prevents non-loopback exposure, and the fallback idempotency key trusts
  the payload `id` (`:~377`).
- **Low:** no dedicated test directory.

### retention

AES-256-GCM encrypted archive of terminal workloads with optional purge.

- **Medium:** purge raises `ValueError` when a batch row has `r2_key`, `object_key`, or
  `staging_key` and no `object_delete` adapter is passed (`retention/archive.py:216-217`). No
  production caller passes one (`reconciler/__init__.py:1045`, `retention/__main__.py:44-51`). The
  archive directory and manifest are written first (`:164-185`), so each weekly run leaves an
  orphan, and the same oldest batch is selected every time.
- **Medium (not run):** compose mounts a named volume at `/var/lib/pitwall/archive`, but
  `Dockerfile.reconciler` never creates or chowns it for uid 10001; the mount is likely
  root-owned and `_secure_directory` would raise `PermissionError`, which is only logged
  (`reconciler/__init__.py:1052-1057`).
- **Low:** object deletes run inside the DB transaction (`archive.py:219`); non-purge mode
  re-archives the same rows every run; `FOR UPDATE SKIP LOCKED` locks are held across file I/O.

### gitops

- **Medium (latent):** `apply_plan` writes actor `gitops:admin` (`gitops/reconcile.py:93`) and
  action `gitops:<op>` (`:117`), which the `config_audit` check constraints
  (`db/migrations/0027_config_audit_lease_actions.sql:9-27`) reject. Tests use a fake audit
  writer. A real apply would perform each operation and then fail on the audit insert.
- **Medium:** `apply_plan` has no production caller; the MCP copilot uses only
  `build_reconcile_plan` (`mcp/tools/copilot.py:~139`). About 330 LOC.
- **Low:** the apply loop is not transactional.

### policy

- **Medium:** policy documents are parsed with the private `_parse_simple_yaml` from `seed.py`
  (`policy/loader.py:14,76`), although `pyyaml` is a declared dependency (`pyproject.toml:39`).
  Anchors parse silently to literal strings; flow collections and block scalars raise.
- **Low:** `seed.py:242-244` falls back to the hand parser only on `ModuleNotFoundError`, which
  cannot happen for a hard dependency; about 150 lines (`seed.py:648-790`) are redundant.

### audit

The 19-check audit gate.

- **Medium:** several inputs are hardcoded `True` in `audit/_runtime_config.py` (`:72` webhook
  idempotent and fast-200; `:75-80` retention; `:93` ssh-first; `:119-121` template cache; `:206`
  404-as-success; `:210-212` kill switch). Check 07 (`audit/checks.py:1022-1037`) then only
  verifies a POST route exists and reports "idempotent and fast-200".
- **Low:** source-text checks via `inspect.getsource` (`checks.py:687,706,754-786,859,861,898`);
  `check_15` monkeypatches `pods._rest_request` (`:~1261-1284`); check 07 imports
  `webhook_receiver` and triggers `require_runtime_env`; `checks.py` is 1,554 LOC.

### security

`security/pre_spend.py` is deterministic and bounded (depth, item, byte, time limits; any limit
hit blocks, `:596-645`), fails closed on schema errors (`:557-594`), and is wired into the
openai, messages, inference, routing, and guardrail endpoints. No defects found.

### workers, worker.py, live.py

`workers/vllm.py` and `workers/header_policy.py` have no `src` callers (ADR 0002 deferred the
worker). `worker.py` is an intentional fail-closed tombstone (exit 69). `live.py` is a test-only
helper shipped in the wheel.

### autopilot, ops, observability, core

Autopilot is policy-gated and invoked only from the operator TUI (`tui/operations.py:823`).
Observability creates Langfuse clients lazily with guarded failure (`observability/langfuse.py:25-50`).
Core has no defects; `recent_workloads_read` in `core/cost_reporting.py` is 158 lines.

### Root modules, docker, compose

- `config.py` is 1,663 LOC with a 228-line `_explicit_env_settings_data` (`:711`);
  `onboarding.py` is 2,766 LOC with a 309-line `_apply_locked` (`:925`).
- `enqueue_submit_runpod_job` and `insert_passthrough_workload` in `workload_lifecycle.py` have
  no production callers.
- Doc line references are stale: `docs/sdlc/02-api-rest.md:539` and `docs/sdlc/14-security.md:253`
  cite `app.py:41` (router imports; the real code is at `app.py:85-86`);
  `docs/sdlc/16-core-config.md:208,414` cite unrelated `config.py` lines and say `worker`
  requires runtime env, which `_REQUIRED_ENV_BY_SERVICE` (`config.py:997-1003`) does not.
- Compose hardening is good: read-only rootfs, `cap_drop: ALL`, no-new-privileges, an internal
  backend network, digest-pinned images, and required secrets via `:?`.

---

## Lane B: routing, providers, RunPod, models

| Subsystem | src LOC | Tests (functions) | Grade |
|---|---|---|---|
| routing (26 files) | 10,899 | tests/routing 278; property ~97 (shared) | C+ |
| providers (incl. model_studio) | 6,975 | tests/providers 205 | B- |
| runpod_client | 8,309 | tests/runpod_client 410 | B- |
| runpod_control_plane.py | 1,991 | 33 | B |
| runpod_market.py | 951 | ~60 | B+ |
| runpod_files.py | 2,244 | ~83 | B |
| runpod_credentials.py | 71 | 9 | B |
| resolver | 670 | 57 | C+ |
| models | 1,129 | 65 | A- |
| gateway_catalog | 625 | ~33 | B |
| recommendations | 496 | ~38 | B- |
| rate_limits | 855 | ~39 | C |
| serve.py | 1,798 | tests/serve 64 | C+ |
| tests/fakes | 2,356 | 9 | B |
| tests/live | 341 | 6 (gated) | B |

### providers

A registry of narrow Protocol contracts (`interface.py:365-447`) validated against declared
capabilities (`registry.py:~262`), six default adapters, and `service.py` as the read service
behind REST, MCP, CLI, and TUI. Adapters: runpod, vast, lambda_cloud, together, gateway,
model_studio.

- **Medium:** RunPod `infer` only does embeddings (`providers/runpod.py:97-123`), so the sync
  inference contract is not provider-neutral.
- **Medium:** `RunPodProvider.submit` (`runpod.py:125-137`) calls `QueueClient.run`, which retries
  POSTs on transport errors and 5xx (`runpod_client/queue.py:108,134-172`; verified). This
  contradicts `production.py:1061` ("never retry an ambiguous provider write").
- **Medium (Model Studio):** `availability()` never calls `catalog.require_automation`
  (`model_studio/adapter.py:222-258`) while `infer` does (`:161`), so the Token Plan
  interactive-use gate is bypassed on the availability and health-probe path.
- **Medium (Model Studio):** `_collect_stream` (`adapter.py:261-332`) has an unguarded
  `json.loads` (`:276`); an SSE error chunk or a non-SSE 200 body yields a successful empty result;
  `availability()` calls `response.json()` unguarded (`:239`). None of this is covered by the nine
  Model Studio provider tests.
- **Medium:** `ModelStudioProviderError` is a bare `RuntimeError` (`adapter.py:50`); Model Studio
  reuses `QuotaExhausted` from `gateway.py:88`, so its 429s read "gateway request failed"
  (`gateway.py:84`).
- **Medium:** in `service.py`, availability requires `credential_ref in environ`
  (`:206-210`), so keyless gateways report `credential_unavailable`; credentials are always
  `{"api_key": env}` (`:~236`); `_provider_error_code` matches class names as strings
  (`:356-362`).
- **Medium:** `providers/drift.py` (421 LOC) has no production caller.
- **Low:** Model Studio ignores upstream `Retry-After` (`adapter.py:189-220`); `classify_429`
  treats naive body timestamps as local time (`gateway.py:158-160`) while the header path treats
  them as UTC (`:135`); legacy kwargs on `infer` in together and model_studio; gateway uses its own
  `_resolve` (`gateway.py:202-216`); `_wave2_feasibility.py` (396 LOC) is test-only;
  `model_studio/catalog.json` duplicates Agent Routing's copy (parity-tested) with a parallel
  Python implementation; vast and lambda_cloud are near-duplicates at about 1,000 LOC each.

### routing

Provider selection, pricing, constraints, cooldown, lockout, the OpenAI-compatible fallback
proxy, and pure routing libraries. Production entry: `build_production_plan`
(`production.py:458-742`) and `ProductionRoutingService` (`:743`).

- **High/Medium: three planners.** `planner.plan_route` (875 LOC) is used only by
  `cost/simulator.py` and `finops/time_machine.py`; live routing uses `build_production_plan`,
  which re-implements the same stages; `resolver.select_stage12_provider` feeds
  `core/inference.py`. Simulations can diverge from real routing. `docs/sdlc/04-routing.md:23`
  leads with `plan_route`.
- **High/Medium: proxy and adapter paths handle quotas differently.** `fallback.py:269-270` retries
  only on 5xx, so a 429 is returned to the caller without failover; lockouts are recorded only on
  the adapter path (`production.py:983`), never in `api/routes/openai.py`.
- **Medium:** about 3.7k LOC of primitives with no production caller: canary 772, prewarm 760,
  failover 547, semantic_cache 370, carbon 306, cascade 287, hedging 255, quality_routing 248,
  arbitrage 228.
- **Medium:** provider-specific branches in `fallback.py:200-209,222-225,~258` and
  `lockout.py:227-231`. Adding a provider touches `core/enums.py:97,108`, `registry.py:226`,
  `providers/__init__.py:77,110-116`, seed, reconciler, provider_schemas, fallback, and lockout.
- **Medium:** `build_production_plan` is documented as I/O-free (`:477`) but reads the
  process-global lockout table (`:556`), written at `:983` and `:1005`; plans differ between
  workers unless persistence is configured.
- **Medium:** `execute_sync_prepared` (`:884-1049`) falls through on any `Exception` (`:978`) with
  no backoff; only `QuotaExhausted` updates lockout, so timeouts and 5xx leave no cooldown signal.
- **Low:** overlapping parameters on `plan_route` (`planner.py:~117-124`);
  `is_openai_compatible_provider` returns True for `None` (`openai.py:300`).
- Positive: redacted errors (`production.py:2339`), persisted cancellation (`:955-976`),
  consistent idempotency replay and admission-before-spend.

### runpod_client

REST, GraphQL, serverless, queue, LB, pods (2,494 LOC), templates, registry, mounts, billing, and
discovery clients.

- **Medium:** credential resolution is inconsistent despite `runpod_credentials.py:3-7` claiming a
  saved runpodctl credential works identically everywhere. `pods.py:323`, `serverless.py:329`, and
  `registry.py:82` use `resolve_runpod_api_key`; `mounts.py:176`, `templates.py:442`,
  `runpod_files.py:1747`, and `graphql.py:374` read only `RUNPOD_API_KEY`.
- **Medium:** four near-duplicate retry loops (`queue.py:134-172`, `lb.py:104-130`,
  `serverless.py:~120-134`, `serverless_lb.py:128-197`); the `on_429` hook (`queue.py:108`) is
  never wired.
- **Medium:** `pods.py` is sync-first under `asyncio.to_thread` (`:1448`, `:2043`, `:2105`) with
  blocking `time.sleep` and `httpx.Client`; `asyncio.wait_for` cannot cancel those threads.
- **Low:** three near-duplicate parameter lists for `create_pod_with_fallback` (`:1352`, `:1403`,
  `:1467`); REST base URLs defaulted separately in four modules.

### runpod_control_plane.py

Typed operator boundary for raw RunPod resources with validation, dry-run, audit, and redaction.

- **High:** `create_pod` runs through `_call`'s `asyncio.wait_for(..., 60 s)` (`:630`,
  `:1585-1596`) around a threaded backend that allows 120 s per attempt (`pods.py:1448,1467`). On
  timeout the caller gets `provider_timeout, retryable=True` while the thread can still create the
  pod, and no audit row is written because `_audit` runs after `_call` returns (`:~690`).
- **Low:** the 300 s ceiling (`:632`) can be shorter than multi-GPU fallback plus startup.
- Positive: redaction (`:1886`), a mandatory audit precheck (`:1464`), dry-run previews.

### runpod_market.py, runpod_files.py

`runpod_market` is a cached read composing GraphQL discovery and the REST v2 catalogue; no
defects. `runpod_files` is a bounded S3 and pod-log service with a Postgres mutation journal and
no-follow, no-overwrite path handling; `VolumeFileService` is about 960 lines (`:769-1730`) and
reads `RUNPOD_API_KEY` from the environment only (`:1747`).

### resolver

- **Medium:** `providers/gateway.py:39` imports `resolver.provider_urls`, which loads
  `resolver/__init__`, `resolver.service`, all of `pitwall.routing`, and `runpod_client` (about
  24 routing modules); `routing/production.py:53-59` imports back from resolver, and
  `fallback.py:~186` needs a local import to avoid the cycle.
- **Medium:** `validate_openai_base_url` is described as an SSRF guard (`provider_urls.py:161-193`)
  but accepts HTTPS to any host, including private and metadata addresses, and rejects plain-http
  `localhost` although `gateway.is_loopback_base_url` treats it as loopback (`gateway.py:317`).

### models, gateway_catalog, recommendations, rate_limits

- `models`: small, typed, 65 tests plus integrity guards; `models/__init__.py` re-exports core
  symbols.
- `gateway_catalog`: dev-time OmniRoute catalog sync using pinned `npm`/`node` with
  `--ignore-scripts` (`sync.py:386-401`); `REPO_ROOT` is computed at import (`sync.py:41`);
  `cli_gateway.py:33` falls back to a nonexistent `gateway_catalog/data`.
- `recommendations`: used only by the MCP copilot; `ScorecardMetric` (`engine.py:22-45`) predates
  and now overlaps `observability/scorecards.py:34`; drift recommendations depend on the
  unwired `providers/drift`.
- `rate_limits`: `RateBucketStore` (`store.py:24`, 333 LOC) and `halved_capacity` are never
  instantiated in production; only `TokenBucket` and `retry_after.py` are used.

### serve.py

Launch and verify OpenAI-compatible model servers on pod leases.

- **High/Medium: paid pod leak** (verified). After the lease exists, `ServeLaunchFailed` is raised
  without teardown at `serve.py:1674`, `:1677`, `:1690`, `:1719`. Teardown runs only on
  `ServeVerificationFailed` (`:1697-1704`). `CancelledError` or any other exception during the
  60 s verify window (`VERIFY_TOTAL_TIMEOUT_S`, `:103`) also leaves the pod running until its TTL.
- **Medium:** `serve_model` is about 550 lines (`:1248-1798`).
- **Low:** RunPod SDK errors are string-matched (`:1212`, `:1619`).

### tests

Coverage is broad. Property and unit suites keep unwired primitives green. `tests/live` is
correctly gated (`test_model_studio_live.py:16-17`). `tests/fakes` (2,356 LOC) has only nine
direct tests.

---

## Lane C: cost, FinOps, and operator surfaces

| Subsystem | src LOC | Tests (files / functions) | Grade |
|---|---|---|---|
| cost (+ `cost_exporter/` shim, 79 LOC) | 7,009 | 26 / 470 | B- |
| finops | 2,836 | 8 / ~60 | C+ |
| MCP (`mcp/` + `mcp_install.py`) | 4,781 + 356 | 34 / 255 | B |
| CLI (`cli.py` + 15 `cli_*.py`) | ~6,400 | 22 / 288 | C+ |
| TUI | 8,808 | 22 / 221 | B+ |
| personal | 1,235 | 10 / 75 | B |

### cost and FinOps

Admission budget gate (`cost/budget_gate.py`, serialised on `pg_advisory_xact_lock`,
`:141-190`), a Decimal-only estimator that rejects floats (`estimator.py:1091`) and rounds
ceilings up (`:451`, `:1026`), reconciliation, alerts, the Prometheus exporter, burn-rate
forecasting (single schema across CLI, MCP, and REST), and what-if simulation. Used in-process by
API, routing, core, reconciler, CLI, and TUI.

- **High: four month-to-date spend definitions** (gate and alert verified). The gate sums
  `COALESCE(actual, ceiling, estimate)` over all states (`budget_gate.py:18-22`); the 80% alert sums
  `cost_actual_usd` for queued, running, completed (`alerts.py:153-160`) and so lags the gate; the
  exporter sums actuals in any state (`exporter.py:160-165,203-208`); the TUI has its own query
  (`tui/cost.py:467-475`).
- **High: exporter invents a budget** (verified). `exporter.py:126` defaults
  `PITWALL_MONTHLY_BUDGET_USD` to `"1000"`; the gate (`budget_gate.py:131`), `alerts.py:167`, and
  `config.py:1352` treat it as required.
- **Medium:** about 1,900 LOC unwired: `cost/threshold_alerts.py` (also float money math,
  `:23-25,70`; documented as live at `docs/sdlc/05-cost-budget.md:208-225` with stale line numbers),
  `cost/slo_governor.py`, `finops/bidding.py`, `finops/time_machine.py`. Production runs only the
  single 80% Redis-deduped alert (`reconciler/__init__.py:1070-1075`).
- **Medium:** `budget_gate.py:21` compares `timestamptz` to a bare `timestamp`, so the month
  boundary follows the session time zone; `tui/cost.py:472` and `audit/capability.py:590` inherit
  it. The other queries convert the column (`submitted_at AT TIME ZONE 'UTC'`).
- **Medium:** the legacy cost read returns money as JSON floats (`cost/read_models.py:327-353`),
  used by REST, MCP, and `cost/cli.py:33,79`; burn rate returns strings.
- **Low:** `budget_gate.py:341-345` coerces floats via `Decimal(str(float))` while the estimator
  rejects them; a `per_request_cap` rejection reports `mtd_spend_usd=0`
  (`budget_gate.py:167-170,212-215`); the exporter, `cli.py:744`, and `tui/hardware_fit.py:169`
  build pools without `statement_cache_size=0`; helpers are duplicated between `simulator.py` and
  `time_machine.py`; `cost_exporter/app.py` is a deprecated pass-through still used as the
  console-script module.

### MCP server

Stdio FastMCP server with 79 `pitwall_*` tools (`mcp/__main__.py:19-24`). `safe_boundary.py`
returns stable error codes and sets `additionalProperties: false` on every tool (`:56-60`).
`mcp_install.py` registers with claude-code, codex, and opencode by env reference. Handlers call
`get_pool()` and services directly, never HTTP; `tests/mcp/test_no_business_logic_guard.py`
forbids some internal imports, but handlers still import `pitwall.api.leases.*`
(`mcp/tools/leases.py:82,124`).

- **High:** six tools are described as "Admin-only" (`registry.py:224-250`) with no enforcement;
  `ToolSpec.scope` (`registry.py:132`) is set but never read (verified). `actor="mcp:admin"`
  (`mcp/tools/admin.py:118,187`) is a literal, not an identity.
- **Medium:** CLI and MCP lease renewals do not publish the renewal event.
  `leases/mutations.py:134-145` publishes only when both `pool` and `capability_name` are passed;
  REST passes both (`api/routes/leases.py:284-294`), the CLI passes only `pool` (`cli.py:410-416`),
  MCP passes neither (`mcp/tools/leases.py:147-153`).
- **Medium:** serializers are duplicated across MCP and REST: `_capability_to_response`
  (`mcp/tools/discovery.py:25`, `mcp/tools/admin.py:44`, `api/capability_routes.py:142`),
  `_provider_to_response` (`discovery.py:46`, `admin.py:197`, `api/provider_routes.py:75`),
  `_lease_to_response` (`mcp/tools/leases.py:38`, `api/routes/leases.py:142`).
- **Medium:** MCP accepts naive datetimes and interprets them as server-local
  (`mcp/tools/cost.py:27-30`); the CLI rejects them (`cost/cli.py:114-115`).
- **Low:** `pitwall_health` returns a hardcoded `{"ok": "true", "backend": "runpod"}`
  (`registry.py:125-128`); import-time `assert len(...) == 79` (`registry.py:122,370`); the
  `mcp/tools/cost.py` docstring cites a nonexistent module path.
- **Low (unverified):** `mcp_install.py:24` forwards only four env variables; `BudgetGate` also
  needs `PITWALL_MONTHLY_BUDGET_USD` and `PITWALL_PER_REQUEST_MAX_USD` unless they come from the
  config file.

### CLI

`pitwall = pitwall.cli:main`; no arguments launches the TUI (`cli.py:159`). Goes straight to DB
and services; only `cli_gateway.py:114` uses HTTP.

- **Medium:** `cli.py` is 2,736 LOC with a hand-rolled dispatcher (`:157-282`), imports private
  RunPod symbols (`_TEMPLATE_ENV_KEYS` at `:60-66`, `_safe_json` at `:48`), and has no
  business-logic boundary guard (MCP and TUI do).
- **Medium:** secret hygiene is inconsistent: `cli_burn_rate.py:96-101` suppresses exception text,
  `cost/cli.py:181` prints `str(exc)` raw, including in JSON mode.
- **Low:** docstring drift (`cost/cli.py:3-4`, `cli_burn_rate.py:81-84`); a one-off pool at
  `cli.py:744`.

### TUI

Textual dashboard covering overview, providers, leases, models, cost, hardware fit, routing and
jobs, RunPod market and resources, volume files, onboarding, serve, and personal screens. Reads
through injected adapters; `tests/tui/test_no_business_logic_guard.py` AST-forbids direct
business calls in widgets. Money formatting is Decimal and quantized. Findings: its own MTD query
(`tui/cost.py:467-482`); `tui/hardware_fit.py:162-181` opens a pool per lookup; `resources.py`
(1,152 LOC) and `routing_jobs.py` (997) are large.

### personal

Local registry-less serve mode (`serve`, `status`, `stop`, `setup`), state in
`~/.local/state/pitwall`, generated endpoint key, supervised gateway, per-lease TTL.

- **Medium:** no account-wide budget; only `max_usd_per_hour` and TTL
  (`personal/service.py:85,235-236,247`). A stray `DATABASE_URL` silently switches between
  personal and registry modes (`personal/backend.py:14`).
- **Medium:** `StateStore.upsert` (`personal/state.py:68-71`) is an unlocked read-modify-write;
  concurrent `serve` and `stop` can lose an update.
- **Low:** `personal/service.py:453` sends `float(spec.max_usd_per_hour)` to RunPod.
- Positive: key file `O_EXCL` 0600 and state directory 0700 (`keys.py:26-35`,
  `state.py:88-91`).

---

## Lane D: packages/agent-routing

| System | LOC | Tests | Grade |
|---|---|---|---|
| runtime/model_routing (40 modules) | 19,769 | ~884 across 79 files | B |
| runtime/model_routing/providers (13 adapters) | 1,855 | test_provider_adapters 43, test_shim_contract 35 | B- |
| scripts/ shims (14), launcher, parser | 20 each, 45, 56 | test_shim_contract, test_shim_receipt | A |
| scripts/bootstrap.sh, install.sh | 592 + 241 | test_bootstrap, test_installer, test_uninstall | B |
| plugins (pitwall, pitwall-codex, pitwall-copilot) | 8,759 | test_claude_tripwires 49, test_plugin_identity, test_marketplaces | B |
| prompting | 1,683 (markdown) | none | B |
| references | 14 | none | A |
| tools (validators, release) | 1,600 | run in CI | B |
| examples | 354 | test_journeys, test_client_rehearsal | B |
| tests | 22,010 | 890 run, 4 skipped | A- |

The project uses unittest; pytest is not installed in its `.venv`.

### Boundary and standard-library contract

- Standard-library-only: **passes.** `pyproject.toml` has `dependencies = []`; every runtime
  import is stdlib. The only exception is `hatchling` in `runtime/model_routing/_hatch_build.py:7`,
  a build hook excluded from the wheel.
- Project boundary: **clean.** The only coupling to root Pitwall is HTTP (`pitwall.py`,
  `pitwall_sync.py:625` via `urllib`, configured by `PITWALL_API_URL` and
  `PITWALL_AGENT_ROUTING_API_TOKEN`). No Python import of the root package.

### runtime/model_routing

Shared runtime for dispatch, run store, mailbox, the channel MCP server, the workflow scheduler,
routes, `doctor`, and provider setup. Largest files: `doctor.py` 2,668, `cli.py` 1,561,
`dispatch.py` 1,410, `scheduler.py` 1,290, `managed_channel.py` 1,202, `provider_setup.py` 1,193.

- **Medium:** `_dispatch_legacy` is 573 lines (`dispatch.py:688-1260`); also long:
  `doctor._check_provider_auth_probe` (243 lines, `doctor.py:993`), `cli.build_parser` (165,
  `cli.py:1275`).
- **Medium:** an invalid or non-positive `SHIM_TIMEOUT_SECS` becomes a 1 ms timeout with no usage
  error (`dispatch.py:876-881`; verified).
- **Medium:** GNU `timeout`/`gtimeout` is a dead prerequisite. Dispatch fails with exit 127 when
  neither is found (`dispatch.py:802-806`; verified), but nothing executes them; the timeout is
  enforced in Python (`process.py:217,305`). Documented at `README.md:27`,
  `docs/architecture.md:91`, `docs/migration-v0.3.md:8`.
- **Low:** `doctor.py:370-375` imports `tools.sync_routes` via a `sys.path` hack; safe because it
  runs only in source mode (`doctor.py:2397-2406`).
- **Low:** subprocess use is sound: argv arrays only, no `shell=True`, `os.system`, `eval`, or
  `exec`; children get their own process group (`process.py:176-186,254-264`). `route_sync.py:141`
  sets no explicit env or stdio.
- **Low:** every child inherits the full parent environment (`process.py:181,259`); only
  dispatcher-identity variables are stripped (`dispatch.py:69-75`).

### Provider adapters

One adapter per harness; `providers/base.py` is the contract and `providers/__init__.py` the
registry. Prompt delivery varies by harness: stdin (codex, goose, opencode), file (muse), argv
(the rest).

- **Medium:** `SUBAGENT_MODEL_ROUTING_UNRESTRICTED` is ignored by `kimi.py:83-94` and
  `dsh.py:46-52` (verified). Every other adapter maps it to a harness flag (for example codex
  `--dangerously-bypass-approvals-and-sandbox` at `codex.py:67-68`, grok `--always-approve` at
  `grok.py:53`, qwen `--yolo` at `qwen.py:113`, opencode via preflight at `opencode.py:145-177`).
  `base.py:107` defaults it on.
- **Low:** `workflow_supported` is true for 6 of 13 adapters (claude, codex, grok, kimi, opencode,
  pi), which limits DAG workflows to that subset.
- **Low:** each adapter re-implements `-m`/`--model=` parsing (`codex.py:35-46`, `kimi.py:55-67`,
  `muse.py:33-38`); muse handles only `--model`.
- **Low:** `sanitize_args` (`base.py:158-177`) redacts only dash-prefixed flags with sensitive
  names, so positional secrets are recorded.
- **Low:** kimi, grok, dsh, and pi put the full prompt in argv (`kimi.py:87`, `grok.py:58`,
  `dsh.py:51`), visible to `ps` and subject to `ARG_MAX`.

### Shims and launcher

Each `*-shim.sh` is a 20-line `exec pitwall-agent-routing _shim <id> "$@"`. Twelve are
byte-identical to `codex-shim.sh` after name substitution; `pi-shim.sh` and `route-shim.sh` differ
only in naming and one header comment (`route-shim.sh:2`). All emit `SHIM-DONE exit=127` when the
runtime is missing. `parse-shim-result.py` reads only the last two lines (`:37-43`).
`bootstrap.sh:64` uses `eval` only with literal names (`:70`); `rm -rf --` targets are quoted and
scoped to a `mktemp` transaction directory (`:371,375,428,482`).

### provider_setup

- **Medium:** 8 of 20 installer recipes in `resources/config/provider-installers.json` have
  `sha256: null`; `download_installer` skips the hash check (`provider_setup.py:806`) and warns
  (`:744-747`), and the script still executes via `run_installer` (`:818`).
- Positive: redirects limited to approved hosts (`:769-786`), size and time caps and a shebang
  requirement (`:797`), constant-time comparison, argv execution with a timeout (`:816-832`).

### Plugins, prompting, references, examples, tools

- Three host packages; all manifests at 0.12.0, matching `pyproject.toml`. `steer-gate.py` is
  deliberately duplicated between `plugins/pitwall/hooks/` and `plugins/pitwall-codex/hooks/`;
  generated references are triplicated and guarded by `tools/check_generated.py`.
- **Low:** the steer-gate hook fails open on a missing CLI, timeout, or error
  (`plugins/pitwall/hooks/steer-gate.py:14-21`), as documented.
- **Low:** large hook scripts (`launch-guard.py` 791 lines, `dag-tripwire.py` 440,
  `ledger-tripwire.py` 319), stdlib-only and tested.
- The legacy `SUBAGENT_MODEL_ROUTING_*` names are frozen as public API (`CHANGELOG.md:9`);
  `PITWALL_API_TOKEN` remains a legacy alias (`pitwall.py:14-15,52-60`).

---

## Lane E: TypeScript packages

| Package / module | LOC | Tests | Grade |
|---|---|---|---|
| gateway `src/` | ~1,660 (`shim.ts` 1,131) | 132 pass, 11 files | B |
| gateway `open-sse/` (8 vendored files) | 3,335 | none (excluded, `vitest.config.ts:6`) | B- |
| gateway `scripts/` | 273 | launcher test only | B |
| pi-workbench `src/` | ~4,580 | 284 (1 flaky) | B |
| pi-workbench `scripts/` | ~1,500 | partial | C+ |

Both packages gitignore `dist/`. Running the gateway tests rebuilds `dist/` through
`tests/launcher.test.ts:8`; the directory is ignored and `git status` stayed clean.

### packages/gateway

A hardened loopback HTTP shim over a stripped OmniRoute subset (MIT). `UPSTREAM.lock:1-3` pins
`v3.8.51`; the eight kept files (`UPSTREAM.lock:5-13`) all pass `sha256sum -c`, so the vendored
code is unmodified. The shim is local code and uses only `buildErrorBody` from upstream
(`src/errors.ts:11-19`). Five routes (`route()` at `shim.ts:1030`). No runtime dependencies.
`tsconfig.json:9-16` is strict with unused and implicit-return checks. Integrated via
`src/pitwall/personal/gateway.py:24,59-64,102-111` (spawns `node dist/src/shim.js` on port 20130),
`src/pitwall/providers/gateway.py:44-46` (route and compression headers), and the catalog pipeline
(`gateway_catalog/sync.py`, `tools/gateway/*`, generating `config/gateway-catalog.json` 15,714
lines, `config/gateway-routes.json`, `seed/gateway-providers.yaml` 13,513 lines). CI:
`gateway-ci.yml` and the `gateway-catalog-drift` job in `ci.yml:151`.

- **High: SSE truncated at 30 s and ended cleanly** (verified). `DEFAULT_UPSTREAM_TIMEOUT_MS =
  30_000` (`config.ts:24`) arms a timer (`shim.ts:401-402`) cleared only in `finally`
  (`:442-443`). On abort, `relayUpstreamStream` exits its loop and calls `res.end()` (`:588`), so
  the client sees a well-formed but truncated stream; the `res.destroy()` branch (`:437`) is not
  reached. No test covers a mid-stream timeout; `README.md:71` calls it only a "30 s upstream
  deadline".
- **Medium:** 500 bodies carry `err.message` (`shim.ts:421-424`, `:975`), relying on
  `buildErrorBody`'s sanitiser (`open-sse/utils/error.ts:304-318`).
- **Medium:** `README.md:17,68-73` describes merging executor ids with upstream `/models`; with a
  route table loaded, `shim.ts:683-694` returns route names and executor ids without contacting
  upstream.
- **Medium:** `registerExecutor` and `clearExecutors` (`shim.ts:62,66`) have only test callers but
  feed `/v1/models` (`:684`) and telemetry `executor_count` (`:912`).
- **Low:** bearer comparison is not constant-time and is copy-pasted four times
  (`shim.ts:241,671,871,894`).
- **Low:** the rate limiter is a module-level singleton (`shim.ts:95`) applied only to inference
  routes.
- **Low:** the 413 message hardcodes "1 MiB" (`shim.ts:273,296`); `readBodyCapBytes` and
  `readRateLimitRpm` return constants; a rethrow-only catch (`:952-957`); `new URL` on a malformed
  Host header (`:1036`) becomes a generic 500; non-SSE bodies are buffered uncapped (`:560`).
- **Low:** `scripts/STRIP-NOTES.md` still describes files removed in 0.2.0.

### packages/pi-workbench

Launches stock `@earendil-works/pi-coding-agent@0.84.4` with an exact provider and model profile
in an isolated `PI_CODING_AGENT_DIR`, with opt-in scout, writer, and reviewer roles via
`@tintinweb/pi-subagents@0.19.0`, a read-only planning mode, a Linux `--restricted` mode
(bubblewrap plus seccomp), and a handoff workspace (`src/workspace.ts:58-379`). Apache-2.0.
Nothing in `src/` or `packages/agent-routing/` consumes it; references are limited to release
tooling (`tools/release_acceptance/node_inventory.py:75-85`, `test_index.py:78`,
`scripts/release/run_bound_tests.py:25`), CI (`.github/workflows/ci.yml:472`), and docs.
`docs/sdlc/25-pi-workbench.md:3-6` describes it as deliberately separate from Agent Routing.

- **Medium: flaky test.** `tests/workspace.test.ts:271` failed in two of three full-suite runs
  with `ENOENT … timeout.pid`; it passes 24/24 alone. The 150 ms timeout can kill the check before
  the nested process writes its pid file; the abort half of the same test already waits with
  `waitForFile`.
- **Medium:** approved checks spawn with the full parent environment (`workspace.ts:305`), unlike
  restricted tool children (`restricted.ts:33-41`).
- **Medium:** `tsconfig.json:6` sets only `strict`; 16 `any` uses in `src/`; about 35 bare
  `catch {}` blocks.
- **Low:** very long single-line statements (for example `restricted.ts:11-12`,
  `workspace.test.ts:257-262`).
- **Low:** `scripts/two-tui-native.mts` has no references.
- **Low:** the 0.84.4 pin is repeated across the README, `docs/source-lock.md`, and
  `docs/sdlc/25-pi-workbench.md`.
- Positive: restricted mode refuses unrestricted fallback (`restricted.ts:56-66`); the seccomp
  filter denies socket syscalls, accept4, recvmmsg, sendmmsg, and io_uring and rejects x32
  (`:70-73`); sensitive paths are hidden behind tmpfs (`:12-14`). Provider secrets use
  `timingSafeEqual`, 0600 `wx` profile writes, and response-model checks
  (`provider-extension.ts:1,24-34,64-80`).

### Cross-package

Toolchain versions differ: vitest 5.0.1 vs 4.1.9, TypeScript `^5.7.2` vs pinned 5.9.3, Node
`>=22` vs `>=22.22.1`; gateway CI runs Node 22.12.0.

---

## Lane F: engineering-process systems

Lane F read `ci.yml` except the container-build, compose, SAST, secrets, and fuzz steps (about
lines 175-285, grepped only), read `release-readiness.yml` with comments filtered, read
`release.yml` as job and `run:` lines, and did not open `gateway-ci.yml`, `agent-routing-ci.yml`,
`agent-routing-release.yml`, or `gateway-release.yml`.

| System | Size | Invoked by | Grade |
|---|---|---|---|
| `tools/release_acceptance/` (20 modules) | 14,646 LOC | humans, `run_bound_tests.py`, tests; no workflow | C |
| `release_acceptance/` data and docs | 17 files, 29,822 lines (20,918 in `reviewed-bindings.json`) | the tooling above | C |
| `scripts/release/`, `install_acceptance.py`, `mutmut_score_gate.py` | 14 files, 2,196 lines | `release.yml`, agent-routing workflows, `Makefile`, humans | B |
| `tools/ci/` | 6 scripts, 574 lines | `ci.yml`, `release-readiness.yml`, `Makefile` | A |
| `tools/security/` | 259 lines | `ci.yml`, `release-readiness.yml`, `Makefile`, pre-commit | A |
| `tools/guards/` | 5 scripts, 772 lines | pre-commit (all), CI (two) | C |
| `tools/gateway/` | 467 lines | `ci.yml` (drift check) | B |
| `tools/models/convert_research_dossier.py` | 277 lines | human, one-off | B |
| `tools/engines/smoke_launch_shape.py` | 788 lines | `make engine-smoke*` | B |
| `tools/smoke_*` (6), `tools/benchmark_embedding_latency` | 1,088 lines | nothing | D |
| `.github/workflows/` (7 workflow files plus `actionlint.yaml`), `Makefile` | 1,747 + 60 lines | GitHub, humans | C |
| tests/release, tests/release_acceptance, tests/tools | 69 / 43 / 13 files | `ci.yml` test job; `release-readiness.yml` for `-m release` | B |
| `qa/` | 101 files, 8,892 lines | the beginner tester | B |
| `docs/` | 182 files, 89,127 lines | `docs` job link check | B |

### Release acceptance

Discovers every product surface (CLI, REST, MCP, TUI, config, jobs, provider ops, node), binds each
to a test, and records run evidence. Entry points: `tools/release_acceptance/matrix.py` (1,458),
`evidence.py` (1,537), `tui_inventory.py` (1,468), `test_index.py` (1,403);
`scripts/release/run_bound_tests.py`, called from `run-user-journeys.sh:774`.

- **High: no workflow runs it** (verified). The matrix gate lives only in
  `scripts/release/run-user-journeys.sh:765-780`, reached through `run-alpha-readiness.sh` by a
  human.
- **High: CI drops harness-only journeys** (verified). `tests/release/conftest.py:33-37`
  deselects `journey_harness` tests unless `PITWALL_JOURNEY_HARNESS=1`, which only
  `run-user-journeys.sh:40` and `run_bound_tests.py:52` set. `release-readiness.yml:138` therefore
  skips `test_matrix_complete` and the REST, CLI, and MCP all-operations journeys
  (`tests/release/test_rest_all_operations_journey.py:33`, `test_cli_all_commands_journey.py:33`,
  `test_mcp_all_tools_journey.py:27`, `test_matrix_complete.py:29`). The deselection is pinned by
  `tests/test_release_scripts.py:107` and is intentional because these journeys reset the shared
  database.
- **Medium:** `RELEASING.md` does not mention acceptance or journeys. The last recorded run is
  `docs/release/acceptance-packet-2026-09-24.md:23` (42 passed; 1,270 surfaces, 1,483 bound cases),
  a snapshot rather than a gate.
- **Medium: proportionality.** About 62k lines (14,646 tooling, 29,822 data, 18,184 tests) against
  about 105,800 lines of `src/`. Hash-pinned bindings go stale on every source edit;
  `release_acceptance/MATRIX.md:8-10` itself says "NOT RELEASE PROOF".

### scripts and tools/ci

`scripts/release/` validators and smoke scripts are called from `release.yml` and the
agent-routing workflows and are tested. `scripts/install_acceptance.py` is tested but not in CI.
`scripts/mutmut_score_gate.py` backs `make mutation-gate` and has no test. All six `tools/ci`
scripts run in CI and have tests.

### tools/guards

- **Medium:** `forbidden_imports.py` and `python_policy.py` run only in pre-commit
  (`.pre-commit-config.yaml:13,43`), and no workflow runs pre-commit. `forbidden_imports.py`
  bans `uio` and `pipeline_cost`, which no longer appear anywhere in `src`.
- `dep_pin_checker.py` runs in `release-readiness.yml` and is tested; `repo_text_policy.py` runs
  in `ci.yml` and `release-readiness.yml`, with its `.local` pattern file gitignored
  (`.gitignore:49`).

### tools/smoke_* and benchmark

- **Medium:** six `smoke_*` drills and `benchmark_embedding_latency` (1,088 lines) have no
  invoker in docs, tests, workflows, `Makefile`, or `qa/`. Several need live RunPod credentials;
  `smoke_model_council` documents bare `python` usage. Their function is covered by
  `tests/admin/test_kill_switch*.py` (the "Kill-switch drill" step) and `docs/evidence/`.

### tools/gateway, tools/models, tools/engines, tools/security

`check_catalog_drift.py` runs in CI; `sync_catalog.py`, `bench_free_pools.py`,
`extract_upstream.mjs` are maintainer tools with tests or docs. `convert_research_dossier.py`
is a tested one-off (`docs/research/2026-08-27-serve-model/README.md:5`).
`smoke_launch_shape.py` runs via `make engine-smoke` and is tested, not in CI. Security tooling
(bandit, pip-audit, semgrep, licenses, secrets) runs in `ci.yml` and again in
`release-readiness.yml`.

### Workflows and Makefile

- **Medium:** `release-readiness.yml` is a near-copy of `ci.yml` (ruff, format, `mypy --strict`,
  coverage, security, openapi, integration), and `release.yml:16-17` calls it on every tag, re-running
  work the tagged commit already passed.
- **Medium:** inside `ci.yml`, the `test` job runs the hermetic suite at a 74% floor,
  `coverage-combined` (`:419`) reruns hermetic plus integration at 77% (schedule-only), and
  `integration` runs the integration suite again.
- **Medium:** the required `CI` aggregate (`ci.yml:504-519`) omits `gateway-catalog-drift`
  (verified); `coverage-combined`, `dependency-compatibility`, and `mutation-smoke` are
  schedule-only by design.
- **Low:** `mut` and `mutation` are duplicate targets (`Makefile:41-42`); the `pi-workbench` job
  (`ci.yml:472`) depends on `ubuntu-26.04` with `apt-get` and `sysctl` changes.
- ADR 0003 (`docs/decisions/0003-github-only-ci.md:1`) says GitHub Actions is the sole CI, and
  the workflows match.

### qa

Beginner-tester onboarding: `bootcamp/` (7), `concepts/` (31), `coach/` (4), `handbook/` (11),
`missions/` tier 1 to 5 (37), `templates/` (6). Every CLI command it uses exists in
`uv run pitwall --help` (flags not checked); the link checker passes over 420 files.

### docs

`sdlc/` (27 files, 12,458 lines, per-subsystem design), `operator/` (21, runbooks),
`decisions/` (ADRs 0001 to 0007), `evidence/` (dated run records), `release/` and `releases/`,
`research/` (42) and `models/` (25), `superpowers/` (14 files, 33,170 lines of plans and specs,
larger than all of `sdlc/`), and `api/openapi-baseline.json` (21,376 lines). The internal link
check passes. `docs/sdlc/03-mcp-server.md:595` uses an ambiguous bare path
(`tools/runpod_resources.py` for `src/pitwall/mcp/tools/runpod_resources.py`). See Lane A for
stale `file:line` references.
