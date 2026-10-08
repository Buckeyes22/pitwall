"""REST synchronous inference through the shared production routing service."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse

from pitwall.api.exceptions import (
    CapabilityDisabled,
    CapabilityNotFound,
    ChangeSetTooBroad,
    IdempotencyMismatch,
    PreSpendPayloadRejected,
    ProviderNotFound,
    ProviderUnavailable,
)
from pitwall.api.schemas.inference import InferenceRequest, InferenceResponse
from pitwall.api.schemas.leases import lease_patch_conflicting_fields
from pitwall.config import get_settings
from pitwall.resolver import (
    CapabilityDisabledError,
    CapabilityNotFoundError,
    NoHealthyProviderError,
    ProviderNotFoundError,
)
from pitwall.routing.coalescing import AsyncRequestCoalescer, build_inference_coalescing_key
from pitwall.routing.production import (
    ProductionRoutingService,
    RouteExecutionResult,
    RouteGuardrailRejected,
    RoutePlanningError,
    RoutingOperation,
)

router = APIRouter()

_INFERENCE_CONTROL_FIELDS = {
    "capability_id",
    "capability",
    "capability_name",
    "provider_id",
    "dry_run",
    "idempotency_key",
}

_INFERENCE_COALESCER = AsyncRequestCoalescer[RouteExecutionResult]()


def _routing_service(request: Request) -> ProductionRoutingService:
    configured = getattr(request.app.state, "production_routing_service", None)
    if configured is not None:
        return cast(ProductionRoutingService, configured)
    pool: Any | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return ProductionRoutingService(pool, settings=get_settings())


@router.post("/v1/inference", response_model=InferenceResponse)
async def create_inference(
    body: InferenceRequest,
    request: Request,
    idempotency_key_header: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ] = None,
    service: ProductionRoutingService = Depends(_routing_service),
) -> Response:
    """Validate once, then preview or execute the exact production route."""

    raw_body = await request.json()
    conflicting_fields = lease_patch_conflicting_fields(raw_body)
    if conflicting_fields:
        raise ChangeSetTooBroad(conflicting_fields)
    capability_params = {
        key: value for key, value in raw_body.items() if key not in _INFERENCE_CONTROL_FIELDS
    }
    idempotency_key = idempotency_key_header or body.idempotency_key

    try:
        if body.dry_run:
            plan = await service.preview(
                capability_id=body.capability_id,
                payload=capability_params,
                operation=RoutingOperation.SYNC_INFERENCE,
                provider_id=body.provider_id,
            )
            workload_id = f"dry_run_inference_{plan.plan_id.removeprefix('plan_')[:16]}"
            return JSONResponse(
                content={
                    "workload_id": workload_id,
                    "result": {"dry_run": True, "plan": plan.to_dict()},
                },
                headers={
                    "X-Pitwall-Workload-ID": workload_id,
                    "X-Pitwall-Capability": plan.capability_name,
                    "X-Pitwall-Provider-ID": plan.selected_provider_id,
                    "X-Pitwall-Route-Plan-ID": plan.plan_id,
                },
            )

        coalescing_key = build_inference_coalescing_key(
            idempotency_key=idempotency_key,
            capability_id=body.capability_id,
            provider_id=body.provider_id or "auto",
            capability_params=capability_params,
            capability_class=await service.capability_class(body.capability_id),
        )
        execution = await _INFERENCE_COALESCER.run(
            coalescing_key,
            lambda: service.execute_sync(
                capability_id=body.capability_id,
                payload=capability_params,
                provider_id=body.provider_id,
                idempotency_key=idempotency_key,
            ),
        )
    except Exception as exc:  # reason: normalize shared-service failures at the REST boundary
        raise _api_error(exc, body.capability_id) from exc

    headers = {
        "X-Pitwall-Workload-ID": execution.workload.id,
        "X-Pitwall-Capability": str(execution.plan.to_dict().get("capability_name", "")),
        "X-Pitwall-Provider-ID": execution.workload.provider_id,
        "X-Pitwall-Route-Plan-ID": execution.plan.plan_id,
    }
    if execution.workload.langfuse_trace_id:
        headers["X-Pitwall-Trace"] = execution.workload.langfuse_trace_id
    return JSONResponse(
        content={"workload_id": execution.workload.id, "result": execution.output},
        headers=headers,
    )


def _api_error(exc: Exception, capability_id: str) -> Exception:
    if isinstance(exc, CapabilityNotFoundError):
        return CapabilityNotFound(exc.capability_name)
    if isinstance(exc, CapabilityDisabledError):
        return CapabilityDisabled(exc.capability_name)
    if isinstance(exc, ProviderNotFoundError):
        return ProviderNotFound(exc.provider_id)
    if isinstance(exc, NoHealthyProviderError):
        hatch = getattr(exc, "escape_hatch", None)
        return ProviderUnavailable(
            exc.capability_name,
            escape_hatch=hatch.to_dict() if hatch is not None else None,
        )
    if isinstance(exc, RouteGuardrailRejected):
        return PreSpendPayloadRejected(
            decision="block",
            findings=[{"rule": rule_id} for rule_id in exc.rule_ids],
        )
    if isinstance(exc, RoutePlanningError) and "idempotency key" in str(exc):
        return IdempotencyMismatch("existing")
    if isinstance(exc, RoutePlanningError):
        return ProviderUnavailable(capability_id)
    return exc


__all__ = ["router"]
