# Retained product backlog completion evidence (2026-09-01)

This document reconciles the eleven retained packets from the 2026-09-01 execution specification
against the integrated repository. The implementation base was
`c1a39e36ba7a4a2e494c1ae5ac89d232f2cb8a23` (tree
`1049e8ac587e70ae0b53f58fff5c27e69d6b26b6`). Real-provider countersigns were not authorized and
remain explicitly separate from hermetic product completion.

The primary implementation was reviewed and merged in
[PR #30](https://github.com/Buckeyes22/pitwall/pull/30). A post-merge requirement audit identified
and corrected terminal-lifecycle, exact-cost, notification-concurrency, onboarding-plan, and
volume-mutation evidence gaps in
[PR #32](https://github.com/Buckeyes22/pitwall/pull/32). PR #32 is the terminal validation unit and
must not merge until Root CI, Agent Routing CI, CodeQL, and every other required check are green on
its final head SHA.

## Packet disposition

| Packet | Disposition | Primary evidence |
| --- | --- | --- |
| `CORE-01` | Done | Narrow provider contracts/static registry, credential references, generic identifiers, migrations `0028`/`0030`, contract/migration/serialization tests |
| `COST-01` | Done | Structured Decimal quote, active-idle and per-unit pricing, conservative admission and actual-cost contracts, migration `0029`, property/reconciliation tests |
| `RP-01` | Done hermetically | Cached market/catalogue/balance/billing service and four surfaces; recorded GraphQL/REST and cache/concurrency tests; live countersign pending |
| `RP-02` | Done hermetically | Shared RunPod resource service with strict pod/endpoint/template/volume/registry operations and four-surface contract tests; live countersign pending |
| `RP-04` | Done hermetically | Bounded S3 object/log service, path/checksum/redaction/cancellation controls, durable mutation journal with ambiguity-safe retry rules, four surfaces, and real-PostgreSQL integration evidence; live countersign pending |
| `RP-05` | Done hermetically | Serialized plan/apply/status/resume/rollback workflow, exact rendered write/rollback plan, PostgreSQL advisory locking, compatibility fingerprints and failure compensation; live apply/probe pending |
| `MC-01` | Done hermetically | Vast/Lambda compute+availability, Together sync inference+availability and usage-derived cost, exact executable OpenAI fallback chains, current endpoint/version behavior, and provider-specific no-egress/live gates |
| `FIN-01` | Done | Decimal burn-rate read model, owner-token atomic notification reservations with crash/recovery semantics, non-reflecting delivery boundary, four surfaces, and golden boundary tests |
| `ROUTE-01` | Done | Deterministic production plan/executor, exact attempt-chain admission/truth-up, terminal provider/cost/trace persistence, async lifecycle, prepared payloads, four surfaces, migration `0031` |
| `GOV-04` | Done | Bounded type-safe pre-spend inspection, one-decision prepared request paths, safe validation envelopes and four-surface status/preview |
| `DOC-01` | Done subject to terminal gates | Reconciled README, roadmap/matrices/operator/SDLC docs, inventories and this terminal audit; exact final-head local/hosted results are recorded on PR #32 and in the terminal report before merge |

## Post-merge requirement audit closure

PR #32 adds no new packet or future-register scope. It closes concrete audit findings within the
retained packets:

- OpenAI planning admits only executable transports, preserves the legacy primary → explicit
  fallback → remaining order, keeps skip-primary drills on the exact remaining chain, and reserves
  and truths up the attempts actually executable or made.
- Successful, failed, cancelled, pre-egress, final-5xx, and partial-stream terminal paths persist
  bounded trace/cost/attempt/provider/output truth without reflecting provider-controlled errors.
- Together token usage is bounded and fail-soft, and remains labelled `usage_derived` rather than
  provider billing; RunPod Pod billing remains the only provider-reported actual.
- Forecast-alert delivery uses owner-token atomic reservations, bounded pending leases,
  owner-checked completion/release, and explicit at-least-once behavior after lease expiry.
- RunPod volume-file mutations have a durable PostgreSQL journal, exact request-hash replay rules,
  create-only verification, and fail-closed ambiguity for destructive retries.
- RunPod onboarding and volume-file TUI confirmation render the exact affected resources, writes,
  rollback effects, overwrite state, spend ceiling, and irreversible consequences.

## Four-surface parity inventory

The [capability matrix](../capability-matrix.md) is the canonical semantic inventory. The concrete
registration gates are `tests/api/test_route_inventory.py`, `tests/api/test_openapi_contract.py`,
`tests/mcp/test_registry.py`, `tests/cli/test_cli_dispatch.py`, and the TUI source/Pilot suites.

| Capability group | REST | MCP | CLI | Existing TUI view |
| --- | --- | --- | --- | --- |
| Provider identity/health/availability | `/v1/providers*`, `/v1/provider-ops/*` | provider registry and `pitwall_provider_ops_*` tools | provider administration and `provider-ops` | Providers |
| Structured cost and burn rate | `/v1/cost/*` | cost/recent-workload and `pitwall_burn_rate` tools | `cost`, `burn-rate` | Cost |
| Guardrail status and preview | `/v1/guardrails*` | `pitwall_guardrail_*` | `guardrails` | Operations |
| Production planning and job lifecycle | `/v1/routing/preview`, `/v1/inference`, `/v1/jobs*`, OpenAI proxy | preview, sync/async, status/result/events/cancel tools | `routing plan|submit|status|result|follow|cancel` | Operations |
| RunPod catalogue and resources | `/v1/runpod/catalogue`, `/v1/admin/runpod/*` | catalogue plus strict resource tools | `runpod` | Resources/Providers |
| RunPod volume objects and logs | `/v1/volumes/*/objects`, `/v1/admin/volumes/*/objects`, `/v1/pods/*/logs` | volume-object/log tools | `volume-files` | Operations |
| RunPod onboarding | `/v1/admin/runpod/onboarding/*` | five onboarding tools | `runpod-onboard` | Providers/Resources |

The generated OpenAPI baseline contains 77 paths. The static MCP registry contains 75 tools. The
top-level CLI help advertises the retained `cost`, `burn-rate`, `guardrails`, `routing`, `runpod`,
`runpod-onboard`, `provider-ops`, and `volume-files` command groups. The Textual application keeps
seven coherent views—Overview, Providers, Leases, Models, Cost, Resources, and Operations—rather
than adding one screen per feature.

## Persistence, provider, and pricing inventories

Append-only migrations introduced by the retained program are:

- `0028_provider_neutral_runtime.sql` — adapter identity, credential reference, and generic
  external identifiers with compatibility backfill;
- `0029_cost_quote_truth_up.sql` — structured quote/ceiling and reconciliation provenance;
- `0030_lease_workload_billing_identity.sql` — provider-neutral lease/workload billing identity;
- `0031_workload_route_plan.sql` — non-null production plan identity/document and attempt history.

The static provider capability contract is intentionally narrow:

| Adapter | Compute | Sync inference | Async submit/status/cancel | Availability | Actual cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| RunPod | Yes | Yes | Yes | Market service | Exact Pod billing only |
| Vast.ai | Yes | No | No | Yes | No |
| Together | No | Yes | No | Yes | No |
| Lambda Cloud | Yes | No | No | Yes | No |

Active structured pricing tags are `zero`, `gpu_hour`, `per_request`, `per_second`, `per_token`,
`per_vm_second`, `active_idle`, and `per_unit`. Every quote distinguishes estimate, ceiling,
confidence, provenance, components, currency, and assumptions. Subscription/reserved pricing was
not added because no current adapter requires it.

RunPod actual-cost reads require an exact persisted workload-to-Pod mapping and a bounded
authoritative billing window. Endpoint, network-volume, aggregate, empty, and lagging billing
results remain unavailable and cannot write the ledger.

## Safety and scope audit

- Normal tests use fake adapters, recorded fixtures, `MockTransport`, or the loopback test
  Postgres/Redis stack. No official provider host was contacted.
- Raw credentials remain environment/config references and are resolved only at adapter call
  boundaries. Secret, text-policy, API validation, MCP protocol, trace, and CLI error tests cover
  non-reflection.
- Preview/dry-run paths perform no provider write or budget reservation. Spend/destructive human
  actions retain explicit confirmation and bounded/idempotent behavior where supported.
- No new daemon, database, queue, scheduler, dynamic plugin loader, generic workflow engine, or
  network MCP transport was introduced.
- The 34-item companion future register was not used as an execution source. The diff contains no
  new reserved-pricing, savings-plan, SSH/exec, Hub publishing, worker-runtime, or future routing
  scaffolding.

## External-trigger disposition

`LIVE-RP-01` and `LIVE-MC-01` remain not run: no credential reference, operation allow-list,
resource/token/byte cap, spend cap, cleanup policy, or TTL was supplied. Savings-plan purchase,
account SSH/secret management, remote execution, Hub deploy/publish, reserved pricing, network MCP,
and an in-repository worker also remain excluded. These absent triggers do not block hermetic code
completion and prevent any real-support claim beyond the
[support matrix](../support-matrix.md).

Final local command counts and hosted Root CI, Agent Routing CI, CodeQL, and other required-check
links are recorded on PR #32 and in the terminal report because those results are tied to the
pushed commit SHA.
