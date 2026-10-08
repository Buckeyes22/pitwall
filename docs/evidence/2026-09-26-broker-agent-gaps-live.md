# Broker agent gaps: live verification on the local broker

Date: 2026-09-26 (UTC evening). Branch `feat/broker-agent-gaps`; deployed tree at commit `038b377` (historical, private repository)
(plan `docs/superpowers/plans/2026-09-26-broker-agent-gaps.md`, Task 10). Public pod addresses are
replaced with documentation addresses (`203.0.113.0/24`); ports are the real mappings.

## Deploy

| Step | Command | Result |
| --- | --- | --- |
| Stage release | `git archive HEAD` into `~/.local/share/pitwall/releases/20260926-broker-gaps`; `uv sync --frozen --python 3.14.7` | synced; `pitwall mcp relay --help` available |
| Gateway dist | `npm ci && npm run build` in the release's `packages/gateway` | built (the gateway image copies a prebuilt `dist/`; a fresh archive has none) |
| Point the stack | `pitwall-services` and the durable-gateway compose override switched to the new release and tag `broker-gaps-20260926` (both backed up first) | `pitwall-services config` ok; project name unchanged, so the database volume carried over |
| Build | `pitwall-services build` | api, reconciler, webhook, cost-exporter, gateway images built |
| Roll | `pitwall-services up -d` | all services healthy; migrate service: `Applied 2 migration(s)` (0035, 0036) |
| Host CLI and MCP | `~/.local/bin/pitwall` linked to the release venv; `claude mcp add pitwall_broker -s user -- pitwall mcp relay -- docker exec -i <api-container> pitwall mcp serve --transport stdio` | `claude mcp get pitwall_broker`: Connected, command `pitwall mcp relay …` |

Clients holding a broker connection were notified before the roll.

## Gap checks (scripted MCP client over the registered relay)

| Gap | Check | Result |
| --- | --- | --- |
| Budget errors unexplained | `pitwall_runpod_create_pod` apply, TTL 10080 min at 0.60/h | `{"error": "budget_rejected", "reason": "per_request_cap", "snapshot": {"estimate_usd": "100.800000", "per_request_max_usd": "10.00", …}, "remedy": "raise the limit with pitwall_budget_set …"}`; `pitwall_runpod_list_pods` shows no pod created |
| Preview skipped the budget | same request with `intent: preview` | `budget: {"admitted": false, "reason": "per_request_cap", "estimate_basis": "ttl_minutes x max_cost_per_hour", "remedy": …}`, the verdict the apply returned |
| Limits needed a restart | `pitwall_budget_status`; `pitwall budget show`; `pitwall budget set --monthly 15 --reason "verify runtime limits after the broker-gaps deploy (no-op)"` | status reports limits, spend, remaining; the set returns `source: runtime`; `config_audit` row: actor `cli`, action `budget_limits.set`, entity `global`, old source `environment`, new source `runtime` |
| MCP died with the container | one MCP session: `pitwall_health`, then `pitwall-services up -d api reconciler` replaced the API container, then `pitwall_health` | first call after the replacement returned `isError=False`; the client never reconnected |
| No pod address | `pitwall_runpod_get_pod` on two running pods | `public_ip` `203.0.113.52` / `port_mappings {"22": 22052}` and `203.0.113.180` / `{"22": 27672}`, matching the RunPod REST values looked up by hand earlier |
| Pod logs refused | `pitwall_pod_logs` on a running raw pod, `max_lines` 5 | `isError=False`, `status: completed` (no `volume_file_not_configured`) |

## Findings made during the live check

- The first pod-view check showed no address: the broker's RunPod client returns `runtime.ports`
  as `{ip, private, public, type}` with no public flag, a shape the Task 1 tests did not cover.
  Fixed in `038b377` (addresses that are not private, carrier-grade NAT, loopback, or link-local
  count as public), redeployed, and re-checked above.
- Month-to-date spend including open reservations was 15.326983 USD against the 15 USD monthly
  budget, so new admissions refuse with `monthly_budget` until someone raises it; agents can now
  do that with `pitwall_budget_set` (reason required, audited).
- Early termination left leases open: two farm pods terminated through `pitwall_runpod_terminate_pod`
  (both `changed: true`) kept their leases in `creating` with 1.80 USD reservations until TTL. After
  `754b086` was deployed, the reconciler closed both on its next tick (`stopped`, `pod_absent`,
  workloads `completed` at 2.986668 and 2.981075 USD accrued). Accrual uses the lease's cap rate for
  the time the pod ran, so month-to-date spend rose to 18.604135 USD, above the 15 USD budget.
