# MCP Server & Tools Subsystem

## 1. Purpose & Scope

The MCP server (`pitwall.mcp`) is a server on the MCP Python SDK 2 `MCPServer`, serving protocol `2026-07-28` (stateless, `server/discover`) and the legacy `initialize` handshake (2024-11-05 to 2025-11-25) from one process, that exposes 81 named tools to MCP clients (e.g., Claude, Cursor, Codex). It translates MCP tool calls into Pitwall business-logic operations, bridging the MCP wire protocol to shared services for the registry, inference, leases, providers, RunPod resources/files/onboarding, FinOps, guardrails, production routing, and the free-tier gateway (catalog read and quota list).

The server runs as `python -m pitwall.mcp` over local stdio. The public alpha rejects SSE and
streamable-HTTP because it does not yet implement MCP HTTP authentication. The server accesses the
same database and domain services as REST handlers without routing calls through the REST API.

## 2. Components

### `pitwall.mcp` — Package init & server bootstrap

**File:** `src/pitwall/mcp/__init__.py`

Creates the `MCPServer("pitwall")` instance and registers all 81 tools via `register_all`, including `pitwall_health` (`{"ok", "database", "redis", "provider_registry"}`, all booleans). The server carries `INSTRUCTIONS`, the text returned in `server/discover` and `initialize` results: the catalogue-first serving flow (`pitwall_models_list`, `pitwall_models_fit`, `pitwall_serve_model`), RunPod mutation `intent` and `idempotency_key` rules (a repeated apply key replays the stored result), the optional `idempotency_key` of `pitwall_submit_inference`, `pitwall_submit_job`, `pitwall_lease_pod`, and `pitwall_serve_model` (a repeated key with the same request returns the original result or lease instead of running the work or launching a pod again, and a serve whose lease is no longer serving is refused; `mutation_in_progress` means retry the same key, and `idempotency_conflict`, `idempotency_mismatch`, or `lease_state_conflict` means use a new key), the budget refusal codes and their `remedy` field, and the `{"error": "<code>"}` tool-result shape. `server/discover` and `tools/list` results carry a one-hour public cache hint (`ttlMs: 3600000`, `cacheScope: "public"`). `register_all` removes the SDK's prompt, resource, completion, and logging handlers, so discovery advertises `tools` only; `subscriptions/listen` stays served. Exposes the `ensure_runtime_env()` guard so the stdio entrypoint can validate required env vars at startup (not at import time, keeping `import pitwall.mcp` hermetic for test collection).

**Invariant:** All 81 tools are registered before the server starts. The server must not start if `require_runtime_env("mcp")` fails.

### `pitwall.mcp.__main__` — Transport entrypoint

**File:** `src/pitwall/mcp/__main__.py`

`main()` reads `PITWALL_MCP_TRANSPORT` (default `"stdio"`). Any other value raises `SystemExit`
with an explicit security-boundary message before the MCP server starts. The accepted path calls
`mcp.run(transport="stdio")`. One stdio process answers both protocol eras: a 2026-07-28 client
sends `server/discover` and per-request `_meta`, a legacy client sends `initialize` first.

**Signature:** `def main(argv: Sequence[str] | None = None) -> None`

### `pitwall.mcp.registry` — Tool registry

**File:** `src/pitwall/mcp/registry.py`

Defines `TOOL_NAMES: frozenset[str]` (81 names) and `TOOL_REGISTRY: list[ToolSpec]`; the
name set and registry count remain exactly 81.

`ToolSpec` is a frozen dataclass:
```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., dict[str, Any]] | Callable[..., Awaitable[dict[str, Any]]]
    scope: str = "general"
```

