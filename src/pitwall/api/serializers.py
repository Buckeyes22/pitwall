"""Single response serialisers shared by the REST routes and the MCP tools."""

from __future__ import annotations

from typing import Any

from pitwall.core.models import (
    Capability,
    Lease,
    Provider,
    redact_provider_serialized_config,
)
from pitwall.providers.selfhosted import self_hosted_profile, self_hosted_state


def active_lease_summary(lease: Lease | None) -> dict[str, str] | None:
    if lease is None:
        return None
    return {
        "lease_id": lease.id,
        "state": lease.state.value if hasattr(lease.state, "value") else str(lease.state),
        "expires_at": lease.expires_at.isoformat(),
    }


_SELF_HOSTED_NULLS: dict[str, Any] = {
    "provider_kind": None,
    "readiness": None,
    "resident": None,
    "cold_start_s": None,
    "slot_group": None,
    "exclusive": None,
    "context_length": None,
    "tool_calling": None,
    "idle_unload_s": None,
}


def self_hosted_metadata(cap: Capability, provider: Provider | None) -> dict[str, Any]:
    if provider is None:
        return dict(_SELF_HOSTED_NULLS)
    profile = self_hosted_profile(provider)
    if profile is None:
        return dict(_SELF_HOSTED_NULLS)
    state = self_hosted_state(provider.config.get("self_hosted_state"))
    model = next((item for item in profile.models if item.id == cap.served_model_id), None)
    resident_ids = {item for item in state.get("resident", []) if isinstance(item, str)}
    readiness_state = state.get("readiness")
    if readiness_state not in {"ready", "starting", "absent"}:
        readiness_state = "absent"
    raw_cold_start = state.get("cold_start_s")
    cold_start = None
    if isinstance(raw_cold_start, dict):
        p50 = raw_cold_start.get("p50")
        p95 = raw_cold_start.get("p95")
        if (
            isinstance(p50, (int, float))
            and not isinstance(p50, bool)
            and isinstance(p95, (int, float))
            and not isinstance(p95, bool)
        ):
            cold_start = {"p50": p50, "p95": p95}
    observed_tools = state.get("tool_calling")
    tool_calling = (
        observed_tools
        if observed_tools in {"enabled", "disabled", "unknown"}
        else model.tool_calling
        if model is not None
        else "unknown"
    )
    return {
        "provider_kind": "self_hosted",
        "readiness": {"kind": profile.readiness.kind, "state": readiness_state},
        "resident": cap.served_model_id in resident_ids,
        "cold_start_s": cold_start,
        "slot_group": model.slot_group if model is not None else None,
        "exclusive": model.exclusive if model is not None else None,
        "context_length": model.context_length if model is not None else None,
        "tool_calling": tool_calling,
        "idle_unload_s": profile.idle_unload_s,
    }


def capability_to_response(
    cap: Capability,
    lease: Lease | None = None,
    provider: Provider | None = None,
) -> dict[str, Any]:
    return {
        "id": cap.id,
        "name": cap.name,
        "version": cap.version,
        "class": cap.class_.value,
        "description": cap.description,
        "input_schema": cap.input_schema,
        "output_schema": cap.output_schema,
        "defaults": cap.defaults.model_dump(mode="json"),
        "cost_mode": cap.cost_mode.value,
        "hints_supported": [h.value for h in cap.hints_supported],
        "source": cap.source.value,
        "last_applied_yaml_hash": cap.last_applied_yaml_hash,
        "served_model_id": cap.served_model_id,
        "active_lease": active_lease_summary(lease),
        "idle_timeout_min": lease.idle_timeout_min if lease is not None else None,
        "last_traffic_at": (
            lease.last_traffic_at.isoformat()
            if lease is not None and lease.last_traffic_at
            else None
        ),
        "renewal_policy": lease.renewal_policy.value if lease is not None else None,
        "max_usd_per_hour": (
            str(lease.max_usd_per_hour)
            if lease is not None and lease.max_usd_per_hour is not None
            else None
        ),
        **self_hosted_metadata(cap, provider),
        "enabled": cap.enabled,
        "created_at": cap.created_at.isoformat(),
        "updated_at": cap.updated_at.isoformat(),
    }


def provider_to_response(prov: Provider) -> dict[str, Any]:
    return {
        "id": prov.id,
        "capability_id": prov.capability_id,
        "name": prov.name,
        "adapter_id": prov.adapter_id.value,
        "credential_ref": prov.credential_ref,
        "provider_type": prov.provider_type.value,
        "runpod_endpoint_id": prov.runpod_endpoint_id,
        "runpod_template_id": prov.runpod_template_id,
        "region": prov.region,
        "cloud_type": prov.cloud_type,
        "config": redact_provider_serialized_config(prov.config),
        "priority": prov.priority,
        "enabled": prov.enabled,
        "health_status": prov.health_status,
        "consecutive_failures": prov.consecutive_failures,
        "cooldown_trips": prov.cooldown_trips,
        "cold_start_p50_ms": prov.cold_start_p50_ms,
        "cold_start_p95_ms": prov.cold_start_p95_ms,
        "recent_error_rate": prov.recent_error_rate,
        "cooldown_until": prov.cooldown_until.isoformat() if prov.cooldown_until else None,
        "source": prov.source.value,
        "last_applied_yaml_hash": prov.last_applied_yaml_hash,
        "updated_at": prov.updated_at.isoformat(),
    }


def lease_to_response(lease: Lease) -> dict[str, Any]:
    return {
        "id": lease.id,
        "provider_id": lease.provider_id,
        "external_resource_id": lease.external_resource_id,
        "runpod_pod_id": lease.runpod_pod_id,
        "state": lease.state.value if hasattr(lease.state, "value") else lease.state,
        "created_at": lease.created_at.isoformat(),
        "expires_at": lease.expires_at.isoformat(),
        "renewal_policy": (
            lease.renewal_policy.value
            if hasattr(lease.renewal_policy, "value")
            else lease.renewal_policy
        ),
        "auto_teardown_on_expiry": lease.auto_teardown_on_expiry,
        "endpoints": lease.endpoints.model_dump(mode="json") if lease.endpoints else None,
        "readiness": (lease.readiness.model_dump(mode="json") if lease.readiness else None),
        "cost_accrued_usd": (
            str(lease.cost_accrued_usd) if lease.cost_accrued_usd is not None else None
        ),
        "last_health_at": (lease.last_health_at.isoformat() if lease.last_health_at else None),
        "terminated_at": (lease.terminated_at.isoformat() if lease.terminated_at else None),
        "terminated_reason": lease.terminated_reason,
    }
