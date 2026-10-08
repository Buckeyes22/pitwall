# Public alpha support matrix

This matrix separates repository support from external verification. “Supported hermetically”
means executable contracts, fixtures, security tests, and CI exist. “Countersign pending” means
the code is complete but no provider credential, spend, or resource authorization was supplied for
this release.

| Surface or capability | Status | Boundary |
| --- | --- | --- |
| REST API on loopback or authenticated non-loopback | Supported | Single-operator deployment; scoped bearer authorization and admin-secret controls |
| MCP over local stdio | Supported | 81 statically registered tools; network MCP remains unavailable |
| Operational CLI and database migrations | Supported | Managed Python 3.14.7; stable human/JSON output; explicit confirmations for human-operated spend/destruction |
| Textual dashboard | Supported hermetically | Overview, Providers, Leases, Models, Serve, Pods, Routes, Cost, Resources, and Operations; injectable sources and Pilot coverage |
| Postgres, Redis, reconciler, inbound webhook, and cost exporter | Supported | Canonical Compose topology; loopback host bindings by default |
| Provider-neutral runtime and persisted external identifiers | Supported hermetically | Static built-in adapters; credential references only; RunPod compatibility identifiers retained |
| Production routing | Supported hermetically | Deterministic priority/weighted plans, hard constraints, conservative budget admission, persisted plan identity, bounded job events |
| Structured pricing and cost truth-up | Supported hermetically | `zero`, `gpu_hour`, `per_request`, `per_second`, `per_token`, `per_vm_second`, `active_idle`, and `per_unit`; USD values are Decimal-authoritative |
| Pre-spend guardrails | Supported hermetically | High-confidence secrets block; supported PII may redact; bounded/opaque content follows configured policy; preview does not persist |
| RunPod serverless queue/LB and public OpenAI-compatible endpoints | Existing supported path | Operator supplies credentials and endpoint configuration; no live call was made for this release |
| RunPod market/catalogue and supported billing reads | Code-complete; countersign pending | Cached GPU/DC/availability/rate/bid/balance snapshot; billing categories remain explicitly unavailable when the captured contract cannot attribute an actual |
| RunPod resource control | Code-complete; countersign pending | Pods, serverless endpoints, templates, volumes, registry auth, and read-only Hub browse/search/get; Hub deploy/publish is unavailable |
| RunPod volume objects and bounded pod logs | Code-complete; countersign pending | Separate S3 credentials, path/checksum/byte/time limits, explicit overwrite/delete confirmation; no remote exec |
| RunPod onboarding | Code-complete; countersign pending | Plan-first, resumable and idempotent; exact plan confirmation for apply/resume; separately authorized bounded inference probe only |
| Vast.ai | Code-complete; countersign pending | Compute and availability only; no fake inference or actual-cost capability |
| Together | Code-complete; countersign pending | Synchronous inference and availability; bounded token pricing required before admission |
| Lambda Cloud | Code-complete; countersign pending | Compute and availability only; no fake inference or actual-cost capability |
| Alibaba Cloud Model Studio | Code-complete; countersign pending | Synchronous inference and availability in both halves from one committed catalog; Token Plan automation is gated behind an explicit acceptance; no live call was made for this release |
| Self-hosted OpenAI-compatible endpoints | Alpha/limited | Authenticated, budgeted, probed/cooled down, capacity-aware, and fit-aware; no host orchestration or host telemetry |
| RunPod pod leases and serve | Alpha/limited | vLLM, llama.cpp, and SGLang; operator remains responsible for model/image provenance, access, and capacity fit |
| Model catalogue and guarded console launch | Alpha/limited | Dossier arithmetic is advisory; launch remains behind serve budget, guardrail, and kill-switch gates |
| Outbound signed webhooks | Supported | Public HTTPS:443 destinations only, plus plain HTTP to exact loopback `host:port` targets the operator lists in `PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST`; version 1 envelope |
| Pitwall Agent Routing wheel/sdist and CLI | Supported component | Part of the one `pitwall` package and lock (Python 3.14); `pitwall agents`; `pitwall agents migrate` moves a standalone install |
| Agent Routing source plugins and shims | Supported component | `pitwall agents install` writes the shims and the Claude Code, Codex, and GitHub Copilot CLI plugins from package data; no clone required |
| Agent Routing on macOS | Hosted portable lane | Managed Python 3.14.7 Agent Routing smoke; broker images and Compose remain Linux-only |
| Free-tier gateway catalog sync and drift gate | Supported hermetically | Pinned upstream release with SHA-256 provenance, regenerated seeds, and a catalog drift gate; upstream is data only |
| Free-tier gateway fork and loopback supervisor | Supported component | Python gateway (`pitwall gateway serve`) in the one `pitwall` package; loopback-only sidecar supervised with bearer `PITWALL_GATEWAY_TOKEN` |
| Pi Workbench | Limited component | `pitwall workbench` in the one `pitwall` package; pinned Pi installed by `pitwall agents setup pi`, so Node 22.22.1 or later, Linux, and `flock` (util-linux); the workbench runs on Linux only, and `pitwall workbench launch` refuses to start on other systems; optional Bubblewrap restricted mode requires permitted user namespaces and util-linux 2.41 or later for `setpriv --seccomp-filter`; no unfinished native-child resume, no network allowlist, and no per-child credential sandbox |
| Free-pool quota state, lockouts, and zero-cost routing | Supported hermetically | Quota windows, per-model lockouts, strict zero-cost filter, and burn-down persistence; free routing stays behind the existing budget gate, guardrails, and kill switch |
| `openai_gateway` provider adapter | Supported hermetically | Keyless or optional `PITWALL_GATEWAY_API_KEY` credential reference; availability from catalog evidence with no egress; typed 429 quota signals; no compute, async, or actual-cost capability |
| In-repository GPU worker image | Deferred/unavailable | No image, workflow, default deployment, or successful worker entry point |
| MCP network transport | Deferred/unavailable | Local stdio only; no network listener or new identity subsystem |
| Hosted control plane, telemetry service, or SaaS | Not provided | Self-hosted software; Langfuse is opt-in |

## Provider capability contract

| Adapter | Compute | Sync inference | Async submit/status/cancel | Availability | Provider actual cost |
| --- | ---: | ---: | ---: | ---: | ---: |
| RunPod | Yes | Yes | Yes | Market service, not adapter | Exact Pod billing only |
| Vast.ai | Yes | No | No | Yes | No |
| Together | No | Yes | No | Yes | No |
| Lambda Cloud | Yes | No | No | Yes | No |
| OpenAI-compatible gateway | No | Yes | No | Catalog evidence only; no egress | No |
| Alibaba Cloud Model Studio | No | Yes | No | Yes | No |

The static registry rejects unsupported capabilities instead of relying on
`NotImplementedError`. Real-provider verification requires the named provider credential reference,
an explicit operation allow-list, resource/token/byte and spend caps, cleanup policy, and TTL.
RunPod actual-cost reads additionally require an exact persisted workload-to-Pod mapping and a
bounded authoritative billing window; endpoint, network-volume, aggregate, empty, and lagging
billing results remain unavailable and cannot write the ledger.

Pre-1.0 interfaces can change under the compatibility policy. Security fixes may remove unsafe
behavior without a deprecation period.