`register_all(server: Any) -> None` iterates `TOOL_REGISTRY` and calls `server.add_tool(spec.handler, name=..., title=..., description=..., annotations=...)` for each, fills missing parameter descriptions, then installs the safe call boundary, `strip_unused_features(server)` (drops the prompt, resource, completion, and logging handlers, because SDK 2 advertises a capability for every registered handler), and `install_list_tools_filter(server)` (omits a tool's `outputSchema` when its keys are a subset of `GENERIC_OUTPUT_SCHEMA_KEYS = {"type", "additionalProperties", "title"}`, the generic dict schema that conveys no structure; `structuredContent` on `tools/call` is unaffected).

**Invariant:** `len(TOOL_REGISTRY) == 81`, and `set(ToolSpec.name for ToolSpec in TOOL_REGISTRY) == TOOL_NAMES`.

### `pitwall.mcp.schema_adapter` — Pydantic → JSON Schema

**File:** `src/pitwall/mcp/schema_adapter.py`

`pydantic_to_mcp_schema(model_cls: type[BaseModel]) -> dict[str, Any]`

Calls `model_cls.model_json_schema(by_alias=True)`, then:
1. Pops the `$defs` section and resolves all `$ref` pointers by inlining definitions.
2. Strips Pydantic-internal noise keys (`title`, `additionalProperties`).
3. Preserves `type`, `properties`, `required`, descriptions, constraints, defaults, and enum values.

Helper: `_resolve_refs(node: Any, defs: dict[str, Any]) -> Any` (recursive `$ref` resolution with deep-copy to avoid aliasing).
Helper: `_strip_keys(node: Any, strip: frozenset[str], keep_at_top: frozenset[str] | None, depth: int) -> Any`.

### `pitwall.mcp.error_adapter` — Exception mapping

**File:** `src/pitwall/mcp/error_adapter.py`

`adapt_error(exc: Exception) -> MCPError`

Extracts error data from service exceptions using `to_response_body()` → `to_dict()` → `error_code` attribute → fallback `"internal_error"`. Wraps it in `MCPError(code=<class>, message=..., data=...)` where `code` is the Pitwall class code from `error_codes`, application-defined and outside the JSON-RPC reserved range (see below). `register_error_code(error_code: str, mcp_code: int)` maintains the `dict[str, int]` populated at import time by `pitwall.mcp.error_codes.register_error_codes()`.

**Invariant:** `adapt_error` is bootstrapped — importing `pitwall.mcp.error_adapter` automatically populates the class map so callers never need to import `error_codes` explicitly. `data` still carries the original `{"error": "<code>", ...}` from the REST vocabulary. Clients never receive these integers: `safe_boundary` returns every tool failure as `isError: true` with `{"error": "<code>"}`, plus server-written fields for budget refusals and same-key-retry codes (see the retry rule below the class table). The class number is used inside Pitwall and in tests only.

### `pitwall.mcp.error_codes` — Class partition bootstrap (§12.2)

**File:** `src/pitwall/mcp/error_codes.py`

`register_error_codes() -> None` (idempotent) populates the `register_error_code()` map with the existing string vocabulary (no new strings invented). Unmapped codes (e.g. `internal_error`) stay at the fallback `PITWALL_ERROR_CODE_BASE = -31000`.

| MCP code | Class | String `error_code` members (existing vocabulary only) |
|---|---|---|
| `-31001` | authn/z | `capability_disabled`, `credential_reference_unset` |
| `-31002` | budget/spend | `budget_exhausted`, `budget_not_configured`, `budget_rejected`, `sub_budget_rejected`, `cap_exceeded`, `price_unknown`, `kill_switch_engaged` |
| `-31003` | validation (400/422) | `capability_not_found`, `invalid_proxy_path`, `provider_not_found`, `provider_capability_missing`, `invalid_provider_config`, `change_set_too_broad`, `unsupported_lease_patch`, `empty_lease_patch`, `lease_not_found`, `workload_not_found`, `webhook_subscription_not_found`, `webhook_target_not_allowed`, `idempotency_mismatch`, `pre_spend_payload_rejected`, `no_serve_history`, `rate_required`, `invalid_gpu_class`, `unknown_model`, `unknown_variant`, `invalid_template`, `invalid_budget_limits`, `stale_price`, `invalid_volume_file_request`, `volume_file_limit_exceeded`, `volume_file_not_found`, `volume_file_checksum_mismatch`, `invalid_gpu_selection`, `invalid_request`, `invalid_resource_id`, `resource_not_found`, `volume_grow_only`, `ttl_below_startup` |
| `-31004` | upstream/provider (502/503/504) | `no_providers_available`, `no_healthy_provider`, `provider_chain_exhausted`, `rate_limited`, `launch_failed`, `warm_failed`, `served_model_mismatch`, `provider_error`, `provider_timeout`, `malformed_provider_response`, `audit_unavailable`, `audit_write_failed`, `template_create_partial_failure`, `registry_replace_partial_failure`, `volume_file_provider_error`, `volume_file_timeout`, `volume_file_not_configured`, `volume_file_audit_unavailable`, `volume_file_audit_failed_after_change`, `job_cancel_failed`, `teardown_failed` |
| `-31005` | conflict/state (409) | `capability_conflict`, `provider_conflict`, `lease_state_conflict`, `lease_expiry_limit_exceeded`, `serve_conflict`, `job_not_ready`, `idempotency_conflict`, `mutation_outcome_ambiguous`, `mutation_in_progress`, `resource_name_conflict`, `volume_file_confirmation_required`, `volume_file_idempotency_conflict`, `volume_file_precondition_conflict`, `volume_file_mutation_outcome_ambiguous`, `illegal_lease_transition`, `lease_transition_error`, `job_not_cancellable` |
| `-31000` | fallback | any unmapped string, e.g. `internal_error`, `resolver_error`, `volume_file_error` |
| `-31010` | relay (`RELAY`, retryable) | `mcp_server_restarted`, `mcp_server_unavailable`; sent by `pitwall mcp relay`, not by the broker, so `register_error_codes()` maps no string to it |

Structured `ErrorData.data` is unchanged across all five classes.

**Retry rule.** `audit_unavailable` is the one code whose documented remedy is a retry with the same `idempotency_key` (§3.4 and `docs/operator/runpod-resource-controls.md`). A new key would repeat the mutation, and for a pod create that means a second pod. So the boundary adds a fixed, server-written `remedy` to it, and copies `retryable: true` when the error carries that flag. When a pod was created (`changed: true`, resource type `pod`), it also copies `changed` and the pod's `resource_id`, which is the id RunPod returned, and uses a remedy that says the pod is kept. The `detail` text and any request-supplied resource id never cross (`safe_boundary._same_key_retry_detail`). The idempotency-key refusals get a fixed remedy and a fixed `retryable` flag (`safe_boundary._IDEMPOTENCY_KEY_REMEDIES`): `mutation_in_progress` is retryable ("retry with the same idempotency_key once the first call finishes"), `idempotency_conflict` is not ("this key is spent; use a new idempotency_key"), and `idempotency_mismatch` is not ("this key was used for a different request; use a new idempotency_key"). These come from the RunPod control-plane journal, the lease_pod/serve_model replay, and the inference tools alike. No exception text or request value crosses.

### `pitwall.mcp.tools.admin` — Capability & provider write tools

**File:** `src/pitwall/mcp/tools/admin.py`

Implements 6 admin tools. All use `_capability_to_response` / `_provider_to_response` helpers to shape responses, call `insert_audit(pool, actor="mcp:admin", ...)` for every mutation, and raise domain exceptions. `pitwall_update_capability` and `pitwall_update_provider` write the changed fields and then apply `enabled` through the repository's enable/disable in one database transaction, so a failed toggle leaves no partial update. A call that changes `enabled` audits as `enable` or `disable` (so a `pitwall_audit_log` `action` filter finds it), plus an `update` row when it changed other fields too.

| Function | Signature |
|---|---|
| `pitwall_create_capability` | `(name: str, version: str, capability_class: str, cost_mode: str, description: str \| None = None, input_schema: dict[str, Any] \| None = None, output_schema: dict[str, Any] \| None = None) -> dict[str, Any]` |
| `pitwall_update_capability` | `(capability_id: str, name: str \| None = None, version: str \| None = None, description: str \| None = None, cost_mode: str \| None = None, enabled: bool \| None = None, input_schema: dict[str, Any] \| None = None, output_schema: dict[str, Any] \| None = None) -> dict[str, Any]` |
| `pitwall_create_provider` | `(capability_id: str, name: str, provider_type: str, runpod_endpoint_id: str \| None = None, runpod_template_id: str \| None = None, region: str \| None = None, cloud_type: str \| None = None, config: dict[str, Any] \| None = None, priority: int = 0, enabled: bool = True) -> dict[str, Any]` |
| `pitwall_update_provider` | `(provider_id: str, name: str \| None = None, provider_type: str \| None = None, runpod_endpoint_id: str \| None = None, runpod_template_id: str \| None = None, region: str \| None = None, cloud_type: str \| None = None, config: dict[str, Any] \| None = None, priority: int \| None = None, enabled: bool \| None = None, health_status: str \| None = None, consecutive_failures: int \| None = None, cooldown_trips: int \| None = None, cold_start_p50_ms: int \| None = None, cold_start_p95_ms: int \| None = None, recent_error_rate: float \| None = None) -> dict[str, Any]` |
| `pitwall_disable_provider` | `(provider_id: str) -> dict[str, Any]` |
| `pitwall_hibernate_provider` | `(provider_id: str) -> dict[str, Any]` |

All six are async and raise `CapabilityConflict`, `CapabilityNotFound`, `ProviderConflict`, or `ProviderNotFound` from `pitwall.api.exceptions`.

### `pitwall.mcp.tools.discovery` — Read-only capability/provider tools

**File:** `src/pitwall/mcp/tools/discovery.py`

Implements 4 discovery tools. All use `_capability_to_response` and `_provider_to_response` helpers.

| Function | Signature |
|---|---|
| `pitwall_list_capabilities` | `(capability_class: str \| None = None, cost_mode: str \| None = None, enabled: bool \| None = None) -> dict[str, Any]` |
| `pitwall_describe_capability` | `(name: str) -> dict[str, Any]` |
| `pitwall_list_providers` | `(capability_id: str \| None = None, provider_type: str \| None = None, enabled: bool \| None = None) -> dict[str, Any]` |
| `pitwall_get_provider_health` | `(provider_id: str) -> dict[str, Any]` |

Helpers `_parse_capability_class(value: str | None) -> CapabilityClass | None` and `_parse_provider_type(value: str | None) -> ProviderType | None` catch `ValueError` and return `None`. Raises `CapabilityNotFound` / `ProviderNotFound` from `pitwall.api.exceptions`.

### `pitwall.mcp.tools.inference` — Inference submission & job management

**File:** `src/pitwall/mcp/tools/inference.py`

Implements 5 inference tools as thin adapters over `ProductionRoutingService`. The shared service
owns capability/provider selection, conservative admission, idempotency, invocation, persisted
route identity, job lifecycle, and cost truth-up. Unsafe transport payload or webhook metadata is
rejected before pool acquisition and no transport contains a second resolver or job engine.

| Function | Signature |
|---|---|
| `pitwall_submit_inference` | `(capability_id: str, payload: dict[str, Any] \| None = None, provider_id: str \| None = None, dry_run: bool = False, idempotency_key: str \| None = None) -> dict[str, Any]` |
| `pitwall_submit_job` | `(capability_id: str, input: dict[str, Any], provider_id: str \| None = None, dry_run: bool = False, idempotency_key: str \| None = None, webhook_url: str \| None = None) -> dict[str, Any]` |
| `pitwall_get_job_status` | `(workload_id: str) -> dict[str, Any]` |
| `pitwall_get_job_result` | `(workload_id: str) -> dict[str, Any]` |
| `pitwall_cancel_job` | `(workload_id: str) -> dict[str, Any]` |

Submission, status, and cancellation use `normalize_workload_output` for the provider-neutral
persisted workload shape. Result reads use `ProductionRoutingService.job_result()` so REST, MCP,
CLI, and TUI share the same bounded, input-free result and unavailable-reason contract. The MCP
adapter layers that page onto its established workload envelope—cost, provider, external job,
plan, state, result, and trace fields remain present—without re-reading or serializing workload
input. Plan ids and payload-free plan explanations match REST, CLI, the OpenAI proxy, eligible
leases, and the Operations TUI (`pitwall.mcp.tools.inference`).

### `pitwall.mcp.tools.leases` — Pod lease management

**File:** `src/pitwall/mcp/tools/leases.py`

Implements 4 lease tools. All use `_lease_to_response` to shape responses.

| Function | Signature |
|---|---|
| `pitwall_lease_pod` | `(capability_id: str, provider_id: str \| None = None, dry_run: bool = False, idempotency_key: str \| None = None) -> dict[str, Any]` |
| `pitwall_get_lease` | `(lease_id: str) -> dict[str, Any]` |
| `pitwall_renew_lease` | `(lease_id: str, extends_minutes: int = 60, idempotency_key: str \| None = None) -> dict[str, Any]` |
| `pitwall_stop_lease` | `(lease_id: str, reason: str \| None = None) -> dict[str, Any]` |

`pitwall_lease_pod` calls `create_routed_lease` from `pitwall.api.leases.launch`, the same resolve, plan, launch, and plan-recording path as `POST /v1/leases`. `pitwall_stop_lease` calls `run_teardown` from `pitwall.api.leases.teardown`. Renewal uses the same atomic, bounded, audited, optionally idempotent mutation service as REST; it reserves the extension (rate × minutes) against the budget, and an extension past the budget is refused with `budget_rejected` (with the budget snapshot and remedy) and leaves the lease's expiry unchanged (`docs/sdlc/06-leases.md`, "Renewal reserves budget"). Lease creation raises `CapabilityNotFound`, `ProviderNotFound`, or `ProviderUnavailable`; the lease tools raise `LeaseNotFound` for an unknown lease.

`pitwall_lease_pod` is retry-safe with an `idempotency_key`. A repeated key with the same request (capability and provider pin) returns the lease the first call launched, in the same shape plus `replayed: true` (a first launch carries `replayed: false`), with the route plan that launch recorded (null until that launch finishes, and for good if it failed after recording its lease); nothing is planned, created at the provider, or budget-admitted again. This holds for RunPod, Lambda Cloud, and Vast leases. The same key with a different request is `idempotency_mismatch`; a key whose first launch has not recorded its lease yet is the retryable `mutation_in_progress` (retry the same key); a key whose launch ended without a lease is `idempotency_conflict` (use a new key). The rules are in `docs/sdlc/06-leases.md` ("Idempotent launches").

### `pitwall.mcp.tools.serve` — Model-serving lease

**File:** `src/pitwall/mcp/tools/serve.py`

| Function | Signature |
|---|---|
| `pitwall_serve_model` | `(capability: str, model: str, gpu_class: str, gpu_count: int = 1, engine: Engine = "vllm", variant: str \| None = None, template_id: str \| None = None, ttl_minutes: int = 120, idle_timeout_min: int \| None = None, max_usd_per_hour: Decimal \| None = None, renewal_policy: LeaseRenewalPolicy \| None = None, route: str \| None = None, image: str \| None = None, served_model_name: str \| None = None, rate_per_second: Decimal \| None = None, gated: bool = False, dry_run: bool = False, idempotency_key: str \| None = None) -> dict[str, Any]` |

The tool is a thin adapter that delegates only to `pitwall.serve` and uses the configured local catalogue. It launches vLLM, llama.cpp, or SGLang rows. Use the catalogue-first workflow: call `pitwall_models_fit`, select a fitting `variant`, then call `pitwall_serve_model` with that variant. GPU classes must use canonical RunPod names; supported legacy aliases normalize automatically. A model without a dossier requires `image` and may not pass `variant`. For catalogue models, result `model_id` is the dossier's `served_model_name` (falling back to the HF ID); consumers must use returned `model_id`, not the requested `model`. When fit output is `unpriced` (pricing unavailable), supply `rate_per_second`. `dry_run` validates and returns the resolved plan without launching a pod. `idempotency_key` makes a launch retry-safe: the same key and request return the first launch's lease (`created: false`) once it is active and the served model is listed within a verification window (`VERIFY_REQUEST_TIMEOUT_S`, up to three requests), with no registry write, price step, pod, or budget admission; a different request is `idempotency_mismatch`, a first launch still without a lease or a lease not yet active and verified is `mutation_in_progress` (retry the same key; an active lease whose pod does not list the model adds `reason: lease_not_serving`, its lease `id`, and a fixed remedy naming `pitwall_stop_lease`), a key whose lease is no longer serving is `lease_state_conflict`, and a key whose launch ended without a lease is `idempotency_conflict` (both: use a new key). No pod-lease serve path, keyed or not, publishes `lease.ready` before `verify_served_model` passes (the lease-less self-hosted warm path publishes after its own readiness probe) (`docs/sdlc/06-leases.md`, "Idempotent launches"). Service exceptions propagate unchanged: 409 `serve_conflict`; 422 `rate_required`, `unknown_variant` (including non-chat or unsupported-companion rows), `invalid_template`, or `invalid_gpu_class`; 502 `served_model_mismatch`; and 503 `launch_failed`. The manually serialized MCP fit response does not gain a field from the `FitOption.container_disk_gb` projection.

MCP dry runs remain registry-backed. The separate database-free catalogue preview is exposed only
as CLI `serve --plan-only`, so the MCP tool schema and mutation semantics are unchanged.
The CLI operator sequence and its paid/live verification boundary are documented in
[`docs/operator/serve-quickstart.md`](../operator/serve-quickstart.md).

`pitwall_serve_model(..., route: str | None = None)` performs the same local
registration hook. A supplied route adds `route_registration: {ok, action,
stderr}` to the result; a routing failure is returned there and does not turn a
successful serve into an MCP error.

### `pitwall.mcp.tools.models` — Model catalogue and hardware fit

**File:** `src/pitwall/mcp/tools/models.py`

| Function | Signature |
|---|---|
| `pitwall_models_list` | `() -> dict[str, Any]` |
| `pitwall_models_fit` | `(model: str, variant: str \| None = None, ttl_minutes: int = 120, cloud: str = "secure") -> dict[str, Any]` |

Both are thin wrappers over the public `pitwall.models` services and require no database. Use
`pitwall_models_fit` to select a catalogue variant and canonical GPU class before calling
`pitwall_serve_model`. `unpriced` means price data is unavailable rather than free hardware;
the later serve call needs `rate_per_second` for an unpriced selection.
The list response is `{models: [{model_id, vendor, family, openai_chat, variant_ids,
default_variant}]}`. Fit returns `{model_id, variant, confidence, price_source,
price_checked_at, price_age_seconds, price_stale, options}`; each option contains `gpu_class`, `gpu_count`, `vram_gb`,
`headroom_gb`, `fit`, `warm_cache`, `price_per_hour`, `cost_for_ttl`, `cloud`, and `max_count`.
Because this catalogue-only tool has no provider lookup, `warm_cache` is false.
The `fit` enum is `fits`, `tight` (positive single-GPU headroom below 10% of
aggregate VRAM), `tp`, or `no`; this is additive and does not change the response shape.
Decimals are strings, unavailable prices are null, and the timestamp is ISO 8601.
`ttl_minutes` must be at least 1 and `cloud` must be `secure` or `community`;
catalogue exceptions propagate to the MCP error adapter.
The optional `PITWALL_PRICE_MAX_AGE_S` policy marks fallback or over-age live data as stale.

### `pitwall.mcp.tools.cost` — Cost reporting

**File:** `src/pitwall/mcp/tools/cost.py`

| Function | Signature |
|---|---|
| `pitwall_cost_summary` | `(capability_class: str \| None = None, since: str \| None = None, until: str \| None = None) -> dict[str, Any]` |
| `pitwall_recent_workloads` | `(limit: int = 20, state: str \| None = None, capability_id: str \| None = None, provider_id: str \| None = None, provider_type: str \| None = None, since: str \| None = None, until: str \| None = None) -> dict[str, Any]` |

Both delegate to `pitwall.core.cost_reporting` (respectively `cost_summary` and `recent_workloads` service functions). Date strings are parsed via `_parse_date` (ISO format) and `_parse_datetime` (ISO format, UTC).

### `pitwall.mcp.tools.audit` — Audit log read

**File:** `src/pitwall/mcp/tools/audit.py`

| Function | Signature |
|---|---|
| `pitwall_audit_log` | `(entity_type: str \| None = None, entity_id: str \| None = None, action: str \| None = None, limit: int = 50) -> dict[str, Any]` |

Delegates to `pitwall.db.repository.list_audit`. Returns entries with id, actor, action, entity_type, entity_id, old_value, new_value, change_reason, and created_at.

### `pitwall.mcp.tools.copilot` — Broker Copilot proposals

**File:** `src/pitwall/mcp/tools/copilot.py`

| Function | Signature |
|---|---|
| `pitwall_copilot_propose` | `(intent: str, provider_ref: str \| None = None, provider_enabled: bool \| None = None, provider_priority: int \| None = None, provider_patch: dict[str, Any] \| None = None, scorecards: list[dict[str, Any]] \| None = None) -> dict[str, Any]` |

Proposal-only operator assistant. It reads current capabilities/providers through the repository layer, translates constrained provider intents (enable, disable, priority, or explicit provider patch) into a GitOps `DesiredState`, calls `pitwall.gitops.build_reconcile_plan`, and returns `desired_state`, `plan`, `diff`, `rationale`, and `recommendations`. It can convert optional scorecard snapshots (`DimensionScore` values) through `RecommendationEngine.recommend(...)` and use supported provider enablement recommendations as proposal input. It has no apply path and never mutates repositories.

### `pitwall.mcp.tools.output` — Workload normalization

**File:** `src/pitwall/mcp/tools/output.py`

`normalize_workload_output(workload: Workload) -> dict[str, Any]`

Returns a consistent dict:
```python
{
    "workload_id": workload.id,
    "cost": {
        "estimate_usd": _decimal_to_str(workload.cost_estimate_usd),
        "actual_usd": _decimal_to_str(workload.cost_actual_usd),
    },
    "provider_id": workload.provider_id,
    "state": workload.state.value if hasattr(workload.state, "value") else workload.state,
    "result": workload.result,
    "trace_id": workload.langfuse_trace_id,
}
```

Used by all inference and job tools. Decimal fields are serialized as strings.

### `pitwall.mcp.tools.burn_rate` — Burn-rate forecast

**File:** `src/pitwall/mcp/tools/burn_rate.py`

| Function | Signature |
|---|---|
| `pitwall_burn_rate` | `(window_days: int = 30) -> dict[str, object]` |

Read-only. Obtains the database pool and delegates to `pitwall.finops.burn_rate.read_configured_burn_rate`, which reads `pitwall.cost_daily` UTC rollups and forecasts against the effective monthly cap: the runtime `pitwall.budget_limits` row when one exists (`pitwall_budget_set`), else `PITWALL_MONTHLY_BUDGET_USD`. Returns the `BurnRateRead.to_dict()` shape: spend-to-date, daily burn rate, forecast total, remaining/percent-consumed budget, trend, confidence, data sufficiency, staleness, and a projected budget-breach timestamp. No mutation, no provider egress.

### `pitwall.mcp.tools.budget` — Budget limits

**File:** `src/pitwall/mcp/tools/budget.py`

| Function | Signature |
|---|---|
| `pitwall_budget_status` | `() -> dict[str, Any]` |
| `pitwall_budget_set` | `(reason: str, monthly_budget_usd: str \| None = None, per_request_max_usd: str \| None = None) -> dict[str, Any]` |

Both are async. `pitwall_budget_status` obtains the database pool and delegates to `pitwall.cost.budget_limits.budget_status`, returning the effective monthly budget and per-request cap (`source` is `runtime` when the audited `pitwall.budget_limits` row exists, else `environment`), the row's `updated_at`/`updated_by`/`reason`, `mtd_spend_usd`, and `budget_remaining_usd`. `pitwall_budget_set` calls `set_limits(..., actor="mcp")` with decimal-string limits; at least one limit and a non-empty `reason` are required, and a change takes effect on the next admission without a restart. The change and its `config_audit` row (`budget_limits.set`) are written in one transaction under the budget advisory lock. A `BudgetLimitsError` (`invalid_budget_limits`, 422) is mapped through `adapt_error`. Returns `{"limits": ..., "status": ...}`. See [`docs/operator/budget-limits.md`](../operator/budget-limits.md) for the runtime-versus-environment semantics. `BUDGET_REMEDY` in `pitwall.mcp.safe_boundary` points agents at `pitwall_budget_set` on `budget_rejected`, `sub_budget_rejected`, and `budget_exhausted` refusals.

### `pitwall.mcp.tools.guardrails` — Pre-spend guardrail inspection

**File:** `src/pitwall/mcp/tools/guardrails.py`

| Function | Signature |
|---|---|
| `pitwall_guardrail_status` | `() -> dict[str, Any]` |
| `pitwall_guardrail_preview` | `(payload: Any) -> dict[str, Any]` |

Both are synchronous, read-only adapters over the shared `PreSpendInspectionService` — the same pre-spend secret/PII scanner used before capability launches and volume-file uploads. `pitwall_guardrail_status` returns the configured policy mode (`PITWALL_PRE_SPEND_MODE`: `balanced`, `block`, or `redact`), the inspection limits (max depth/items/bytes/timeout), the active rule set, and non-sensitive aggregate counters. `pitwall_guardrail_preview` scans one arbitrary payload against the same rules and returns the decision (allow/redact/block) and findings; it performs no database, provider, audit, or counter writes.

### `pitwall.mcp.tools.provider_operations` — Provider operations read model

**File:** `src/pitwall/mcp/tools/provider_operations.py` (registration metadata in `src/pitwall/mcp/provider_operations_specs.py`, mirroring the onboarding-specs split)

| Function | Signature |
|---|---|
| `pitwall_provider_ops_list_descriptors` | `(capability_id: str \| None = None, enabled_only: bool = False, limit: int = 100) -> dict[str, object]` |
| `pitwall_provider_ops_describe` | `(provider_id: str) -> dict[str, object]` |
| `pitwall_provider_ops_availability` | `(provider_id: str, limit: int = 100) -> dict[str, object]` |
| `pitwall_provider_ops_health` | `(provider_id: str, probe: bool = False) -> dict[str, object]` |

All four are thin async adapters over `ProviderOperationsService`. Listing and describing read only persisted, sanitized provider descriptors — no provider-network egress. `pitwall_provider_ops_availability` always performs one explicit, bounded, read-only live availability probe against the provider. `pitwall_provider_ops_health` reads persisted health by default and performs a live probe only when `probe=true` is passed explicitly. Raises `ProviderNotFound` (via `adapt_error`) for an unknown provider; every other failure collapses to one of two stable, detail-free codes, `provider_operations_invalid_request` or `provider_operations_unavailable`.

### `pitwall.mcp.tools.runpod_market` — RunPod catalogue cache

**File:** `src/pitwall/mcp/tools/runpod_market.py`

| Function | Signature |
|---|---|
| `pitwall_runpod_catalogue` | `(force_refresh: bool = False) -> dict[str, object]` |

Read-only. Delegates to a lazily-built, process-local `RunpodMarketService` (cache TTL `PITWALL_RUNPOD_MARKET_CACHE_TTL_S`, default 300s). Returns the cached GPU catalogue, per-GPU availability, Decimal hourly prices, the account's current USD balance, and supported billing categories. `force_refresh=True` bypasses the cache for one live RunPod read; it spends nothing but does count against RunPod's own API rate limits.

### `pitwall.mcp.tools.runpod_resources` — Raw RunPod resource control plane

**File:** `src/pitwall/mcp/tools/runpod_resources.py` (shared service in `src/pitwall/runpod_control_plane.py`)

29 thin async tools — `RUNPOD_RESOURCE_TOOL_SPECS` — expose direct CRUD-style control of raw RunPod account resources (pods, serverless endpoints, templates, network volumes, container-registry auth) plus read-only Hub template search, all through one shared `RunPodControlPlaneService`. This is a separate control surface from the broker's lease/serve tools (`pitwall_lease_pod`, `pitwall_serve_model`): **a pod created through `pitwall_runpod_create_pod` now requires `ttl_minutes`, is admitted through `BudgetGate.try_launch_admission` (via `admit_raw_pod_lease`), creates a `pitwall.leases` row, and returns `{"lease_id": …}` under a TTL-bounded contract: expired raw-pod leases are terminated by the existing lease expiry reconciler and cost-closed at `max_cost_per_hour` when supplied, else at the pod's RunPod `costPerHr`, else at the admission's reservation rate ($0.50/h), never $0; raw pods expose no probe surface, so no readiness lifecycle applies. Other resources (endpoints, volumes, templates, registry auth) created here remain outside lease tracking, are not covered by Pitwall's in-pod self-termination deadline, and keep billing until explicitly stopped or deleted through these same tools (or the RunPod console). Every live mutation is gated through one shared boundary: `BudgetGate.check_available` (via `admit_resource_mutation`, is refused with the `budget_rejected` error code) runs before any provider I/O, and the service records a `config_audit` row on success. Previews and reads are never gated or recorded.**

The broker's `pitwall_lease_pod` and `pitwall_serve_model` follow the same retry rule without the journal: a repeated optional `idempotency_key` with the same request returns the first launch's lease and never creates a second pod, and the same key with a different request is refused (`idempotency_mismatch`). Their key state lives on the budget workload (`docs/sdlc/06-leases.md`, "Idempotent launches").

Every mutating request is a `MutationRequest` subclass carrying an explicit `intent: "preview" | "apply"` (`"preview"` validates and returns the planned effect, cost ceiling, and irreversibility without calling RunPod or writing an audit row; `"apply"` performs the call and requires a configured audit pool) and a required `idempotency_key`. Every mutation returns a `MutationResult` dict: `{operation, resource_type, resource_id, dry_run, changed, already_absent, effect, estimated_ceiling, irreversible, idempotency_key, resource, replayed}`; a repeated `idempotency_key` replays the stored result (§3.4). Deletes converge (`already_absent: true` on a repeat with a new key) and `irreversible: true`; `pitwall_runpod_grow_volume` is also `irreversible: true` (shrinking or an equal size is rejected). `pitwall_runpod_replace_registry_auth` deletes the existing credential and then recreates it — a failure between those two steps raises `registry_replace_partial_failure` and leaves the account with no valid registry auth in that slot rather than failing silently. Credentials and template environment values are never echoed back: registry-auth responses carry only `id`/`name`, and template responses carry `env_keys` plus opaque content fingerprints, never values.

**Error surfacing:** like `pitwall.mcp.tools.volume_files` and `pitwall.mcp.tools.provider_operations`, every one of these 29 handlers routes through a shared `_call` helper that calls `pitwall.mcp.error_adapter.adapt_error` on any `RunPodControlPlaneError` raised by the service. `RunPodControlPlaneError.to_dict()` is already redacted and credential-free (`{error, detail, operation, resource_type, resource_id, retryable, provider_status, changed}`), so the adapted `MCPError` carries that same structured code — e.g. `resource_not_found`, `resource_name_conflict`, `invalid_gpu_selection`, `volume_grow_only`, `provider_error`, `provider_timeout`, `credential_reference_unset`, `audit_unavailable`, `audit_write_failed`, `pre_spend_payload_rejected`, `malformed_provider_response`, `invalid_resource_id`, `invalid_request`, `template_create_partial_failure`, `registry_replace_partial_failure` — instead of the generic fallback. REST/CLI/TUI callers of the same `RunPodControlPlaneService` and MCP callers of these 29 tools now receive the same specific code. Any exception that is *not* a `RunPodControlPlaneError` is left to propagate: `pitwall.mcp.safe_boundary.install_safe_call_boundary` remains the backstop that reduces a genuinely unexpected failure to the generic `{"error": "tool_execution_failed"}`.

### `pitwall.mcp.tools.volume_files` — Network-volume files & pod logs

**File:** `src/pitwall/mcp/tools/volume_files.py` (registration metadata in `src/pitwall/mcp/volume_file_specs.py`; shared service in `src/pitwall/runpod_files.py`)

| Function | Signature |
|---|---|
| `pitwall_volume_list_objects` | `(volume_id: str, data_center_id: str, prefix: str = "", max_items: int = 200) -> dict[str, object]` |
| `pitwall_volume_read_chunk` | `(volume_id: str, data_center_id: str, object_key: str, offset: int = 0, max_bytes: int = 131072) -> dict[str, object]` |
| `pitwall_volume_upload_object` | `(volume_id: str, data_center_id: str, object_key: str, content_base64: str, *, intent: Literal["upload"], idempotency_key: str, confirm_overwrite: bool = False, expected_sha256: str \| None = None, dry_run: bool = False) -> dict[str, object]` |
| `pitwall_volume_delete_object` | `(volume_id: str, data_center_id: str, object_key: str, *, intent: Literal["delete"], idempotency_key: str, confirm_delete: bool = False, dry_run: bool = False) -> dict[str, object]` |
| `pitwall_pod_logs` | `(pod_id: str, max_lines: int = 100, max_bytes: int = 65536) -> dict[str, object]` |

All five are thin async adapters over the shared `VolumeFileService` and route every `VolumeFileError` subclass through `adapt_error` (`invalid_volume_file_request`, `volume_file_limit_exceeded`, `volume_file_confirmation_required`, `volume_file_not_found`, `volume_file_checksum_mismatch`, `volume_file_provider_error`, `volume_file_idempotency_conflict`, `volume_file_mutation_outcome_ambiguous`, `pre_spend_payload_rejected`, and more). Reads and writes talk to RunPod's S3-compatible network-volume API and are bounded to a single 128 KiB base64-framed transfer per call — MCP never streams a whole bucket. Upload and delete both require an explicit literal `intent` and an `idempotency_key`; upload additionally refuses to overwrite an existing object unless `confirm_overwrite=true`, verifies an optional `expected_sha256`, and scans the decoded body through the shared pre-spend guardrail before any provider write. Because file bytes have no safe rewrite contract, *any* non-`allow` guardrail decision (including `redact`) rejects the whole upload with `pre_spend_payload_rejected` instead of silently changing the uploaded bytes. Delete is irreversible; a delete retried after an ambiguous prior outcome raises `volume_file_mutation_outcome_ambiguous` rather than risk deleting a different object that reused the same key. `pitwall_pod_logs` reads ordered, provider-bounded pod stdout/stderr and passes every returned line through `redact_text` before it leaves the service. `pitwall_pod_logs` needs only `RUNPOD_API_KEY`; the four object tools return `volume_file_not_configured` before any validation, journaling, or provider call until the S3 credential references (`RUNPOD_S3_ACCESS_KEY`, `RUNPOD_S3_SECRET_KEY`) are set.

### `pitwall.mcp.tools.gateway` — Free-tier gateway catalog & quota snapshot

**File:** `src/pitwall/mcp/tools/gateway.py`

| Function | Signature |
|---|---|
| `pitwall_gateway_catalog_read` | `(tos: str \| None = None, routable_only: bool = False) -> dict[str, Any]` |
| `pitwall_quota_list` | `() -> dict[str, Any]` |

`pitwall_gateway_catalog_read` reads the synced `config/gateway-catalog.json` produced by `tools/gateway/sync_catalog.py` and optionally filters rows by ToS verdict or by ADR 0007 routability (`eligibility_gate`, `tos in {avoid, unknown}`, and `discontinued` rows are excluded when `routable_only=true`); the response always carries `source: "config/gateway-catalog.json"` plus `curated_at`, `totals`, and `avoid_list`. `pitwall_quota_list` obtains the shared database pool and delegates to `QuotaRepository.list_all`, adding a derived `headroom` ratio to each persisted record. Both tools are read-only — no provider egress, no database or audit writes, no `pitwall.cost` import — and the catalog handler never reaches the network even when the file is missing (returns an empty `providers` list). Both handlers carry the explicit `scope="gateway"` so the routing policy that gates free-tier rows stays discoverable from `TOOL_REGISTRY`.

## 3. Tool Inventory

`registry.py`'s `TOOL_REGISTRY` defines 36 tools as literal `ToolSpec` entries. The table below lists 30 of them, including `pitwall_health`, `pitwall_doctor`, and the two free-tier gateway tools (`pitwall_gateway_catalog_read`, `pitwall_quota_list`); the other 6 literal entries — the FinOps and guardrail tools (`pitwall_burn_rate`, `pitwall_budget_status`, `pitwall_budget_set`, `pitwall_guardrail_status`, `pitwall_guardrail_preview`) and `pitwall_runpod_catalogue` — are in §§ 3.1 and 3.3. The remaining 45 tools are contributed by feature-local `*_TOOL_SPECS` manifests that `registry.py` merges into `TOOL_REGISTRY`; onboarding (5 tools) and production-routing (2 tools) are covered in the Addendum and § 9 below, and the other 38 — provider operations, raw RunPod resource management, and volume-file/pod-log access — are covered in §§ 3.2, 3.4, and 3.5.

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_list_capabilities` | List registered capabilities; `cost_mode` and `enabled` filter SQL-side (`true`/`false`/omitted; omitted applies no enabled predicate) | `capability_class`, `cost_mode`, `enabled` | `{capabilities: [...]}` |
| `pitwall_describe_capability` | Single capability details | `name` | capability dict |
| `pitwall_list_providers` | List registered providers; `enabled` filters SQL-side (`true`/`false`/omitted) | `capability_id`, `provider_type`, `enabled` | `{providers: [...]}` |
| `pitwall_get_provider_health` | Provider health & cooldown | `provider_id` | health dict |
| `pitwall_submit_inference` | Sync inference request | `capability_id`, `provider_id`, `dry_run`, `idempotency_key`, `**kwargs` | workload dict |
| `pitwall_submit_job` | Async job submission | `capability_id`, `input`, `provider_id`, `dry_run`, `idempotency_key`, `webhook_url`, `**kwargs` | workload dict |
| `pitwall_get_job_status` | Async job state | `workload_id` | workload dict |
| `pitwall_get_job_result` | Async job result | `workload_id` | workload dict |
| `pitwall_cancel_job` | Cancel async job | `workload_id` | workload dict with `cancelled` flag |
| `pitwall_lease_pod` | Create pod lease | `capability_id`, `provider_id`, `dry_run`, `idempotency_key` | lease dict |
| `pitwall_get_lease` | Lease details | `lease_id` | lease dict |
| `pitwall_renew_lease` | Extend lease | `lease_id`, `extends_minutes`, `idempotency_key` | lease dict |
| `pitwall_stop_lease` | Tear down lease | `lease_id`, `reason` | lease dict |
| `pitwall_serve_model` | Launch or replay a model-serving pod lease | `capability`, `model`, `gpu_class`, `gpu_count`, `engine`, `variant`, `template_id`, `ttl_minutes`, `idle_timeout_min`, `max_usd_per_hour`, `renewal_policy`, `route`, `image`, `served_model_name`, `rate_per_second`, `gated`, `dry_run`, `idempotency_key` | serve result dict |
| `pitwall_models_list` | List curated model dossiers and variants | none | `{models: [...]}` with REST-compatible model summaries |
| `pitwall_models_fit` | Compare a model variant with GPU classes and estimate TTL cost | `model`, `variant`, `ttl_minutes`, `cloud` | REST-compatible fit response with `options` |
| `pitwall_cost_summary` | Aggregated cost | `capability_class`, `since`, `until` | `{total_usd, entries}` |
| `pitwall_recent_workloads` | Recent workload list | `limit`, `state`, `capability_id`, `provider_id`, `provider_type`, `since`, `until` | `{workloads}` |
| `pitwall_create_capability` | Register capability | `name`, `version`, `capability_class`, `cost_mode`, `description`, `input_schema`, `output_schema` | capability dict |
| `pitwall_update_capability` | Update capability | `capability_id`, `name`, `version`, `description`, `cost_mode`, `enabled`, `input_schema`, `output_schema` | capability dict |
| `pitwall_create_provider` | Register provider | `capability_id`, `name`, `provider_type`, `runpod_endpoint_id`, `runpod_template_id`, `region`, `cloud_type`, `config`, `priority`, `enabled` | provider dict |
| `pitwall_update_provider` | Update provider | `provider_id`, many optional fields | provider dict |
| `pitwall_disable_provider` | Disable provider | `provider_id` | provider dict |
| `pitwall_hibernate_provider` | Hibernate provider | `provider_id` | provider dict |
| `pitwall_audit_log` | Config audit trail | `entity_type`, `entity_id`, `action`, `limit` | `{entries}` |
| `pitwall_copilot_propose` | Proposal-only GitOps copilot | `intent`, optional provider patch fields, optional scorecard signals | `{proposal_only, applied, desired_state, plan, diff, rationale, recommendations}` |
| `pitwall_health` | Server health | (none) | `{ok, database, redis, provider_registry}`; every value is a boolean and `ok` is true only when all three checks pass |
| `pitwall_doctor` | Installation readiness report (install, config, services, spend controls) with a next step for every problem | `canary` | `{schema_version, mode, version, status, summary, checks}` |
| `pitwall_gateway_catalog_read` | Read the synced free-tier gateway catalog | `tos`, `routable_only` | `{source, providers, totals, avoid_list, curated_at}` |
| `pitwall_quota_list` | List persisted provider-quota snapshots via QuotaRepository | (none) | `{quotas: [{..., headroom}, ...]}` |

Every tool's display `title` and its `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) come from `pitwall.mcp.tool_metadata.TOOL_METADATA`. A tool is open-world when it reaches RunPod or another provider, and idempotent when an idempotency key or a converging operation makes a repeat harmless. `tests/mcp/test_tool_metadata.py` requires an entry for every registered name.

