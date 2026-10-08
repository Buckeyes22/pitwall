# Production routing

Pitwall's production router turns one capability request and a point-in-time provider snapshot
into an explicit, deterministic plan. The same service owns preview, synchronous execution,
asynchronous submission, cancellation, provider-neutral status/result reads, and bounded lifecycle
events. A preview performs only repository and budget-snapshot reads: it does not reserve budget,
persist a workload, resolve credentials or webhook DNS, or call a provider.

## Configuration

Priority compatibility is the rollout default:

```dotenv
PITWALL_ROUTING_MODE=priority
PITWALL_ROUTING_WEIGHTS={"*":{"cost":"1","latency":"0"}}
PITWALL_ROUTING_MAX_ATTEMPTS=3
PITWALL_JOB_EVENT_LIMIT=25
```

`PITWALL_ROUTING_WEIGHTS` is a JSON object keyed by capability ID, capability name, capability
class, or `*`, in that precedence order. Each value has Decimal `cost` and `latency` fields in the
inclusive range 0–1000. In `weighted` mode Pitwall minimizes this direct objective:

```text
(provider cost ceiling × cost weight) + (expected latency milliseconds × latency weight)
```

Priority, provider name, and provider ID are stable tie breakers. `priority` mode selects the
lowest configured priority and reproduces the compatibility behavior without using the weighted
objective for selection.

## Preview and submit

Use `routing plan` to inspect the exact payload-free explanation. The input contributes only a
SHA-256 digest to the returned plan.

```bash
pitwall routing plan llm.chat \
  --operation sync_inference \
  --input-json '{"messages":[],"max_output_tokens":64}' \
  --json
```

An asynchronous dry-run also performs no provider write or spend:

```bash
pitwall routing submit llm.chat --input-json '{}' --dry-run --json
```

A spend-bearing submission requires an exact confirmation:

```bash
pitwall routing submit llm.chat \
  --input-json '{"messages":[]}' \
  --idempotency-key operator-example-1 \
  --confirm llm.chat
```

REST callers use `POST /v1/routing/preview` and `POST /v1/jobs`; MCP callers use
`pitwall_preview_route` and the existing job lifecycle tools. REST, MCP, CLI, and the Operations
TUI render the stored plan ID, selected provider, and safe score components rather than deriving a
second route.

## Admission and fallback safety

Execution order is fixed: validate and inspect the body, evaluate hard/provider/cost eligibility,
remove candidates whose individual ceiling exceeds the available per-request or monthly budget,
then apply priority or weighted scoring. Admission reserves the bounded fallback cost and persists
the input plus complete route plan in the same budget-locked transaction. Only after that commit
may an asynchronous submission resolve webhook DNS and invoke its selected adapter. A blocked
guardrail decision, missing price, incompatible model/stream shape, explicit unavailable capacity,
unsupported adapter operation, disabled/unhealthy provider, cooldown, or unaffordable ceiling
prevents that candidate from reaching soft scoring or provider egress. The budget gate repeats the
authoritative spend check under its transaction lock, so a stale read-only planning snapshot cannot
race past the cap.

Synchronous fallback reserves the sum of every attempted provider ceiling before the first call.
Provider failures are recorded using a safe error code and exception type; the provider message is
not persisted. Each attempted inference, submission, or cancellation is appended to the workload's
bounded `route_attempts` audit with provider/adapter identity, outcome, timestamps, and any external
job ID. Asynchronous submission invokes only the selected provider once. Pitwall does not fall back
after an ambiguous async write because doing so could duplicate paid work.

An idempotency key replays only a workload with the same operation, capability, inspected payload,
explicit provider constraint, and webhook identity. Its input and route-plan identity are committed
atomically with admission; an incomplete legacy/orphan row fails closed instead of being treated as
a valid replay. Submit, status refresh, and cancel serialize on a workload-scoped Postgres advisory
lock while reusing that connection, including with a one-connection pool. Concurrent cancels see the
terminal state after the first confirmed provider cancel and do not call the provider twice.
An idempotent replay may resume a fully persisted job that is still queued before dispatch. If a
crash leaves dispatch marked running without a durable external job ID, replay reports an unknown
outcome and does not risk a second paid submission.

Missing cost fails closed. Explicit `capacity_available=false` fails closed; absent capacity is
neutral. Missing latency uses the worst known eligible latency, or zero when every eligible
provider lacks a usable signal. Invalid latency values follow the same explicit neutral policy.
The plan records these policies and every elimination reason.

## Job lifecycle and recovery

Use `routing status`, `result`, `cancel`, or bounded `follow`. Status refreshes an active async job
through the selected adapter's provider-neutral status contract with a bounded timeout. Results are
persisted and returned only up to 1 MiB; larger or unavailable results have stable explicit reasons.
Cancellation requires an exact workload-ID confirmation. `follow` accepts at most 100 polls and a
0–60 second interval. Event reads return at most 100 persisted lifecycle entries. Until an adapter
offers an already-supported bounded event stream, responses explicitly return
`streaming_supported=false` and `provider_event_stream_unavailable`; Pitwall does not invent a
second stream engine.

Pitwall records an exact zero actual only when an admitted workload reaches a terminal state before
any inference or submit attempt (for example, rejected webhook DNS or local pre-dispatch cancel).
Once provider execution may have occurred, an unavailable provider actual remains unavailable; the
router never relabels its estimate or ceiling as exact billing. Existing authoritative reconciliation
contracts remain responsible for later actual-cost truth-up.

To roll selection back without changing persisted history, set `PITWALL_ROUTING_MODE=priority`
and restart the API/CLI process. Existing workloads retain their original append-only plan document
and remain readable. Deploy the final migration `0031` definition before applying this unreleased
feature branch; once released/applied, follow-up schema repairs require a new migration.

Common failures are safe and actionable:

| Result | Meaning | Operator action |
| --- | --- | --- |
| no providers available | Every candidate was eliminated | Inspect plan eliminations, health, capability, price, and capacity metadata |
| budget rejected | The route ceiling exceeded an existing budget gate | Reduce request bounds or adjust the existing approved budget |
| pre-spend payload rejected | The configured guardrail blocked the request | Remove the reported category; matched content is never returned |
| provider call failed | A selected adapter failed after admission | Inspect provider health; async submissions are not automatically retried |
| provider event stream unavailable | Only persisted lifecycle events exist | Use bounded status/result polling |

All normal tests use fake adapters and placeholder credentials. Real provider countersigning
remains an explicitly authorized live operation and is not part of this workflow.

## Source evidence

- Planner and executor: `src/pitwall/routing/production.py`
- Strict REST models and routes: `src/pitwall/api/routing_schemas.py`,
  `src/pitwall/api/routes/routing.py`
- Operator commands: `src/pitwall/cli/routing.py`
- Plan persistence: `db/migrations/0031_workload_route_plan.sql`
- Hermetic and real-Postgres evidence: `tests/routing/test_production_routing.py`,
  `tests/integration/test_workload_route_plan_persistence.py`
