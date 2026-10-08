# Active cost pricing and admission

Pitwall accepts tagged provider cost configuration and produces one
Decimal-authoritative quote for admission. Rates and counts should be decimal
strings. The two new variants (`active_idle` and `per_unit`) reject binary
floats so that provider configuration cannot lose precision before quoting.

## Quote contract

Every structured quote has the same fields:

```json
{
  "model": "per_unit",
  "components": [
    {
      "name": "run_units",
      "unit": "image",
      "rate": "0.125",
      "ceiling_rate": "0.125",
      "count": "2",
      "ceiling_count": "3",
      "estimate": "0.250000",
      "ceiling": "0.375000"
    }
  ],
  "estimate": "0.250000",
  "ceiling": "0.375000",
  "confidence": "bounded",
  "provenance": "provider_config",
  "currency": "USD",
  "assumptions": [
    "unit_count is explicit, provider-enforced, and never inferred from payload text"
  ]
}
```

Python callers retain `Decimal` values. JSON-safe serialization uses exact
decimal strings. `confidence` is `exact` when the configured count is final,
`bounded` when a maximum or timeout supplies the admission ceiling, and
`estimated` for operator energy estimates.

Estimates retain the legacy six-decimal `ROUND_HALF_UP` behavior. Every
component ceiling rounds positive fractions upward to the next micro-dollar,
so an admission ceiling is never below the exact unquantized cost merely
because the charge is small.

## Active variants

The following examples show the provider `cost` map and any payload fields
needed for a bounded admission.

### Zero or operator energy

```json
{"kind": "zero"}
```

An optional energy estimate supplies both fields and a bounded duration:

```json
{
  "cost": {"kind": "zero", "watts": "350", "usd_per_kwh": "0.14"},
  "payload": {"expected_seconds": "600"}
}
```

When duration is omitted, the capability execution timeout is used.

### Legacy GPU-hour-derived active rate

```json
{"kind": "gpu_hour", "per_second_active": "0.000205"}
```

The rate is stored per active second for compatibility. The capability
execution timeout is the admission duration.

### Per request

```json
{"kind": "per_request", "per_request": "0.005"}
```

One budget admission is exactly one provider invocation. Client-supplied
`request_count` and `max_requests` are rejected rather than trusted as billing
caps; callers that launch a batch must admit each invocation separately.

### Per second, with optional bid ceiling

```json
{
  "kind": "per_second",
  "rate_per_second": "0.00020",
  "bid_rate_per_second": "0.00025"
}
```

The capability execution timeout is the duration. Admission uses the larger
of the normal and bid rates.

### Per token

```json
{
  "cost": {
    "kind": "per_token",
    "per_million_input_tokens": "0.30",
    "per_million_output_tokens": "0.60"
  },
  "payload": {
    "input_tokens": 1000,
    "output_tokens": 100,
    "max_output_tokens": 500
  }
}
```

Declared input counts cannot reduce observed input accounting. The estimate is
at least the request text heuristic, while admission reserves at least the
UTF-8 byte count across `system`, `messages`, `prompt`, `input`, and the live
sync-inference `texts` list. A structured quote always requires a positive
output-token maximum that the selected adapter forwards to and has enforced by
the provider. Missing, zero, or lower-than-estimated bounds fail before spend.

### Per VM-second

```json
{"kind": "per_vm_second", "rate_per_second": "0.00040"}
```

The capability execution timeout is the bounded VM duration.

### Active plus idle standing endpoint

```json
{
  "cost": {
    "kind": "active_idle",
    "active_rate_per_second": "0.00040",
    "idle_rate_per_second": "0.00010",
    "minimum_billing_increment_seconds": "60",
    "scale_to_zero": false,
    "max_idle_seconds": "300"
  },
  "payload": {
    "active_seconds": "61",
    "idle_seconds": "1"
  }
}
```

Active and idle durations round up independently to the billing increment.
The active ceiling always comes from the capability
`execution_timeout_ms`; client `max_active_seconds` is rejected. The standing
ceiling `max_idle_seconds` belongs to trusted provider/operator pricing
configuration, never the client payload. It may be omitted only when
`scale_to_zero` is explicitly true (or the idle rate is zero); otherwise the
quote is rejected as unbounded.

### Provider-defined per-run unit

```json
{
  "cost": {
    "kind": "per_unit",
    "unit": "video_second",
    "rate_per_unit": "0.025"
  },
  "payload": {"unit_count": "4", "max_unit_count": "6"}
}
```

The lowercase unit name, rate, and count are explicit. Pitwall never derives a
unit count from prompts or other arbitrary payload text. `max_unit_count` must
be positive and no lower than the requested count; when it is omitted, the
explicit provider-enforced count is treated as final. Adapter integration must
forward/enforce the same unit count contract rather than accept a client-only
metadata cap.

## Admission and dry-run behavior

Pass the `CostQuote` to `BudgetGate`; the gate reserves `upper_bound()`, not the
optimistic estimate. Quote calculation is pure and performs no provider or
database I/O, so it is the safe dry-run/preflight operation. Admission itself
still takes the existing transaction-scoped budget lock before recording a
new workload. A caller that needs additional workload initialization may pass
a bounded post-insert initializer to `try_launch_admission`; it runs on the same
connection and transaction, so an initializer failure rolls back the admission
instead of leaving a spend-reserved orphan. Migration
`0029_cost_quote_truth_up.sql` stores the estimate, distinct admission ceiling,
and complete structured quote. Legacy callers that pass a scalar retain
estimate-equals-ceiling semantics.

The request fails closed before a provider invocation when a required bound is
missing, is below its estimate, is negative/non-finite, or overflows the
six-decimal USD representation.

## Actuals and reconciliation

Provider actual billing is a capability, not an inference from usage, current
balance, catalogue rates, or an empty report. RunPod advertises this capability
only for provider-reported Pod billing with an exact persisted
workload-to-`podId` mapping and a bounded billing window. Endpoint and
network-volume histories cannot be attributed to a terminal Pitwall workload,
so they remain unavailable as actual cost. Vast.ai, Together AI, and Lambda do
not advertise actual-cost lookup; reconciliation callers for unsupported or
unavailable reads return `actual_unavailable` and make no ledger write.

When an adapter has a selected authoritative API, an available result maps each
actual to an exact terminal Pitwall workload. The amount, provider source, and
observation time are persisted under one Postgres transaction and advisory
lock; affected daily summaries are then recomputed from workloads in that same
transaction. Replaying the same absolute actual returns `in_sync` and does not
charge twice. Aggregate-only provider totals remain unavailable because they
cannot be allocated honestly. See
[RunPod cost reconciliation](cost-reconciliation.md) and
[RunPod catalogue, balance, and billing reads](../operator/runpod-market.md) for
the current reporting limitations.

## Failure handling and rollback

- A malformed tagged model, missing bound, or unknown field is a configuration
  error. Correct the provider cost map; do not bypass the budget gate.
- An unavailable or lagging provider actual is not zero. Preserve the estimate
  and retry only after an authoritative window exists.
- A database failure rolls back the complete truth-up transaction.
- To roll back tagged pricing for a provider, restore its previous accepted
  legacy cost map. Migration 0029 is additive; rolling application code back
  leaves its nullable columns and legacy estimates readable. Do not drop the
  columns until all readers and retained truth-up audit data have been handled.

Pricing maps contain no credentials. Keep provider authentication in the
credential-reference path and never place tokens, invoice payloads, or account
identifiers in quote assumptions or provenance.

Reserved/subscription pricing, exchange rates, tax, and invoice ingestion are
parked. The existing recommendation-only reservation analysis does not create
a reserved-price admission model.