**Service-layer mapping:** Discovery tools → `CapabilityRepository` / `ProviderRepository`. Inference tools → `ProductionRoutingService` (`pitwall.routing.production`), the same service as the REST routes. Admin tools → `CapabilityRepository` / `ProviderRepository` + `insert_audit`. Lease tools → `LeaseRepository` + `run_launch` / `run_teardown`. Cost tools → `pitwall.core.cost_reporting`. Audit tool → `list_audit`. Copilot tool → `CapabilityRepository` / `ProviderRepository` + `pitwall.gitops.build_reconcile_plan` + optional `RecommendationEngine`. Burn-rate tool → `pitwall.finops.burn_rate.read_configured_burn_rate`. Budget tools → `pitwall.cost.budget_limits` (`budget_status`, `set_limits`). Guardrail tools → `PreSpendInspectionService`. Provider-operations tools → `ProviderOperationsService`. RunPod catalogue tool → `RunpodMarketService`. Raw RunPod resource tools → `RunPodControlPlaneService`. Volume-file and pod-log tools → `VolumeFileService`.

### 3.1 FinOps & guardrail tools

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_burn_rate` | Monthly budget burn-rate forecast from persisted UTC daily cost rollups | `window_days` (default 30) | `{spend_to_date_usd, daily_rate_usd, forecast_total_usd, budget_usd, remaining_budget_usd, percent_consumed, trend, confidence, data_sufficiency, stale, projected_breach_at, …}` |
| `pitwall_budget_status` | Effective monthly budget and per-request cap (runtime row or environment default), month-to-date spend, and remaining budget | none | `{monthly_budget_usd, per_request_max_usd, source: runtime\|environment, updated_at, updated_by, reason, mtd_spend_usd, budget_remaining_usd}` (decimals as strings) |
| `pitwall_budget_set` | Change the monthly budget and/or per-request cap without a restart; audited, reason required | `reason`, `monthly_budget_usd`, `per_request_max_usd` (decimal strings; at least one limit) | `{limits: {...}, status: {...}}` |
| `pitwall_guardrail_status` | Configured pre-spend guardrail policy, limits, rule set, and non-sensitive counters | none | `{mode, limits, rules, …}` |
| `pitwall_guardrail_preview` | Scan one payload for secrets/PII against the guardrail rules without writing anything | `payload` (any JSON value) | `{decision: allow\|redact\|block, findings: [...]}` |

`pitwall_burn_rate`, `pitwall_guardrail_status`, `pitwall_guardrail_preview`, and `pitwall_budget_status` are read-only: none writes to the database, calls a provider, or records an audit entry. `pitwall_budget_set` is the one write in this group: it upserts the single `pitwall.budget_limits` row and writes a `config_audit` entry (`budget_limits.set`, actor `mcp`) with the old and new values; it never calls a provider. Environment defaults (`PITWALL_MONTHLY_BUDGET_USD`, `PITWALL_PER_REQUEST_MAX_USD`) apply until a runtime row exists; see [`docs/operator/budget-limits.md`](../operator/budget-limits.md). `pitwall_burn_rate` forecasts against the same effective monthly cap; the guardrail tools read the `PITWALL_PRE_SPEND_MODE` policy (`balanced`, `block`, or `redact`) — the same guardrail service that inspects capability-launch payloads and volume-file uploads before they reach a provider.

### 3.2 Provider-operations tools

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_provider_ops_list_descriptors` | List safe, persisted provider descriptors; no provider-network egress | `capability_id`, `enabled_only`, `limit` (1-100, default 100) | `{items: [...], total}` |
| `pitwall_provider_ops_describe` | Describe one provider's persisted configuration; no provider-network egress | `provider_id` | descriptor dict |
| `pitwall_provider_ops_availability` | One explicit, bounded, read-only live availability probe against the provider | `provider_id`, `limit` (1-100, default 100) | availability dict |
| `pitwall_provider_ops_health` | Persisted provider health; a live probe only when `probe=true` | `provider_id`, `probe` (default false) | health dict |

