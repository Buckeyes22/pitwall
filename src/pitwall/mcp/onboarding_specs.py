"""Feature-local MCP declarations for RunPod onboarding registry integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pitwall.mcp.tools.onboarding import (
    pitwall_runpod_onboarding_apply,
    pitwall_runpod_onboarding_plan,
    pitwall_runpod_onboarding_resume,
    pitwall_runpod_onboarding_rollback,
    pitwall_runpod_onboarding_status,
)


@dataclass(frozen=True, slots=True)
class OnboardingToolSpec:
    name: str
    description: str
    handler: Callable[..., dict[str, Any]] | Callable[..., Awaitable[dict[str, Any]]]


ONBOARDING_TOOL_SPECS: tuple[OnboardingToolSpec, ...] = (
    OnboardingToolSpec(
        "pitwall_runpod_onboarding_plan",
        "Plan a complete RunPod topology with zero provider or database writes.",
        pitwall_runpod_onboarding_plan,
    ),
    OnboardingToolSpec(
        "pitwall_runpod_onboarding_apply",
        "Apply an exact confirmed RunPod onboarding plan idempotently.",
        pitwall_runpod_onboarding_apply,
    ),
    OnboardingToolSpec(
        "pitwall_runpod_onboarding_status",
        "Observe resumable RunPod onboarding state without writing.",
        pitwall_runpod_onboarding_status,
    ),
    OnboardingToolSpec(
        "pitwall_runpod_onboarding_resume",
        "Resume an incomplete exact confirmed RunPod onboarding plan.",
        pitwall_runpod_onboarding_resume,
    ),
    OnboardingToolSpec(
        "pitwall_runpod_onboarding_rollback",
        "Return safe rollback and retained-resource guidance without writing.",
        pitwall_runpod_onboarding_rollback,
    ),
)


__all__ = ["ONBOARDING_TOOL_SPECS", "OnboardingToolSpec"]
