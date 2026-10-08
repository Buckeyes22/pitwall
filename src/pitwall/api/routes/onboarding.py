"""Thin REST adapter for the shared RunPod onboarding service."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from pitwall.api.exceptions import ApiErrorResponse
from pitwall.api.routes.runpod_resources import _SafeValidationRoute
from pitwall.onboarding import (
    OnboardingAction,
    OnboardingCommand,
    OnboardingError,
    OnboardingResult,
    RunPodOnboardingRequest,
    RunPodOnboardingService,
    create_runpod_onboarding_service,
)

router = APIRouter(
    prefix="/v1/admin/runpod/onboarding",
    tags=["runpod-onboarding"],
    route_class=_SafeValidationRoute,
)


class ConfirmedOnboardingRequest(OnboardingCommand):
    """Confirmed apply/resume envelope using the shared request model."""


def runpod_onboarding_service(request: Request) -> RunPodOnboardingService:
    configured = getattr(request.app.state, "runpod_onboarding_service", None)
    if configured is not None:
        if not isinstance(configured, RunPodOnboardingService):
            raise RuntimeError("app.state.runpod_onboarding_service has an invalid type")
        return configured
    pool: Any | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    service = create_runpod_onboarding_service(pool, actor="rest:admin")
    request.app.state.runpod_onboarding_service = service
    return service


Service = Annotated[RunPodOnboardingService, Depends(runpod_onboarding_service)]


async def _execute(command: OnboardingCommand, service: Service) -> OnboardingResult:
    try:
        return await service.execute(command)
    except OnboardingError as exc:
        raise ApiErrorResponse(_status(exc), exc.to_dict()) from exc


def _status(exc: OnboardingError) -> int:
    if exc.code in {
        "plan_confirmation_mismatch",
        "broker_state_conflict",
        "resource_drift",
        "unowned_resource_conflict",
        "onboarding_lock_timeout",
    }:
        return 409
    if exc.code in {
        "credential_reference_unset",
        "data_center_not_discovered",
        "gpu_not_discovered",
        "gpu_cloud_unavailable",
        "gpu_count_unavailable",
        "gpu_unavailable_in_data_center",
        "guardrail_rejected",
        "pricing_unavailable",
        "rate_below_discovered_price",
        "selected_resource_not_found",
        "volume_unsupported_in_data_center",
    }:
        return 422
    if exc.code.startswith("runpod_audit_") or exc.code == "onboarding_lock_unavailable":
        return 503
    return 502


@router.post("/plan", response_model=OnboardingResult)
async def plan(body: RunPodOnboardingRequest, service: Service) -> OnboardingResult:
    return await _execute(OnboardingCommand(request=body), service)


@router.post("/apply", response_model=OnboardingResult)
async def apply(body: ConfirmedOnboardingRequest, service: Service) -> OnboardingResult:
    if body.action != OnboardingAction.APPLY:
        raise ApiErrorResponse(
            422, {"error": "invalid_request", "detail": "apply route requires action=apply"}
        )
    return await _execute(body, service)


@router.post("/status", response_model=OnboardingResult)
async def status(body: RunPodOnboardingRequest, service: Service) -> OnboardingResult:
    return await _execute(OnboardingCommand(action=OnboardingAction.STATUS, request=body), service)


@router.post("/resume", response_model=OnboardingResult)
async def resume(body: ConfirmedOnboardingRequest, service: Service) -> OnboardingResult:
    if body.action != OnboardingAction.RESUME:
        raise ApiErrorResponse(
            422, {"error": "invalid_request", "detail": "resume route requires action=resume"}
        )
    return await _execute(body, service)


@router.post("/rollback", response_model=OnboardingResult)
async def rollback(body: RunPodOnboardingRequest, service: Service) -> OnboardingResult:
    return await _execute(
        OnboardingCommand(action=OnboardingAction.ROLLBACK, request=body), service
    )


__all__ = ["ConfirmedOnboardingRequest", "router", "runpod_onboarding_service"]