Raises `ProviderNotFound` for an unknown `provider_id`; every other failure collapses to `provider_operations_invalid_request` or `provider_operations_unavailable` so no provider or database detail crosses the MCP boundary.

### 3.3 RunPod catalogue tool

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_catalogue` | Read the cached RunPod GPU catalogue, availability, Decimal prices, account USD balance, and supported billing categories | `force_refresh` (default false) | catalogue snapshot dict |

Read-only against a process-local cache (`PITWALL_RUNPOD_MARKET_CACHE_TTL_S`, default 300s, controls freshness); `force_refresh=true` makes one live RunPod call instead of serving the cache but does not create or change any resource.

### 3.4 Raw RunPod resource-management tools

These 29 tools give direct, low-level control over raw RunPod account resources (pods, serverless endpoints, templates, network volumes, container-registry auth) plus read-only RunPod Hub template search. They are a separate control surface from the broker's lease/serve tools (`pitwall_lease_pod`, `pitwall_serve_model`, `pitwall_get_lease`, `pitwall_stop_lease`): **a pod created through `pitwall_runpod_create_pod` now requires `ttl_minutes`, is admitted through `BudgetGate.try_launch_admission` (via `admit_raw_pod_lease`), creates a `pitwall.leases` row, and returns `{"lease_id": …}` under a TTL-bounded contract: expired raw-pod leases are terminated by the existing lease expiry reconciler and cost-closed at `max_cost_per_hour` when supplied, else at the pod's RunPod `costPerHr`, else at the admission's reservation rate ($0.50/h), never $0; raw pods expose no probe surface, so no readiness lifecycle applies. Other resources (endpoints, volumes, templates, registry auth) created here remain outside lease tracking, are not covered by Pitwall's in-pod self-termination deadline, and will keep billing until explicitly stopped or deleted through these same tools (or the RunPod console). Every live mutation is gated through one shared boundary: `BudgetGate.check_available` (via `admit_resource_mutation`, is refused with the `budget_rejected` error code) runs before any provider I/O, and the service records a `config_audit` row on success. Previews and reads are never gated or recorded.**

Every mutating tool takes a request object with an explicit `intent` field — `"preview"` validates the request and returns the planned effect, cost ceiling, and irreversibility without calling RunPod or writing an audit row; `"apply"` performs the call, requires a configured audit pool, and records an audit row on success — plus a required `idempotency_key`. Every mutation returns a `MutationResult`-shaped dict: `{operation, resource_type, resource_id, dry_run, changed, already_absent, effect, estimated_ceiling, irreversible, idempotency_key, resource, replayed}`. The key gives exact-key replay on `"apply"` (previews never touch it). Before any RunPod call, the service takes a per-key advisory lock, waiting at most the service timeout (else the retryable `mutation_in_progress`), and looks the key up in a journal of `pitwall.config_audit` rows (`kind: runpod_control_plane_mutation`; states `started`, `completed`, `failed`, `compensated`; the same design as the volume-file journal, indexed by `0041`). Read-only preconditions (unique-name checks, existence reads, the current volume size, the registry-replace checks) run under the lock before the `started` row, so a failed read leaves the key free. The same key with the same request returns the stored result with `replayed: true` and makes no RunPod call. The same key with a different request is refused with `idempotency_conflict`; over MCP, that refusal and `mutation_in_progress` carry the fixed remedy and `retryable` flag of the §12.2 retry rule. A key whose earlier attempt has an unknown outcome (a timeout, a 5xx or transport error, a partial failure, or a crash after the call started) is refused with `mutation_outcome_ambiguous` and is never re-applied: inspect the resource, delete or adopt it, and retry with a new key. A write that provably changed nothing (a precondition-style refusal or a RunPod 4xx) records `failed` and releases the key; a pod create never does, because its fallback attempts can leave a pod behind before the final error. `release_idempotency_key` appends `compensated` for a key whose resource the caller deleted, after which the key accepts a new request for the same operation, resource type, and resource id (any other request is an `idempotency_conflict`). Onboarding compensation uses it. An onboarding resume uses it only once the step's earlier outcome is settled: a completed step when a get by its recorded resource id answers not found (a lagging list alone replays the live resource), and an unknown outcome once the attempt is older than the 300 s timeout ceiling plus a 60 s margin (until then resume refuses with the ambiguous remedy). The `pitwall_runpod_create_pod` lease rollback uses it too. The journal stores only a SHA-256 request hash and the result fields above, plus, for `pod.create`, a `recovery` record of the attempt marker, pod name, `ttl_minutes`, and `max_cost_per_hour`. Credential-bearing values (environment values, process arguments, the registry username) enter that hash only through an HMAC-SHA256 keyed by SHA-256 of `pitwall-runpod-journal-v1` and the RunPod API key, so changing any of them is a different request, and the journal holds no plaintext and no unkeyed digest. Rotating the API key turns a pending same-key retry into a conflict. Such a request needs a resolvable RunPod API key (`credential_reference_unset` otherwise). Deletes with a new key converge (`already_absent: true` once the resource is gone) and are `irreversible: true`. `pitwall_runpod_grow_volume` is also `irreversible: true` (a request to shrink or keep size is rejected outright). Credentials and template environment values are never echoed back: registry-auth responses carry only `id`/`name`, and template responses carry `env_keys` plus opaque content fingerprints, never values.

**Budget gate:** before an `"apply"` mutation runs, `_call` checks the monthly budget through `BudgetGate.check_available` and answers `budget_rejected` when it is spent, then writes the audit row. Mutations whose only effect is to stop or remove billing (`terminate_pod`, `action_pod` with `stop`, and every `delete_*`) skip the budget check, so an exhausted budget never refuses the operations that end spend; they are still audited. A pod create whose provider result carries no pod id answers `malformed_provider_response`, records no lease, and names the pod for the operator to check in the RunPod console. A replayed `pitwall_runpod_create_pod` returns the lease the first attempt recorded for that pod rather than inserting a second one (it records the lease only if the first attempt stopped before doing so). When the lease insert fails, the tool first re-reads the lease for this pod. If one bound to this workload now exists (the reaper recorded it), the tool returns that lease. Otherwise it terminates the pod and then, in a separate step with its own log line, releases the create key, so a retry creates a new pod. If the re-read also fails, ownership is unknown, so the tool never terminates the pod. It answers the retryable `audit_unavailable`. Over MCP the client receives `{error, remedy, retryable: true, changed: true, resource_id: <pod id>}`, and the remedy says to retry with the same `idempotency_key` once the database is back (§12.2 retry rule). A failed terminate is treated the same way. In both cases the workload is not closed at $0: it stays open with its key, and the journal's `completed` row names the pod. The reaper then leases it by that id once the database is back, TTL teardown bounds its cost, and a same-key retry replays the create and records its lease. A replayed create with no lease whose pod is gone (a 404, or status `terminated` as the lease reconciler treats it; an `exited` pod still exists and is leased) is refused with `resource_not_found` rather than leasing a dead pod. If this call admitted a fresh workload, that workload is closed at $0. If the call reuses the original attempt's workload (`is_new: false`, kept open after a failed rollback or an unreadable lease store), the pod existed and billed. So the tool leases it by its id through `raw_pod_lease`, with the TTL counted from the attempt's start (its journal `started` row, `JournalEntry.attempt_age_s`). It then settles that lease at once as `pod_absent` through `settle_absent_raw_pod_lease` in `pitwall.api.leases.teardown`, the same function the reconciler's lease sweep uses for a gone pod. That function charges `rate × (now − attempt start)` and closes the workload with that cost, releasing its reservation. It does not wait for the sweep, which probes only leases within an hour of expiry. It does not close the workload at $0. A lease recorded late (this replay, or the reaper's adoption of a `started` or `completed` create) has `created_at` at the attempt's start, and `close_lease_cost` charges `rate × (terminated_at − created_at)` with no cap. The rate is the request's `max_cost_per_hour` when supplied. Otherwise it is RunPod's price for the pod: the `costPerHr` the create's result carried, as the journal's `completed` row recorded it (`teardown.raw_pod_observed_usd_per_hour`). That price is read at settlement and is never written into the lease's `max_usd_per_hour`. A price that rounds to $0 at 4 decimal places, or is above $1000/h, is ignored (`representable_usd_per_hour`). With neither known, it is the rate the budget admission reserved (`raw_pod_reservation_usd_per_hour`, $0.50/h), applied by `close_lease_cost`. The charge is never $0. Counting from the attempt's start overstates the cost of a pod that died early. A charge at the reservation rate can be above or below the pod's real price. So the figure can be wrong in either direction until an operator reconciles it against RunPod billing. A replay that finds a lease recorded against another attempt's workload reports that lease's workload and closes the fresh workload this call admitted. A failed create's workload is closed at $0 only when the journal shows the attempt stopped before its `started` row (no entry, or `failed`/`compensated`), so nothing was created. Closing it clears its idempotency key in the same statement (`WorkloadRepository.fail_and_release_idempotency_key`), so a same-key retry admits a fresh workload through the budget check. A create whose outcome is unknown (the journal kept `started`, for example after a timeout, a transport error, or `audit_write_failed`; an unreadable journal counts too) leaves its workload open, with its key bound and its reserved ceiling still counting, for the reconciler to settle. When the error names the pod (`audit_write_failed` carries the result's pod id; some client errors carry `pod_id`), the tool records that pod's lease as on success, so the lease expiry reconciler closes the workload at the lease's accrued cost once RunPod confirms the pod absent or its TTL teardown runs. Every applied pod create sets `PITWALL_CREATE_ATTEMPT` in the pod's env to a non-secret marker: the first 32 hex characters of SHA-256 over `pitwall-create-attempt-v1:` and the idempotency key (`create_attempt_marker`). It overrides any caller value and never enters the request hash. Without a pod id, `reap_orphaned_workloads` resolves the create from the marker and name its journal `started` row recorded. `pod.create` has no call ceiling (`OPERATION_TIMEOUT_CEILING_S`). What keeps the reaper off a running create is the key's advisory lock, which the create holds until it returns, so the reaper acts only when it can take that lock. It also waits until the key's newest journal row is more than 360 s old (`UNKNOWN_OUTCOME_GRACE_S`, the default 300 s call ceiling plus 60 s, timed from that row's `created_at`). That wait is a settling margin, checked on the reaper's first read and again on its re-read under the lock. It leaves a create that just completed to the tool's own lease insert, and lets a just-created pod appear in RunPod's list. It cannot exclude every race: a same-key replay of an old `completed` create inserts its lease after the margin. Leases are unique per workload (`idx_leases_workload_billing_identity`), so if the reaper's lease lands first, the tool's insert fails. The tool then re-reads the lease for this pod and workload and returns it, rather than rolling back:

- **One live pod whose env, as RunPod returns it, carries the attempt's marker:** its lease is recorded (`raw_pod_lease`, TTL counted from the attempt), so the lease sweep settles the workload at accrued cost.
- **No live pod with the marker or the name:** the workload closes at $0 (`raw_pod_create_absent`) and its key is freed.
- **A lookup error, several pods with the marker, or a pod with the name but no matching marker:** the workload is held and the pod ids are logged. Pitwall never adopts, and so never tears down, a pod it cannot prove it created.

The reaper also covers a `completed` create with no lease, which happens when the process stopped between the journal's `completed` write and the tool's lease insert. It leases the pod id that row recorded, because that id came from the create's own provider call, with the same TTL-from-attempt rule. A live record carrying another attempt's marker is held instead. A cancelled pod create waits for its create thread, and terminates any pod the thread produced, before it unwinds. Further cancellations are absorbed until then, so the key lock stays held while the thread runs.

A workload still unresolved past the orphan age threshold is charged its reserved ceiling (`reaped_unfinished`). Only an HTTP 404 from a get by id counts as not found: an empty or non-object 200 body is a `provider_error` or `malformed_provider_response`, so neither this replay check nor an onboarding resume releases a key on a provider glitch. An `idempotency_conflict`, `mutation_outcome_ambiguous`, or `mutation_in_progress` refusal leaves open a workload an earlier attempt admitted (`is_new: false`) and closes one the refused call admitted itself. The lease lookup is `LeaseRepository.latest_for_external_resource`.

**Error surfacing:** like the provider-operations and volume-file tools, these 29 handlers call `pitwall.mcp.error_adapter.adapt_error` on any `RunPodControlPlaneError` raised by the shared service, via a shared `_call` helper applied to every handler. `RunPodControlPlaneError` already carries a structured, redacted, credential-free `{code, resource_type, resource_id, retryable, provider_status, changed}` payload (`to_dict()`), so the adapted `MCPError`'s `error.data` carries that same code. Callers of the same `RunPodControlPlaneService` through REST/CLI/TUI and MCP callers of these 29 tools now receive the same specific code. An exception that is not a `RunPodControlPlaneError` is left unadapted; `pitwall.mcp.safe_boundary.install_safe_call_boundary` remains the backstop that reduces such an unexpected cause to the generic `{"error": "tool_execution_failed"}`.

#### Pods

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_list_pods` | List sanitized RunPod account pods | none | `{pods: [...]}` |
| `pitwall_runpod_get_pod` | Get one sanitized RunPod account pod | `resource_id` | pod dict |
| `pitwall_runpod_create_pod` | Preview or create a raw RunPod pod as a lease — requires `ttl_minutes`, budget-admitted via `BudgetGate` (through `admit_raw_pod_lease`), creates a `pitwall.leases` row and returns `{"lease_id": …}` with `MutationResult`. TTL-bounded: expired raw-pod leases are terminated by the existing lease expiry reconciler and cost-closed at `max_cost_per_hour` when supplied, else at the pod's RunPod `costPerHr`, else at the admission's reservation rate ($0.50/h), never $0; raw pods expose no probe surface, so no readiness lifecycle applies | `request`: name, image, gpu_type_ids, gpu_count, template_id, disk_gb, cloud, data_center_id, network_volume_id, ports, env, args, registry_auth_id, max_cost_per_hour, ttl_minutes, intent, idempotency_key | `{"lease_id": string, …MutationResult}` |
| `pitwall_runpod_update_pod` | Preview or update a pod's mutable fields (env, ports, or registry_auth_id) | `request`: resource_id, env, ports, registry_auth_id, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_action_pod` | Preview or start/stop/restart/reset a raw pod | `request`: resource_id, action, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_terminate_pod` | Preview or idempotently, permanently terminate a raw pod | `request`: resource_id, intent, idempotency_key | `MutationResult` dict |

