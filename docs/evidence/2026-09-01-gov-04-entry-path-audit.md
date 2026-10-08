# GOV-04 pre-spend entry-path audit (2026-09-01)

This adversarial audit began against an intermediate backlog integration commit and was closed on
the final integration branch before terminal validation. It covers current operator inputs that can
reach paid provider execution, provider mutation, durable mutation metadata, or RunPod
object/log requests. It does not treat provider responses as input: response scanning remains
an explicit GOV-04 non-goal, while existing response redaction and bounded parsing still apply.

`enforced` below means the stateful `PreSpendInspectionService.inspect()` decision occurs before
the first provider write, budget reservation, audit/database mutation, or resource counter write.
Dry-run/preview mutations use `preview()` and therefore do not change the guardrail counters or
last-decision state. `not applicable` means the path carries only a validated identifier, enum, or
bounded number and cannot carry an arbitrary secret/PII payload to a provider.

## Final integration disposition

The detailed matrix below preserves the leaf-audit observations that drove serialized integration;
phrases such as “must” or “gap” describe that intermediate state. The following final disposition
is authoritative:

| Intermediate requirement | Final disposition |
| --- | --- |
| Application-wide validation could reflect input | Closed by the global type-only `invalid_request` envelope and boundary tests |
| FastMCP pre-handler validation could reflect input | Closed at the MCP protocol seam with end-to-end canary coverage |
| Async REST/CLI/TUI routing lifecycle was absent | Closed by `ProductionRoutingService`, `/v1/jobs`, routing CLI commands, and Operations TUI actions |
| Legacy CLI sequencing bypassed inspection | Closed for `register-template`, `terminate-pod`, and `warm-volume`; inspection precedes pool/provider work |
| OpenAI query/header forwarding was not guarded | Closed by one whole provider-bound request inspection, strict metadata allowlists, prepared-payload reuse, and provider credential replacement |
| Provider storage-time inspection was inconsistent | Closed by the shared strict provider serialized-config/model validation boundary, retained again at launch as defense in depth |
| Final inventories were pending | Closed by route/OpenAPI, 75-tool MCP, CLI help/dispatch, TUI Pilot, capability, and support-matrix tests |

Actual inference and job execution now record exactly one inspection decision. OpenAI request body,
query, and provider-bound headers are inspected together once, and its attested redacted payload is
reused for production planning. MCP inspects the complete request before pool acquisition and calls
the prepared production-service API, avoiding a second scan. Preview remains non-persisting.

## Entry-path matrix

