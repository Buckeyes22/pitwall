# Codebase review remediation: live verification on the local broker

Date: 2026-09-28 (UTC). Branch `fix/review-remediation-0928`; plan
`docs/superpowers/plans/2026-09-28-codebase-review-remediation.md`, Task 17.

## Deploy

| Step | Command | Result |
| --- | --- | --- |
| Stage release | `git archive HEAD` into `~/.local/share/pitwall/releases/20260928-review-remediation`; `uv sync --frozen --python 3.14.7`; gateway `npm ci && npm run build` | synced and built |
| Point the stack | `pitwall-services` and the durable-gateway compose override switched to the new release and tag `review-remediation-20260928` (both backed up with a `.bak-20260928` suffix); host `pitwall` linked to the release venv | `pitwall-services config` ok; project name unchanged, so the database volume carried over |
| Roll | `pitwall-services build && pitwall-services up -d` | all services healthy; migrate service `Applied 3 migration(s)` (0037, 0038, 0039) |
| Schema | `schema_migrations`, `pg_indexes` | latest `0039_money_column_constraints`; `idx_workloads_submitted_at` present, `idx_workloads_month_spend` gone |
| Provider health | `SELECT health_status, count(*) FROM pitwall.providers` | `disarmed 1`, `healthy 1`: migration 0038 reclassified the lease-disarmed serve provider |
| MCP | `claude mcp get pitwall_broker` | Connected through `pitwall mcp relay` |

## Checks

| Finding | Check | Result |
| --- | --- | --- |
| Rollup dropped raw-pod spend (#1) | run the daily rollup, then compare the month's `cost_daily` total with the gate's `MONTH_TO_DATE_SPEND_SQL` | rollup `18.604135`, gate `18.604135`, open reservations `0`; raw-pod rows `18.294914` (previously absent) |
| Doctor was false-green (#13 area) | `pitwall doctor` in the API container | `spend.burn_rate` FAIL: spend to date $18.60 (budget gate) against $15.00, forecast $25.78, data sparse; `registry.providers` ok, 1 disarmed counted apart |
| Readers disagreed on month-to-date (#5) | `curl 127.0.0.1:9109/metrics` | `pitwall_cloud_spend_month_usd 18.604135`, `pitwall_cloud_budget_usd 15.0` (the runtime limit), `pitwall_cloud_budget_pct 124.03`, `pitwall_provider_spend_month_usd{provider="runpod_direct"} 18.294914` |
| Disarmed counted as unhealthy (#2) | same metrics | `pitwall_providers_unhealthy 0.0` |
| Budget over MCP | `pitwall_budget_status` through the registered relay | `mtd_spend_usd 18.604135`, `budget_remaining_usd 0`, `source runtime` |

## Found during the live check

- Doctor still reported "rollup data is stale" right after a rollup, because staleness compared
  the newest `cost_daily` day with yesterday and this broker has run nothing since 2026-09-26.
  The same flag suppressed the forecast alert. Fixed in `8df41c9` (stale now means finished work
  from before today has no rollup row), redeployed, and the warning is gone.
- No email notifier is configured on this broker (`RESEND_API_KEY`, `PITWALL_ALERT_TO`,
  `PITWALL_ALERT_FROM` unset), so alerts use the log notifier: the 80% alert, now able to fire,
  will be written to the reconciler log at the next daily rollup (01:00 UTC) rather than emailed.
  No `pitwall:budget-alert:*` dedupe key existed before this deploy, consistent with the alert
  never having fired.