`pitwall_runpod_create_pod`'s optional `max_cost_per_hour` is surfaced back as the result's `estimated_ceiling`. The create enforces it: a pod RunPod prices above it is skipped or terminated before readiness (`_gate_pod_cost_before_readiness`). It is also the charge rate for a lease settled from it. A cap that rounds to $0 at the lease's 4 decimal places is refused as `invalid_request` before any provider call. The lease's `max_usd_per_hour` (shown by `pitwall_get_lease`, the CLI and the TUI) is only this caller's cap, and stays null for an uncapped pod. Action `"reset"` and `terminate_pod` are always `irreversible: true`.

#### Serverless endpoints

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_list_endpoints` | List RunPod serverless endpoints and their nested scaling state | none | `{endpoints: [...]}` |
| `pitwall_runpod_get_endpoint` | Get one RunPod serverless endpoint | `resource_id` | endpoint dict |
| `pitwall_runpod_create_endpoint` | Preview or create a QUEUE or LOAD_BALANCER serverless endpoint from a template or image | `request`: name, endpoint_type, template_id, image, workers (minimum/maximum/idle_timeout_seconds), scaling (type/value), gpu (pools/excluded_type_ids/count), flashboot, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_update_endpoint` | Preview or replace an endpoint's nested workers/scaling/GPU selection | `request`: resource_id, workers, scaling, flashboot, gpu, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_delete_endpoint` | Preview or idempotently, permanently delete a serverless endpoint | `request`: resource_id, intent, idempotency_key | `MutationResult` dict |

