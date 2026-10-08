# Codebase Review Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix every verified finding from the 2026-09-28 read-only codebase review (23 findings against `feat/model-studio` @ `f59e971`), starting with the live false-green on a breached budget.

**Architecture:** Each task fixes one finding at its cause, test first, one commit per task, on `fix/review-remediation-0928` (from `feat/broker-agent-gaps` @ `1a9c91b`, the code the local broker runs). Money paths converge on one month-to-date definition; the kill switch latches before it destroys; personal mode gains a local budget ledger and audit log.

**Tech Stack:** Python 3.14.7, asyncpg, arq, FastAPI, pytest (`pg_pool` integration fixture), TypeScript (gateway), Docker Compose (`pitwall-services`).

**Spec:** the review text (pasted 2026-09-28) plus the verification below. Decisions from the maintainer (2026-09-28): delete the two dead modules and keep the seven `tools/` scripts (renamed and documented); coalesce anonymous requests only when deterministic; add real controls to personal mode.

## Verification of the review (done before planning)

| # | Verdict | Evidence |
| --- | --- | --- |
| 1 | Confirmed live | gate MTD 18.604135; `cost_daily` MTD 0.309221; 11 workloads with no capability/provider row hold 18.294914; no `runpod_direct` capability row; `pitwall doctor` → `spend.burn_rate ok`, `spend.budget ok` |
| 2 | Confirmed | `api/leases/teardown.py` disarm writes `health_status="unhealthy"`; `fetch_providers_for_health_probe` covers only `serverless_lb`/`public_endpoint` |
| 3 | Partly | `core/inference.py` (439 lines, false docstring) and `core/jobs.py` (73) have no `src/` callers; `cost/sync_gate.py` is live (`api/leases/launch.py` imports `estimate_cost`) |
| 4 | Confirmed in code | no reaper for non-terminal workloads without `runpod_job_id`; none stuck live today |
| 5 | Confirmed | six month-to-date queries (`budget_gate`, `alerts`, `threshold_alerts`, `exporter` ×2, `tui/cost`) with different columns and state sets |
| 6 | Confirmed | `cost/alerts.py` `redis_client.exists(alert_key)` not awaited; the reconciler passes the async arq client; the call is wrapped in `suppress(Exception)` |
| 7 | Confirmed by reading | retention cutoff is a timestamp; the rollup recomputes the straddling day from survivors |
| 8 | Confirmed | `retention/archive.py` deletes objects before `_delete_related` and before commit |
| 9 | Confirmed | `leases.cost_accrued_usd` has no CHECK; `volumes.monthly_cost_usd NUMERIC(10,2)` |
| 10 | Confirmed | `idx_workloads_month_spend` is partial on `state IN (queued,running,completed)`; the gate query has no state predicate |
| 11 | Half right | ordering confirmed (`emergency.run_kill` writes `kill_log`, the latch, only after `activate()`); the breach path goes through `run_kill`, so it does not bypass the latch |
| 12 | By design, now changed | docstring: "Anonymous requests coalesce only by content"; decision: deterministic only |
| 13 | Already fixed | `feat/broker-agent-gaps` removed the keyed-skip pre-lock check; the in-lock cap applies to all admissions |
| 14 | To verify in Task 11 | replay validation without an idempotency key |
| 15 | Confirmed; payload scan not applicable | personal mode has a per-hour price cap and per-lease max spend but no monthly total or audit; harnesses talk to the pod directly, so no request payload passes through Pitwall to scan |
| 16 | Confirmed | `packages/gateway/src/shim.ts` relays non-stream upstream bodies byte for byte |
| 17 | Confirmed, rarer than stated | arq `unique` stops duplicate enqueue, not overlap of a tick that runs past a minute; `_health_probe` writes absolute counters |
| 18 | No change | plans are the per-batch record by design |
| 19 | Confirmed | `SECURITY.md` "No public release has been declared yet" vs tags `v0.1.0a1`, `v0.1.0a2` |
| 20 | Confirmed | `.env.example` lacks `POSTGRES_PASSWORD`/`REDIS_PASSWORD`, which `docker-compose.yml` requires |
| 21 | Confirmed | seven extension-less live scripts in `tools/`, unreferenced |
| 22 | Intentional | versions are staged ahead of tags for the single-commit public release |
| 23 | Confirmed | `AGENTS.md`, `.superpowers/`, `.lanes/`, `.lane-report/` are excluded only via `.git/info/exclude` |