| Entry path | REST | MCP | CLI | TUI | Shared enforcement and disposition |
| --- | --- | --- | --- | --- | --- |
| Synchronous inference | `/v1/inference` | `pitwall_submit_inference` | No current submit command | No current submit action | **Capability payload enforced.** REST and MCP inspect capability parameters before idempotency lookup, resolution, budget, persistence, or provider invocation. ROUTE-01 must put future CLI/TUI adapters over that same service. Capability/provider/idempotency control values are not yet part of the canonical candidate; the global safe-validation requirement below prevents transport reflection but does not replace that shared-service follow-up. |
| Asynchronous inference submit | No current REST submit route | `pitwall_submit_job` | No current submit command | No current submit action | **Enforced on the only current submit path.** MCP inspects input, capability/provider identity, idempotency metadata, and webhook URL before pool access, DNS, budget, persistence, or provider invocation. Input PII can be schema-safely redacted; webhook/routing/idempotency rewrites block. Dry-run uses non-recording preview. ROUTE-01 must reuse this seam for REST/CLI/TUI parity. |
| Async status/result/cancel | GET/result/cancel routes | Matching three tools | No current commands | No current actions | **REST identifiers are bounded; MCP transport remains open.** These paths carry only workload identity, but the actual FastMCP pre-handler validation/error gap can reflect malformed values and cancel reaches repository/provider lifecycle. ROUTE-01/global MCP integration must apply the same non-reflecting validation and a shared identity decision before cancel I/O. |
| OpenAI-compatible proxy | All supported `/v1/openai/{capability}/v1/{path}` methods | Not a separate MCP proxy | Not exposed | Not exposed | **Body enforced; outbound metadata gap remains.** Raw JSON is inspected before provider resolution, budget, workload state, or upstream creation, with schema-safe redaction. The raw query string and copied inbound headers are not inspected/allow-listed before forwarding; root must close that shared proxy boundary, including never forwarding Pitwall consumer authorization as an upstream provider credential. |
| Model serve and volume warm | `/v1/serve` | `pitwall_serve_model` | `serve-model`, `warm-volume` | Serve preview/apply panel | **Serve request and launch egress enforced.** `serve_model` inspects the typed request before registry writes. `run_launch` separately previews stored provider image/argv/placement/env and identity before logging or egress without recording a second decision. Launch-only `HF_TOKEN` remains an intentional credential boundary. Legacy `warm-volume` still performs a catalogue price network read before the shared guard and needs the root-only CLI sequencing fix below. |
| Lease create/provision | `/v1/leases` | `pitwall_lease_pod` | No separate create command | No create action | **Enforced at the canonical launch seam.** `run_launch` inspects payload, non-credential `extra_env`, capability/provider/request/idempotency identity, and the complete JSON-normalized provider config before logging, kill-switch/budget/database/provider work. Only typed environment PII may be safely rewritten; image, argv, identity, placement, volume, port, or other config changes block. Intentional `HF_TOKEN` material is excluded only at its credential boundary. |
| Lease stop | `/v1/leases/{id}/stop` and DELETE | `pitwall_stop_lease` | `leases stop` | No stop action | **Enforced.** `run_teardown` inspects the internal stop code and free-text termination reason before repository access, state transition, provider teardown, durable reason/audit writes, or publication. Secrets block with safe findings; supported PII is redacted before persistence and egress. Delete uses the constant reason `delete`. |
| Lease renew/settings | `/v1/leases/{id}/renew` and paid-axis PATCH | `pitwall_renew_lease` | `leases renew` | No mutation action | **Enforced.** The shared mutation service inspects lease and idempotency identity before repository writes; non-allow decisions block because durable identities cannot be rewritten. Numeric/policy fields remain bounded typed values. MCP and CLI still need the global safe error boundaries below. |
| RP-02 raw pod mutations | Create/update/action/terminate | Matching four tools | `runpod pods ...` | Resources mutation editor | **Enforced in `RunPodControlPlaneService`.** All four mutations inspect the typed request before provider reads/writes or audit. Environment, image, args, names, and ids fail closed on any non-allow decision because silently rewriting resource identity or argv is unsafe. |
| RP-02 endpoint mutations | Create/update/delete | Matching three tools | `runpod endpoints ...` | Resources mutation editor | **Enforced in the same service.** Nested workers, scaling, GPU pools/exclusions, template/name fields, ids, and idempotency metadata are covered before provider/audit I/O. |
| RP-02 account-template mutations | Create/update/delete | Matching three tools | `runpod templates ...` | Resources mutation editor | **Enforced in the same service.** Image, command args, ports, names, and ids are inspected before provider/audit I/O. |
| RP-02 volume mutations | Create/grow/delete | Matching three tools | `runpod volumes ...` | Resources mutation editor | **Enforced in the same service.** Names, datacenter, size, ids, and idempotency metadata are inspected. Grow-only validation and dry-run semantics remain unchanged. |
| RP-02 registry-auth mutations | Create/replace/delete | Matching three tools | `runpod registry-auths ...` | Resources mutation editor | **Enforced before credential resolution.** Only the environment-variable reference and non-secret metadata are inspected. The referenced password is resolved later at the strict provider boundary and is never scanned, audited, persisted, or serialized. Replace remains explicit delete/recreate. |
| RP-02 account/Hub reads | Account resource list/get; Hub list/get/search | Matching read tools | Matching read commands | Resources refresh/filter | **Read-only.** Sanitized account reads and separate Hub reads perform no provider mutation or spend; strict ids/bounds apply. Hub search text is applied locally to a bounded sanitized page and never becomes a provider payload. |
| Provider registration/update | Admin provider create/update/state routes | Admin create/update/disable/hibernate tools | Legacy registration/GitOps paths | Provider reads only | **Credential-value storage is fail-closed; general pre-spend storage is partial.** CORE recursively rejects secret-bearing config values, while launch now re-inspects all stored egress metadata. General token/PII scanning is not yet shared across REST, MCP, seed/GitOps, and legacy CLI writes, so unsafe metadata can be persisted even though launch blocks it before spend. Root must choose one shared storage-time inspection seam before repository/audit writes. |
| RP-04 object list/chunk/download | List and bounded chunk | List and bounded chunk | List/download | Operations panel | **Enforced in `VolumeFileService`.** Volume/datacenter ids, prefix, object key, range, and local destination metadata are inspected before S3 or local destination mutation. Downloads inspect request metadata, not provider-returned object bytes. |
| RP-04 upload | Bounded base64 upload | Bounded base64 upload | Safe-root local upload | Operations preview/apply | **Enforced in `VolumeFileService`.** Decoded UTF-8 content and request metadata are inspected before object lookup or PutObject. Opaque binary, unsupported content, and scanner-oversized content fail closed; upload dry-run uses non-persisting preview. REST validation failures have a feature-local non-reflecting 422 envelope. |
| RP-04 object delete | Confirmed DELETE | Confirmed delete tool | Confirmed delete | Typed-confirm preview/apply | **Enforced in `VolumeFileService`.** Request identity and idempotency metadata are inspected before DeleteObject; dry-run performs no provider or guardrail state write. |
| RP-04 pod logs | Bounded log GET | Bounded log tool | `volume-files logs` | Operations panel | **Enforced on request metadata.** Pod id and bounds are inspected before the provider call. Returned log text is not scanned, as required by the GOV-04 non-goal, but remains byte/line bounded and passes existing credential redaction before serialization. |
| Guardrail status/rules/preview | `/v1/guardrails`, `/preview` | Status/preview tools | `guardrails status|preview` | Operations/Security panel | **Shared model.** Status exposes only rule metadata, aggregate counters, and last-decision metadata. Preview uses the same scanner without provider, database, audit, counter, or last-decision writes. |
| Legacy `register-template` command | Not applicable | Not applicable | Direct legacy command | Not exposed | **Open serialized CLI gap.** It still calls `ensure_template` directly and can carry image/name content to cache/provider creation without the shared service. The top-level CLI is a serialized hotspot; it must delegate to guarded `RunPodControlPlaneService.create_template` (preferred) or invoke the same inspection before `get_pool()`/`ensure_template`. The guarded `runpod templates create` command is the current safe replacement. |
| Legacy `terminate-pod` command | Not applicable | Not applicable | Direct legacy command | Not exposed | **Open serialized CLI gap.** It accepts an arbitrary pod id, calls the strict client directly without the RP-02 typed request, inspection, audit, idempotency, or confirmation, and can reflect the id/error. Rewire or retire it in favor of guarded `runpod pods terminate`. |
| Emergency kill switch | Admin REST | Not exposed | Existing admin command path | Not exposed | **Excluded from blocking pre-spend policy.** It only stops spend. A DLP rejection must never prevent emergency cleanup; its authentication, bounded reason, audit, and kill-log controls are separate. |
| Reconciler, health, billing, catalogue, and Hub reads | Internal/read surfaces | Read tools | Read commands | Read panels | **Not applicable.** These paths have no new operator payload and do not initiate paid work. Provider responses continue through bounded typed parsing and redaction rather than pre-spend scanning. |

