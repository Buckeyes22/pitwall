"""Thin MCP tools for the shared RunPod onboarding service."""

from __future__ import annotations

from contextlib import suppress
from typing import Annotated, Any

from mcp.shared.exceptions import MCPError
from pydantic import Field, ValidationError

from pitwall.db import get_pool
from pitwall.mcp.error_adapter import PITWALL_ERROR_CODE_BASE
from pitwall.onboarding import (
    OnboardingAction,
    OnboardingCommand,
    OnboardingError,
    RunPodOnboardingRequest,
    RunPodOnboardingService,
    create_runpod_onboarding_service,
)

ConfirmedPlanId = Annotated[
    str,
    Field(
        pattern=r"^runpod_onboard_[a-f0-9]{24}$",
        description="Exact plan_id returned by the matching plan call.",
    ),
]


async def get_runpod_onboarding_service() -> RunPodOnboardingService:
    return create_runpod_onboarding_service(await get_pool(), actor="mcp:admin")


async def pitwall_runpod_onboarding_plan(
    request: RunPodOnboardingRequest,
) -> dict[str, object]:
    """Plan a complete RunPod topology; this is always zero-write."""
    return await _call(OnboardingAction.PLAN, request)


async def pitwall_runpod_onboarding_apply(
    request: RunPodOnboardingRequest,
    confirmed_plan_id: ConfirmedPlanId,
) -> dict[str, object]:
    """Apply the exact confirmed RunPod onboarding plan."""
    return await _call(OnboardingAction.APPLY, request, confirmed_plan_id)


async def pitwall_runpod_onboarding_status(
    request: RunPodOnboardingRequest,
) -> dict[str, object]:
    """Recompute onboarding completion from current provider and broker state."""
    return await _call(OnboardingAction.STATUS, request)


async def pitwall_runpod_onboarding_resume(
    request: RunPodOnboardingRequest,
    confirmed_plan_id: ConfirmedPlanId,
) -> dict[str, object]:
    """Resume incomplete onboarding after confirming the unchanged plan."""
    return await _call(OnboardingAction.RESUME, request, confirmed_plan_id)


async def pitwall_runpod_onboarding_rollback(
    request: RunPodOnboardingRequest,
) -> dict[str, object]:
    """Return safe rollback and retained-resource guidance without writing."""
    return await _call(OnboardingAction.ROLLBACK, request)


async def _call(
    action: OnboardingAction,
    raw_request: RunPodOnboardingRequest | dict[str, Any],
    confirmed_plan_id: str | None = None,
) -> dict[str, object]:
    service: RunPodOnboardingService | None = None
    try:
        request = (
            raw_request
            if isinstance(raw_request, RunPodOnboardingRequest)
            else RunPodOnboardingRequest.model_validate(raw_request)
        )
        command = OnboardingCommand(
            action=action,
            request=request,
            confirmed_plan_id=confirmed_plan_id,
        )
        service = await get_runpod_onboarding_service()
        result = await service.execute(command)
        return result.model_dump(mode="json")
    except MCPError:
        raise
    except ValidationError, ValueError:
        raise _invalid_request() from None
    except OnboardingError as exc:
        raise MCPError(
            code=PITWALL_ERROR_CODE_BASE,
            message="RunPod onboarding failed safely",
            data=exc.to_dict(),
        ) from None
    except Exception:  # reason: provider/persistence detail must not cross MCP
        raise MCPError(
            code=PITWALL_ERROR_CODE_BASE,
            message="RunPod onboarding unavailable",
            data={"error": "runpod_onboarding_unavailable"},
        ) from None
    finally:
        if service is not None:
            # Cleanup failure must not replace a bounded MCP result/error with
            # transport or credential-bearing client detail.
            with suppress(Exception):
                await service.aclose()


def _invalid_request() -> MCPError:
    return MCPError(
        code=PITWALL_ERROR_CODE_BASE,
        message="invalid RunPod onboarding request",
        data={"error": "runpod_onboarding_invalid_request"},
    )


__all__ = [
    "get_runpod_onboarding_service",
    "pitwall_runpod_onboarding_apply",
    "pitwall_runpod_onboarding_plan",
    "pitwall_runpod_onboarding_resume",
    "pitwall_runpod_onboarding_rollback",
    "pitwall_runpod_onboarding_status",
]