## Global Constraints

- `uv run` only; pipe test output through `| tail -40`; one commit per task.
- DB suites share `pitwall_test` on 127.0.0.1:5444: never `make up`/`make down`, never two DB-backed runs at once. Integration: `PITWALL_TEST_DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test PITWALL_TEST_REDIS_URL=redis://127.0.0.1:6380/0 uv run --frozen pytest -m "integration and not live" <paths>`.
- Every failure met while working is root-caused and fixed (memory `root-cause-every-failure`): capture failing test names to a log; reproduce flakes under load with fixed seeds.
- Run `ruff format --check .`, the text-policy guard over all tracked files, and `check_secrets.py` before every commit that touches those areas; CI enforces all three.
- Next migration number: `0037`.

## Review Focus

1. Raw-pod workloads (`runpod_direct`) appear in `cost_daily` and in doctor's spend figure (Task 1).
2. A kill switch whose destructive step raises still leaves admission closed (Task 5).
3. A sampled chat request from two callers never shares one provider call (Task 10).
4. Personal mode refuses a lease that would push the month past the budget, and records every launch and stop (Task 12).
5. An upstream error body that echoes an `Authorization` header never reaches operator logs unredacted (Task 13).

---

### Task 1: Raw-pod workloads count in `cost_daily` (#1)

**Files:** `src/pitwall/reconciler/cost_daily_rollup.py` (`_AGGREGATE_DAILY_SQL`); test `tests/integration/test_cost_daily_raw_pods.py`.

- [ ] Failing integration test: insert a terminal workload with `capability_id = provider_id = 'runpod_direct'` and `cost_actual_usd = 1.5`, run the rollup, assert a `cost_daily` row `('raw_pod', 'runpod_direct', 1, 1.5)` for its day, and that registered workloads keep their existing grain.
- [ ] Change both joins to `LEFT JOIN` and select `COALESCE(c.class, 'raw_pod')`, `COALESCE(p.provider_type, w.provider_id)` in the SELECT and GROUP BY.
- [ ] Run the rollup tests and `tests/reconciler`; commit `fix(cost): count raw-pod workloads in the daily cost rollup`.

### Task 2: Doctor stops reporting green on a breached or unmeasured budget (#1 root signal)

**Files:** `src/pitwall/doctor.py` (spend checks around line 629 and the burn-rate check), `src/pitwall/finops/burn_rate.py` (`BurnRateRead`); tests `tests/test_doctor_spend.py`.

- [ ] Failing tests: (a) `spend.budget` is FAIL when gate month-to-date spend ≥ the effective monthly budget and WARN at ≥ 80%; (b) `spend.burn_rate` carries `stale` and `data_sufficiency` in its details and is WARN when stale or `sparse`; (c) `spend.burn_rate` is WARN when rollup month-to-date differs from gate month-to-date by more than 1% or 0.01 USD, naming both figures.
- [ ] Implement with the effective limits (`cost.budget_limits.effective_limits`) and the gate's month-to-date query (Task 3's shared function).
- [ ] Commit `fix(doctor): budget and burn-rate checks read the gate's spend and flag stale or sparse data`.

### Task 3: One month-to-date spend definition and an index that serves it (#5, #10)

**Files:** `src/pitwall/cost/budget_gate.py` (add `async month_to_date_spend(conn) -> Decimal` next to `MONTH_TO_DATE_SPEND_SQL`), `cost/alerts.py`, `cost/threshold_alerts.py`, `cost/exporter.py` (both queries), `tui/cost.py`; `db/migrations/0037_workloads_month_spend_index.sql`; tests `tests/cost/test_month_to_date_single_definition.py`, `tests/db/test_workloads_month_spend_index_migration.py`.

