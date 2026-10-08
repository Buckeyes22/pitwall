"""Thin REST surface for production planning and async job submission."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Query, Request

from pitwall.api.exceptions import (
    CapabilityDisabled,
    CapabilityNotFound,
    PreSpendPayloadRejected,
    ProviderNotFound,
    ProviderUnavailable,
    WorkloadNotFound,
)
from pitwall.api.routing_schemas import (
    JobEventPageResponse,
    RoutedJobResponse,
    RoutePlanResponse,
    RoutePreviewRequest,
)
from pitwall.api.schemas.jobs import JobSubmitRequest
from pitwall.api.schemas.params import PathId
from pitwall.config import get_settings
from pitwall.core.models import Workload
from pitwall.cost import BudgetRejected
from pitwall.resolver.exceptions import (
    CapabilityDisabledError,
    CapabilityNotFoundError,
    NoHealthyProviderError,
    ProviderNotFoundError,
)
from pitwall.routing.production import (
    ProductionRoutingService,
    RouteBudgetQuote,
    RouteGuardrailRejected,
    RoutingOperation,
)

routing_router = APIRouter(tags=["routing"])


def _service(request: Request) -> ProductionRoutingService:
    configured = getattr(request.app.state, "production_routing_service", None)
    if configured is not None:
        return cast(ProductionRoutingService, configured)
    pool: Any | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return ProductionRoutingService(pool, settings=get_settings())


@routing_router.post(
    "/v1/routing/preview",
    response_model=RoutePlanResponse,
    response_model_exclude_unset=True,
)
async def preview_route(
    body: RoutePreviewRequest,
    service: ProductionRoutingService = Depends(_service),
) -> dict[str, object]:
    """Preview the exact planner without persistence, spend, or provider egress."""

    try:
        plan = await service.preview(
            capability_id=body.capability_id,
            payload=body.payload,
            operation=RoutingOperation(body.operation),
            provider_id=body.provider_id,
        )
    except BudgetRejected:
        raise
    except Exception as exc:  # reason: normalize shared-service failures at the REST boundary
        raise _api_error(exc, body.capability_id) from exc
    return plan.to_dict()


@routing_router.post("/v1/jobs", response_model=RoutedJobResponse)
async def submit_job(
    body: JobSubmitRequest,
    service: ProductionRoutingService = Depends(_service),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
) -> dict[str, object]:
    """Submit through the same guarded planner/executor used by MCP and operators."""

    payload = body.input
    request_key = idempotency_key or body.idempotency_key
    try:
        if body.dry_run:
            plan = await service.preview(
                capability_id=body.capability_id,
                payload=payload,
                operation=RoutingOperation.ASYNC_INFERENCE,
                provider_id=body.provider_id,
            )
            return {
                "workload_id": f"dry_run_job_{plan.plan_id.removeprefix('plan_')[:16]}",
                "state": "queued",
                "provider_id": plan.selected_provider_id,
                "external_job_id": None,
                "plan": plan.to_dict(),
                "cost": RouteBudgetQuote(plan, fallback_spend=False).to_serializable_dict(),
            }
        workload = await service.submit_job(
            capability_id=body.capability_id,
            payload=payload,
            provider_id=body.provider_id,
            idempotency_key=request_key,
            webhook_url=body.webhook_url,
        )
    except BudgetRejected:
        raise
    except Exception as exc:  # reason: normalize shared-service failures at the REST boundary
        raise _api_error(exc, body.capability_id) from exc
    return _job_response(workload)


@routing_router.get(
    "/v1/jobs/{workload_id}/events",
    response_model=JobEventPageResponse,
)
async def job_events(
    workload_id: PathId,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    service: ProductionRoutingService = Depends(_service),
) -> dict[str, object]:
    """Return a bounded lifecycle page or the stable unsupported stream state."""

    try:
        return (await service.job_events(workload_id, limit=limit)).to_dict()
    except LookupError as exc:
        raise WorkloadNotFound(workload_id) from exc


def _job_response(workload: Workload) -> dict[str, object]:
    return {
        "workload_id": workload.id,
        "state": _value(workload.state),
        "provider_id": workload.provider_id,
        "external_job_id": workload.external_job_id,
        "plan": workload.route_plan or {},
        "cost": workload.cost_quote or {},
    }


def _api_error(exc: Exception, capability_id: str) -> Exception:
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
    return ProviderUnavailable(capability_id)


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = ["job_events", "preview_route", "routing_router", "submit_job"]
