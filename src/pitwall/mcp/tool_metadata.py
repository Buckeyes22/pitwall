"""Display titles, behavior annotations, and parameter descriptions for every broker tool.

One table, keyed by tool name, so the 81 handlers and their six feature-local spec
types stay unchanged. ``registry.register_all`` applies it; tests pin full coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.types import ToolAnnotations


@dataclass(frozen=True, slots=True)
class ToolMetadata:
    title: str
    annotations: ToolAnnotations


def _meta(
    title: str,
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool,
    open_world: bool,
) -> ToolMetadata:
    return ToolMetadata(
        title=title,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=read_only,
            destructive_hint=None if read_only else destructive,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        ),
    )


def _read(title: str, *, open_world: bool = False) -> ToolMetadata:
    return _meta(title, read_only=True, idempotent=True, open_world=open_world)


TOOL_METADATA: dict[str, ToolMetadata] = {
    # Discovery and catalogue reads (database or local files only).
    "pitwall_list_capabilities": _read("List capabilities"),
    "pitwall_describe_capability": _read("Describe a capability"),
    "pitwall_list_providers": _read("List providers"),
    "pitwall_get_provider_health": _read("Get provider health"),
    "pitwall_models_list": _read("List catalogue models"),
    "pitwall_models_fit": _read("Fit a model to GPUs", open_world=True),
    "pitwall_cost_summary": _read("Summarize cost"),
    "pitwall_recent_workloads": _read("List recent workloads"),
    "pitwall_burn_rate": _read("Forecast budget burn rate"),
    "pitwall_budget_status": _read("Get budget status"),
    "pitwall_guardrail_status": _read("Get guardrail status"),
    "pitwall_guardrail_preview": _read("Preview a guardrail decision"),
    "pitwall_audit_log": _read("Read the audit log"),
    "pitwall_copilot_propose": _read("Propose a GitOps change"),
    "pitwall_health": _read("Check broker health"),
    "pitwall_doctor": _read("Run installation doctor", open_world=True),
    "pitwall_gateway_catalog_read": _read("Read the gateway catalog"),
    "pitwall_quota_list": _read("List provider quotas"),
    "pitwall_get_job_status": _read("Get job status", open_world=True),
    "pitwall_get_job_result": _read("Get job result", open_world=True),
    "pitwall_get_job_events": _read("Get job events"),
    "pitwall_get_lease": _read("Get a lease"),
    "pitwall_preview_route": _read("Preview a route"),
    "pitwall_provider_ops_list_descriptors": _read("List provider descriptors"),
    "pitwall_provider_ops_describe": _read("Describe a provider"),
    "pitwall_provider_ops_availability": _read("Probe provider availability", open_world=True),
    "pitwall_provider_ops_health": _read("Read provider health", open_world=True),
    "pitwall_runpod_catalogue": _read("Read the RunPod catalogue", open_world=True),
    "pitwall_runpod_onboarding_plan": _read("Plan RunPod onboarding", open_world=True),
    "pitwall_runpod_onboarding_status": _read("Get RunPod onboarding status", open_world=True),
    "pitwall_runpod_onboarding_rollback": _read("Get RunPod rollback guidance", open_world=True),
    "pitwall_runpod_list_pods": _read("List RunPod pods", open_world=True),
    "pitwall_runpod_get_pod": _read("Get a RunPod pod", open_world=True),
    "pitwall_runpod_list_endpoints": _read("List RunPod endpoints", open_world=True),
    "pitwall_runpod_get_endpoint": _read("Get a RunPod endpoint", open_world=True),
    "pitwall_runpod_list_templates": _read("List RunPod templates", open_world=True),
    "pitwall_runpod_get_template": _read("Get a RunPod template", open_world=True),
    "pitwall_runpod_list_volumes": _read("List RunPod volumes", open_world=True),
    "pitwall_runpod_get_volume": _read("Get a RunPod volume", open_world=True),
    "pitwall_runpod_list_registry_auths": _read("List registry auths", open_world=True),
    "pitwall_runpod_get_registry_auth": _read("Get a registry auth", open_world=True),
    "pitwall_runpod_list_hub_templates": _read("List Hub templates", open_world=True),
    "pitwall_runpod_get_hub_template": _read("Get a Hub template", open_world=True),
    "pitwall_runpod_search_hub_templates": _read("Search Hub templates", open_world=True),
    "pitwall_volume_list_objects": _read("List volume objects", open_world=True),
    "pitwall_volume_read_chunk": _read("Read a volume object chunk", open_world=True),
    "pitwall_pod_logs": _read("Read pod logs", open_world=True),
    # Spend-creating work (additive, provider-facing; optional idempotency key -> not idempotent).
    "pitwall_submit_inference": _meta(
        "Submit inference", read_only=False, idempotent=False, open_world=True
    ),
    "pitwall_submit_job": _meta("Submit a job", read_only=False, idempotent=False, open_world=True),
    # Non-destructive: a successful launch re-points the provider's active pod as bookkeeping and
    # removes nothing.
    "pitwall_lease_pod": _meta("Lease a pod", read_only=False, idempotent=False, open_world=True),
    # Destructive: serving rewrites an existing capability's served_model_id and replaces the
    # provider's whole config, and re-runs `profiles refresh` on an existing route.
    "pitwall_serve_model": _meta(
        "Serve a model", read_only=False, destructive=True, idempotent=False, open_world=True
    ),
    "pitwall_renew_lease": _meta(
        "Renew a lease", read_only=False, idempotent=False, open_world=False
    ),
    # Stops and cancellations.
    "pitwall_cancel_job": _meta(
        "Cancel a job", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    "pitwall_stop_lease": _meta(
        "Stop a lease", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    # Registry and budget administration (database only unless noted).
    "pitwall_budget_set": _meta(
        "Set budget limits", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_create_capability": _meta(
        "Create a capability", read_only=False, idempotent=False, open_world=False
    ),
    "pitwall_update_capability": _meta(
        "Update a capability", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_create_provider": _meta(
        "Create a provider", read_only=False, idempotent=False, open_world=False
    ),
    "pitwall_update_provider": _meta(
        "Update a provider", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_disable_provider": _meta(
        "Disable a provider", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    "pitwall_hibernate_provider": _meta(
        "Hibernate a provider", read_only=False, destructive=True, idempotent=True, open_world=False
    ),
    # RunPod onboarding (confirmed plan id makes apply/resume idempotent and additive).
    "pitwall_runpod_onboarding_apply": _meta(
        "Apply RunPod onboarding", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_onboarding_resume": _meta(
        "Resume RunPod onboarding", read_only=False, idempotent=True, open_world=True
    ),
    # Raw RunPod resources (idempotency_key is required on every mutation).
    "pitwall_runpod_create_pod": _meta(
        "Create a RunPod pod", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_update_pod": _meta(
        "Update a RunPod pod", read_only=False, destructive=True, idempotent=True, open_world=True
    ),
    "pitwall_runpod_action_pod": _meta(
        "Start, stop, restart, or reset a pod",
        read_only=False,
        destructive=True,
        idempotent=False,
        open_world=True,
    ),
    "pitwall_runpod_terminate_pod": _meta(
        "Terminate a RunPod pod",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_endpoint": _meta(
        "Create a RunPod endpoint", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_update_endpoint": _meta(
        "Update a RunPod endpoint",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_endpoint": _meta(
        "Delete a RunPod endpoint",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_template": _meta(
        "Create a RunPod template", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_update_template": _meta(
        "Update a RunPod template",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_template": _meta(
        "Delete a RunPod template",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_volume": _meta(
        "Create a RunPod volume", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_grow_volume": _meta(
        "Grow a RunPod volume",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_volume": _meta(
        "Delete a RunPod volume",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_create_registry_auth": _meta(
        "Create a registry auth", read_only=False, idempotent=True, open_world=True
    ),
    "pitwall_runpod_replace_registry_auth": _meta(
        "Replace a registry auth",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_runpod_delete_registry_auth": _meta(
        "Delete a registry auth",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    # Network-volume file mutations (intent, idempotency key, and confirmation required).
    "pitwall_volume_upload_object": _meta(
        "Upload a volume object",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
    "pitwall_volume_delete_object": _meta(
        "Delete a volume object",
        read_only=False,
        destructive=True,
        idempotent=True,
        open_world=True,
    ),
}


def metadata_for(name: str) -> ToolMetadata:
    return TOOL_METADATA[name]


_CAPABILITY_CLASSES = "embedding, rerank, llm, vision, transcribe, gpu_lease, or custom"
_COST_MODES = "per_second, per_request, per_token, or zero"
_PROVIDER_TYPES = (
    "serverless_queue, serverless_lb, public_endpoint, pod_lease, openai_gateway, or model_studio"
)
_ADAPTER_IDS = "runpod, vast, together, lambda_cloud, openai_gateway, or model_studio"
_IDEMPOTENCY_KEY_RULES = (
    "Required. Caller-chosen key (letters, digits, '.', '_' or '-'; up to 128 characters) "
    "that makes retries safe."
)
_MUTATION_REQUEST = (
    "Mutation request. 'intent' is 'preview' (validate only, nothing changes) or 'apply'; "
    "'idempotency_key' is required and makes retries safe. Other fields are listed in this "
    "tool's input schema."
)

# Shared descriptions apply wherever a parameter name means the same thing in every tool.
PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "action": "Only return audit entries with this action, for example 'create' or 'update'.",
    "adapter_id": f"Provider adapter id: {_ADAPTER_IDS}. Defaults to runpod when creating.",
    "canary": "Optional enabled embedding capability to exercise with a dry-run inference.",
    "capability_class": f"Capability class: {_CAPABILITY_CLASSES}.",
    "capability_id": "Capability name or id, as listed by pitwall_list_capabilities.",
    "cloud_type": (
        "RunPod cloud type: 'SECURE', 'COMMUNITY', or 'ALL'. 'ALL' is rejected when the "
        "provider attaches a network volume."
    ),
    "cold_start_p50_ms": "Observed median cold start in milliseconds.",
    "cold_start_p95_ms": "Observed 95th-percentile cold start in milliseconds.",
    "config": "Provider configuration object, validated against the provider type's schema.",
    "confirm_delete": "Must be true for the delete to run.",
    "confirm_overwrite": (
        "Must be true to replace an object that already exists at object_key; "
        "otherwise an existing object is never overwritten."
    ),
    "consecutive_failures": "Consecutive failure count to record for the provider.",
    "content_base64": (
        "Object bytes, base64-encoded; at most 131072 bytes (128 KiB) before encoding."
    ),
    "cooldown_trips": "Cooldown trip count to record for the provider.",
    "cost_mode": f"Cost mode: {_COST_MODES}.",
    "credential_ref": (
        "Name of the environment variable that holds the provider credential, never the "
        "credential itself. Defaults to the adapter's standard variable."
    ),
    "data_center_id": "RunPod data center id the network volume lives in, for example 'EU-RO-1'.",
    "description": "Human-readable capability description.",
    "dry_run": (
        "When true, validate the request and return the plan or result without executing it: "
        "no provider call, spend, or write."
    ),
    "enabled_only": "When true, list only enabled providers.",
    "engine": "Serving engine: vllm, llama.cpp, or sglang.",
    "entity_id": "Only return audit entries for this entity id.",
    "entity_type": (
        "Only return audit entries for this entity type, for example 'capability' or 'provider'."
    ),
    "expected_sha256": (
        "Optional lowercase hex SHA-256 of the decoded bytes; the upload is rejected on mismatch."
    ),
    "extends_minutes": (
        "Minutes to add to the lease's current expiry, 1 to 43200 (default 60). A renewal "
        "that would push expiry more than 30 days from now is rejected."
    ),
    "force_refresh": "When true, make one live RunPod read instead of serving the cached catalogue.",
    "health_status": (
        "Provider health status to record, for example 'unknown', 'healthy', 'unhealthy', "
        "'hibernated', or 'disarmed'."
    ),
    "idempotency_key": (
        "Optional caller-chosen key that makes retries safe: repeating the same request with "
        "the same key returns the original result instead of repeating the action."
    ),
    "input": "Job input object passed to the capability.",
    "input_schema": "JSON Schema object describing the capability's input.",
    "lease_id": "Lease id, as returned by pitwall_lease_pod or pitwall_serve_model.",
    "max_items": "Maximum objects returned in this page (default 200; larger values are rejected).",
    "monthly_budget_usd": (
        "New monthly budget cap in USD as a positive decimal string, for example '75.00'. "
        "Omit to leave it unchanged; at least one of the two limits is required."
    ),
    "object_key": "Relative object key (path) inside the network volume, up to 1024 bytes.",
    "output_schema": "JSON Schema object describing the capability's output.",
    "per_request_max_usd": (
        "New per-request cost cap in USD as a positive decimal string. "
        "Omit to leave it unchanged; at least one of the two limits is required."
    ),
    "pod_id": "RunPod pod id whose diagnostics to read.",
    "prefix": "Object key prefix to list under; empty lists from the volume root.",
    "priority": "Routing priority, 0 or higher; lower numbers are preferred.",
    "provider_enabled": "Desired enabled state for the referenced provider.",
    "provider_id": "Provider id, as returned by pitwall_list_providers.",
    "provider_patch": (
        "Explicit provider fields to change: name, provider_type, runpod_endpoint_id, "
        "runpod_template_id, region, cloud_type, config, priority, or enabled. Unknown "
        "fields are rejected."
    ),
    "provider_priority": "Desired routing priority for the referenced provider; lower is preferred.",
    "provider_ref": (
        "Provider name or id the proposal applies to; when omitted it is parsed from intent."
    ),
    "provider_type": f"Provider type: {_PROVIDER_TYPES}.",
    "query": (
        "Case-insensitive text matched against the id, name, display name, image, and "
        "description of the first 100 Hub templates."
    ),
    "recent_error_rate": "Recent error rate to record for the provider, between 0 and 1.",
    "region": "Provider region or data center label.",
    "runpod_endpoint_id": "RunPod serverless endpoint id backing the provider.",
    "runpod_template_id": "RunPod template id backing the provider.",
    "scorecards": (
        "Optional scorecard entries that feed the recommendation engine; each has "
        "capability_id, provider_id, dimension, score, benchmark, and optional message."
    ),
    "state": (
        "Only return workloads in this state: queued, running, completed, failed, cancelled, "
        "or timed_out."
    ),
    "tos": (
        "Only return catalog providers with this terms-of-service verdict: ok, caution, "
        "ambiguous, avoid, or unknown."
    ),
    "routable_only": (
        "When true, return only providers the gateway can route to: no eligibility gate, a "
        "terms-of-service verdict other than avoid or unknown, a free type other than "
        "discontinued, and a guaranteed hard stop or a keyless free tier."
    ),
    "volume_id": "RunPod network volume id.",
    "webhook_url": (
        "Optional HTTPS URL notified when the job finishes; targets that resolve to private "
        "or loopback addresses are rejected."
    ),
    "window_days": (
        "Number of trailing UTC days of daily spend the forecast observes, 1 to 366 (default 30)."
    ),
    "workload_id": ("Workload id, as returned by pitwall_submit_job or pitwall_submit_inference."),
}

_REQUEST_OBJECT = "request"
_RESOURCE_READ = "RunPod id of the {kind} to read."

# Overrides cover the names whose meaning differs by tool.
PARAMETER_OVERRIDES: dict[str, dict[str, str]] = {
    "pitwall_audit_log": {"limit": "Maximum audit entries to return (default 50)."},
    "pitwall_recent_workloads": {
        "limit": "Maximum workloads to return, 1 to 100 (default 20).",
        "capability_id": "Only return workloads for this capability id.",
        "provider_id": "Only return workloads that ran on this provider id.",
        "provider_type": f"Only return workloads on providers of this type: {_PROVIDER_TYPES}.",
        "since": (
            "Inclusive start timestamp, ISO 8601 with a UTC offset, for example "
            "'2026-10-01T00:00:00+00:00'."
        ),
        "until": (
            "Inclusive end timestamp, ISO 8601 with a UTC offset, for example "
            "'2026-10-06T00:00:00+00:00'."
        ),
    },
    "pitwall_cost_summary": {
        "capability_class": (
            f"Only include cost for capabilities of this class: {_CAPABILITY_CLASSES}."
        ),
        "since": "Inclusive start date, YYYY-MM-DD.",
        "until": "Inclusive end date, YYYY-MM-DD.",
    },
    "pitwall_list_capabilities": {
        "capability_class": f"Only list capabilities of this class: {_CAPABILITY_CLASSES}.",
        "cost_mode": f"Only list capabilities with this cost mode: {_COST_MODES}.",
        "enabled": "true lists only enabled capabilities, false only disabled ones; omit for all.",
    },
    "pitwall_list_providers": {
        "capability_id": "Only list providers that fulfil this capability id.",
        "provider_type": f"Only list providers of this type: {_PROVIDER_TYPES}.",
        "enabled": "true lists only enabled providers, false only disabled ones; omit for all.",
    },
    "pitwall_runpod_list_hub_templates": {
        "limit": "Maximum templates to return, 1 to 100 (default 50).",
        "offset": "Number of templates to skip before the first one returned (default 0).",
    },
    "pitwall_runpod_search_hub_templates": {
        "limit": "Maximum matching templates to return, 1 to 100 (default 50)."
    },
    "pitwall_get_job_events": {
        "limit": "Maximum lifecycle events to return, 1 to 100 (default 25)."
    },
    "pitwall_volume_read_chunk": {
        "offset": "Byte offset to start reading from (default 0).",
        "max_bytes": (
            "Maximum bytes to read in this chunk (default 131072; larger values are rejected)."
        ),
    },
    "pitwall_pod_logs": {
        "max_bytes": "Maximum log bytes returned (default 65536; larger values are rejected).",
        "max_lines": "Maximum log lines returned (default 100; larger values are rejected).",
    },
    "pitwall_describe_capability": {"name": "Capability name."},
    "pitwall_create_capability": {
        "name": "Unique capability name.",
        "version": "Capability version string, for example '1.0.0'.",
    },
    "pitwall_update_capability": {
        "capability_id": "Id of the capability to update.",
        "name": "New capability name.",
        "version": "New capability version string.",
        "description": "Replacement capability description.",
        "cost_mode": f"New cost mode: {_COST_MODES}.",
        "enabled": "Set true to enable or false to disable the capability; omit to leave unchanged.",
        "input_schema": "Replacement JSON Schema object for the capability's input.",
        "output_schema": "Replacement JSON Schema object for the capability's output.",
    },
    "pitwall_create_provider": {
        "capability_id": "Id of the capability this provider fulfils.",
        "name": "Unique provider name.",
        "enabled": "Whether the new provider is enabled (default true).",
    },
    "pitwall_update_provider": {
        "name": "New provider name.",
        "credential_ref": (
            "Name of the environment variable that holds the provider credential, never the "
            "credential itself. Omit to keep the current reference; it resets to the adapter's "
            "standard variable only when adapter_id changes."
        ),
        "enabled": "Set true to enable or false to disable the provider; omit to leave unchanged.",
        "adapter_id": (
            f"New provider adapter id: {_ADAPTER_IDS}. The credential reference resets to the "
            "adapter's standard variable unless credential_ref is also given."
        ),
    },
    "pitwall_submit_inference": {
        "provider_id": (
            "Optional provider id to pin the request to; omit to let the router choose."
        ),
        "payload": (
            "Inference request body for the capability. Control keys such as capability_id "
            "and provider_id are ignored here."
        ),
    },
    "pitwall_submit_job": {
        "provider_id": ("Optional provider id to pin the job to; omit to let the router choose."),
    },
    "pitwall_lease_pod": {
        "provider_id": ("Optional provider id to pin the lease to; omit to let the router choose."),
    },
    "pitwall_preview_route": {
        "provider_id": "Optional provider id to plan for; omit to plan across all providers.",
        "payload": (
            "Optional request body the plan is computed for; it is guardrail-scanned and "
            "never sent to a provider."
        ),
        "operation": (
            "Routing operation to plan: sync_inference, async_inference, or compute "
            "(default sync_inference)."
        ),
    },
    "pitwall_guardrail_preview": {"payload": "Any JSON value to scan for secrets and PII."},
    "pitwall_copilot_propose": {
        "intent": (
            "Operator intent in plain words, for example 'disable provider NAME', "
            "'enable provider NAME', or 'set provider NAME priority 2'. Explicit provider_* "
            "arguments take precedence over the parsed intent."
        ),
    },
    "pitwall_volume_upload_object": {
        "intent": "Must be the literal 'upload'.",
        "dry_run": (
            "When true, validate and make one read-only lookup for an existing object; "
            "nothing is uploaded or written."
        ),
        "idempotency_key": _IDEMPOTENCY_KEY_RULES,
    },
    "pitwall_volume_delete_object": {
        "intent": "Must be the literal 'delete'.",
        "idempotency_key": _IDEMPOTENCY_KEY_RULES,
    },
    "pitwall_stop_lease": {"reason": "Optional note recorded as the lease's termination reason."},
    "pitwall_budget_set": {
        "reason": "Required. Why the limit is changing; recorded in the audit trail."
    },
    "pitwall_models_fit": {
        "inventory": (
            "Optional local GPU inventory; when given, fit against it instead of RunPod "
            "cloud prices."
        ),
        "context": (
            "Context length in tokens, used only with inventory; defaults to the variant's "
            "context and is required when that is unverified."
        ),
    },
    "pitwall_runpod_get_pod": {"resource_id": _RESOURCE_READ.format(kind="pod")},
    "pitwall_runpod_get_endpoint": {
        "resource_id": _RESOURCE_READ.format(kind="serverless endpoint")
    },
    "pitwall_runpod_get_template": {"resource_id": _RESOURCE_READ.format(kind="template")},
    "pitwall_runpod_get_volume": {"resource_id": _RESOURCE_READ.format(kind="network volume")},
    "pitwall_runpod_get_registry_auth": {
        "resource_id": _RESOURCE_READ.format(kind="container registry auth")
    },
    "pitwall_runpod_get_hub_template": {
        "resource_id": "Hub template id, as returned by pitwall_runpod_list_hub_templates."
    },
    **{
        name: {
            _REQUEST_OBJECT: (
                "Desired RunPod onboarding topology: names, image, GPUs, hourly rate, template, "
                "endpoint, and optional registry and volume. Contains no credential values."
            )
        }
        for name in (
            "pitwall_runpod_onboarding_plan",
            "pitwall_runpod_onboarding_apply",
            "pitwall_runpod_onboarding_status",
            "pitwall_runpod_onboarding_resume",
            "pitwall_runpod_onboarding_rollback",
        )
    },
    **{
        name: {_REQUEST_OBJECT: _MUTATION_REQUEST}
        for name in (
            "pitwall_runpod_create_pod",
            "pitwall_runpod_update_pod",
            "pitwall_runpod_action_pod",
            "pitwall_runpod_terminate_pod",
            "pitwall_runpod_create_endpoint",
            "pitwall_runpod_update_endpoint",
            "pitwall_runpod_delete_endpoint",
            "pitwall_runpod_create_template",
            "pitwall_runpod_update_template",
            "pitwall_runpod_delete_template",
            "pitwall_runpod_create_volume",
            "pitwall_runpod_grow_volume",
            "pitwall_runpod_delete_volume",
            "pitwall_runpod_create_registry_auth",
            "pitwall_runpod_replace_registry_auth",
            "pitwall_runpod_delete_registry_auth",
        )
    },
}


def describe_parameter(tool: str, parameter: str) -> str:
    """Return the model-facing description for one tool parameter.

    A per-tool override wins over the shared description. Raises ``KeyError`` when neither
    exists, so a new parameter cannot ship undescribed.
    """
    override = PARAMETER_OVERRIDES.get(tool, {}).get(parameter)
    return override if override is not None else PARAMETER_DESCRIPTIONS[parameter]


__all__ = [
    "PARAMETER_DESCRIPTIONS",
    "PARAMETER_OVERRIDES",
    "TOOL_METADATA",
    "ToolMetadata",
    "describe_parameter",
    "metadata_for",
]