- [ ] Failing test: a static check that every month-to-date spend query in `src/` is `MONTH_TO_DATE_SPEND_SQL` (grep for `date_trunc('month'` over `pitwall.workloads` outside `budget_gate.py` finds none), plus a unit test per caller that it reports the gate's figure for one fixture.
- [ ] Route every caller through `month_to_date_spend`; exporter per-class breakdowns keep their grouping but use the same cost expression (`COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)`) and no state filter.
- [ ] Migration 0037: `CREATE INDEX IF NOT EXISTS idx_workloads_submitted_at ON pitwall.workloads (submitted_at);` and drop the unusable partial `idx_workloads_month_spend`; migration test asserts the index exists and the old one is gone; update the latest-migration pin.
- [ ] Commit `fix(cost): one month-to-date spend definition, served by an index`.

### Task 4: The 80% budget alert sends (#6)

**Files:** `src/pitwall/cost/alerts.py` (`check_and_send_budget_alert`), `src/pitwall/reconciler/__init__.py` (`_rollup_job` hook); tests `tests/cost/test_budget_alerts.py`.

- [ ] Failing test with an async Redis fake (`exists`/`set` are coroutines) and spend at 85%: the notification is sent once and the dedupe key is set; a second call is skipped as duplicate.
- [ ] `already_sent = await redis_client.exists(alert_key)` and await the key write; update tests that used a sync fake to the async client the reconciler really passes.
- [ ] Replace `suppress(Exception)` in `_rollup_job`'s hook with a logged, redacted warning per alert.
- [ ] Commit `fix(cost): await the budget-alert dedupe check so the 80% alert can send`.

### Task 5: The kill switch latches before it destroys (#11)

**Files:** `src/pitwall/api/admin/emergency.py` (`run_kill`), `src/pitwall/db` kill-log writer (`persist_kill_report`); tests `tests/api/test_kill_switch_latch_order.py` (+ integration).

- [ ] Failing test: `CloudKillSwitch.activate` raises; afterwards `enforce_kill_switch_admission` raises `kill_switch_engaged`, and the kill-log row records the error.
- [ ] Write the latch row first (reason, actor, `pods_terminated = 0`, errors `["kill in progress"]`), then run `activate()`, then update that row with the report; on exception update it with the redacted error and re-raise.
- [ ] Commit `fix(kill-switch): latch admission closed before any destructive step`.

### Task 6: Provider health separates "disarmed" from "failing"; probe writes are atomic (#2, #17)

**Files:** `src/pitwall/api/leases/teardown.py` (disarm write), provider health CHECK (migration 0037 if the constraint lists values), `src/pitwall/reconciler/__init__.py` (`_health_probe` write, `fetch_providers_for_health_probe`), `src/pitwall/doctor.py` (provider health count); tests `tests/api/test_teardown_disarm_health.py`, `tests/reconciler/test_health_probe_atomic.py`.

- [ ] Failing tests: disarming a pod-lease provider leaves `consecutive_failures`/`cooldown_trips` untouched and sets a distinct `disarmed` status; arming sets `healthy`; doctor counts only enabled, probe-capable, armed providers when judging health; two overlapping probe results increment `consecutive_failures` by two (compare-and-set or single-statement increment).
- [ ] Implement; routing treats `disarmed` like `unhealthy` for selection.
- [ ] Commit `fix(providers): disarmed is not unhealthy, and health-probe counters update atomically`.

### Task 7: Reap workloads stuck non-terminal without a job id (#4)

**Files:** `src/pitwall/reconciler/__init__.py` (new `_reap_orphaned_workloads` cron); tests `tests/reconciler/test_reap_orphaned_workloads.py`.