A non-zero `workers.minimum` keeps that many workers warm and billed even while idle; the default `workers.minimum: 0` scales to zero between invocations.

#### Templates

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_list_templates` | List mutable, account-owned RunPod templates | none | `{templates: [...]}` |
| `pitwall_runpod_get_template` | Get one account-owned RunPod template | `resource_id` | template dict |
| `pitwall_runpod_create_template` | Preview or create an account template (no Hub publication) | `request`: name, image, disk_gb, volume_gb, volume_mount_path, args, env, serverless, registry_auth_id, ports, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_update_template` | Preview or update one or more fields on an account-owned template | `request`: resource_id, name, image, args, disk_gb, volume_gb, volume_mount_path, ports, env, serverless, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_delete_template` | Preview or idempotently, permanently delete an account template | `request`: resource_id, intent, idempotency_key | `MutationResult` dict |

Templates carry no direct RunPod cost themselves; cost follows the pods/endpoints later created from them. `env` values supplied here are stored by RunPod and never read back — read tools return `env_keys` and content fingerprints only.

#### Network volumes

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_list_volumes` | List RunPod network volumes | none | `{volumes: [...]}` |
| `pitwall_runpod_get_volume` | Get one RunPod network volume | `resource_id` | volume dict |
| `pitwall_runpod_create_volume` | Preview or create a RunPod network volume (billable persistent storage) | `request`: name, size_gb, data_center_id, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_grow_volume` | Preview or grow a volume's size; shrinking or an equal size is rejected | `request`: resource_id, size_gb, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_delete_volume` | Preview or idempotently, permanently delete a volume and all its data | `request`: resource_id, intent, idempotency_key | `MutationResult` dict |

