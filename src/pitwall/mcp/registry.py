"""MCP tool registry — stable names, descriptions, schemas, and handler callables.

This module defines the canonical ``TOOL_REGISTRY`` containing every
``pitwall_*`` MCP tool (the count is pinned by ``tests/mcp/test_tool_count.py``).  Each entry is a ``ToolSpec`` dataclass with a
stable tool name, human-readable description, and a handler callable whose
typed parameters become the tool's input schema.

Every registry entry points at its production tool handler; this module contains
metadata and registration only, not a parallel mock implementation.

Usage::

    from pitwall.mcp.registry import register_all
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("pitwall")
    register_all(mcp)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pitwall.mcp.onboarding_specs import ONBOARDING_TOOL_SPECS
from pitwall.mcp.provider_operations_specs import PROVIDER_OPERATIONS_TOOL_SPECS
from pitwall.mcp.tools.admin import (
    pitwall_create_capability,
    pitwall_create_provider,
    pitwall_disable_provider,
    pitwall_hibernate_provider,
    pitwall_update_capability,
    pitwall_update_provider,
)
from pitwall.mcp.tools.audit import pitwall_audit_log
from pitwall.mcp.tools.budget import pitwall_budget_set, pitwall_budget_status
from pitwall.mcp.tools.burn_rate import pitwall_burn_rate
from pitwall.mcp.tools.copilot import pitwall_copilot_propose
from pitwall.mcp.tools.cost import (
    pitwall_cost_summary,
    pitwall_recent_workloads,
)
from pitwall.mcp.tools.discovery import (
    pitwall_describe_capability,
    pitwall_get_provider_health,
    pitwall_list_capabilities,
    pitwall_list_providers,
)
from pitwall.mcp.tools.doctor import pitwall_doctor
from pitwall.mcp.tools.gateway import (
    pitwall_gateway_catalog_read,
    pitwall_quota_list,
)
from pitwall.mcp.tools.guardrails import (
    pitwall_guardrail_preview,
    pitwall_guardrail_status,
)
from pitwall.mcp.tools.health import pitwall_health
from pitwall.mcp.tools.inference import (
    pitwall_cancel_job,
    pitwall_get_job_result,
    pitwall_get_job_status,
    pitwall_submit_inference,
    pitwall_submit_job,
)
from pitwall.mcp.tools.leases import (
    pitwall_get_lease,
    pitwall_lease_pod,
    pitwall_renew_lease,
    pitwall_stop_lease,
)
from pitwall.mcp.tools.models import pitwall_models_fit, pitwall_models_list
from pitwall.mcp.tools.routing import ROUTING_TOOL_SPECS
from pitwall.mcp.tools.runpod_market import pitwall_runpod_catalogue
from pitwall.mcp.tools.runpod_resources import RUNPOD_RESOURCE_TOOL_SPECS
from pitwall.mcp.tools.serve import pitwall_serve_model
from pitwall.mcp.volume_file_specs import VOLUME_FILE_TOOL_SPECS

TOOL_NAMES: frozenset[str] = frozenset(
    {
        "pitwall_list_capabilities",
        "pitwall_describe_capability",
        "pitwall_list_providers",
        "pitwall_get_provider_health",
        "pitwall_submit_inference",
        "pitwall_submit_job",
        "pitwall_get_job_status",
        "pitwall_get_job_result",
        "pitwall_cancel_job",
        "pitwall_lease_pod",
        "pitwall_get_lease",
        "pitwall_renew_lease",
        "pitwall_stop_lease",
        "pitwall_serve_model",
        "pitwall_models_list",
        "pitwall_models_fit",
        "pitwall_cost_summary",
        "pitwall_recent_workloads",
        "pitwall_burn_rate",
        "pitwall_budget_status",
        "pitwall_budget_set",
        "pitwall_guardrail_status",
        "pitwall_guardrail_preview",
        "pitwall_create_capability",
        "pitwall_update_capability",
        "pitwall_create_provider",
        "pitwall_update_provider",
        "pitwall_disable_provider",
        "pitwall_hibernate_provider",
        "pitwall_audit_log",
        "pitwall_copilot_propose",
        "pitwall_runpod_catalogue",
        "pitwall_health",
        "pitwall_doctor",
        "pitwall_gateway_catalog_read",
        "pitwall_quota_list",
        *(spec.name for spec in PROVIDER_OPERATIONS_TOOL_SPECS),
        *(spec.name for spec in ONBOARDING_TOOL_SPECS),
        *(spec.name for spec in RUNPOD_RESOURCE_TOOL_SPECS),
        *(spec.name for spec in ROUTING_TOOL_SPECS),
        *(spec.name for spec in VOLUME_FILE_TOOL_SPECS),
    }
)


@dataclass(frozen=True)
class ToolSpec:
    """A single registered MCP tool."""

    name: str
    description: str
    handler: Callable[..., dict[str, Any]] | Callable[..., Awaitable[dict[str, Any]]]


TOOL_REGISTRY: list[ToolSpec] = [
    ToolSpec(
        name="pitwall_list_capabilities",
        description="List all registered capabilities, optionally filtered by class, cost mode, or enabled state.",
        handler=pitwall_list_capabilities,
    ),
    ToolSpec(
        name="pitwall_describe_capability",
        description="Return full details for a single capability by name or ID.",
        handler=pitwall_describe_capability,
    ),
    ToolSpec(
        name="pitwall_list_providers",
        description="List all registered providers, optionally filtered by capability, type, or enabled state.",
        handler=pitwall_list_providers,
    ),
    ToolSpec(
        name="pitwall_get_provider_health",
        description="Return health status, cooldown state, and recent error rate for a single provider.",
        handler=pitwall_get_provider_health,
    ),
    ToolSpec(
        name="pitwall_submit_inference",
        description="Submit a synchronous inference request to a capability. Returns the result directly.",
        handler=pitwall_submit_inference,
    ),
    ToolSpec(
        name="pitwall_submit_job",
        description="Submit an asynchronous job to a capability. Returns a workload ID for polling.",
        handler=pitwall_submit_job,
    ),
    ToolSpec(
        name="pitwall_get_job_status",
        description="Return the current state of an async job by workload ID.",
        handler=pitwall_get_job_status,
    ),
    ToolSpec(
        name="pitwall_get_job_result",
        description="Return the completed result of an async job by workload ID.",
        handler=pitwall_get_job_result,
    ),
    ToolSpec(
        name="pitwall_cancel_job",
        description="Cancel a pending or running async job by workload ID.",
        handler=pitwall_cancel_job,
    ),
    ToolSpec(
        name="pitwall_lease_pod",
        description="Create a pod lease for a capability. Routes to a pod_lease provider and tracks readiness.",
        handler=pitwall_lease_pod,
    ),
    ToolSpec(
        name="pitwall_get_lease",
        description="Return the current state and details of a pod lease.",
        handler=pitwall_get_lease,
    ),
    ToolSpec(
        name="pitwall_renew_lease",
        description="Extend an active pod lease by a number of minutes.",
        handler=pitwall_renew_lease,
    ),
    ToolSpec(
        name="pitwall_stop_lease",
        description="Stop and tear down an active pod lease.",
        handler=pitwall_stop_lease,
    ),
    ToolSpec(
        name="pitwall_serve_model",
        description=(
            "Launch or replay a vllm, llama.cpp, or sglang OpenAI-compatible model-serving "
            "pod lease behind a capability. "
            "Use the catalogue-first workflow: call pitwall_models_fit, then pass its selected "
            "catalogue variant here. GPU classes must use canonical RunPod names; legacy aliases "
            "normalize. A model without a dossier requires image and may not pass variant. "
            "When fit pricing is unavailable (unpriced), rate_per_second is required. dry_run "
            "validates and returns the resolved plan without launching. Stable failures are 409 "
            "serve_conflict; 422 rate_required, unknown_variant, invalid_template, or "
            "invalid_gpu_class; 502 served_model_mismatch; and 503 launch_failed."
        ),
        handler=pitwall_serve_model,
    ),
    ToolSpec(
        name="pitwall_models_list",
        description=(
            "List curated model dossiers and published serving variants for the catalogue-first "
            "workflow; use pitwall_models_fit before pitwall_serve_model."
        ),
        handler=pitwall_models_list,
    ),
    ToolSpec(
        name="pitwall_models_fit",
        description=(
            "Compare a catalogue model variant with canonical RunPod GPU classes and estimate TTL "
            "cost before pitwall_serve_model. unpriced means prices are unavailable; provide "
            "rate_per_second to serve_model in that case."
        ),
        handler=pitwall_models_fit,
    ),
    ToolSpec(
        name="pitwall_cost_summary",
        description="Return aggregated cost summary, optionally filtered by capability class and date range.",
        handler=pitwall_cost_summary,
    ),
    ToolSpec(
        name="pitwall_recent_workloads",
        description="Return a list of recent workloads with their states and cost estimates.",
        handler=pitwall_recent_workloads,
    ),
    ToolSpec(
        name="pitwall_burn_rate",
        description="Return the monthly budget burn-rate forecast from persisted UTC daily rollups.",
        handler=pitwall_burn_rate,
    ),
    ToolSpec(
        name="pitwall_budget_status",
        description="Return the effective monthly budget and per-request cap (runtime or environment), month-to-date spend, and remaining budget.",
        handler=pitwall_budget_status,
    ),
    ToolSpec(
        name="pitwall_budget_set",
        description=(
            "Change the monthly budget and/or per-request cap without a restart. A reason is required; "
            "the change is audited. Use it when a budget_rejected error names the limit you need raised."
        ),
        handler=pitwall_budget_set,
    ),
    ToolSpec(
        name="pitwall_guardrail_status",
        description="Return pre-spend guardrail rules, limits, counters, and safe last-decision metadata.",
        handler=pitwall_guardrail_status,
    ),
    ToolSpec(
        name="pitwall_guardrail_preview",
        description="Inspect one payload without provider, database, audit, counter, or last-decision writes.",
        handler=pitwall_guardrail_preview,
    ),
    ToolSpec(
        name="pitwall_create_capability",
        description=(
            "Register a new capability with the Pitwall broker."
            " Any local process that can start the stdio server has full access; there is no per-tool authorization."
        ),
        handler=pitwall_create_capability,
    ),
    ToolSpec(
        name="pitwall_update_capability",
        description=(
            "Update fields on an existing capability."
            " Any local process that can start the stdio server has full access; there is no per-tool authorization."
        ),
        handler=pitwall_update_capability,
    ),
    ToolSpec(
        name="pitwall_create_provider",
        description=(
            "Register a new provider (RunPod endpoint) for a capability."
            " Any local process that can start the stdio server has full access; there is no per-tool authorization."
        ),
        handler=pitwall_create_provider,
    ),
    ToolSpec(
        name="pitwall_update_provider",
        description=(
            "Update fields on an existing provider."
            " Any local process that can start the stdio server has full access; there is no per-tool authorization."
        ),
        handler=pitwall_update_provider,
    ),
    ToolSpec(
        name="pitwall_disable_provider",
        description=(
            "Disable a provider so it is excluded from routing."
            " Any local process that can start the stdio server has full access; there is no per-tool authorization."
        ),
        handler=pitwall_disable_provider,
    ),
    ToolSpec(
        name="pitwall_hibernate_provider",
        description=(
            "Hibernate a serverless provider by scaling workers to zero."
            " Any local process that can start the stdio server has full access; there is no per-tool authorization."
        ),
        handler=pitwall_hibernate_provider,
    ),
    ToolSpec(
        name="pitwall_audit_log",
        description="Return config mutation audit entries, optionally filtered by entity and action.",
        handler=pitwall_audit_log,
    ),
    ToolSpec(
        name="pitwall_copilot_propose",
        description=(
            "Return a proposal-only GitOps plan/diff for an operator intent. Never applies changes."
        ),
        handler=pitwall_copilot_propose,
    ),
    ToolSpec(
        name="pitwall_runpod_catalogue",
        description=(
            "Read the cached RunPod GPU catalogue, availability, Decimal prices, balance, "
            "and supported billing categories; optionally force one refresh."
        ),
        handler=pitwall_runpod_catalogue,
    ),
    ToolSpec(
        name="pitwall_health",
        description=(
            "Check that the database and Redis answer and the provider registry is loaded; "
            "each check is a boolean."
        ),
        handler=pitwall_health,
    ),
    ToolSpec(
        name="pitwall_doctor",
        description=(
            "Return the installation readiness report (install, config, services, spend controls) "
            "with a next step for every problem. Read-only; the optional canary is a dry-run."
        ),
        handler=pitwall_doctor,
    ),
    ToolSpec(
        name="pitwall_gateway_catalog_read",
        description=(
            "Read the synced free-tier gateway catalog from config/gateway-catalog.json; "
            "filter by tos verdict or routable rows. Read-only; no provider egress, no writes."
        ),
        handler=pitwall_gateway_catalog_read,
    ),
    ToolSpec(
        name="pitwall_quota_list",
        description=(
            "List persisted provider-quota snapshots via QuotaRepository, with derived "
            "headroom for each (provider, pool) tuple. Read-only; no provider egress."
        ),
        handler=pitwall_quota_list,
    ),
    *(
        ToolSpec(name=spec.name, description=spec.description, handler=spec.handler)
        for spec in PROVIDER_OPERATIONS_TOOL_SPECS
    ),
    *(
        ToolSpec(name=spec.name, description=spec.description, handler=spec.handler)
        for spec in ONBOARDING_TOOL_SPECS
    ),
    *(
        ToolSpec(name=spec.name, description=spec.description, handler=spec.handler)
        for spec in RUNPOD_RESOURCE_TOOL_SPECS
    ),
    *(
        ToolSpec(name=spec.name, description=spec.description, handler=spec.handler)
        for spec in ROUTING_TOOL_SPECS
    ),
    *(
        ToolSpec(name=spec.name, description=spec.description, handler=spec.handler)
        for spec in VOLUME_FILE_TOOL_SPECS
    ),
]

_REGISTRY_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_REGISTRY}


GENERIC_OUTPUT_SCHEMA_KEYS = frozenset({"type", "additionalProperties", "title"})
_UNSERVED_METHODS = (
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "resources/subscribe",
    "resources/unsubscribe",
    "completion/complete",
    "logging/setLevel",
)


def strip_unused_features(server: Any) -> None:
    """Drop the prompt, resource, completion, and logging handlers the broker does not serve.

    SDK 2 advertises a capability for every registered handler.
    """
    handlers = server._lowlevel_server._request_handlers
    for method in _UNSERVED_METHODS:
        handlers.pop(method, None)


def install_list_tools_filter(server: Any) -> None:
    """Omit outputSchema when it is the generic dict schema that conveys no structure."""
    from mcp.types import PaginatedRequestParams

    original = server._handle_list_tools

    async def list_tools(ctx: Any, params: PaginatedRequestParams | None) -> Any:
        result = await original(ctx, params)
        for tool in result.tools:
            schema = tool.output_schema
            if isinstance(schema, dict) and set(schema) <= GENERIC_OUTPUT_SCHEMA_KEYS:
                tool.output_schema = None
        return result

    server._lowlevel_server.add_request_handler("tools/list", PaginatedRequestParams, list_tools)


def register_all(server: Any) -> None:
    """Register every tool in ``TOOL_REGISTRY`` with an SDK 2 ``MCPServer``."""
    from pitwall.mcp.safe_boundary import install_safe_call_boundary
    from pitwall.mcp.tool_metadata import describe_parameter, metadata_for

    for spec in TOOL_REGISTRY:
        meta = metadata_for(spec.name)
        server.add_tool(
            spec.handler,
            name=spec.name,
            title=meta.title,
            description=spec.description,
            annotations=meta.annotations,
        )
        # A description already written on the handler's Field(...) wins; fill only the gaps.
        registered = server._tool_manager.get_tool(spec.name)
        for parameter, schema in registered.parameters.get("properties", {}).items():
            if not str(schema.get("description", "")).strip():
                schema["description"] = describe_parameter(spec.name, parameter)
    install_safe_call_boundary(server)
    strip_unused_features(server)
    install_list_tools_filter(server)


__all__ = [
    "GENERIC_OUTPUT_SCHEMA_KEYS",
    "TOOL_NAMES",
    "TOOL_REGISTRY",
    "ToolSpec",
    "install_list_tools_filter",
    "register_all",
    "strip_unused_features",
    "pitwall_health",
]
