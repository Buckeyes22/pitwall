"""MCP inference and job lifecycle adapters over production routing."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from pitwall.api.exceptions import (
    CapabilityDisabled,
    CapabilityNotFound,
    PreSpendPayloadRejected,
    ProviderNotFound,
    ProviderUnavailable,
    WorkloadNotFound,
)
from pitwall.api.routes.jobs import cancel_error
from pitwall.config import get_settings
from pitwall.core.enums import WorkloadState
from pitwall.db import get_pool
from pitwall.mcp.tools.output import normalize_workload_output
from pitwall.resolver import (
    CapabilityDisabledError,
    CapabilityNotFoundError,
    NoHealthyProviderError,
    ProviderNotFoundError,
    ResolverError,
)
from pitwall.routing.production import (
    JobResultPage,
    PreparedRoutingPayload,
    ProductionRoutingService,
    RouteGuardrailRejected,
    RoutePlanningError,
    RoutingOperation,
)
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendPayloadScanResult,
    get_pre_spend_inspection_service,
)

_CONTROL_FIELDS = {
    "capability_id",
    "capability",
    "capability_name",
    "provider_id",
    "dry_run",
    "idempotency_key",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


async def get_production_routing_service() -> ProductionRoutingService:
    return ProductionRoutingService(await get_pool(), settings=get_settings())


def _prepare_request(
    request_payload: dict[str, Any],
    *,
    routing_payload_key: str,
    record: bool,
) -> tuple[dict[str, Any], PreparedRoutingPayload]:
    """Inspect one whole MCP request before pool acquisition and attest its input."""

    service = get_pre_spend_inspection_service()
    inspect = service.inspect if record else service.preview
    result = inspect(
        request_payload,
        validate_redacted=_require_dict,
    )
    if result.decision == PreSpendDecision.BLOCK:
        raise PreSpendPayloadRejected(
            decision=result.decision.value,
            findings=[finding.to_dict() for finding in result.findings],
        )
    safe_request = _require_dict(result.redacted_payload)
    safe_payload = _require_dict(safe_request[routing_payload_key])
    routing_result: PreSpendPayloadScanResult = replace(
        result,
        redacted_payload=safe_payload,
    )
    return safe_request, PreparedRoutingPayload.from_inspection(routing_result)


def _require_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("inference payload must be a JSON object")
    return value


async def pitwall_submit_inference(
    capability_id: str,
    payload: dict[str, Any] | None = None,
    provider_id: str | None = None,
    dry_run: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    capability_params = {
        key: value for key, value in (payload or {}).items() if key not in _CONTROL_FIELDS
    }
    guarded_request, prepared_payload = _prepare_request(
        {
            "capability_id": capability_id,
            "payload": capability_params,
            "provider_id": provider_id,
            "idempotency_key": idempotency_key,
        },
        routing_payload_key="payload",
        record=not dry_run,
    )
    capability_params = _require_dict(guarded_request["payload"])
    service = await get_production_routing_service()
    try:
        if dry_run:
            plan = await service.preview_prepared(
                capability_id=capability_id,
                prepared_payload=prepared_payload,
                operation=RoutingOperation.SYNC_INFERENCE,
                provider_id=provider_id,
            )
            return {
                "workload_id": f"dry_run_inference_{plan.plan_id.removeprefix('plan_')[:16]}",
                "cost": plan.selected.quote.to_serializable_dict(),
                "provider_id": plan.selected_provider_id,
                "external_job_id": None,
                "plan_id": plan.plan_id,
                "plan": plan.to_dict(),
                "state": WorkloadState.COMPLETED.value,
                "result": {"dry_run": True},
                "trace_id": None,
            }
        execution = await service.execute_sync_prepared(
            capability_id=capability_id,
            prepared_payload=prepared_payload,
            provider_id=provider_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # reason: normalize shared-service failures at the MCP boundary
        raise _transport_error(exc, capability_id) from exc
    output = normalize_workload_output(execution.workload)
    output["plan_id"] = execution.plan.plan_id
    output["plan"] = execution.plan.to_dict()
    output["result"] = execution.output
    return output


async def pitwall_submit_job(
    capability_id: str,
    input: dict[str, Any],
    provider_id: str | None = None,
    dry_run: bool = False,
    idempotency_key: str | None = None,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    guarded_request, prepared_payload = _prepare_request(
        {
            "capability_id": capability_id,
            "input": input,
            "provider_id": provider_id,
            "idempotency_key": idempotency_key,
            "webhook_url": webhook_url,
        },
        routing_payload_key="input",
        record=not dry_run,
    )
    guarded_webhook = guarded_request.get("webhook_url")
    webhook_url = guarded_webhook if isinstance(guarded_webhook, str) else None
    service = await get_production_routing_service()
    try:
        if dry_run:
            plan = await service.preview_prepared(
                capability_id=capability_id,
                prepared_payload=prepared_payload,
                operation=RoutingOperation.ASYNC_INFERENCE,
                provider_id=provider_id,
            )
            return {
                "workload_id": f"dry_run_job_{plan.plan_id.removeprefix('plan_')[:16]}",
                "cost": plan.selected.quote.to_serializable_dict(),
                "provider_id": plan.selected_provider_id,
                "external_job_id": None,
                "plan_id": plan.plan_id,
                "plan": plan.to_dict(),
                "state": WorkloadState.QUEUED.value,
                "result": {"dry_run": True},
                "trace_id": None,
            }
        workload = await service.submit_job_prepared(
            capability_id=capability_id,
            prepared_payload=prepared_payload,
            provider_id=provider_id,
            idempotency_key=idempotency_key,
            webhook_url=webhook_url,
        )
    except Exception as exc:  # reason: normalize shared-service failures at the MCP boundary
        raise _transport_error(exc, capability_id) from exc
    return normalize_workload_output(workload)


async def pitwall_get_job_status(workload_id: str) -> dict[str, Any]:
    service = await get_production_routing_service()
    try:
        return normalize_workload_output(await service.get_job(workload_id))
    except LookupError as exc:
        raise WorkloadNotFound(workload_id) from exc


async def pitwall_get_job_result(workload_id: str) -> dict[str, Any]:
    service = await get_production_routing_service()
    try:
        return _job_result_output(await service.job_result(workload_id))
    except LookupError as exc:
        raise WorkloadNotFound(workload_id) from exc


def _job_result_output(page: JobResultPage) -> dict[str, Any]:
    """Layer the bounded result onto the established MCP workload envelope."""

    return {
        "workload_id": page.workload_id,
        "cost": {
            "estimate_usd": (
                str(page.cost_estimate_usd) if page.cost_estimate_usd is not None else None
            ),
            "actual_usd": str(page.cost_actual_usd) if page.cost_actual_usd is not None else None,
        },
        "provider_id": page.provider_id,
        "external_job_id": page.external_job_id,
        "plan_id": page.plan_id,
        "plan": page.plan,
        "state": page.state,
        "result": page.result,
        "trace_id": page.trace_id,
        "available": page.available,
        "unavailable_reason": page.unavailable_reason,
    }


async def pitwall_cancel_job(workload_id: str) -> dict[str, Any]:
    service = await get_production_routing_service()
    try:
        workload = await service.cancel_job(workload_id)
    except (LookupError, ResolverError, RoutePlanningError) as exc:
        mapped = cancel_error(exc, workload_id)
        if mapped is exc:
            raise
        raise mapped from exc
    output = normalize_workload_output(workload)
    output["cancelled"] = (
        workload.state.value if hasattr(workload.state, "value") else workload.state
    ) == WorkloadState.CANCELLED.value
    return output


def _transport_error(exc: Exception, capability_id: str) -> Exception:
    if isinstance(exc, CapabilityNotFoundError):
        return CapabilityNotFound(exc.capability_name)
    if isinstance(exc, CapabilityDisabledError):
        return CapabilityDisabled(exc.capability_name)
    if isinstance(exc, ProviderNotFoundError):
        return ProviderNotFound(exc.provider_id)
    if isinstance(exc, NoHealthyProviderError):
        return ProviderUnavailable(exc.capability_name)
    if isinstance(exc, RouteGuardrailRejected):
        return PreSpendPayloadRejected(
            decision="block",
            findings=[{"rule": rule_id} for rule_id in exc.rule_ids],
        )
    if isinstance(exc, RoutePlanningError):
        return ProviderUnavailable(capability_id)
    return exc


__all__ = [
    "get_production_routing_service",
    "pitwall_cancel_job",
    "pitwall_get_job_result",
    "pitwall_get_job_status",
    "pitwall_submit_inference",
    "pitwall_submit_job",
]
