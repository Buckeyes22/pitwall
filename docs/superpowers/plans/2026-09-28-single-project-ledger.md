# Single-project finding ledger

One row per finding in
[`docs/evidence/2026-09-28-repo-systems-evaluation.md`](../../evidence/2026-09-28-repo-systems-evaluation.md),
owned by a task in [`2026-09-28-single-project.md`](2026-09-28-single-project.md).

States: `open`, `fixed`, `already fixed on main`, `closed by removal`, `closed by port`,
`intended behaviour`. The branch lands only when no row is `open`.

**Ownership additions** (files a finding cites that no task's Owns list names): `src/pitwall/models/__init__.py` → 0.6;
`tests/fakes/` → 0.3; `src/pitwall/tui/resources.py` and `src/pitwall/tui/routing_jobs.py` → 0.4;
`docs/superpowers/README.md` (new) → 5.2; `packages/agent-routing/runtime/model_routing/cli.py` → 0.8.

## Lane A: control-plane core

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| A-01 | H | `reconciler/__init__.py`, `webhook_receiver/__init__.py` | Terminal-status job enqueued under an unregistered name | 0.1 | fixed | 25d80889 `tests/reconciler/test_job_names.py::test_every_enqueued_job_name_is_registered` |
| A-02 | H | `workload_lifecycle.py` | `submit_runpod_job` enqueued but never registered | 2.5 | closed by removal | 90df1741, 1f91cdfd dead enqueue removed; exemption removed from `tests/reconciler/test_job_names.py` |
| A-03 | H | `reconciler/__init__.py` | One failing teardown stops the lease expiry sweep | 0.1 | fixed | 025da932 `tests/reconciler/test_lease_sweep_isolation.py::test_teardown_failure_does_not_stop_sweep` |
| A-04 | M | `docker/Dockerfile.reconciler` | No `pg_dump`/`pg_restore` for the backup drill | 0.2 | fixed | c57f3a6c `tests/test_dockerfiles.py::test_reconciler_image_has_pg_dump` |
| A-05 | M | `ops/backup_drill.py` | Synchronous subprocesses block the worker event loop | 0.2 | fixed | c57f3a6c `tests/ops/test_backup_drill_async.py::test_drill_does_not_block_event_loop` |
| A-06 | M | `docker-compose.yml` | Default `archive-purge` retention cannot succeed | 0.2 | fixed | c57f3a6c `test_rows_with_object_keys_skipped_without_adapter`, `test_no_orphan_directory_when_nothing_archived` |
| A-07 | L | `config/prometheus/pitwall-cloud-alerts.yml` | `PitwallWebhookRetriesDue` can never fire | 0.1 | fixed | e99b2665, 025da932 `tests/reconciler/test_webhook_retry_job.py::test_dispatch_records_next_retry_at_from_the_dispatcher` |
| A-08 | L | `reconciler/__init__.py`, `tests/ops/test_backup_drill.py` | Docstring says Sunday, cron runs Monday | 0.1 | fixed | 025da932 `tests/ops/test_backup_drill.py` schedule expects Sunday |
| A-09 | L | `reconciler/__init__.py` | Budget breaker state reset every job | 0.1 | fixed | 025da932 `tests/reconciler/test_breaker_state.py::test_breaker_cooldown_survives_between_jobs` |
| A-10 | L | `reconciler/__init__.py` | Duplicated `decide_renewal` plus teardown blocks | 0.1 | fixed | 025da932 shared `_teardown_for_decision` |
| A-11 | L | `reconciler/__init__.py` | Dead `expires_at is None` guard | 0.1 | fixed | 025da932 dead guards removed |
| A-12 | L | `reconciler/__init__.py` | N+1 provider and Redis reads per lease each minute | 0.1 | fixed | 025da932 `tests/reconciler/test_traffic_write_through.py::test_single_batched_read_per_tick` |
| A-13 | L | `reconciler/__init__.py` | Literal 60-element minute set | 0.1 | fixed | 025da932 `set(range(60))`, docstring lists every job |
| A-14 | L | `reconciler/__init__.py` | `_lease_expiry_reconcile` is 271 lines | 0.1 | fixed | 025da932 `_lease_expiry_reconcile` 33 lines |
| A-15 | L | `docker/Dockerfile.reconciler` | Healthcheck targets PID 1 (init) | 0.2 | fixed | c57f3a6c `test_reconciler_healthcheck_is_not_pid1` |
| A-16 | M | `api/routes/openai.py` | `openai_proxy` is 663 lines | 0.6 | fixed | 5e8f1bf6 `openai_proxy` split to ~65 lines; existing `tests/api/test_openai_proxy*.py` pass |
| A-17 | M | `api/leases/launch.py` | `_run_launch_runpod` over 245 lines | 0.7 | fixed | 0f11f289 `tests/api/test_function_sizes.py` |
| A-18 | M | `api/leases/teardown.py` | `run_teardown` over 245 lines | 0.7 | fixed | e6509e48 `tests/api/test_function_sizes.py` |
| A-19 | M | `onboarding.py` | `_apply_locked` is 309 lines | 0.7 | fixed | 4b0171b6 `tests/api/test_function_sizes.py` |
| A-20 | M | `api/leases/teardown.py` | Completed teardown reported as failure on audit/disarm error | 0.7 | fixed | e6509e48 `tests/api/test_teardown_result.py::test_audit_error_does_not_fail_completed_teardown` |
| A-21 | L | `api/app.py` | Rate-limit buckets never evicted | 0.7 | fixed | aa4a9d5d `tests/api/test_rate_limit_eviction.py::test_idle_buckets_evicted` |
| A-22 | L | `api/app.py` | Body buffered before authentication | 0.7 | fixed | aa4a9d5d `tests/api/test_auth_before_body.py::test_unauthenticated_large_body_rejected_without_buffering` |
| A-23 | L | `api/app.py` | Dead public paths, `"backend": "runpod"`, unused globals | 0.7 | fixed | aa4a9d5d `tests/api/test_health.py::test_health_is_provider_neutral` |
| A-24 | L | `config.py` | API refuses to boot without `RUNPOD_API_KEY` | 0.7 | fixed | aa4a9d5d `tests/config/test_runpod_key_optional.py::test_api_boots_without_runpod_key` |
| A-25 | L | `api/leases/teardown.py` | Missing-provider fallback assumes RunPod | 0.7 | fixed | e6509e48 `tests/api/test_teardown_result.py::test_missing_provider_uses_lease_provider_type` |
| A-26 | L | `api/routes/jobs.py` | Job input and result visible to any read token | 0.7 | fixed | 19da09f9 `tests/api/test_jobs_visibility.py::test_read_only_token_sees_metadata_only` |
| A-27 | M | `db/__init__.py` | `_docker_psql` ignores `DATABASE_URL` | 0.7 | fixed | 3f855a95 `tests/db/test_docker_psql_target.py::test_uses_database_url_components` |
| A-28 | L | `db/__init__.py` | `status` writes, hides connection errors, skips drift | 0.7 | fixed | 3f855a95 `tests/db/test_status_readonly.py` |
| A-29 | L | `db/__init__.py` | Two DB clients, dead async helper, lifespan without finally, `assert dsn` | 0.7 | fixed | 3f855a95 `tests/db/test_pool_lifecycle.py` |
| A-30 | L | `db/migrations/` | Audit CHECK needs a migration per new action | 0.7 | intended behaviour | Decision 2 (2026-09-28): the constraint is the review control |
| A-31 | L | `leases/state.py`, `db/repository.py` | Alias names and compatibility alias | 0.5 / 0.1 | fixed | leases/state.py aliases a0d01224 (`test_lease_state_module_has_no_alias_names`); repository alias 55552a75 |
| A-32 | M | `webhook_dispatcher/dispatcher.py` | Retry scheduling never implemented | 0.1 | fixed | e99b2665, 025da932 `test_retryable_failure_sets_next_retry_at`, `test_due_retries_are_redelivered_and_cleared` |
| A-33 | L | `webhook_dispatcher/dispatcher.py` | Egress rejection labelled `retry_scheduled` | 0.1 | fixed | e99b2665 `test_egress_rejection_is_not_retryable` |
| A-34 | L | `webhook_dispatcher/dispatcher.py` | Inline backoff sleeps in reconciler jobs | 0.1 | fixed | e99b2665, 025da932, 55552a75 `tests/leases/test_event_delivery.py::test_lease_event_delivery_never_sleeps` |
| A-35 | L | `webhook_dispatcher/` | Narrow exceptions; only first resolved address tried | 0.1 | fixed | e99b2665 `test_all_resolved_addresses_attempted`, `test_http_exception_is_caught` |
| A-36 | M | `webhook_receiver/__init__.py` | Delivery marked seen before enqueue | 0.1 | fixed | 25d80889 `test_enqueue_failure_does_not_mark_delivery_seen` |
| A-37 | L | `webhook_receiver/__init__.py` | Arq pool per request | 0.1 | fixed | 25d80889 `test_pool_reused_across_requests` |
| A-38 | L | `webhook_receiver/__init__.py` | No signature check without a secret | 0.1 | fixed | 25d80889 `tests/webhook_receiver/test_startup.py::test_refuses_to_start_without_secret` |
| A-39 | L | `tests/` | No dedicated webhook receiver tests | 0.1 | fixed | 25d80889 new `tests/webhook_receiver/` |
| A-40 | M | `retention/archive.py` | Purge fails on object-storage keys, leaves orphans | 0.2 | fixed | c57f3a6c `test_rows_with_object_keys_skipped_without_adapter` |
| A-41 | M | `docker/Dockerfile.reconciler` | Archive directory not created for uid 10001 | 0.2 | fixed | c57f3a6c `test_reconciler_archive_dir_owned_by_service_user` |
| A-42 | L | `retention/archive.py` | Deletes inside txn; re-archives; locks held across I/O | 0.2 | fixed | delete-after-commit already on main (1da03ea1); non-purge marker and lock release fixed in c57f3a6c: `test_non_purge_rows_not_rearchived`, `test_locks_released_during_encryption` |
| A-43 | M | `gitops/reconcile.py` | `apply_plan` violates audit constraints | 2.5 | closed by removal | 90df1741 `apply_plan` removed (removal item 6); `test_removed_symbols_are_gone[pitwall.gitops-apply_plan]` |
| A-44 | M | `gitops/reconcile.py` | `apply_plan` has no production caller | 2.5 | closed by removal | 90df1741 (removal item 6) |
| A-45 | L | `gitops/reconcile.py` | Apply loop not transactional | 2.5 | closed by removal | 90df1741 (removal item 6) |
| A-46 | M | `policy/loader.py` | Lossy home-grown YAML parser | 0.7 | fixed | 0be9e186 `tests/policy/test_loader_yaml.py` |
| A-47 | L | `seed.py` | Dead hand-parser fallback | 0.7 | fixed | 0be9e186 `_parse_simple_yaml` removed; `tests/policy/test_loader_yaml.py` |
| A-48 | M | `audit/_runtime_config.py`, `audit/checks.py` | Checks attest hardcoded `True` | 0.7 | fixed | 75ab388c `tests/audit/test_no_hardcoded_attestations.py::test_runtime_config_inputs_are_derived` |
| A-49 | L | `audit/checks.py` | Source-text checks, monkeypatching, import side effect, 1,554 LOC | 0.7 | fixed | 75ab388c `tests/audit/test_check_07.py::test_verifies_receiver_dedupe`; checks split by family |
| A-50 | L | `workers/` | `vllm.py`, `header_policy.py` have no callers | 2.5 | closed by removal | 90df1741 (removal item 7) `test_removed_modules_not_importable[pitwall.workers.vllm]` |
| A-51 | L | `live.py` | Test-only helper shipped in the wheel | 2.5 | closed by removal | 90df1741 moved to `tests/support/live.py` (removal item 9) |
| A-52 | L | `core/cost_reporting.py` | `recent_workloads_read` is 158 lines | 0.7 | fixed | d2f7eb0b `tests/api/test_function_sizes.py` |
| A-53 | L | `config.py` | `_explicit_env_settings_data` is 228 lines | 0.7 | fixed | aa4a9d5d `tests/api/test_function_sizes.py` |
| A-54 | L | `workload_lifecycle.py` | `insert_passthrough_workload` has no callers | 2.5 | closed by removal | 90df1741 (removal item 8) |
| A-55 | L | `docs/sdlc/` | About 535 stale `file:line` references | 5.2 | fixed | feba472d — `grep -rnoE '[a-z_/]+\.py:[0-9]+' docs/sdlc | wc -l` → 0; module:symbol references |
| A-56 | L | `docs/sdlc/16-core-config.md` | Claims `worker` requires runtime env | 5.2 | fixed | 904a6118 — docs/sdlc/16-core-config.md:391 matches `_REQUIRED_ENV_BY_SERVICE` (config.py:1083), states no `worker` service |

## Lane B: routing, providers, RunPod, models

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| B-01 | M | `providers/runpod.py` | `infer` only does embeddings | 0.3 | fixed | 1be5783c `tests/providers/test_runpod_infer_contract.py::test_non_embedding_request_rejected` |
| B-02 | M | `runpod_client/queue.py` | `/run` POST retried after ambiguous failure | 0.3 | fixed | 6f930f58 `tests/runpod_client/test_retry_policy.py::test_run_post_not_retried_after_read_timeout` (+3) |
| B-03 | M | `providers/model_studio/adapter.py` | Availability bypasses the automation gate | 0.6 | fixed | 5e8f1bf6 `test_model_studio_provider.py::test_availability_respects_automation_gate` |
| B-04 | M | `providers/model_studio/adapter.py` | Stream and availability JSON parsing unguarded | 0.6 | fixed | 5e8f1bf6 `test_malformed_chunk_raises_typed_error`, `test_sse_error_chunk_raises`, `test_non_sse_200_raises` |
| B-05 | M | `providers/model_studio/adapter.py`, `providers/gateway.py` | Error types borrowed from gateway | 0.6 | fixed | 5e8f1bf6 `test_quota_error_is_model_studio_type`; shared `providers/errors.py` |
| B-06 | M | `providers/service.py` | Keyless availability, fixed credential shape, name-matched error codes | 0.6 | fixed | 5e8f1bf6 `test_service_availability.py::test_keyless_provider_available`, `::test_error_code_by_type` |
| B-07 | M | `providers/drift.py` | No production caller | 2.4 | closed by removal | e8fcb7e8 providers/drift.py removed (removal item 4) |
| B-08 | L | `providers/model_studio/adapter.py` | Ignores `Retry-After` | 0.6 | fixed | 5e8f1bf6 `test_retry_after_honoured` |
| B-09 | L | `providers/gateway.py` | `classify_429` parses naive body timestamps as local | 0.6 | fixed | 5e8f1bf6 `test_gateway_classify_429.py::test_naive_body_timestamp_is_utc` |
| B-10 | M | `providers/together.py`, `model_studio/adapter.py`, `gateway.py` | Legacy `infer` kwargs; gateway's own `_resolve` | 0.6 | fixed | 5e8f1bf6 legacy `infer` kwargs removed; gateway uses `resolve_adapter_credentials` |
| B-11 | L | `providers/_wave2_feasibility.py` | Test-only module | 2.4 | closed by removal | e8fcb7e8 providers/_wave2_feasibility.py removed (removal item 4) |
| B-12 | L | `providers/model_studio/catalog.json` | Duplicated catalog and parallel implementation | 2.1 | fixed | 42317cb6, 9e2e9997 one catalog (`git ls-files | grep -c model_studio.*catalog.json` = 1); agents copies removed; `tests/agents/test_dispatch_import_weight.py::test_route_dispatch_path_imports_no_heavy_modules` |
| B-13 | L | `providers/vast.py`, `lambda_cloud.py` | Near-duplicate modules | 0.6 | fixed | 5e8f1bf6 shared `providers/_lease_compute.py` |
| B-14 | H | `routing/planner.py`, `production.py`, `resolver/service.py` | Three route planners | 2.2 | fixed | 6ecd9e65, b8aa6bef `tests/cost/test_simulator_uses_production_planner.py::test_simulation_matches_live_plan`; no `plan_route` in src |
| B-15 | H | `routing/fallback.py`, `api/routes/openai.py` | Proxy path no failover or lockout on 429 | 0.6 | fixed | 5e8f1bf6 `test_proxy_quota.py::test_429_fails_over_to_next_provider`, `::test_429_records_lockout` |
| B-16 | M | `routing/*` | ~3.7k LOC of unwired primitives | 2.5 | closed by removal | 90df1741, 1f91cdfd nine routing primitives removed (removal item 1) |
| B-17 | M | `routing/fallback.py`, `lockout.py` | Provider-specific branches in shared layers | 2.4 | fixed | e8fcb7e8, db015e73 `tests/providers/test_declarations.py::test_no_provider_type_branches_in_shared_layers` (fallback, lockout, provider_schemas, seed, reconciler, routing/openai), `::test_new_provider_needs_only_adapter_module` |
| B-18 | M | `routing/production.py` | "I/O-free" plan reads global lockout | 0.6 | fixed | 5e8f1bf6, b878290a `test_production_plan_purity.py::test_plan_reads_only_injected_lockout`; TUI caller `test_routing_summary_respects_active_lockouts` |
| B-19 | M | `routing/production.py` | `execute_sync_prepared` no backoff or cooldown; 165 lines | 0.6 | fixed | 5e8f1bf6 `test_execute_attempts.py::test_timeout_records_cooldown`, `::test_backoff_between_attempts` |
| B-20 | L | `routing/planner.py` | Overlapping parameters | 2.2 | closed by removal | b8aa6bef `plan_route` removed with routing/planner.py (removal item 2) |
| B-21 | L | `routing/openai.py` | `is_openai_compatible_provider(None)` is True | 0.6 | fixed | 06b6461f `test_is_openai_compatible_provider_rejects_none` |
| B-22 | M | `runpod_client/*`, `runpod_files.py` | Inconsistent RunPod key resolution | 0.3 | fixed | 56d2dc92 `tests/runpod_client/test_credential_resolution.py::test_saved_runpodctl_key_used_by_every_client` |
| B-23 | M | `runpod_client/*` | Four retry loops; unwired `on_429` | 0.3 | fixed | 6f930f58 `test_retry_policy.py::test_all_clients_use_shared_helper`; `on_429` removed (removal item 5) |
| B-24 | M | `runpod_client/pods.py` | Sync bodies under `to_thread` cannot be cancelled | 0.3 | fixed | 70b22a56 `tests/runpod_client/test_create_cancellation.py` |
| B-25 | L | `runpod_client/pods.py` | Duplicated parameter lists and base URLs | 0.3 | fixed | 70b22a56, ed274af5 `PodCreateArgs`; public signatures pinned by `tests/test_public_api.py` |
| B-26 | H | `runpod_control_plane.py` | `create_pod` timeout can orphan an unaudited pod | 0.3 | fixed | 0feeec6e `tests/runpod_control_plane/test_create_pod_timeout.py::test_slow_create_is_audited_with_pod_id` |
| B-27 | L | `runpod_control_plane.py` | 300 s ceiling may be too short | 0.3 | fixed | 0feeec6e `OPERATION_TIMEOUT_CEILING_S`; `test_other_operations_keep_their_timeout` |
| B-28 | L | `runpod_files.py` | 960-line `VolumeFileService`; env-only key | 0.3 | fixed | d25538b7 `tests/test_runpod_files.py::test_volume_file_service_classes_stay_under_400_lines` |
| B-29 | M | `resolver/__init__.py` | Eager import coupling and cycle | 0.6 | fixed | 5e8f1bf6 `test_import_weight.py::test_gateway_adapter_does_not_import_routing` |
| B-30 | M | `resolver/provider_urls.py` | "SSRF guard" allows private HTTPS hosts | 0.6 | fixed | 06b6461f `test_provider_urls.py::TestValidateOpenAIBaseUrl::test_https_private_address_rejected` |
| B-31 | L | `models/__init__.py` | Re-exports core symbols | 0.6 | fixed | 06b6461f `test_public_models_package.py::test_models_package_does_not_reexport_core_symbols` |
| B-32 | L | `gateway_catalog/sync.py` | `REPO_ROOT` computed at import | 0.6 | fixed | 06b6461f `tests/gateway_catalog/test_sync_lazy_root.py` |
| B-33 | L | `cli_gateway.py` | Dead `gateway_catalog/data` fallback | 0.6 | intended behaviour | verified false positive: the fallback is live in wheels (`pyproject.toml:94-98` force-include; pinned by `tests/cli/test_gateway_cli.py::test_installed_gateway_catalog_fallback`); the evaluation checked only a source checkout |
| B-34 | L | `recommendations/engine.py` | Stale `ScorecardMetric` comment, overlapping type | 0.6 | fixed | 06b6461f comment corrected; type renamed `DimensionScore` |
| B-35 | L | `recommendations/engine.py` | Drift recommendations depend on unwired module | 2.4 | closed by removal | e8fcb7e8 drift recommendations removed |
| B-36 | M | `rate_limits/store.py` | `RateBucketStore`, `halved_capacity` unused | 2.5 | closed by removal | 90df1741, 1f91cdfd, 5661ca28 RateBucketStore, rate_buckets table (0040), RateBucket chain removed (R-extra-4/5); halved_capacity kept (used by capacity_after_429) |
| B-37 | H | `serve.py` | Post-launch failures leak paid pods | 0.3 | fixed | e9de009d `tests/serve/test_post_launch_teardown.py` (5 named + 2) |
| B-38 | M | `serve.py` | `serve_model` is ~550 lines | 0.3 | fixed | e9de009d `serve_model` split to 120 lines |
| B-39 | L | `serve.py` | RunPod errors string-matched | 0.3 | fixed | e9de009d `isinstance(exc, runpod.error.QueryError)` |
| B-40 | M | `tests/property/` | Tests keep unwired primitives green | 2.5 | closed by removal | 90df1741 property tests of removed modules removed |
| B-41 | L | `tests/fakes/` | 2,356 LOC with 9 direct tests | 0.3 | fixed | b36fb081 `tests/fakes/test_fakes_direct.py` (12 tests) |

## Lane C: cost, FinOps, operator surfaces

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| C-01 | H | `cost/budget_gate.py`, `alerts.py`, `exporter.py`, `tui/cost.py` | Four month-to-date spend definitions | 0.4 | fixed | e06bf25d `tests/cost/test_mtd_single_source.py::test_all_readers_use_shared_function` |
| C-02 | H | `cost/exporter.py` | Silent $1000 budget default | 0.4 | fixed | e06bf25d `tests/cost/test_exporter_budget.py::test_refuses_without_budget` |
| C-03 | M | `cost/threshold_alerts.py`, `slo_governor.py`, `finops/bidding.py` | ~1.9k LOC unwired, float money math | 2.5 | closed by removal | 90df1741, 1f91cdfd slo_governor, bidding, threshold_alerts removed (removal item 3) |
| C-04 | M | `cost/budget_gate.py`, `audit/capability.py` | Session-time-zone month boundary | 0.4 | fixed | e06bf25d `test_month_boundary_utc_under_non_utc_session` (index-preserving form) |
| C-05 | M | `cost/read_models.py` | Money serialised as JSON floats | 0.4 | fixed | e06bf25d `tests/cost/test_money_wire_format.py::test_cost_read_money_is_string` |
| C-06 | L | `cost/budget_gate.py` | Float coercion via `Decimal(str(float))` | 0.4 | fixed | e06bf25d `tests/cost/test_budget_gate_inputs.py::test_float_rejected` |
| C-07 | L | `cost/budget_gate.py` | Per-request rejection snapshot reports zero spend | 0.4 | fixed | e06bf25d `test_per_request_rejection_snapshot_reports_real_spend` |
| C-08 | L | `cost/exporter.py`, `tui/hardware_fit.py` | Pools without `statement_cache_size=0` | 0.4 | fixed | e06bf25d, 6650c1ee `test_pool_comes_from_the_shared_pool_factory`, `test_live_fit_source_reuses_the_shared_pool_across_lookups` |
| C-09 | L | `cost/simulator.py`, `finops/time_machine.py` | Duplicated helpers | 2.2 | closed by removal | 6ecd9e65 finops/time_machine.py removed (removal item 3) |
| C-10 | L | `cost_exporter/app.py` | Deprecated pass-through module | 0.4 | closed by removal | e06bf25d, c56329de R-extra-1; `test_cost_exporter_package_removed`, `test_python_dash_m_exporter_module_serves_the_exporter` |
| C-11 | H | `mcp/registry.py` | "Admin-only" tools unenforced; `scope` never read | 0.5 | fixed | a0d01224 `tests/mcp/test_admin_descriptions.py::test_no_tool_claims_enforcement` |
| C-12 | M | `cli.py`, `mcp/tools/leases.py`, `leases/mutations.py` | Renewal event not published from CLI or MCP | 0.5 | fixed | a0d01224 `tests/leases/test_renewal_event.py::test_renewal_event_published_from_every_surface` |
| C-13 | M | `mcp/tools/*`, `api/*` | Serialisers copied up to three times | 2.3 | fixed | 8eeaddb1 `tests/api/test_serializers.py::test_rest_and_mcp_outputs_identical`, `::test_no_private_copies` |
| C-14 | M | `mcp/tools/cost.py` | Naive datetimes accepted as local | 0.5 | fixed | a0d01224 `tests/mcp/test_cost_datetimes.py::test_naive_datetime_rejected` |
| C-15 | L | `mcp/registry.py` | Hardcoded `pitwall_health` | 0.5 | fixed | a0d01224 `tests/mcp/test_health.py::test_health_reports_real_checks` |
| C-16 | L | `mcp/registry.py`, `mcp/tools/cost.py` | Import-time asserts; wrong docstring path | 0.5 | fixed | a0d01224 `tests/mcp/test_tool_count.py::test_registry_has_documented_tool_count`, `test_docstring_cites_real_modules` |
| C-17 | L | `mcp_install.py` | Env forwarding may miss required variables | 0.5 | already fixed on main | 27b62131; pinned by `tests/mcp/test_install_env.py::test_forwards_every_required_mcp_variable` |
| C-18 | M | `cli.py` | 2,736-line dispatcher, private imports, no boundary guard | 1.3 | fixed | afa0323f, 634924f2 — `src/pitwall/cli/` package, largest module 478 lines; tests/cli/test_no_private_imports.py `1 passed` |
| C-19 | M | `cost/cli.py` | Raw exception text printed | 0.4 | fixed | 6650c1ee `test_cost_command_failure_prints_a_fixed_message_without_exception_text` |
| C-20 | L | `cost/cli.py`, `cli_burn_rate.py` | Docstring drift | 0.4 / 0.5 | fixed | cost/cli.py 6650c1ee (`test_cost_cli_module_docstring_describes_the_command_group`); cli_burn_rate.py a0d01224 |
| C-21 | L | `cli.py` | One-off asyncpg pool | 0.5 | fixed | a0d01224 `tests/cli/test_models_commands.py` (shared `get_pool`) |
| C-22 | M | `tui/cost.py` | Own month-to-date query | 0.4 | fixed | 6650c1ee `tests/tui/test_cost_screen.py` |
| C-23 | L | `tui/hardware_fit.py` | Pool per lookup | 0.4 | fixed | 6650c1ee hardware_fit shared-pool test |
| C-24 | L | `tui/resources.py`, `tui/routing_jobs.py` | 1,152 and 997 LOC modules | 0.4 | fixed | aa1963c0 resources.py 520 lines, routing_jobs.py 314 lines |
| C-25 | M | `personal/` | No account budget; `DATABASE_URL` silently switches backend | 0.4 | fixed | 6650c1ee, c56329de `test_database_url_alone_does_not_switch_backend`, status backend tests; monthly cap already on main 732c4b10 (`test_serve_refused_over_monthly_budget`) |
| C-26 | M | `personal/state.py` | Unlocked read-modify-write | 0.4 | fixed | 6650c1ee `tests/personal/test_state_lock.py::test_concurrent_upserts_keep_both_records` |
| C-27 | L | `personal/service.py` | Float sent to RunPod | 0.4 | fixed | 6650c1ee `tests/personal/test_price_boundary.py::test_hourly_cap_converted_only_at_sdk_call` |
| C-28 | L | `docs/sdlc/05-cost-budget.md` | Describes `threshold_alerts` as live, stale lines | 5.2 | fixed | 904a6118 — `grep -rn threshold_alerts docs/sdlc` → no matches |

## Lane D: Agent Routing

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| D-01 | M | `dispatch.py`, `doctor.py`, `cli.py` | `_dispatch_legacy` 573 lines; other long functions | 0.8 | fixed | 334182f6, c2b04ecc `_LegacyDispatch` steps; build_parser and auth probe split |
| D-02 | M | `dispatch.py` | Invalid timeout becomes 1 ms | 0.8 | fixed | 334182f6 `tests/test_dispatch.py::test_invalid_timeout_is_usage_error` |
| D-03 | M | `dispatch.py` | Dead GNU `timeout` prerequisite | 0.8 | fixed | 334182f6 `tests/test_dispatch.py::test_dispatch_runs_without_gnu_timeout` (removal item 14) |
| D-04 | L | `doctor.py` | `sys.path` hack importing `tools` | 0.8 | fixed | c2b04ecc `tests/test_doctor.py::test_generated_routes_check_without_sys_path` |
| D-05 | L | `route_sync.py` | Subprocess without explicit env or stdio | 0.8 | fixed | c2b04ecc `tests/test_route_sync.py::test_sync_command_gets_explicit_environment_and_stdio` |
| D-06 | L | `process.py` | Children inherit full environment | 0.8 | intended behaviour | Decision 6 (2026-09-28): harnesses read their own credentials from env |
| D-07 | M | `providers/kimi.py`, `dsh.py` | Unrestricted setting ignored | 0.8 | fixed | 334182f6 `tests/test_provider_adapters.py::test_unrestricted_honoured_by_every_adapter` (kimi, dsh refuse restricted mode) |
| D-08 | L | `providers/*`, `scheduler.py`, `workflow.py` | Workflow support for 6 of 13 harnesses | 2.6 | fixed | 82fd2dbe `tests/agents/test_workflow_all_harnesses.py::test_every_harness_builds_workflow_args`, `::test_workflow_runs_with_fake_harness_binaries`; dsh model via shim/profile contract like one-shot dispatch |
| D-09 | L | `providers/*` | Model-flag parsing duplicated; muse lacks `-m` | 0.8 | fixed | 334182f6 `tests/test_provider_adapters.py::test_model_flag_forms` |
| D-10 | L | `providers/base.py` | `sanitize_args` misses positional secrets | 0.8 | fixed | 334182f6 `tests/test_sanitize_args.py::test_positional_secret_redacted` |
| D-11 | L | `providers/kimi.py`, `grok.py`, `dsh.py`, `pi.py` | Prompts in argv | 0.8 | fixed | 334182f6, 15d8ef24 pi and grok via private prompt file; `test_argv_prompt_over_limit_refused` |
| D-12 | M | `provider_setup.py`, `provider-installers.json` | 8 of 20 installers unpinned | 0.8 | fixed | b5ca1352 `tests/test_provider_setup.py::test_unpinned_recipe_refused`; goose/agy hashes independently re-verified; kimi/qwen fetched via prod by the lane (see report) |
| D-13 | L | `plugins/pitwall/hooks/steer-gate.py` | Hook fails open | 1.5 | fixed | a7ac5327 `tests/agents/test_steer_gate_fail_closed.py` (missing CLI, timeout, error block with recovery message; uninstall removes hooks first); `test_doctor_hooks.py::test_doctor_fails_on_hook_with_unrunnable_cli` |
| D-14 | L | `plugins/pitwall/hooks/*` | Large hook scripts | 1.5 | fixed | a7ac5327 hook modules at most 361 lines; `test_claude_tripwires.py` unchanged, 51 passed |
| D-15 | L | runtime, README | Legacy names persist | 1.2 | fixed | 90185b8a `tests/agents/test_legacy_env_guard.py::test_shim_refuses_when_legacy_variable_set`, `tests/agents/test_no_legacy_names.py::test_no_legacy_identifiers_remain`; verified live: legacy var -> exit 2 naming replacement |
| D-16 | L | `scripts/bootstrap.sh` | `eval` with variable name | 1.5 | closed by removal | a7ac5327 `bootstrap.sh` deleted (removal item 13); `test_install.py::test_shims_are_not_committed` |

## Lane E: TypeScript packages

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| E-01 | H | `packages/gateway/src/shim.ts` | SSE truncated at 30 s and ended cleanly | 3.2 | closed by port | 588184eb `tests/gateway/test_relay.py::test_idle_deadline_sends_error_event_and_aborts`, `::test_first_byte_deadline`, `::test_long_healthy_stream_not_cut`, `::test_client_disconnect_cancels_upstream` |
| E-02 | M | `packages/gateway/src/shim.ts` | 500 bodies carry `err.message` | 3.3 | closed by port | f9391bc5 `tests/gateway/test_app_executors.py::test_500_bodies_never_carry_the_exception_message` |
| E-03 | M | `packages/gateway/README.md` | `/v1/models` docs mismatch | 3.3 | closed by port | f9391bc5 `tests/gateway/test_models.py::test_models_lists_the_routes_without_contacting_an_upstream` (README goes with packages/gateway) |
| E-04 | M | `packages/gateway/src/shim.ts` | Test-only executor registry in production | 3.3 | closed by port | f9391bc5 `test_app_executors.py::test_there_is_no_module_level_executor_registry` |
| E-05 | L | `packages/gateway/src/shim.ts` | Non-constant-time bearer compare, copied four times | 3.3 | closed by port | f9391bc5 `test_auth.py::test_constant_time_compare_used` |
| E-06 | L | `packages/gateway/src/shim.ts` | Rate limiter singleton, inference only | 3.3 | closed by port | f9391bc5 `test_auth.py::test_rate_limit_applies_to_models_and_telemetry`, `::test_two_apps_do_not_share_a_rate_limiter` |
| E-07 | L | `packages/gateway/src/*` | Hardcoded 413 text, constant fns, rethrow, Host parse, uncapped non-SSE body | 3.3 / 3.2 | closed by port | 588184eb non-SSE cap; f9391bc5 `test_app.py::test_413_message_reports_configured_cap`, `::test_a_malformed_host_header_is_not_a_server_error` |
| E-08 | L | `packages/gateway/scripts/STRIP-NOTES.md` | Stale notes | 3.4 | closed by removal | e1a5e047 STRIP-NOTES.md removed with packages/gateway (removal item 15) |
| E-09 | M | `packages/pi-workbench/tests/workspace.test.ts` | Flaky timeout test | 4.4 | closed by port | 154fad65 `tests/workbench/test_workspace.py::test_approved_checks_kill_owned_descendants_on_timeout_and_abort` waits for the pid file; 20/20 consecutive passes |
| E-10 | M | `packages/pi-workbench/src/workspace.ts` | Approved checks inherit full env | 4.4 | closed by port | 154fad65 `tests/workbench/test_workspace.py::test_approved_checks_get_scrubbed_environment` |
| E-11 | M | `packages/pi-workbench/tsconfig.json`, `src/*` | Weak strictness, `any`, bare catches | 4.5 | closed by port | 8bfe2991 `mypy --strict src/pitwall/workbench` clean; no Any/bare except/type: ignore in comparison/ and hosted/ |
| E-12 | L | `packages/pi-workbench/src/*` | Very long single-line statements | 4.2 | closed by port | 1c481347 restricted.py/seccomp.py ported with normal line lengths; ruff format clean |
| E-13 | L | `packages/pi-workbench/scripts/two-tui-native.mts` | Orphan script | 4.5 | closed by removal | 8bfe2991 two-tui-native.mts not ported (removal item 12) |
| E-14 | L | pi-workbench docs | Pin repeated across three docs | 5.2 | fixed | d2e03457, c7ee56a0 (merge c0a5e76d) — tests/workbench/test_pi_pin.py 7 passed; harness-installers.json is the single source; extension literals checked (mutation fails test) |
| E-15 | L | `packages/*/package.json` | Toolchain version drift between packages | 4.6 | closed by removal | efa212d5 packages/pi-workbench removed (removal item 17); pinned Pi enforced by `tests/workbench/test_cli.py::test_wrong_pi_version_fails_before_state_written` |

## Lane F: engineering process

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| F-01 | H | `.github/workflows/*` | Acceptance matrix runs in no workflow | 0.9 | fixed | 00b3ec80 `journeys` job in `ci.yml`, in `required.needs` (first CI pass to be cited at I-final); superseded by 8de21160, which moved the journey harness out of PR CI by user decision, so it runs locally and in `release-readiness.yml` |
| F-02 | H | `tests/release/conftest.py`, workflows | CI drops harness-only journeys | 0.9 | fixed | 00b3ec80 `tests/tools/test_ci_required_gate.py::test_journeys_job_runs_harness_against_its_own_services` |
| F-03 | M | `RELEASING.md` | No journey or acceptance requirement | 0.9 | fixed | 00b3ec80 `RELEASING.md` requires `scripts/release/run-user-journeys.sh` |
| F-04 | M | `release_acceptance/` | Machinery unenforced relative to its size | 0.9 | fixed | 00b3ec80 acceptance machinery now enforced by the required `journeys` job |
| F-05 | L | `scripts/mutmut_score_gate.py` | No test | 0.9 | fixed | 00b3ec80 `tests/test_mutmut_score_gate.py::test_below_floor_fails`, `::test_at_floor_passes` |
| F-06 | M | `tools/guards/python_policy.py` | Runs only in pre-commit | 0.9 | fixed | 00b3ec80 `tests/tools/test_ci_required_gate.py::test_python_policy_runs_in_ci` (104 pre-existing violations tracked for the python-policy cleanup lane) |
| F-07 | M | `tools/guards/forbidden_imports.py` | Guards names that no longer exist | 0.9 | closed by removal | 00b3ec80 removal item 11; `test_forbidden_imports_guard_is_gone` |
| F-08 | M | `tools/smoke_*`, `tools/benchmark_embedding_latency` | No invoker; bare `python` usage | 0.9 | closed by removal | 00b3ec80 removal item 10 |
| F-09 | M | `.github/workflows/release-readiness.yml` | Re-runs CI on every tag | 5.1 | fixed | a3425570 `tests/release/test_workflows.py::test_readiness_does_not_rerun_ci_jobs` |
| F-10 | M | `.github/workflows/ci.yml` | Hermetic and integration suites run up to three times | 5.1 | fixed | a3425570 `test_single_ci_workflow`, `test_coverage_combined_reuses_the_suites_coverage_data` |
| F-11 | M | `.github/workflows/ci.yml` | `gateway-catalog-drift` not in required gate | 0.9 | fixed | 00b3ec80 `tests/tools/test_ci_required_gate.py::test_required_needs_journeys_and_catalog_drift` |
| F-12 | L | `Makefile` | Duplicate `mut`/`mutation` targets | 0.9 | fixed | 00b3ec80 `mut` target removed |
| F-13 | L | `.github/workflows/ci.yml` | `pi-workbench` job depends on `ubuntu-26.04` quirks | 5.1 | fixed | a3425570 `test_pi_extensions_job_pins_node_and_runner_with_a_reason` (ubuntu-26.04, Node 22.22.1, fails on sandbox skips) |
| F-14 | L | `docs/sdlc/03-mcp-server.md` | Ambiguous bare path | 5.2 | fixed | feba472d — docs/sdlc/03-mcp-server.md uses module paths; no bare `server.py`/`tools.py` |
| F-15 | L | `docs/superpowers/` | 33k lines of historical plans, no index marking them historical | 5.2 | fixed | 6ef2a4c7 — docs/superpowers/README.md indexes the plans and marks them historical |
| F-16 | L | `qa/` | Packet size and command drift risk | 5.2 | fixed | 6ef2a4c7 — qa missions/handbook use current commands (9 files); docs check `markdown links passed: 457 files` |

## Integration findings (discovered during execution)

| ID | Sev | File | Finding | Task | State | Evidence |
|---|---|---|---|---|---|---|
| G-01 | M | `tests/_hermetic_env.py`, `tests/conftest.py` | Fast suite silently runs real-DB tests against shared `pitwall_test` (5444) because `DATABASE_URL` defaults to it; parallel runs collide | 0.10 | fixed | 9bd323b5 `tests/test_fast_suite_hermetic.py::test_non_integration_postgres_connect_is_refused`, `::test_integration_marked_tests_may_connect`, `::test_every_db_module_is_integration_marked`, `::test_hermetic_placeholder_is_unreachable`; 43 DB tests moved to integration (193→236) |
| G-02 | M | tracked `*.py` | 104 pre-existing `python_policy` violations now enforced in CI lint | 0.9b | fixed | 4f160b9b (35 files) and b878290a; `python_policy.py` over every tracked `*.py` outside packages/agent-routing exits 0 on 22d99409 |
| G-03 | M | `src/pitwall/agents/*` | Moved agent code: `urlopen` on variable URLs (bandit B310) without an enforced http(s) scheme check | 1.1b | fixed | 5d5a72bc `tests/agents/test_url_schemes.py::test_non_http_scheme_refused`, `::test_http_and_https_allowed`; single triaged B310 baselined at `agents/http_urls.py:25` |
| G-04 | L | `tools/agents/validate_plugins.py` | Plugin-version check temporarily compares plugins to each other (component pyproject gone); must again pin plugins to the package version | 5.3 | fixed | 0655a14c `tests/test_version.py::test_versions_agree`; validate_plugins pins every manifest to the pyproject version |
| G-05 | M | `tests/release/test_cli_all_commands_journey.py`, `tests/release/cli_explorer.py` | Harness-only journey `test_every_argument_surface_is_registered_and_documented` fails on main (pre-existing: `config check --service`; now `serve --dry-run`): some CLI argument surfaces are never observed by the runtime explorer | 1.3 | fixed | RA2 (merge 982fa57f) — tests/release/test_cli_all_commands_journey.py::test_every_argument_surface_is_registered_and_documented passes over 485 surfaces inside J36; harness `42 passed, 0 failed` |
| G-06 | H | `tests/release/*`, `scripts/release/run-user-journeys.sh` | Journey harness on the branch: 11 of 43 journeys fail (J20 exporter, J24 serve automation, J27 README unit lane, J28 personal, J34 MCP tools, J35 REST health, J36 CLI, J37 config keys, J38 console views, MATRIX) — harness-only journeys no lane could run | J / 1.3 | fixed | RA2 (merge 982fa57f) + GW 6c43931d + WB ec87adec — full journey harness on disposable services: `42 passed, 0 failed`, MATRIX 1316 surfaces, 1530 bound, 0 unbound, 0 failing; tests/release_acceptance 510 passed |
| G-07 | M | `tests/api/test_openai_proxy_fallback.py` | `test_body_relay_cancellation_closes_upstream_and_records_failure` failed in a full fast run, passed alone (lane 2.2 report) — reproduce under load with fixed seeds and fix | G-07 lane | fixed | e994e0f6 reproduced 2/10 under core-pinned CPU starvation (1s readiness limit in the test helper); now 50/50 starved and tests/api green in random seeds 21-25 |
| G-08 | M | `config/gateway-catalog.lock.json` | Lock pins omniroute 3.8.51, which the npm registry no longer serves (latest 3.8.50), so the pinned catalog sync cannot run | 3.1 | fixed | 7e734bc9 lock re-pinned to published omniroute 3.8.50; outputs regenerated by the Python sync (integrity-checked); `check_catalog_drift.py` exit 0 |
| G-09 | H | `src/pitwall/agents/installation.py`, `tests/agents/` | `installation.install` runs host CLIs (`claude mcp add-json`, plugin commands) from the real PATH; a test that does not isolate PATH/HOME reaches the maintainer's real host config (happened once in lane 1.7's draft; verified no change) | 1.7 | fixed | b612fe07 `tests/agents/test_host_cli_isolation.py::test_real_host_cli_cannot_run_in_tests`; registration via injectable runner (`_apply_registration`) |
| G-10 | H | `src/pitwall/config.py`, `personal/*`, `mcp_install.py`, `agents/*`, harness adapters | Clean break incomplete: `pitwall serve`/`setup` shell out to the deleted `pitwall-agent-routing` executable (fail on a clean install); `mcp_install` keys on `pitwall-mcp`; hints and `--version` print the old command; `agents routes` not renamed `profiles`; harness config files still get `# managed by subagent-model-routing` markers | 1.8 | fixed | 4a1f442e `tests/test_no_legacy_command_name.py::test_old_command_name_only_in_migration_tables`, clean-install test in `tests/personal/test_routes.py`, marker migration in `tests/agents/test_migrate.py`; J36/J39/J41 PASS |
| G-11 | M | `src/pitwall/agents/workspace.py`, `migrate.py` | Dispatch worktree branch prefix renamed to `pitwall-agents/`; worktrees created before migration keep the old prefix and fail the ownership check | G-11 lane | fixed | 484779f3 `tests/agents/test_migrate.py::test_migrate_renames_legacy_worktree_branches`, `::test_migrated_worktree_passes_ownership_check` (+3) |
| G-12 | H | `src/pitwall/agents/migrate.py` `_active_runs` | I-final migration dry run (temp-HOME copy): migrate exits 2 forever because 102 legacy `running` records whose runners died (oldest last transition 2026-08-21) count as active; no liveness check | MG | fixed | 593047f9 (merge 237f5018) — tests/agents/test_migrate.py stale/live/fresh/dead-pid cases; real-state temp-HOME dry run: `102 non-terminal dispatch record(s) have no live runner and are treated as abandoned`, exit 0 |
| G-13 | H | `src/pitwall/agents/migrate.py`, `mcp_registration._is_ours` | Dry run exit 1: reinstall refuses the legacy `pitwall-channel` entry (`pitwall-agent-routing mcp`) the old install wrote in opencode.json as "not managed by pitwall"; every workstation with a registered channel fails | MG | fixed | 593047f9 (merge 237f5018) — legacy opencode/kimi/qwen/codex/claude channel cases + foreign-entry refusals in test_migrate.py; dry run rewrote opencode pitwall-channel to `pitwall mcp serve channel`, exit 0 |
| G-14 | L | `src/pitwall/agents/migrate.py` output | Dry run printed 61,890 stdout lines (61,784 per-file `moved` lines), burying conflicts, abandoned runs, and removals | MG | fixed | 593047f9 (merge 237f5018) — test_moves_are_reported_per_top_level_item; dry run stdout 61,890 -> 243 lines |
| G-15 | L | `src/pitwall/agents/migrate.py` | Dry run leaves the legacy `~/.local/bin/model-routing` launcher alias, a symlink to the removed `pitwall-agent-routing`, dangling | MG2 | fixed | 0c52c825 — tests/agents/test_migrate.py::test_legacy_alias_symlinked_to_the_launcher_is_removed (+4 cases); real-state temp-HOME dry run removed ~/.local/bin/model-routing and scripts/model-routing, exit 0, files 61778 -> 61778 |
| G-16 | M | `src/pitwall/workbench/cli.py` | J36: `pitwall workbench --help` prints usage to stderr and exits 2; its hand-dispatched subcommands are not argparse-checked either | WB | fixed | ec87adec — tests/workbench/test_cli.py::test_every_subcommand_answers_help_on_stdout_without_running (57 passed); `pitwall workbench --help` exit 0, `nosuch` exit 2 |
| G-17 | M | `tests/agents/fixtures/fake_harness.py`, `tests/agents/test_graceful_abort.py` | I-final journey J27 (full unit lane under load): `test_sigterm_to_the_supervisor_ends_with_a_receipt` failed with `int('')` reading the child pid file; passed in the standalone run. The fake harness writes the pid non-atomically while three tests wait on `exists()` only | AB | fixed | c05841e2, 4aedb950 (merge below) — atomic pid publish in tests/agents/fixtures/fake_harness.py; integrator revert check: widened non-atomic write reproduces `ValueError: invalid literal for int() with base 10: ''` at test_graceful_abort.py:68; fixed: graceful_abort + shim_contract + dispatch_contract `52 passed`; lane: 50/50 seeded loop under load avg 55-120, full agents suite clean at seeds 11/22/33 |
| G-18 | M | `tests/agents/shim_test_support.py` `run`/`run_route` | I-final rerun (load avg ~170): `test_dispatch.py::test_restricted_run_is_refused_for_harnesses_without_an_approval_flag[kimi]` hit the helper's fixed 10 s wall-clock default (`TimeoutExpired`). Integrator profile: the refusal path is one exec chain costing ~0.23 s CPU with no blocking syscalls; 3.5 s wall at load 110. The hang guard acts as a latency assertion under load | AB | fixed | d72104ba (merged) — HANG_GUARD_SECS=120 in tests/agents/shim_test_support.py and mcp_test_client.py; lane repro: 60 burners pinned with taskset -c 0 hit the 10 s TimeoutExpired pre-fix, passed post-fix; agents suite clean at seeds 44 and 55; integrator: test_dispatch + test_mcp_server 14 passed |
| G-19 | M | `docs/sdlc/04-routing.md, plugins/*/hooks/steer-gate.py` | PR #54 CI format/lint: CI checks the whole tree (`ruff format --check .`, `ruff check .`); lanes checked src and tests only; hooks used %-format (UP031) | integrator | fixed | 6c02f5a5 — `ruff format --check .` 1869 formatted; `ruff check .` clean |
| G-20 | H | `tools/agents/validate_json_schemas.py, harness-installers.schema.json` | PR #54 CI lint: validator read the moved examples/agents/routes/profiles.json; next gate found the installer schema rejecting task 4.6's extraPackages | integrator | fixed | 66c95ed8 — validates pitwall.toml via profiles_toml.parse + validate_profiles; schema declares extraPackages; `all schemas and representative runtime documents are valid` |
| G-21 | H | `uv.lock` | PR #54 CI security-sast: pip-audit flags pyjwt 2.13.0 (ten CVEs fixed in 2.14.0) | integrator | fixed | 66c95ed8 — pyjwt 2.15.1; pip-audit exit 0; mcp/api tests 932 passed |
| G-22 | M | `src/pitwall/agents/http_urls.py, gateway_catalog/sync.py, agents/managed_channel.py` | PR #54 semgrep newly scans pitwall.agents: two urlopen calls (schemes validated first) and one argv-list Popen of pitwall's own dispatch flagged | integrator | fixed | 66c95ed8 — per-line nosemgrep after audit; `Ran 221 rules on 506 files: 0 findings` |
| G-23 | M | `.github/workflows/ci.yml integration` | PR #54 CI integration: job collects coverage for coverage-combined but was held to the combined 77% floor (28%) | integrator | fixed | 66c95ed8 — --cov-fail-under=0 there; coverage-combined still enforces 77; local combined 83% |
| G-24 | H | `docker/Dockerfile.*, .dockerignore` | PR #54 CI container-build: wheel force-includes plugins/ but no Dockerfile copied it and .dockerignore excluded it | integrator | fixed | 66c95ed8 — all five images build; api image has 71 plugin files, `pitwall --version` 0.2.0a1 |
| G-25 | M | `PR commit range` | PR #54 CI DCO: 292 of 396 non-merge commits lacked Signed-off-by | integrator (maintainer-approved) | fixed | message-only sign-off rewrite, trees identical (463 commits, 66 merges, 0-line tree diff); `DCO sign-off passed for 403 non-merge commit(s)` |
| G-26 | M | `tests/release/cli_fixtures.json` | PR #54 CI J36: `pitwall mcp install|uninstall` fixtures passed only on hosts with claude/codex/opencode installed | J36M | fixed | 97ee00ba — fixtures name claude-code; real J36 `163 passed`; dry run exit 0 with harness binaries stripped from PATH |
| G-27 | M | `tests/workbench/pi_extensions/provider-extension.test.mjs, test_node_suites.py` | Pi provider deadline subtest flaky under event-loop load; node wrapper hid failing subtests | NODE | fixed | 072ae16b — 16/16 under 40 CPU hogs (was 4/14 failing); wrapper prints every `not ok` block |
| G-28 | M | `tests/workbench/pi_extensions/native-extension.test.mjs` | PR #54 CI test job (ubuntu 24.04, util-linux 2.39): seccomp-boundary node subtest fails instead of gating on setpriv --seccomp-filter like the Python parity tests | NODE | fixed | 871611a0 (merge 057651bc) — gate reuses the extension's setprivSupportsSeccompFilter; runs and passes on util-linux 2.41 (local, CI pi-extensions ubuntu-26.04); CI test job on 057651bc: node suite no longer fails |
| G-29 | M | `src/pitwall/agents/installation.py shim_script` | PR #54 CI agents-macos: 14 shims exit 126 instead of 127 when pitwall is missing: macOS bash 3.2 resolves `pitwall` to `$PWD/pitwall` on an empty PATH and `exec` fails with 126 (reproduced with GNU bash 3.2.57) | MAC | fixed | ad4ba65e, 76294ef2 — explicit `command -v pitwall` guard exits 127; bash 3.2.57: old shim exit 126, guarded shim 127 (empty cwd and `pitwall` dir in cwd); CI agents-macos success on 057651bc; upgrade over an old install replaces the shim |
| G-30 | L | `tests/agents/test_migrate.py` | Local run after the lint step's compileall: fixture copytree carried __pycache__ into the fake HOME; snapshot UnicodeDecodeError (6 tests) | integrator | fixed | fede98a1 — copytree ignores __pycache__; 37 passed with the stale .pyc present |
| G-31 | M | `tests/api/test_handler_latency.py` | Wall-clock p95 < 50 ms budget fails under machine load (local run at load ~100-170) | LAT | fixed | 4b34047e (merge 9682d6a9) — budget on time.process_time; 40 busy loops on one core: 3 failed before, 10/10 passed after; injected 80 ms CPU handler fails (83.92 ms) |
| G-32 | M | `tests/agents/test_shim_contract.py`, test subprocess waits across `tests/` | PR #54 CI test (057651bc): concurrent-dispatch test's hard-coded `communicate(timeout=10)` expired under load and abandoned 7 processes with open pipes; their GC-time ResourceWarning failed the next test (`test_teardown`) | HG | fixed | 5d14733b (merge 9717ada4) — HANG_GUARD_SECS + reap()/process-group ownership across tests; 8-dispatch test 10/10 under pinned load; forced-timeout cascade and Directory-not-empty teardown reproduced on baseline, gone after; agents+leases green at seeds 1234/98765; integrator: full integration suite 234 passed on the branch; rebind reviewed (1530 bindings, only line/digest moves in 7 HG-edited files) |
| G-33 | M | `tests/api/test_production_routing_routes.py` | `test_preview_route_serves_a_real_planner_plan` failed once in a `tests/api` run (seed unrecorded); four reruns passed | PRV | fixed | 7b8e087a — the only order/state dependence found (mid-run import of tests.routing.test_production_routing while other tests purge pitwall.* from sys.modules) moved to collection; not reproduced before or after: 61 unpinned seeds, 58 pairwise polluter runs, 20 full tests/api runs and 20 file runs pinned to core 0 under load (on pre-fix code); plan to_dict/route path verified clock-free; the test reports response text and full diff on any recurrence |
| G-34 | L | `tests/security/test_pre_spend_payload_guardrails.py` | I-final J27 security lane: Hypothesis generated `suffix='hf_0000000000000000'` as "safe" text; the guardrail correctly blocks it as a Hugging Face token, failing the redaction property | integrator | fixed | b9bbbe8c — property assumes each surrounding text is allowed alone; counterexample pinned as BLOCK test; failed deterministically before (pinned @example), 30 hypothesis seeds 0 failures after; security lane 116 passed |
| G-35 | M | `tests/agents/test_mcp_event_channel.py`, deadline loops across `tests/` | PR #54 CI agents-macos on f635a3f7: launcher-zombie check's 3 s `time.monotonic()` deadline expired (`AssertionError: 'Z' == 'Z'`); 21 such deadline loops were outside G-32's `timeout=` inventory | DL | fixed | c62a28c1 — launcher reaper blocks in process.wait and reaps on exit (worst 0.99 s under 40x load), so the 3 s bound was a hang guard; 19 of 20 deadline loops moved to HANG_GUARD_SECS (test_scheduler tearDown kept: best-effort cleanup); zombie test 10/10 under load; agents+workbench+personal green at seeds 1234/98765 |
| G-36 | M | `tests/api/test_handler_latency.py` | PR #54 CI journey J27 on f635a3f7: G-31's `process_time` budget read 57.14 ms for `/v1/cost/summary` inside the full suite (handler baseline a few ms): the measurement bills CPU that is not the request's own (other threads or GC) | LAT | fixed | 35317940 — thread_time of the calling thread plus each threadpool call (autouse, monkeypatch-scoped to the file); busy-thread prelude: process_time version 3 failed (409 ms), new 3 passed; 8M-object heap prelude passed on old code (GC ruled out); injected 80 ms async and sync-dependency spins still fail; full fast lane seeds 101/202: 8511 passed |
| G-37 | M | `src/pitwall/agents/managed_channel.py` `CHANNEL_PROBE_TIMEOUT_SECONDS` | Found by lane DL: under CPU load the 2.0 s child-channel MCP handshake probe expires and a valid managed dispatch is refused (`cannot verify the child channel ... handshake failed`) | PROBE | fixed | cf7e1272 — probe timeout 2.0 s -> 30.0 s hang guard; handshake ~0.08 s CPU, wall ~1.1 s at 12 and 2.6-3.1 s at 30 pinned busy loops; 30-loop repro refused 5/5 before, 10/10 accepted after; tests/agents/test_channel_probe.py slow-healthy accepted and hung child refused (both failed before); tests/agents seeds 101/202: 1323 passed |
| G-38 | L | `tests/release_acceptance/test_pytest_collection.py` | I-final run from an isolated worktree under /tmp: inner pytest rootdir defaulted to the common ancestor of the checkout and pytest's basetemp (/tmp), so two receipt tests saw /tmp-relative node ids | integrator | fixed | c33a968d — `--rootdir` pinned to the candidate root; reproduced from a /tmp checkout (2 failed), 3 passed from /tmp and from the normal checkout |
| G-39 | M | `src/pitwall/agents/` steer send/ack event logging | PR #54 CI test on 3a8aeeea (seed 52781076): `events.jsonl` recorded `steer.acked` (line 6) before `steer.sent` (line 8); the sender appears to publish the steer before logging it, so a fast consumer's ack precedes it | STEER | fixed | e7661544 — write-ahead: a mailbox journal hook appends steer.sent before the steer file is published and steer.failed if the publish does not happen (lost sequence race retried); tests/agents/test_steer_log_ordering.py 3 cases: integrator revert check 3 failed on the old source, 27 passed with the steer/mailbox suites on the fix; 20 consecutive green runs; tests/agents seeds 52781076 (the CI seed) and 12345: 1326 passed |
| G-40 | H | `uv.lock` | PR #54 CI security-sast on 4819587e: pip-audit found CVE-2026-97687 and CVE-2026-97689 in urllib3 2.7.0 (fixed in 2.8.0), published after the previous green run | integrator | fixed | 7dbac9db — urllib3 2.8.0; pip-audit exit 0 |
| CR-1 | M | `scripts/release/run-user-journeys.sh` `j27()`, `ci.yml` journeys | J27 re-runs the full unit lane the `test` job already gates (journeys job 44 min) | J27 + integrator | fixed | 76c2d965 (merge fad39332) + ci.yml PITWALL_JOURNEYS_UNIT_LANE=covered — J27 skips only the unit lane when covered; tests/release/test_journeys_j27_script.py 2 passed; journeys job command locally at b1c2a341: `42 passed, 0 failed`, MATRIX green, 958 s |
| CR-2 | M | `Makefile`, `README.md`, `ci.yml` test, `tests/` | No parallel test execution (`test` job 41 min, single pytest process) | XD + integrator | fixed | d97c5717, a7c2209e (merge b1c2a341) + ci.yml -n auto — fast suite under pytest-xdist: seeds 1-3 0 failures; test job command locally at b1c2a341: `8512 passed`, coverage 82.06% (>=74) in 413 s (serial 24m25s); benchmark-marked tests skip on xdist workers and run in a serial CI step (`4 passed, 2 skipped`) |
| CR-3 | M | `ci.yml` agents-macos, `tests/agents/conftest.py` | macOS runs all 110 agents test files; 55 exercise platform behaviour (25 min) | MACSUB + integrator | fixed | 52110236 (merge) + ci.yml `-m macos -n auto` — 59 modules marked macos (867 of 1,331 tests); agents-macos command locally: `863 passed, 4 skipped` in 77 s |
| G-41 | M | `src/pitwall/security/pre_spend.py` `check_deadline` | Journey J01 on a fully loaded machine: `pitwall init --non-interactive` refused with `provider configuration rejected by pre-spend policy`; the scan's 50 ms `timeout_ms` is checked against wall-clock time, so scheduler delay trips the fail-closed limit on valid input | PS | fixed | 70c9fc9e — scan budget is thread CPU time (time.thread_time_ns), 50 ms kept (heaviest legitimate input 38 ms CPU); 80 ms SIGSTOP stalls mid-scan: 23/60000 refused before, 0/60000 in each of 10 runs after; tests/security/test_pre_spend_cpu_budget.py: integrator revert check fails on the old code (block != allow), passes on the fix; 80 ms CPU-burn scan still times out; security 130 + fuzz 111 + api/cli 964 passed |
| G-42 | L | `tests/release/test_release_policy.py` | I-final `make ci-tools` on cd23cb6b: the release-policy check pinned the dependency-compatibility job's pre-`-n auto` command, broken by the C.5 wiring and not in the push gate | integrator | fixed | 13a5a964 — literal updated to the parallel command; `make ci-tools` 17 passed; `make ci-tools` added to the push gate |
| CR-4 | M | `.github/workflows/ci.yml` journeys job | The journey harness ran as a required PR CI job (24 min after C.2-C.4; 44 min before), added during this project (`00b3ec80`); the maintainer intends it as a local release tool | integrator (maintainer decision) | fixed | job and required-gate entry removed; tests pin its absence (test_workflows, test_release_scripts, test_ci_required_gate: 37 passed); workflow policy and the local CI lint job pass |
| CR-5 | M | `.github/workflows/ci.yml` (`test`, `dependency-compatibility`, `agents-macos`) | Measured on run 36752485408: the `test` fast-suite step took 37m15s (baseline ~38 min) because `-n auto` counts physical cores via psutil and created `1/1 worker` on the GitHub runner | integrator | fixed | `-n logical` in all three commands (logical CPUs); release-policy literal updated; make ci-tools 17 passed; workflow tests 6 passed. The private-repo runner has 2 vCPUs, so the next CI run measures the actual gain |