## Adversarial ordering and disclosure findings

- Every one of the 16 RP-02 apply mutations now records exactly one inspection decision. A denial
  causes zero provider calls, zero audit writes, and no credential-reference resolution. Preview
  denial records zero counters and performs zero writes.
- Rejected RP-02 identifiers are omitted from the service error envelope. Feature-local REST and
  CLI adapters are non-reflecting. Actual FastMCP registration is not safe yet: validation happens
  before the handler, and the installed SDK flattens even structured `McpError` exceptions.
- RP-04 upload inspection operates on decoded content, so base64 framing cannot hide a supported
  token. Invalid/oversized REST bodies return only `invalid_volume_file_request`; FastAPI's raw
  validation `input` is never reflected.
- Registry password values and the serve path's launch-only Hugging Face token are intentional
  credential material, not arbitrary operator payload. They remain out of the scanner and are
  materialized only at their established credential boundaries. References and all adjacent
  operator metadata are inspected before resolution.
- Provider-returned files and logs are not scanned. This preserves the explicit no-response-DLP
  scope while existing size bounds, type validation, checksum behavior, and output redaction stay
  in force.

## Serialized integration requirements

1. `api/app.py` needs one application-wide non-reflecting `RequestValidationError` envelope.
   Default FastAPI 422 serialization currently includes raw invalid `input` for leases, inference,
   serve, and provider routes. RP-02/RP-04 local route classes are safe but do not solve the global
   contract.
2. `mcp/registry.py` (or the server protocol seam outside the installed SDK `Tool.run`) must
   sanitize pre-handler validation and preserve stable error codes. End-to-end `call_tool` tests
   must prove canaries are absent; direct handler tests are insufficient because FastMCP currently
   flattens `McpError`.
3. ROUTE-01 must add REST async submit plus CLI/TUI job actions as thin adapters over the complete
   guarded async submission seam, and give cancel the same identity decision before repository or
   provider work. It must also close synchronous inference control-metadata coverage.
4. The top-level `cli.py` owner must put `warm-volume` inspection before catalogue/price network
   reads, provide stable non-reflecting lease errors, and retire/rewire legacy `register-template`
   and `terminate-pod` through the guarded RP-02 service. No parser/dispatch edit is in this leaf.
5. The OpenAI proxy owner must inspect or explicitly allow-list query/header egress before budget
   and upstream creation. Consumer authorization must not be copied to a provider request.
6. Provider REST/MCP/GitOps/seed registration should share one storage-time pre-spend decision
   before repository/audit writes. Launch inspection remains required defense in depth.
7. Final route/tool/help/navigation inventories should record integrated RP-02/RP-04 and ROUTE-01
   additions. This leaf intentionally does not edit serialized global inventories.

All audit and regression execution was hermetic. No provider hostname, credential, paid action,
live database, or section-16 external trigger was used.