#### Container-registry auth

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_list_registry_auths` | List registry-auth IDs and names; credentials are never returned | none | `{registry_auths: [...]}` |
| `pitwall_runpod_get_registry_auth` | Get one registry-auth ID and name | `resource_id` | registry-auth dict |
| `pitwall_runpod_create_registry_auth` | Preview or create registry auth; the password is read server-side from the named `password_env` variable, never from tool input | `request`: name, username, password_env, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_replace_registry_auth` | Preview or delete-then-recreate registry auth under the same slot; a failure between delete and recreate is reported explicitly (`registry_replace_partial_failure`) rather than silently leaving no auth configured | `request`: resource_id, name, username, password_env, intent, idempotency_key | `MutationResult` dict |
| `pitwall_runpod_delete_registry_auth` | Preview or idempotently, permanently delete registry auth | `request`: resource_id, intent, idempotency_key | `MutationResult` dict |

#### RunPod Hub templates (read-only)

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_runpod_list_hub_templates` | Paginated read-only list of public RunPod Hub templates | `limit` (default 50), `offset` (default 0) | `{hub_templates: [...]}` |
| `pitwall_runpod_get_hub_template` | Read-only get of one public RunPod Hub template | `resource_id` | hub-template dict |
| `pitwall_runpod_search_hub_templates` | Read-only local keyword search over public RunPod Hub templates | `query`, `limit` (default 50) | `{hub_templates: [...]}` |

### 3.5 RunPod volume-file & pod-log tools

| Tool Name | Description | Inputs | Outputs |
|---|---|---|---|
| `pitwall_volume_list_objects` | List one bounded page of objects on a RunPod network volume | `volume_id`, `data_center_id`, `prefix`, `max_items` (default 200) | `{objects: [...], truncated}` |
| `pitwall_volume_read_chunk` | Read one bounded (≤128 KiB), base64-framed object chunk | `volume_id`, `data_center_id`, `object_key`, `offset` (default 0), `max_bytes` (default 131072) | chunk dict (base64 body) |
| `pitwall_volume_upload_object` | Upload one bounded (≤128 KiB) base64-encoded object; scans the decoded body through the pre-spend guardrail first and refuses to overwrite without confirmation | `volume_id`, `data_center_id`, `object_key`, `content_base64`, `intent: "upload"`, `idempotency_key`, `confirm_overwrite`, `expected_sha256`, `dry_run` | `VolumeFileResult` dict |
| `pitwall_volume_delete_object` | Delete one network-volume object; requires explicit confirmation and is irreversible | `volume_id`, `data_center_id`, `object_key`, `intent: "delete"`, `idempotency_key`, `confirm_delete`, `dry_run` | `VolumeFileResult` dict |
| `pitwall_pod_logs` | Read ordered, redacted, byte- and line-bounded pod stdout/stderr diagnostics | `pod_id`, `max_lines` (default 100), `max_bytes` (default 65536) | `{logs: [...], truncated}` |

MCP transfers are capped at 128 KiB per call — these tools never stream a whole bucket or log file. Because file bytes have no safe partial-rewrite contract, any guardrail decision other than "allow" (including "redact") rejects an upload outright with `pre_spend_payload_rejected` instead of silently changing the uploaded bytes. `pitwall_pod_logs` passes every returned line through `redact_text` before it leaves the service. Both `pitwall_volume_upload_object` and `pitwall_volume_delete_object` require an `idempotency_key` for any non-`dry_run` call; a delete retried after an ambiguous prior outcome raises `volume_file_mutation_outcome_ambiguous` rather than risk deleting a different object that reused the same key.

## 4. Public Interfaces

Functions and classes callable from other subsystems:

| Symbol | Module | Signature |
|---|---|---|
| `mcp` | `pitwall.mcp` | `MCPServer` instance (run target) |
| `INSTRUCTIONS` | `pitwall.mcp` | `str` (server instructions for `server/discover` and `initialize`) |
| `ensure_runtime_env` | `pitwall.mcp` | `() -> None` |
| `TOOL_NAMES` | `pitwall.mcp.registry` | `frozenset[str]` |
| `TOOL_REGISTRY` | `pitwall.mcp.registry` | `list[ToolSpec]` |
| `ToolSpec` | `pitwall.mcp.registry` | `@dataclass frozen` |
| `register_all` | `pitwall.mcp.registry` | `(server: Any) -> None` |
| `install_list_tools_filter` | `pitwall.mcp.registry` | `(server: Any) -> None` |
| `pydantic_to_mcp_schema` | `pitwall.mcp.schema_adapter` | `(model_cls: type[BaseModel]) -> dict[str, Any]` |
| `adapt_error` | `pitwall.mcp.error_adapter` | `(exc: Exception) -> MCPError` |
| `register_error_code` | `pitwall.mcp.error_adapter` | `(error_code: str, mcp_code: int) -> None` |
| `PITWALL_ERROR_CODE_BASE` | `pitwall.mcp.error_adapter` | `int` (= -31000) |
| `normalize_workload_output` | `pitwall.mcp.tools.output` | `(workload: Workload) -> dict[str, Any]` |

## 5. Configuration

Environment variables read by the MCP subsystem:

| Env Var | Default | Type | Description |
|---|---|---|---|
| `PITWALL_MCP_TRANSPORT` | `"stdio"` | `Literal["stdio"]` | Local transport; every other value is rejected |
| `PITWALL_MONTHLY_BUDGET_USD` | `50.0` | `Decimal` | Environment default for the monthly budget cap, used by `pitwall_burn_rate` and reported by `pitwall_budget_status` until `pitwall_budget_set` writes a runtime row |
| `PITWALL_PER_REQUEST_MAX_USD` | `10.0` | `Decimal` | Environment default for the per-request cost cap reported by `pitwall_budget_status`; overridden by a runtime row written by `pitwall_budget_set` |
| `PITWALL_PRE_SPEND_MODE` | `"balanced"` | `Literal["balanced", "block", "redact"]` | Guardrail policy read by `pitwall_guardrail_status`/`pitwall_guardrail_preview` and enforced on every `pitwall_volume_upload_object` call |
| `PITWALL_RUNPOD_MARKET_CACHE_TTL_S` | `300.0` | `float` | Cache freshness window for `pitwall_runpod_catalogue` |

Required runtime env for the MCP service (validated by `require_runtime_env("mcp")`):
- `RUNPOD_API_KEY`
- `DATABASE_URL`
- `REDIS_URL`

The transport setting is also validated as `Literal["stdio"]` by `PitwallSettings`; the process
entry point reads it directly so it can fail before serving.

Protocol versions: from `mcp_types.version` (`MODERN_PROTOCOL_VERSIONS`, `HANDSHAKE_PROTOCOL_VERSIONS`).

## 6. Failure Modes & Error Types

**Startup errors:**
- `SystemExit(os.EX_CONFIG)` — raised by `require_runtime_env()` if `RUNPOD_API_KEY`, `DATABASE_URL`, or `REDIS_URL` is missing or whitespace-empty.
- `SystemExit` — raised by `main()` if `PITWALL_MCP_TRANSPORT` is anything other than `stdio`.

**Runtime exceptions raised by tools:**

`pitwall.api.exceptions.PitwallApiError` and its subclasses, all with `error_code` class attributes:

| Class | error_code | Raised by |
|---|---|---|
| `CapabilityNotFound` | `"capability_not_found"` | discovery, admin, inference tools |
| `CapabilityDisabled` | `"capability_disabled"` | inference tools |
| `CapabilityConflict` | `"capability_conflict"` | admin create tools |
| `ProviderNotFound` | `"provider_not_found"` | discovery, admin, leases, inference tools |
| `ProviderUnavailable` | `"no_providers_available"` | inference, leases tools |
| `ProviderConflict` | `"provider_conflict"` | admin create tools |
| `LeaseNotFound` | `"lease_not_found"` | leases tools |
| `LeaseStateConflict` | `"lease_state_conflict"` | lease renewal/stop |
| `IdempotencyMismatch` | `"idempotency_mismatch"` | inference tools |
| `WorkloadNotFound` | `"workload_not_found"` | job status/result/cancel |
| `JobNotReady` | `"job_not_ready"` | job result |
| `JobNotCancellable` | `"job_not_cancellable"` | job cancel (not an async job, or the provider cannot cancel) |
| `JobCancelFailed` | `"job_cancel_failed"` | job cancel (provider call failed; retryable) |
| `ChangeSetTooBroad` | `"change_set_too_broad"` | admin update |
| `RateLimited` | `"rate_limited"` | inference (RunPod) |
| `ServeInvalidGpuClass` | `"invalid_gpu_class"` | serve-model |
| `ServeRateRequired` | `"rate_required"` | serve-model |
| `ServeUnknownVariant` | `"unknown_variant"` | serve-model |
| `ServeTemplateInvalid` | `"invalid_template"` | serve-model |
| `ServeTtlBelowStartup` | `"ttl_below_startup"` | serve-model |
| `ServeVerificationFailed` | `"served_model_mismatch"` | serve-model |
| `ServeLaunchFailed` | `"launch_failed"` | serve-model |

`ValueError` — raised directly in admin tools when `capability_class`, `cost_mode`, or `provider_type` has an unrecognized enum value.

**Error adapter:** `adapt_error()` converts every `PitwallApiError` subclass to an `MCPError` with the class code from `error_codes` (fallback `-31000`) and `data` from `to_response_body()`. `safe_boundary` then reduces it to the stable string code in `data["error"]`.

**Idempotency edge case:** If an `idempotency_key` is provided but the canonical JSON of the new capability params differs from the original submission stored in `pitwall.workloads.idempotency_key`, `IdempotencyMismatch` is raised with the replayed workload ID.

**Dry run:** Every submit/lease tool returns a synthetic response with `dry_run: True`, `state: "completed"` or `"queued"`, and no real Workload record is created.

**Volume-file errors** (`pitwall.runpod_files.VolumeFileError` and subclasses, adapted via `adapt_error`):

| Class | error_code | Raised by |
|---|---|---|
| `VolumeFileValidationError` | `"invalid_volume_file_request"` | all five volume-file/pod-log tools |
| `VolumeFileLimitExceeded` | `"volume_file_limit_exceeded"` | list/read/upload when a request exceeds a bounded limit |
| `VolumeFileConfirmationRequired` | `"volume_file_confirmation_required"` | upload over an existing key without `confirm_overwrite`; delete without `confirm_delete` |
| `VolumeFileNotFound` | `"volume_file_not_found"` | read/delete of a missing object |
| `VolumeFileChecksumMismatch` | `"volume_file_checksum_mismatch"` | upload whose body does not match `expected_sha256` |
| `VolumeFileProviderError` | `"volume_file_provider_error"` | any RunPod S3-compatible provider failure |
| `VolumeFileIdempotencyConflict` | `"volume_file_idempotency_conflict"` | `idempotency_key` reused for a different request |
| `VolumeFileMutationAmbiguous` | `"volume_file_mutation_outcome_ambiguous"` | delete retried after an unresolved prior provider outcome |
| `VolumeFilePreSpendRejected` | `"pre_spend_payload_rejected"` | upload/delete/list/logs payload tripping any non-`allow` guardrail decision |
| `VolumeFileAuditUnavailable` | `"volume_file_audit_unavailable"` | live mutation attempted with no audit pool configured |

**Budget-limit errors:** `pitwall_budget_set` raises `BudgetLimitsError` (`"invalid_budget_limits"`, 422, class `-31003` validation) through `adapt_error` when neither limit is given, a limit is not a positive finite decimal, or `reason` is empty or whitespace. `pitwall_budget_status` has no tool-specific error. Spend refusals from other tools (`budget_rejected`, `sub_budget_rejected`, `budget_exhausted`) carry a `reason`, a decimal-string `snapshot`, and a `remedy` (`BUDGET_REMEDY` in `pitwall.mcp.safe_boundary`) that names `pitwall_budget_set`.

**Provider-operations errors:** `ProviderNotFound` (via `adapt_error`) for an unknown `provider_id`; every other failure is deliberately generic — `provider_operations_invalid_request` (bad input) or `provider_operations_unavailable` (any other failure, including provider/database errors, which are never reflected to the client).

**Raw RunPod resource-management errors:** `RunPodControlPlaneService` raises `RunPodControlPlaneError` with a structured `{code, resource_type, resource_id, retryable, provider_status, changed}` payload (`to_dict()`), the same shape the REST/CLI/TUI surfaces of this service consume. The 29 tools in `pitwall.mcp.tools.runpod_resources` call `adapt_error` on it via a shared `_call` helper, so MCP clients receive that same structured code (e.g. `resource_not_found`, `resource_name_conflict`, `provider_error`, `provider_timeout`, `invalid_gpu_selection`, `volume_grow_only`, and the other codes `RunPodControlPlaneError` carries) instead of a generic fallback. Any exception that is not a `RunPodControlPlaneError` still propagates past these tools unadapted; `pitwall.mcp.safe_boundary.install_safe_call_boundary` remains the backstop, reducing that cause to the generic `{"error": "tool_execution_failed"}`. See § 3.4 for detail.

**Global error boundary:** `pitwall.mcp.safe_boundary.install_safe_call_boundary` (installed by `register_all()` for every tool, including all those above) wraps the MCP SDK 2 `MCPServer`'s tool-call dispatch so that only a stable `{"error": "<code>"}` payload ever reaches the client — never raw exception text or echoed request data. Budget refusals and same-key-retry codes add only server-written fields (§12.2 retry rule). It extracts the code from an `MCPError`'s `error.data["error"]` when present, then from a class-level `error_code` (every `PitwallApiError`, `BudgetRejected`, and the catalogue's `UnknownModel`/`UnknownVariant`, so a tool that raises `CapabilityNotFound` directly still reports `capability_not_found` and `pitwall_models_fit` on an unknown model reports `unknown_model`), and otherwise reports the generic `"tool_execution_failed"` (including a Pydantic error raised inside a tool body, which is an execution failure rather than a bad argument). Every advertised input schema sets `additionalProperties: false`, and the boundary refuses a call carrying any undeclared argument before the handler runs, so a misspelled `dry_run` can never fall through to a real, paid call. An unknown tool name is a JSON-RPC error `-32602` with `data: {"error": "unknown_tool"}`. An undeclared argument returns `{"error": "invalid_tool_arguments", "allowed": [...]}`, and a schema-validation failure returns `{"error": "invalid_tool_arguments", "fields": [...]}`. Both lists contain declared parameter names only, never caller values. Every `tools/call` draws from a per-process token bucket (20 per second, burst 200), including calls the boundary then refuses as an unknown tool or an undeclared argument; calls beyond the bucket return `{"error": "rate_limited", "retry_after_s": N}`.

## 7. Testing

| File / Path | What it covers |
|---|---|
| `tests/fakes/mcp.py` | `FakeServiceLayerRecorder` — wraps `TOOL_REGISTRY` handlers to record calls, results, and errors for hermetic contract testing. Provides `install()`/`uninstall()` to patch/restore `ToolSpec.handler` at runtime, `call_tool()`, `get_calls()`, `assert_called()`, and `reset()`. |
| `tests/mcp/test_stdio_transport.py` | Starts the installed-style stdio subprocess and validates MCP initialization and tool discovery. |
| `tests/mcp/test_entrypoint.py` | Proves stdio dispatch and fail-closed rejection of every network transport value. |
| `tests/mcp/test_tool_contract.py` | Validates registered names, schemas, behavior, and error adaptation. |
| `tests/mcp/test_burn_rate_tool.py` | `pitwall_burn_rate` contract against a fake pool/settings. |
| `tests/mcp/test_guardrail_tools.py` | `pitwall_guardrail_status`/`pitwall_guardrail_preview` contract, including the no-write invariant. |
| `tests/mcp/test_provider_operations_tools.py` | `PROVIDER_OPERATIONS_TOOL_SPECS` contract and the two generic error codes. |
| `tests/mcp/test_runpod_resources.py` | `RUNPOD_RESOURCE_TOOL_SPECS` manifest completeness and thin-adapter behavior against a stub `RunPodControlPlaneService`; every handler routes through the shared `_call`/`adapt_error` helper; a `RunPodControlPlaneError` on both a read and a mutation tool surfaces its real structured code; an unexpected exception is not swallowed and still degrades to `tool_execution_failed` via `safe_boundary`. |
| `tests/mcp/test_volume_file_tools.py` | `VOLUME_FILE_TOOL_SPECS` contract, including bounded transfer size and `adapt_error` mapping. |
| `tests/runpod_control_plane/test_service.py` | `RunPodControlPlaneService` behavior directly: preview/apply, idempotent delete, irreversibility flags, and `registry_replace_partial_failure`. |
| `tests/security/test_pre_spend_inspection.py`, `tests/security/test_pre_spend_payload_guardrails.py` | `PreSpendInspectionService` policy modes and payload scanning shared by the guardrail and volume-upload tools. |
| `tests/test_runpod_market.py`, `tests/test_runpod_market_surfaces.py` | `RunpodMarketService` caching/refresh behavior behind `pitwall_runpod_catalogue`. |

The public-alpha suite intentionally contains no SSE server fixture: network MCP is outside the
supported and authenticated surface.

## 8. Dependencies

**Internal imports (Pitwall):**

| Source module | What's imported | Used in |
|---|---|---|
| `pitwall.config` | `require_runtime_env`, `load_settings_from_env` | `__init__.py`, `inference.py` |
| `pitwall.core.enums` | `CapabilityClass`, `CapabilitySource`, `CostMode`, `ProviderType`, `ResultDelivery` | `admin.py`, `discovery.py` |
| `pitwall.core.models` | `Capability`, `Provider`, `Workload`, `CapabilityDefaults` | `admin.py`, `discovery.py`, `output.py` |
| `pitwall.routing.production` | `ProductionRoutingService` and its route/replay errors | `inference.py` |
| `pitwall.core.cost_reporting` | `cost_summary`, `recent_workloads` service functions | `cost.py` |
| `pitwall.db` | `get_pool` | all tool modules |
| `pitwall.db.repository` | `CapabilityRepository`, `ProviderRepository`, `LeaseRepository`, `WorkloadRepository`, `insert_audit`, `list_audit` | `admin.py`, `discovery.py`, `inference.py`, `leases.py`, `audit.py` |
| `pitwall.resolver` | `CapabilityDisabledError`, `CapabilityNotFoundError`, `NoHealthyProviderError`, `ProviderNotFoundError` | `inference.py` |
| `pitwall.api.exceptions` | `PitwallApiError` subclasses | all tool modules |
| `pitwall.api.provider_schemas` | `validate_provider_registration_config` | `admin.py` |
| `pitwall.api.leases.launch` | `run_launch` | `leases.py` |
| `pitwall.api.leases.teardown` | `run_teardown` | `leases.py` |
| `pitwall.core.ids` | `ulid_new` | `admin.py` |
| `pitwall.finops.burn_rate` | `read_configured_burn_rate` | `burn_rate.py` |
| `pitwall.security.pre_spend` | `PreSpendInspectionService`, `get_pre_spend_inspection_service`, `PreSpendDecision` | `guardrails.py`, `runpod_control_plane.py`, `runpod_files.py` |
| `pitwall.providers.service` | `ProviderOperationsService` | `provider_operations.py` |
| `pitwall.runpod_market` | `RunpodMarketService`, `build_configured_runpod_market_service` | `runpod_market.py` |
| `pitwall.runpod_control_plane` | `RunPodControlPlaneService`, `RunPodControlPlaneError`, per-resource request/resource models | `runpod_resources.py` |
| `pitwall.runpod_files` | `VolumeFileService`, `VolumeFileError` subclasses, `build_configured_volume_file_service` | `volume_files.py` |
| `pitwall.security.redaction` | `redact_text` | `runpod_files.py` (pod-log lines) |

**External dependencies:**

| Library | Version/Source | Used for |
|---|---|---|
| `mcp` (SDK) | `mcp.server.mcpserver.MCPServer (mcp>=2.3.0,<3)` | Server bootstrap, `add_tool` registration, cache hints |
| `pydantic` | `BaseModel` | `schema_adapter.pydantic_to_mcp_schema` |

---

## Addendum: RunPod onboarding tools

`pitwall.mcp.tools.onboarding` provides five thin tools:
`pitwall_runpod_onboarding_plan`, `pitwall_runpod_onboarding_apply`,
`pitwall_runpod_onboarding_status`, `pitwall_runpod_onboarding_resume`, and
`pitwall_runpod_onboarding_rollback`. All accept the same secret-free request dictionary; apply and
resume additionally require the exact deterministic `confirmed_plan_id`.
`pitwall.mcp.onboarding_specs.ONBOARDING_TOOL_SPECS` is the feature-local
declaration consumed by the serialized global registry integration
(`pitwall.mcp.tools.onboarding`,
`pitwall.mcp.onboarding_specs`).

Each call validates `RunPodOnboardingRequest`, constructs the shared
`OnboardingCommand`, delegates once, returns the shared result as JSON, and
closes its owned discovery client. Validation and service failures map to a
bounded MCP `-31000` (`PITWALL_ERROR_CODE_BASE`) error without reflecting provider or credential detail
(`pitwall.mcp.tools.onboarding`).
The complete plan-first workflow is documented in
[RunPod onboarding](../operator/runpod-onboarding.md) and tested hermetically in
`tests/onboarding/test_surfaces.py`.

---

## 9. Production routing tools

The routing feature contributes two thin tool specifications for the global static registry:

| Tool | Behavior |
| --- | --- |
| `pitwall_preview_route` | Calls the shared no-egress planner and returns the payload-free plan/score explanation |
| `pitwall_get_job_events` | Returns a bounded persisted lifecycle page and explicit provider-stream support state |

The existing synchronous/async submit, status, result, and cancellation tools consume the same
`ProductionRoutingService` lifecycle after registry integration. The feature-local adapters expose
no network listener, dynamic plugin system, credentials, prompt text, or provider-specific
selection logic. Planning failures return the same codes as the REST routing routes
(`capability_not_found`, `capability_disabled`, `provider_not_found`, `no_providers_available`,
`pre_spend_payload_rejected`), an unknown workload returns `workload_not_found`, an invalid
operation returns `production_routing_invalid_request`, and any other service failure returns
`production_routing_unavailable`; none of them reflect provider exception messages
(`src/pitwall/mcp/tools/routing.py`).
