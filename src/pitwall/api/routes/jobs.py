"""REST reads and cancellation over the provider-neutral job service."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request

from pitwall.api.exceptions import (
    JobCancelFailed,
    JobNotCancellable,
    JobNotReady,
    ProviderNotFound,
    WorkloadNotFound,
)
from pitwall.api.schemas.params import PathId
from pitwall.api.scopes import can_read_payloads
from pitwall.config import get_settings
from pitwall.core.models import Workload
from pitwall.cost.read_models import WorkloadCostRead
from pitwall.db.repository import WorkloadRepository
from pitwall.resolver.exceptions import ProviderNotFoundError, ResolverError
from pitwall.routing.production import (
    JobNotCancellableError,
    ProductionRoutingService,
    RoutePlanningError,
    RouteProviderInvocationError,
)

router = APIRouter()


def _service(request: Request) -> ProductionRoutingService:
    configured = getattr(request.app.state, "production_routing_service", None)
    if configured is not None:
        return cast(ProductionRoutingService, configured)
    pool: Any | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return ProductionRoutingService(
        pool,
        settings=get_settings(),
        workload_repository=_workload_repo(request),
    )


def _workload_repo(request: Request) -> WorkloadRepository:
    pool: Any | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return WorkloadRepository(pool)


def _serialize_workload(workload: Workload, *, include_payload: bool = True) -> dict[str, Any]:
    cost = WorkloadCostRead.from_persisted(
        cost_estimate_usd=workload.cost_estimate_usd,
        cost_ceiling_usd=workload.cost_ceiling_usd,
        cost_quote=workload.cost_quote,
        cost_actual_usd=workload.cost_actual_usd,
        cost_actual_provenance=workload.cost_actual_provenance,
        cost_reconciled_at=workload.cost_reconciled_at,
    )
    return {
        "id": workload.id,
        "capability_id": workload.capability_id,
        "provider_id": workload.provider_id,
        "type": workload.type,
        "state": _value(workload.state),
        "external_job_id": workload.external_job_id,
        "runpod_job_id": workload.runpod_job_id,
        "idempotency_key": workload.idempotency_key,
        "input": workload.input if include_payload else None,
        "result": workload.result if include_payload else None,
        "fallback_chain": workload.fallback_chain,
        "route_plan_id": workload.route_plan_id,
        "route_plan": workload.route_plan,
        "error": workload.error,
        "submitted_at": workload.submitted_at.isoformat() if workload.submitted_at else None,
        "started_at": workload.started_at.isoformat() if workload.started_at else None,
        "completed_at": workload.completed_at.isoformat() if workload.completed_at else None,
        "execution_ms": workload.execution_ms,
        "queue_ms": workload.queue_ms,
        "cold_start_ms": workload.cold_start_ms,
        "input_bytes": workload.input_bytes,
        "output_bytes": workload.output_bytes,
        "cost_estimate_usd": (
            str(workload.cost_estimate_usd) if workload.cost_estimate_usd is not None else None
        ),
        "cost_actual_usd": (
            str(workload.cost_actual_usd) if workload.cost_actual_usd is not None else None
        ),
        "cost": cost.to_serializable_dict(),
        "langfuse_trace_id": workload.langfuse_trace_id,
    }


async def _required_job(service: ProductionRoutingService, workload_id: str) -> Workload:
    try:
        return await service.get_job(workload_id)
    except LookupError as exc:
        raise WorkloadNotFound(workload_id) from exc


@router.get("/v1/jobs/{workload_id}")
async def get_job(
    workload_id: PathId,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)
    return _serialize_workload(
        await _required_job(service, workload_id),
        include_payload=can_read_payloads(request.scope),
    )


@router.get("/v1/jobs/{workload_id}/status")
async def get_job_status(
    workload_id: PathId,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)
    workload = await _required_job(service, workload_id)
    return {
        "id": workload.id,
        "state": _value(workload.state),
        "external_job_id": workload.external_job_id,
        "runpod_job_id": workload.runpod_job_id,
        "submitted_at": workload.submitted_at.isoformat() if workload.submitted_at else None,
        "started_at": workload.started_at.isoformat() if workload.started_at else None,
        "completed_at": workload.completed_at.isoformat() if workload.completed_at else None,
        "error": workload.error,
        "route_plan_id": workload.route_plan_id,
        "selected_provider_id": workload.provider_id,
    }


@router.get("/v1/jobs/{workload_id}/result")
async def get_job_result(
    workload_id: PathId,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)
    try:
        page = await service.job_result(workload_id)
    except LookupError as exc:
        raise WorkloadNotFound(workload_id) from exc
    if page.unavailable_reason == "job_not_terminal":
        raise JobNotReady(workload_id, page.state)
    if not can_read_payloads(request.scope):
        # Metadata only: the stored result needs the scope that creates jobs.
        return {
            "id": page.workload_id,
            "route_plan_id": page.plan_id,
            "selected_provider_id": page.provider_id,
            "result": None,
            "available": False,
            "unavailable_reason": "insufficient_scope",
        }
    return {
        "id": page.workload_id,
        "route_plan_id": page.plan_id,
        "selected_provider_id": page.provider_id,
        "result": page.result,
        "available": page.available,
        "unavailable_reason": page.unavailable_reason,
    }


@router.post("/v1/jobs/{workload_id}/cancel")
async def cancel_job(
    workload_id: PathId,
    request: Request,
) -> dict[str, Any]:
    service = _service(request)
    try:
        return _serialize_workload(await service.cancel_job(workload_id))
    except (LookupError, ResolverError, RoutePlanningError) as exc:
        mapped = cancel_error(exc, workload_id)
        if mapped is exc:
            raise
        raise mapped from exc


def cancel_error(exc: Exception, workload_id: str) -> Exception:
    """The typed error REST and MCP report for a failed job cancellation."""
    if isinstance(exc, ProviderNotFoundError):
        return ProviderNotFound(exc.provider_id)
    if isinstance(exc, LookupError):
        return WorkloadNotFound(workload_id)
    if isinstance(exc, JobNotCancellableError):
        return JobNotCancellable(workload_id)
    if isinstance(exc, RouteProviderInvocationError):
        return JobCancelFailed(workload_id)
    return exc


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = ["cancel_error", "router"]