## Completion evidence

Recorded by Task I-final.

Every row above has a closing state: counting rows whose state cell is `open` in this file returns `0`.

### I-final commands (`feat/single-project` at `0a009167`, 2026-09-30)

Run in a dedicated worktree of that commit, so no other edit could touch the tree under test. The
database commands ran one at a time against `pitwall_test` on 5444; the hermetic `make test-fast`
(no database) ran alongside them.

| Command | Exit | Tail |
|---|---|---|
| `make test-fast` | 0 | `8514 passed, 496 skipped, 20 warnings, 926 subtests passed in 451.37s (0:07:31)` |
| `make test-int` | 0 | `234 passed, 2 skipped, 9363 deselected, 1 warning in 234.36s (0:03:54)` |
| `scripts/release/run-user-journeys.sh` (DATABASE_URL on 5444, REDIS_URL on 6380) | 0 | `MATRIX matrix: 1316 surfaces, 1530 bound cases, 0 unbound, 0 failing / 42 passed, 0 failed` |
| `make docs-check` | 0 | `markdown links passed: 457 files (internal)` |
| `make ci-tools` | 0 | `17 passed in 0.30s` |
| `uv run python tools/security/check_secrets.py` | 0 | `secret scan passed: 589 reviewed findings` |
| `uv build` | 0 | `Successfully built /tmp/lanes/ifinal-dist/pitwall-0.2.0a1-py3-none-any.whl` |
| `make up` | 2 | The testinfra services already run as compose project `pitwall-integ` and hold 5444/6380, so the project `make up` creates (named after the checkout directory) gets no ports; those duplicates were removed. `docker compose -p pitwall-integ -f docker-compose.testinfra.yml up -d` → exit 0; `make test-int` and the journeys ran against it. |

