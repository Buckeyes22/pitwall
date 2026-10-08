# Budget limits

Pitwall enforces two spend limits on every billable workload: a monthly budget and a
per-request cap. Both have environment defaults (`PITWALL_MONTHLY_BUDGET_USD`,
`PITWALL_PER_REQUEST_MAX_USD`), and both can be changed at runtime without restarting any
process.

## The runtime row and the environment defaults

Migration `0036_budget_limits.sql` creates a single audited row (`pitwall.budget_limits`,
`id = 1`). When the row exists, its values are the effective limits and every enforcement
and display path reads them at use time. When the row is absent, the environment values
apply. A change therefore takes effect on the next admission — no restart, no redeploy.

Every change requires a non-empty reason and writes a `config_audit` row
(`action="budget_limits.set"`, `entity_type="budget_limits"`, `entity_id="global"`) carrying
the old and new values. Review history with the MCP `pitwall_audit_log`
tool. Changes and admissions serialize on the same Postgres advisory lock, so a limit change
never interleaves with the admission it affects.

## Surfaces

| Surface | Read | Change |
| --- | --- | --- |
| CLI | `pitwall budget show [--json]` | `pitwall budget set --monthly 50 --per-request 10 --reason "4x4090 batch"` (exit 2 on a validation error) |
| Admin REST | `GET /v1/admin/budget` | `PUT /v1/admin/budget` with `{"monthly_budget_usd": "50", "reason": "batch"}` |
| MCP | `pitwall_budget_status` | `pitwall_budget_set` (actor `mcp`) |

`GET /v1/admin/budget` example:

```sh
curl -s -H "X-Pitwall-Secret: $PITWALL_ADMIN_SECRET" http://127.0.0.1:8080/v1/admin/budget
```

The PUT body takes optional `monthly_budget_usd` / `per_request_max_usd` decimal strings and
a required `reason`; an invalid change answers `422
{"error": "invalid_budget_limits", "detail": …}`. The response pairs the new `limits` with a
fresh `status`.

`pitwall_budget_set` / the admin PUT return the effective limits and current status, so the
caller can confirm the change landed:

```json
{"limits": {"monthly_budget_usd": "50", "per_request_max_usd": "10", "source": "runtime", "reason": "4x4090 batch"}, "status": {"monthly_budget_usd": "50", "mtd_spend_usd": "4.817201", "budget_remaining_usd": "45.182799"}}
```

## Reading a budget_rejected error

When admission is refused, the MCP payload names the rule and carries the server-computed
figures:

```json
{
  "error": "budget_rejected",
  "reason": "monthly_budget",
  "snapshot": {"monthly_budget_usd": "5", "per_request_max_usd": "10", "mtd_spend_usd": "4.817201", "estimate_usd": "1.96", "budget_remaining_usd": "0.182799"},
  "remedy": "raise the limit with pitwall_budget_set (a reason is required), or lower ttl_minutes or max_cost_per_hour"
}
```

`reason` is `monthly_budget` when spend plus the estimate would exceed the monthly budget, or
`per_request_cap` when the estimate alone exceeds the cap. `snapshot` values are decimal
strings of the limits and spend at decision time. `budget_rejected`,
`sub_budget_rejected`, and `budget_exhausted` carry the same shape; every other MCP error
stays a bare `{"error": code}`.

## Previews

A raw-pod lease preview (`pitwall_runpod_create_pod` with `intent="preview"`) evaluates the
same budget rule as the apply that follows and reports the verdict under `budget`:

```json
"budget": {"admitted": false, "reason": "monthly_budget", "snapshot": {"budget_remaining_usd": "0.182799"}, "estimate_basis": "ttl_minutes x max_cost_per_hour", "remedy": "…"}
```

`estimate_basis` records how the estimate was derived (`ttl_minutes x max_cost_per_hour`, or
`ttl_minutes x 0.50 USD/hour default` when no rate was supplied). The verdict is what the
apply would decide at that moment, under the same limits and spend; spend admitted between the
preview and the apply, or a limit change, can still change the outcome.

When the broker has no usable budget (`PITWALL_MONTHLY_BUDGET_USD` or
`PITWALL_PER_REQUEST_MAX_USD` unset, or set to a value that is not a positive finite number),
the preview reports `"reason": "budget_not_configured"` with a remedy naming the setting, and an
apply is refused with the MCP error `budget_not_configured`. The REST lease launch and renew
routes answer HTTP 503 with the same error code; the message names the setting, never its value.
A retry of a renewal that already applied, with the same `idempotency_key`, still returns its
stored result: the replay is answered before the budget is read.

## Breach escalation follows the runtime budget

The reconciler's budget-breach check reads the effective limits each minute, so raising the
monthly budget at runtime also moves the breach threshold: an armed kill switch fires at the
runtime budget, never at a stale environment value. `pitwall doctor` continues to report the
environment values as a static configuration check (runtime limits, when set, override
these).
