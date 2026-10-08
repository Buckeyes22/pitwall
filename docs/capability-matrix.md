# Public capability matrix

“Code-complete” below means the shared service and all four operator surfaces have hermetic
contract coverage. It does not mean a real provider account was contacted. Provider countersigns
remain subject to the provider-specific live gates in the support matrix.

| Capability | REST | MCP stdio | CLI | Textual TUI | Evidence status |
| --- | --- | --- | --- | --- | --- |
| Capability/provider registry reads and administration | `/v1/capabilities`, `/v1/providers`, admin routes | discovery/admin tools | seed, create, register, health commands | Providers | Supported |
| Provider-neutral adapter identity, availability, health, and capability detection | `/v1/provider-ops/*` | `pitwall_provider_ops_*` | `provider-ops` | Providers | Code-complete; live countersign pending for non-RunPod providers |
| Structured estimate, ceiling, actual, and reconciliation state | `/v1/cost/*` | cost summary/recent workloads | `cost` | Cost | Supported hermetically |
| Burn-rate forecast and deduplicated breach alerts | `/v1/cost/burn-rate` | `pitwall_burn_rate` | `burn-rate` | Cost runway | Supported hermetically |
| Pre-spend guardrail catalogue, counters, and non-persisting preview | `/v1/guardrails*` | `pitwall_guardrail_*` | `guardrails` | Operations | Supported hermetically |
| Deterministic production route preview and route explanation | `/v1/routing/preview` | `pitwall_preview_route` | `routing plan` | Operations | Supported hermetically |
| Synchronous inference and OpenAI-compatible proxy | `/v1/inference`, `/v1/openai/*` | `pitwall_submit_inference` | configuration and routed service consumers | Operations route/job detail | Supported for configured adapters |
| Provider-neutral asynchronous submit/status/result/events/cancel | `/v1/jobs*` | inference lifecycle plus `pitwall_get_job_events` | `routing submit/status/result/follow/cancel` | Operations | Supported hermetically; provider capability-dependent |
| Pod lease create/read/mutate with persisted route identity | `/v1/leases*` | lease tools | `leases`, serve/terminate commands | Leases | Alpha/limited; provider capability-dependent |
| RunPod catalogue, availability, price/bid snapshot, credit balance, and supported billing actuals | `/v1/runpod/catalogue` | `pitwall_runpod_catalogue` | `runpod catalogue` | Resources market panel | Code-complete; real-account countersign pending |
| RunPod pods, endpoints, account templates, read-only Hub templates, volumes, and registry auth | `/v1/admin/runpod/*` | `pitwall_runpod_*` resource tools | `runpod` | Resources | Code-complete; real-account countersign pending |
| RunPod bounded volume objects and pod logs | volume-object and pod-log routes | volume/log tools | `volume-files` | Operations | Code-complete; real-account countersign pending |
| Plan/apply/status/resume/rollback-guidance RunPod onboarding | `/v1/admin/runpod/onboarding/*` | onboarding tools | `runpod-onboard` | Providers and Resources | Code-complete; apply/probe live countersign pending |
| Serve vLLM, llama.cpp, or SGLang behind an OpenAI-compatible capability | `/v1/serve` | `pitwall_serve_model` | `serve` | Models | Alpha/limited |
| Model catalogue and hardware fit | `/v1/models/catalogue*` | model tools | `models` | Models | Supported metadata; fit remains advisory |
| Outbound webhook subscription lifecycle | `/v1/webhook-subscriptions*` | Not exposed | Not exposed | Not exposed | Existing REST-only capability; outside the retained parity program |
| Database migration and retention operations | Not exposed | Not exposed | `db`, `retention` | Not exposed | Existing operator-only capability |
| Pitwall Agent Routing | Not broker REST | Not broker MCP (it has its own channel MCP server) | `agents` | Not broker TUI | Part of the one package; validated by `tests/agents/` |
| Authenticated network MCP | Not provided | Local stdio only | `mcp serve broker`, `mcp serve channel` | Not applicable | Deferred by security policy |

Four-surface parity applies to the retained 2026-09-01 product program. Older, intentionally
surface-specific capabilities such as webhook administration and database migration keep their
existing contracts; this work did not manufacture meaningless wrappers for them.

Unsupported provider operations fail explicitly before invocation. Dry-run and preview operations
perform no provider write or spend. Credentials remain references until an adapter invocation
boundary and are never part of these public models.