Earlier I-final passes found G-17 (J27, pid-file race), G-18 (shim helper hang guard), G-34 (a
Hypothesis-found redaction property defect), G-38 (collection tests assumed the checkout is not
under /tmp), and G-42 (a release-policy check pinned a pre-`-n auto` CI command), and a secret baseline not re-keyed after lane RA2 (`6114b587`); all were fixed before this run.

### Migration dry run on a copy of the maintainer's real Agent Routing state

Method: the three legacy directories (`~/.claude/subagent-model-routing`, `~/.config/subagent-model-routing`,
`~/.local/state/subagent-model-routing`, 2.0 GB), `~/.claude/scripts`, the `~/.local/bin` launcher and its
`model-routing` alias, and the three harness configs the migration re-marks (dsh, hermes, opencode) were
copied into a temporary `HOME`. Each run record's `repositoryCommonDir` was redirected into the temporary
directory so no real repository could be touched, and `PATH` held only logging stubs for the host CLIs
(`env -i HOME=<tmp> PATH=<stubs> python -m pitwall agents migrate`). Code: `pitwall.agents.migrate` at `ac45e448` (dry run repeated there). The I-final head `0a009167`
changes one source file after it (`src/pitwall/security/pre_spend.py`) plus tests, CI, docs, and
`uv.lock`; the migration path imports neither `pitwall.security` nor `urllib3`.

