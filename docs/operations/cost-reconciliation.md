# Provider cost reconciliation

Pitwall distinguishes three values: a pre-spend estimate, its conservative
admission ceiling, and a provider-reported actual. An estimate, catalogue-rate
calculation, usage counter, or balance delta is not a provider invoice.

## Current support state

The feature-local RunPod market service can produce a provider actual only for
official REST billing rows filtered and grouped by one exact `podId`, and only
when that Pod is authoritatively linked to one persisted Pitwall workload.
Empty, lagged, mismatched, duplicate, or out-of-window rows remain
`actual_unavailable`. Endpoint rows have no job/workload identity and
network-volume rows have no volume identity, so neither category is mapped.

The built-in registry advertises `ACTUAL_COST` for RunPod. Migration `0030`
supplies the durable, exclusive workload-to-Pod reference; the reconciler uses
only terminal linked leases and retries unavailable/lagged reads without
writing zero. Vast.ai, Together, and Lambda Cloud remain unavailable for
provider actuals unless their official APIs and adapter contracts prove an
equally authoritative workload identity.

Vast.ai and Lambda Cloud leases still reach the budget without provider actuals:
admission reserves the adapter's rate (Vast: the higher of price and bid) for
the whole lease TTL, the lease is linked to that admission workload, and
teardown settles the workload at the same rate for the time the VM ran
(provenance `lease_teardown`), releasing the rest of the reservation. A rate
that cannot be read settles at the $0.50/h fallback, never $0
(`docs/sdlc/06-leases.md`, "Lambda Cloud and Vast lease billing").

This contract is deliberate: it lets reads show estimate versus recorded
actual honestly without guessing at billing support.

## Truth-up contract

An available provider result contains:

- provider record identity;
- `availability="available"`;
- one source name and an aware UTC observation time;
- one or more Decimal USD actuals keyed by exact Pitwall workload id; and
- only terminal workloads belonging to that provider and inside the requested
  `[start_day, end_day)` interval.

An unavailable result contains no workload actuals and carries a non-empty reason.
Unavailable reconciliation performs no repository reads or writes.

For an available result, `AsyncpgCostTruthUpRepository` performs the complete
operation under one Postgres transaction:

1. acquire the cost truth-up advisory lock;
2. lock and validate the exact workload rows;
3. compare each persisted actual and source with its provider-reported value;
4. set changed workload amounts, provenance, and observation time absolutely;
5. recompute affected `pitwall.cost_daily` windows from terminal workload
   actuals; and
6. commit the transaction.

The outcome is `reconciled` when corrections were applied and `in_sync` when a
read or replay already matches. Absolute set semantics plus the advisory lock
make simultaneous and repeated identical provider reads idempotent: the same
actual cannot be added twice. A database error rolls the transaction back.

Only workload ids explicitly present in an available provider result are
compared and written. A partial result therefore cannot turn unrelated spend
into zero; a genuine zero actual must be supplied explicitly for that workload.
The durable source lives on `pitwall.workloads`; the daily table remains a
derived rollup, so a later ordinary rollup preserves the provider actual.

An aggregate provider charge that cannot be mapped authoritatively to exact
Pitwall workload ids must remain `actual_unavailable`. Pitwall does not divide
an account total speculatively or write an aggregate over derived daily data.

## RunPod reporting observations

RunPod billing data is not currently a real-time source of truth for Pitwall
spend. Treat reporting lag as an explicit availability limit. On 2026-08-30,
a default billing query returned seven daily buckets
ending on 2026-08-07, with `podGpuAmount` equal to zero throughout. The observed
default report therefore lagged by 23 days.

An explicit same-day window for 2026-08-30 returned `recordCount: 0` even though
three pods ran and were billed that day. An empty same-day billing window must
not be interpreted as zero spend or reconciled into the ledger.

Passing `granularity: "hourly"` also returned `bucketSize: "day"` without an
error. Always inspect the returned bucket size; never assume the requested
granularity was honored.

Pod uptime multiplied by a catalogue hourly rate remains an estimate. Record
creation and termination times to improve that estimate, but label it as such.
Only a selected, authoritative provider billing mapping may produce an
available provider-actual result.

The RunPod product adapter implements that rule for exact Pod billing only. It
never derives actual cost from uptime, catalogue rate, credit balance, endpoint
aggregate, or network-volume aggregate. The live reporting-lag observations
still apply: an empty provider window is unavailable, never a genuine zero.

## Operator response

- On `actual_unavailable`, retain the existing estimate and retry after the
  provider's authoritative reporting window is available.
- On an out-of-range or malformed workload actual, reject the result and correct
  the adapter mapping; do not widen the reconciliation range implicitly.
- On transaction failure, investigate Postgres and replay the same provider
  result. The absolute write is safe to replay.
- Do not place credentials, raw invoice payloads, or account identifiers in
  the source/provenance string. Authentication stays behind the provider
  credential-reference boundary.

No live provider call is part of the reconciliation foundation or its tests.

Shared reads label a sourced, reconciled workload amount as
`actual_kind="provider_reported"`. A legacy unsourced amount is
`actual_kind="recorded"`. Both retain `provider_invoice=false`: provenance from
a provider billing API proves reported actual cost, not invoice ingestion or
invoice verification.

## Cost screen data sources

The operator console's Cost screen reads from two tables. Runway remaining, the burn-rate
forecast, and the what-if starting spend come from the `cost_daily` rollups. The chargeback
table sums `pitwall.workloads` directly. Until the reconciler's `cost_daily_rollup` job has run
(scheduled daily at 01:00 UTC), workloads admitted today appear in chargeback but not in runway,
so the two figures can disagree by up to one day of spend. Run the reconciler, or wait for the
scheduled rollup, before comparing them.