- [ ] Failing test: a `queued`/`running` workload with no `runpod_job_id`, no open lease, older than its capability's execution timeout plus one hour, becomes `timed_out` with `cost_actual_usd = cost_ceiling_usd` and provenance `reaped_unfinished`; younger ones and lease-linked ones are untouched.
- [ ] Implement as a five-minute cron; the rollup then counts them (it only counts terminal states).
- [ ] Commit `fix(reconciler): reap workloads left non-terminal with no job id`.

### Task 8: Retention keeps whole days and deletes objects only after commit (#7, #8)

**Files:** `src/pitwall/retention/archive.py`; tests in `tests/retention/`.

- [ ] Failing tests: (a) with a cutoff mid-day, the rollup after purge still reports the straddling day's full pre-purge total; (b) when the DB transaction rolls back, `object_delete` was never called; when commit succeeds, it is called with the archived keys.
- [ ] Floor the cutoff to UTC midnight; delete rows inside the transaction, commit, then delete objects; record pending object keys in the run manifest and retry them on the next run when deletion fails.
- [ ] Commit `fix(retention): purge whole UTC days and delete objects after the database commits`.

### Task 9: Money columns are non-negative with full precision (#9)

**Files:** migration 0037 (same file as Task 3, separate statements); test `tests/db/test_money_column_constraints.py`.

- [ ] Verify live data complies first (`SELECT count(*) FROM pitwall.leases WHERE cost_accrued_usd < 0`; volumes with fractional cents).
- [ ] `ALTER TABLE pitwall.leases ADD CONSTRAINT leases_cost_accrued_nonnegative CHECK (cost_accrued_usd IS NULL OR cost_accrued_usd >= 0)`; `ALTER TABLE pitwall.volumes ALTER COLUMN monthly_cost_usd TYPE NUMERIC(12,6)`.
- [ ] Integration test: a negative accrued cost is refused; a 6-dp volume cost round-trips.
- [ ] Commit `fix(db): non-negative lease accrual and 6-dp volume costs`.

### Task 10: Anonymous requests coalesce only when deterministic (#12)

**Files:** `src/pitwall/routing/coalescing.py` (`build_inference_coalescing_key`), its caller in `api/routes/inference.py`; tests `tests/routing/test_coalescing_determinism.py`.

- [ ] Failing tests: two anonymous chat requests with no `temperature` (sampling default) get distinct keys; with `temperature: 0` and no `n > 1` they share one; embedding/rerank capabilities share by content; keyed requests are unchanged.
- [ ] The key builder takes the capability class; anonymous non-deterministic requests get a per-request nonce key.
- [ ] Commit `fix(routing): coalesce anonymous requests only when the answer cannot differ`.

### Task 11: Replay validation without an idempotency key (#14)

**Files:** located while verifying (`routing/production.py` admission/replay path); test beside it.

- [ ] Read the pre-flight/replay path; reproduce the race the review describes in a test (the pre-flight loses the race; the loaded workload is not validated). If it reproduces, validate the loaded workload's capability, provider, and payload digest against the request before returning it; if it cannot occur, record why in this plan's continuation section with the code reference.
- [ ] Commit `fix(routing): validate a raced replay the same way as the pre-flight` (or the continuation note).

### Task 12: Personal mode enforces a monthly budget and keeps an audit log (#15)

**Files:** `src/pitwall/personal/service.py` (`serve`, `stop`), `src/pitwall/personal/state.py` (ledger and audit documents), docs `README.md` Quick Start and `SECURITY.md`; tests `tests/personal/test_personal_budget_and_audit.py`.

- [ ] Failing tests: `serve` refuses with `monthly_budget` when this month's recorded spend plus the new lease's `max_spend_usd` exceeds `PITWALL_MONTHLY_BUDGET_USD`, and with `per_request_cap` when `max_spend_usd` exceeds `PITWALL_PER_REQUEST_MAX_USD`; refuses with `budget_not_configured` when the monthly budget is unset; every serve and stop appends an audit record (`ts`, `action`, `route`, `pod_id`, `max_spend_usd` or `accrued_usd`, `reason`) to `audit.jsonl` in the state root; stop records accrued cost into the month's ledger.
- [ ] Implement with the local `StateStore` (no Postgres). Payload scanning: no request payload passes through Pitwall in personal mode (harnesses call the pod directly); document this in `SECURITY.md` and the Quick Start instead of adding a scanner with nothing to scan.
- [ ] Commit `feat(personal): a local monthly budget and audit log for personal serving`.

