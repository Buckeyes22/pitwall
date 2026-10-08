# Independent review of the retained backlog merge (2026-09-02)

This document records an independent review of the work merged as
[PR #30](https://github.com/Buckeyes22/pitwall/pull/30) and
[PR #32](https://github.com/Buckeyes22/pitwall/pull/32), whose completion report lives in
[2026-09-01-retained-backlog-completion.md](2026-09-01-retained-backlog-completion.md). The
reviewed range is `c1a39e3..87437b7` on `main` (335 files, roughly 70k insertions and 5k
deletions). Nothing in the repository was changed by this review; it is a findings register for
the clean-up that must happen before the work is treated as released.

Every item below was checked against the checked-out source at `87437b7`, not against the
completion report. Where a claim could not be checked from the repository, it is listed as such.

## 1. Completion-report claims

### Verified as claimed

| Claim | Evidence |
| --- | --- |
| Merge commit `87437b7`, tree `ecacc4e7`, local `main` equals `origin/main` | `git rev-parse` on all three |
| Both PRs squash-merged with `Signed-off-by` trailers | `git log --format=%b c1a39e3..87437b7` |
| Migrations `0028` through `0031` present; runner tracks applied checksums | `db/migrations/`, `src/pitwall/migrations.py` |
| OpenAPI baseline: 77 paths, 99 operations, 105 schemas | Parsed `docs/api/openapi-baseline.json` |
| 75 MCP tools | `src/pitwall/mcp/registry.py` asserts the count at import; import succeeds |
| CLI groups `cost`, `burn-rate`, `guardrails`, `routing`, `runpod`, `runpod-onboard`, `provider-ops`, `volume-files` | `pitwall-gpu-broker --help` |
| DNS-level egress guard in the hermetic suite | `tests/conftest.py` patches `socket.getaddrinfo` |
| Root CI, Agent Routing CI, CodeQL green on the merge SHA | `gh run list --commit 87437b7` |
| Agent Routing source tree unchanged | Only three test files under `packages/` changed |
| One open medium Dependabot alert for `pip` | `gh api .../dependabot/alerts?state=open` |
| No workflow, settings, tag, or release changes | `git diff --stat -- .github` is empty; tags unchanged |

### Not as claimed

| Claim | Actual state |
| --- | --- |
| Root hermetic suite: 4,877 passed, 0 failed | Order-dependent. A fresh run gave 14 failed, 4,863 passed. See section 2. |
| All worktrees and branches removed | 15 worktrees and 18 local branches remain. All are clean and their content is merged, so they are safe to delete. |
| DOC-01 done | `CHANGELOG.md` keeps a Keep-a-Changelog `Unreleased` section and was not touched by either PR. |
| "Section 16" untouched | Not a repository document. It refers to the external execution specification; the equivalent content is the external-trigger section of the completion evidence. |

### Not verifiable from the repository

- DCO on the 22 pre-squash commits. Both squash commits carry sign-offs, but the original commits
  are only on local branches that will be deleted.
- The 34-item deferred register. It is not in the repository.
- The mutation, coverage, and image-smoke numbers. They are recorded on PR #32 and were not rerun.

## 2. The hermetic suite is order-dependent

`pytest-randomly` is installed and the CI hermetic job does not pass `-p no:randomly`, so test
order changes with the seed. `tests/test_staging_store.py::test_r2_staging_cleanup_import_does_not_import_boto3`
pops `boto3`, `botocore`, and `botocore.config` from `sys.modules` but leaves `botocore.exceptions`
behind. The next fresh `import botocore` produces a package object without an `exceptions`
attribute, and every later `import boto3` fails with:

```text
AttributeError: module 'botocore' has no attribute 'exceptions'
```

The polluting test predates this work (it is from the initial public release). The 14 tests it
now breaks are the S3 object-store tests added by PR #30 and PR #32. The green CI run on `87437b7`
is therefore seed luck. Deterministic reproduction:

```bash
uv run --frozen pytest -p no:randomly -q \
  tests/test_staging_store.py \
  "tests/runpod_client/test_mounts.py::test_s3_get_object"
# 1 failed, 6 passed
uv run --frozen pytest -p no:randomly -q \
  "tests/runpod_client/test_mounts.py::test_s3_get_object"
# 1 passed
```

Fix: snapshot every `sys.modules` entry whose name starts with `boto3` or `botocore` before the
test and restore all of them afterwards, instead of popping three names.

## 3. Confirmed defects in the merged code

Each entry was confirmed by reading the cited source, and for the first by executing the failing
path. Severity is an operator-impact judgement.

| # | Severity | Location | Defect |
| --- | --- | --- | --- |
| 1 | High | `src/pitwall/core/cost_reporting.py:231` | Cost read path raises after any production-routed workload. See 3.1. |
| 2 | Medium | `src/pitwall/audit/capability.py:585` | Month-to-date spend disagrees between the budget gate and the capability audit. See 3.2. |
| 3 | Medium | `src/pitwall/runpod_files.py:1508` | Missing-key lookup returns a 502 when the key is a prefix of three or more objects. See 3.3. |
| 4 | Medium | `src/pitwall/runpod_client/mounts.py:455` | Range reads of empty objects or past end of file return a 502 instead of an empty chunk or 404. See 3.4. |
| 5 | Medium | `src/pitwall/api/routes/volume_files.py:67` | Unconfigured S3 credentials return a bare 500 on REST instead of the documented 503. See 3.5. |
| 6 | Medium | `src/pitwall/runpod_files.py:816` | Upload transfer cap of 4 MiB is unreachable; bodies over 256 KiB are rejected with a misleading code. See 3.6. |
| 7 | Medium | `src/pitwall/tui/volume_files.py:648` | Starting a second volume-file action while one is running disables cancellation for the second. See 3.7. |
| 8 | Medium | `src/pitwall/tui/providers.py:485` | Providers screen "Armed" and "Active pod" columns are hard-wired to no. See 3.8. |
| 9 | Low | `src/pitwall/models/prices.py:126` | Price snapshot cache is bypassed for CLI and TUI callers. See 3.9. |
| 10 | Low | `src/pitwall/tui/providers.py:234` | Screen binding on `a` shadows the app-level Operations hotkey. |
| 11 | Low | `src/pitwall/tui/providers.py:409` | Refresh failure shows a bare "Providers unavailable." with the reason dropped. |
| 12 | Low | `src/pitwall/tui/resources.py:375` | Provider exceptions are swallowed by `gather(return_exceptions=True)`; only section names are shown. |
| 13 | Low | `src/pitwall/tui/resources.py:422` | Rows retained from a failed section are stamped with the current refresh time. |
| 14 | Low | `src/pitwall/tui/routing_jobs.py:443` | Unknown capability maps to `routing_operation_failed`, not `route_not_found`. |
| 15 | Low | `src/pitwall/tui/onboarding.py:228` | Cancelled task writes its cancellation text over the newer task's output before the guard in `_finish`. |
| 16 | Low | `src/pitwall/tui/operations.py:606` | Guardrail preview input is cleared before the JSON is validated. |
| 17 | Low | `src/pitwall/tui/operations.py:399` | Renders the literal `None` when a limited decision has no reason. |
| 18 | Low | `src/pitwall/tui/leases.py:286` | Table and search use `external_resource_id` when set, so the pod id is neither shown nor matchable. |
| 19 | Low | `src/pitwall/mcp/tools/cost.py:64` | `pitwall_recent_workloads` forwards `limit` unvalidated; the read model raises for values outside 1 to 100. |
| 20 | Low | `src/pitwall/runpod_files.py:2000` | Non-overwrite download publishes with `os.link`, which fails on filesystems without hard links. |
| 21 | Low | `src/pitwall/runpod_files.py:2064` | `or` chains treat an empty log message or an empty `logs` list as absent. |

### 3.1 Cost read path raises after any production-routed workload

`RouteBudgetQuote.to_serializable_dict` (`src/pitwall/routing/production.py:366`) emits a
top-level `plan_id` key and components keyed `name`, `provider_id`, `estimate`, `ceiling`.
`StructuredBudgetEstimate` is `@runtime_checkable` and `RouteBudgetQuote` satisfies it, so
`BudgetGate._admission_cost` (`src/pitwall/cost/budget_gate.py:351`) persists that dict verbatim
into `workloads.cost_quote`. `WorkloadCostRead.from_persisted`
(`src/pitwall/cost/read_models.py:144`) requires exactly the `CostQuote` key set and
`recent_workloads_read` calls it per row with no guard. Reproduced in-process:

```text
ValueError: cost_quote must contain exactly the structured quote fields;
missing=[], unknown=['plan_id']
```

One routed workload makes `GET /v1/cost/workloads`, the `pitwall_recent_workloads` MCP tool, and
`pitwall-gpu-broker cost workloads` fail for the whole result set until the row is scrubbed.
`_OpenAIFallbackBudgetQuote` was adapted to the strict shape; `RouteBudgetQuote` was not. No test
round-trips a routed workload through the cost read model.

### 3.2 Month-to-date spend disagrees between gate and audit

`BudgetGate` sums `COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)`
(`src/pitwall/cost/budget_gate.py:147` and `:184`). The capability audit still sums only
`cost_estimate_usd` (`src/pitwall/audit/capability.py:585`), which since migration `0029` holds the
lower point estimate rather than the admission ceiling. The audit reports headroom while the gate
rejects. `src/pitwall/tui/operations.py` and the legacy `cost_estimate_usd` JSON field have the
same under-reporting.

### 3.3 Missing-key lookup returns a 502

`_find_object` lists with `prefix=key, max_items=2` and raises `VolumeFileProviderError` when the
page is truncated without an exact match. A key that does not exist but is a prefix of three or
more object keys therefore reports a provider failure. Create-only uploads of such a key fail with
a 502 and `download_to_path` of such a key returns 502 instead of 404.

### 3.4 Range reads of empty objects return a 502

`NetworkVolumeClient.get_object_range` sends `Range: bytes=offset-(offset+max_bytes-1)` with no
handling of `416 InvalidRange` or `NoSuchKey`. `VolumeFileService._provider_call`
(`src/pitwall/runpod_files.py:1592`) maps every non-`VolumeFileError` exception to
`VolumeFileProviderError` (502). A zero-byte object, a read at or past end of file, and a missing
key all surface as 502. Chunked paging until an empty chunk therefore always ends in a 502 against
real S3. The test fake returns `value[offset:offset+max_bytes]`, which is empty bytes, so the suite
does not see it.

### 3.5 Unconfigured S3 credentials return a bare 500 on REST

The route dependency `_volume_file_service` calls `build_configured_volume_file_service`, which
raises `VolumeFileConfigurationError` (`src/pitwall/runpod_files.py:1668`) outside the `_result`
helper. `VolumeFileError` derives from `RuntimeError`, not `PitwallApiError`, and `app.py`
registers handlers only for `PitwallApiError`, `BudgetRejected`, and `RequestValidationError`. The
CLI and MCP surfaces return the documented 503 `volume_file_not_configured`; REST returns a plain
500 with a traceback in the log.

### 3.6 Upload transfer cap is unreachable

`upload_bytes` passes the full decoded body to pre-spend inspection. The inspector raises
`_InspectionLimit("max_input_bytes")` above `DEFAULT_MAX_INPUT_BYTES = 262_144`
(`src/pitwall/security/pre_spend.py:26`, `:477`) with a `DEFAULT_TIMEOUT_MS = 50` scan deadline,
while `VolumeFileLimits.max_transfer_bytes` is 4 MiB (`src/pitwall/runpod_files.py:174`). Text
uploads between 256 KiB and 4 MiB fail with `pre_spend_payload_rejected`, and uploads in the tens
of KiB can fail nondeterministically on a loaded host when the scan exceeds 50 ms.

### 3.7 Second volume-file action loses cancellation

`_start` (`src/pitwall/tui/volume_files.py:419`) calls `_cancel_active()` and then creates a new
event and task. Every `except asyncio.CancelledError` handler calls `_show_cancelled`, which calls
`_finish`, which unconditionally nulls `_active_task`, `_cancel_event`, and `_active_mutation` and
disables the Cancel button. The cancelled first task therefore clears the second task's state.
`onboarding.py:313` and `routing_jobs.py:750` guard the same code with
`self._active_task is asyncio.current_task()`; `volume_files.py` does not.

### 3.8 Providers screen "Armed" column is hard-wired

`app.py:388` installs `ServiceProvidersSource` by default. Its descriptor path builds entries with
`armed=False, active_pod_id=None` (`src/pitwall/tui/providers.py:485`) although the descriptor
config still carries `active_pod_id` and `active_lease_id`. The existing test targets
`PostgresProvidersSource`, which the app no longer uses, so the regression is untested.

### 3.9 Price snapshot cache bypassed

`load_gpu_price_snapshot` with `settings=None` (the CLI and TUI path) builds a fresh
`RunpodMarketService` per call and never consults the module `_CACHE` with its 300 second TTL. The
cached `gpu_price_snapshot` path is used only when settings are passed explicitly. `hardware-fit`
and GPU listing therefore make three provider requests per invocation.

## 4. Behaviour changes that look intentional but change operator outcomes

These are not defects on their own, but each changes what an operator sees or what is admitted,
and none is called out in the completion report.

| Location | Change |
| --- | --- |
| `src/pitwall/cost/estimator.py:1190` | Per-token input ceiling is now `max(estimated tokens, UTF-8 byte count)`, and the estimate uses `max(declared, observed)`. Ceilings for text payloads are roughly four times the previous value, so `per_request_max_usd` rejects requests it previously admitted. |
| `src/pitwall/cost/estimator.py:136` | Energy pricing ceiling is now watts times the capability execution timeout, and `expected_seconds` above the timeout raises. The test expectation moved from `0.000017` to `0.010000`. |
| `src/pitwall/tui/cost.py:123` | The screen no longer requires `PITWALL_MONTHLY_BUDGET_USD`. It renders against the settings default of `50.0`, and a budget of `0` shows "Projected breach" immediately because `spend >= budget` holds at zero. |
| `src/pitwall/tui/cost.py:167` | Runway and what-if start from `cost_daily` rollups while chargeback still sums `workloads`, so the three figures on one screen can disagree when the rollup lags. |
| `src/pitwall/tui/operations.py:891` | The "capacity N" figure now counts capacity eliminations rather than capacity decisions evaluated. |
| `src/pitwall/cost/notifications.py:202` | All delivery failures are reported as `notification_delivery_failed` and logged by exception type only. This is deliberate non-reflection, but it hides the "install `pitwall[email]`" hint. |
| `src/pitwall/tui/cost.py:160` | Zero-budget chargeback omits the tag resolver on purpose, per the inline comment, so every workload shows as unallocated. |

## 5. Repository hygiene before release

- Fix the `sys.modules` restoration in `tests/test_staging_store.py` and consider `-p no:randomly`
  in the CI hermetic job until the suite is proven order-independent.
- Add an `Unreleased` entry to `CHANGELOG.md` covering the four migrations, the 52 REST
  operations, the 49 new MCP tools, the CLI groups, and the provider capability table.
- Remove the 15 leftover worktrees under `~/git/pitwall-*` and
  `~/git/pitwall-worktrees-2026-09-01/`, then delete the 18 local branches. All are
  clean; the branches show as unmerged only because the PRs were squash-merged.
- The `artifacts/` and `mutants/` directories (about 50 MB) are git-ignored leftovers from the
  local gates and can be deleted.

## 6. Suggested fix order

1. Section 2 (test isolation), so every later fix can be verified deterministically.
2. Defect 1, with a regression test that round-trips a routed workload through
   `recent_workloads_read`.
3. Defects 3, 4, and 5 in the volume-file service, with fakes that mirror real S3 status codes.
4. Defect 2, by pointing the capability audit at the same `COALESCE` expression as the gate.
5. Defects 6 through 8.
6. The low-severity TUI items, which are each a one-line change.
7. Decide whether the section 4 behaviour changes are wanted and document the ones that are.

## 7. Disposition (2026-09-02)

| Item | Disposition |
| --- | --- |
| Section 2 flake | Fixed in Task 1 of `docs/superpowers/plans/2026-09-02-retained-backlog-review-fixes.md` |
| Defects 1 through 21 | Fixed in Tasks 2 through 24 of the same plan |
| Section 4 behaviour changes | Retained and documented in `CHANGELOG.md` and `docs/operations/cost-reconciliation.md` |
| Section 5 hygiene | `CHANGELOG.md` updated (Task 25); worktree and branch cleanup in Task 27 |

Migration `0032` was verified against a real PostgreSQL instance: a legacy-shaped route-plan
quote is rejected by `WorkloadCostRead.from_persisted` before the migration and accepted after
it, and a second apply is a no-op.
