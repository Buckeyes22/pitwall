# RunPod catalogue, balance, and billing reads

Pitwall has one feature-local `RunpodMarketService` for RunPod GPU types,
datacenters, availability, current on-demand/spot/bid fields, REST v2 catalogue
metadata, account credit balance, and supported provider-actual categories.
The service is read-only except that an explicitly invoked COST reconciliation
may write provider-reported Pod actuals through COST's existing transaction.

## Value and support labels

| Value | Meaning |
|---|---|
| GraphQL prices, bid floor, stock, availability, and credit balance | Live at the component `observed_at`, then cached |
| REST v2 memory, pool, and CUDA availability | Live at the REST component `observed_at`, then cached; not a GraphQL-price substitute |
| Model-fit cost | Estimate using a supported GraphQL on-demand rate; unpriced static fallback when unavailable |
| Pod billing `amount` | Provider-reported actual only after exact persisted workload-to-`podId` mapping and COST validation |
| Endpoint billing | Account history only; unavailable as workload actual because rows lack job identity |
| Network-volume billing | Account history only; unavailable as workload actual because rows lack volume identity |
| Account credit balance or balance delta | Never workload actual cost |

`fresh` means every catalogue/balance component succeeded on the last refresh.
`partial` means at least one component is unavailable and another has current
data. `stale` means a failed refresh retained previously successful data.
`unavailable` means neither GraphQL discovery nor REST catalogue data exists.
Component errors use bounded reason codes; raw provider response bodies, account
ids, and credentials are not serialized.

## Cache and refresh

`RunpodMarketService(..., cache_ttl_s=300)` defaults to a five-minute in-process
TTL. A normal read uses the cache until expiry. REST `?refresh=true`, MCP
`force_refresh=true`, the CLI refresh flag, and the TUI refresh action request a
one-shot refresh. Simultaneous normal or forced reads share one provider
round-trip. There is no background task, daemon, scheduler, retry loop, bid
consumer, or reservation consumer.

`PITWALL_RUNPOD_MARKET_CACHE_TTL_S` configures the typed non-negative cache
TTL and defaults to `300`. Zero is valid and means every non-concurrent read
refreshes; it does not create background work.

## Actual-cost reconciliation

The reconciler constructs `RunpodActualCostReference` values only from durable
Pitwall state, never from an operator-supplied arbitrary workload/Pod pair. A
read is bounded to 100 workloads. For each reference the adapter requests
`GET /billing/pods` with exact `podId`, `grouping=podId`, start/end, and day
buckets. It rejects:

- no rows (including provider reporting lag);
- a missing or different Pod id;
- a bucket outside `[start_day, end_day)`;
- duplicate Pod/time buckets;
- duplicate workload ids or a Pod mapped to multiple workloads; and
- endpoint or network-volume categories.

An available result passes to `AsyncpgCostTruthUpRepository`, which validates
provider ownership, terminal state, and date range under its existing advisory
lock/transaction. Replaying the same provider result is an absolute-set no-op.

Migration `0030_lease_workload_billing_identity.sql` adds a nullable
`leases.workload_id` foreign key without backfilling or inferring historical
identity. Partial unique indexes make both the linked workload and its
`(provider_id, external_resource_id)` exclusive. Launch persists the admitted
workload with the Pod lease, readiness advances it to running, and teardown
closes it terminally. The existing reconciler selects only terminal RunPod
lease/workload pairs whose provider actual has not yet been sourced, then calls
the registry's `ACTUAL_COST` adapter and COST's transactional truth-up. Missing
credentials, no provider rows, lagged/empty billing, or any invalid bucket
leave the workload unreconciled for a later retry.

## Operator surfaces

All operator reads consume the same `RunpodMarketRead` contract:

- REST: `GET /v1/runpod/catalogue` with optional `refresh=true`;
- MCP: `pitwall_runpod_catalogue(force_refresh=false)`;
- CLI: `pitwall runpod catalogue [--refresh] [--json]`; and
- TUI: the RunPod market panel in Resources, refreshed by the existing action.

The API lifespan, MCP process, and production TUI each own and close one cached
service. Existing model-fit reads adapt that same process snapshot through
`gpu_price_snapshot_from_market`; CLI one-shot fit reads construct and close
the same service. When the configured provider is unavailable, model fit keeps
the established static unpriced fallback. `ROUTE-01` consumes
`RunpodMarketRead.to_availability_snapshot()` rather than issuing another live
capacity read.

## Failure recovery and security

- A failed refresh retains prior data as stale; force refresh after provider or
  credential recovery.
- A first-read failure returns unavailable metadata rather than crashing an
  operator surface.
- Billing/provider errors contain no raw response body. API keys remain bearer
  headers and never enter URLs, argv, snapshots, source strings, or logs.
- All ordinary tests use recorded fixtures and `httpx.MockTransport`. They do
  not resolve or contact official RunPod hosts.
- Live verification is excluded here. It requires `LIVE-RP-01` authorization;
  catalogue/billing egress alone is not authorized by ordinary test selection.

Rollback disables the global router/tool/CLI/TUI bindings and stops constructing
the service. Migration `0030` is intentionally retained once applied; its
nullable identity does not alter historical rows. No provider resource, bid,
purchase, reservation, or background process is created by this feature.