- Exit 0 in a single pass; stderr empty; stdout 245 lines.
- `102 non-terminal dispatch record(s) have no live runner and are treated as abandoned; their state is moved over unchanged.`
- Nothing lost: 8,210 run records before and after; 61,826 legacy state files → 61,826 files under
  `~/.local/state/pitwall/agents` (runs 51,248 entries, worktrees 9,167, launches 1,232, routing-sessions 78,
  workflows 40, locks 6, usage 4, ledger 1).
- `wrote [agents.profiles] to ~/.config/pitwall/pitwall.toml`: 16 of 16 profiles, read back by
  `pitwall agents profiles list` (16 rows).
- The legacy `pitwall-channel` entry in opencode.json became the managed `pitwall mcp serve channel` entry;
  managed markers rewritten in `~/.dsh/settings.yaml` and `~/.config/opencode/opencode.json`.
- Host commands (stubbed, recorded): `claude mcp add-json --scope user pitwall-channel …`, the new
  `pitwall-local` marketplace and plugin for claude, codex, and copilot, and removal of the legacy
  `pitwall@pitwall` and `subagent-model-routing-local` plugins and marketplaces (12 commands).
- Removed after every other step succeeded: `~/.claude/scripts/parse-shim-result.py`,
  `~/.claude/scripts/pitwall-agent-routing`, `~/.local/bin/pitwall-agent-routing`,
  `~/.claude/scripts/model-routing`, `~/.local/bin/model-routing`, `~/.config/subagent-model-routing/routes.json`,
  and the three legacy directories.
- Installed shims carry the `command -v pitwall` guard (G-29).
- A second run on the migrated copy exits 0 and issues no host commands.

The dry runs found G-12, G-13, G-14, and G-15; each was fixed and re-verified on real data before this record.