### Task 13: The gateway redacts upstream error bodies (#16)

**Files:** `packages/gateway/src/shim.ts` (non-stream relay near line 559); tests in `packages/gateway/test/`.

- [ ] Failing test: an upstream 401 whose body echoes `Authorization: Bearer <key>` and a provider key relays with both redacted by the shim's existing credential patterns; 2xx bodies are unchanged.
- [ ] Apply the redaction to non-stream bodies with status ≥ 400.
- [ ] Commit `fix(gateway): redact credentials in relayed upstream error bodies`.

### Task 14: Delete the dead inference modules; name and document the drill scripts (#3, #21)

**Files:** delete `src/pitwall/core/inference.py`, `src/pitwall/core/jobs.py` and their tests (`grep -rln "pitwall.core.inference\|pitwall.core.jobs" tests`); remove `core/__init__.py`'s `jobs` re-export; rename the seven `tools/` scripts to `.py`/`.sh`; document them in `docs/operator/live-drills.md` as manual, money-spending drills.
- [ ] Commit `refactor: remove the unused core inference path; name and document the live drill scripts`.

### Task 15: Hygiene (#19, #20, #23)

- [ ] `SECURITY.md`: state the published alpha tags and what they cover.
- [ ] `.env.example`: `POSTGRES_PASSWORD=` and `REDIS_PASSWORD=` with "required: a strong unique value" comments.
- [ ] `.gitignore`: `AGENTS.md`, `.superpowers/`, `.lanes/`, `.lane-report/`.
- [ ] Commit `chore: security status, required compose secrets, and local-only ignore patterns`.

### Task 16: Docs, changelog, release acceptance, full gates

- [ ] Changelog entries; regenerate the OpenAPI baseline if any schema changed; `bind_surfaces` (0 unmapped); release acceptance; `make test-fast`; integration suite; release tier; `ruff format --check .`; text policy; secrets.

### Task 17: Deploy and verify live

- [ ] Stage the release, rebuild, roll with `pitwall-services`; confirm migration 0037 applied.
- [ ] Live checks: `cost_daily` month-to-date equals the gate's figure within rounding; `pitwall doctor` reports `spend.budget` FAIL at 124% and `spend.burn_rate` with its data-sufficiency field; the 80% alert state; evidence doc `docs/evidence/2026-09-28-review-remediation-live.md`.

## Continuation: fixes found while executing

- Task 6: the OpenAI proxy's upstream-outcome write had the same lost-update race as the probe;
  both now compare-and-set on `updated_at`. Migration numbers shifted: 0037 index, 0038 disarmed
  reclassification, 0039 money columns.
- Task 6: the TPM soak fake lacked `effective_limits` (failing since the runtime budget limits
  work on the base branch); fixed with the other stale fakes.
- Task 8: flooring the cutoff alone could not stop a batch-split day from shrinking, so the daily
  rollup also treats days before the latest purge cutoff as final.
- Task 11: the race reproduced in both the sync and async branches; the legacy
  `gate_sync_inference` replay (reservation hash never ran) and provisioning replays (no request
  check at all) were fixed in the same commit.
- Task 13: the gateway re-review moved two discovery-review entries (`525dbc9`).
- Task 15: the two `.env.example` passwords became config surfaces; bound through compose-kind
  fixtures that prove reach and refusal (`5c8781b`).
- Task 16: the full tiers caught stale property-test calls and two journey fakes (`9830867`).
  Gates: `make test-fast` 6603 passed; `make test-int` 193 passed; `pytest -m release` 420
  passed; gateway `npm test` 137 passed; release acceptance 562 passed; docs, OpenAPI, ruff,
  mypy, text policy, and secrets clean.
