"""Strict public schemas for production planning and asynchronous submission."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from pitwall.core.models import PitwallModel


class RoutePreviewRequest(PitwallModel):
    """Non-persisting request for one deterministic production plan."""

    capability_id: Annotated[str, Field(min_length=1, max_length=255)]
    payload: dict[str, Any] = Field(default_factory=dict)
    operation: Literal["sync_inference", "async_inference", "compute"] = "sync_inference"
    provider_id: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class RoutePlanResponse(PitwallModel):
    """Payload-free route plan and explanation."""

    plan_id: str
    observed_at: str
    operation: str
    capability_id: str
    capability_name: str
    payload_sha256: str
    provider_constraint: str | None
    mode: str
    weights: dict[str, str]
    selected_provider_id: str
    fallback_chain: list[str]
    attempts: list[dict[str, Any]]
    ranked_candidates: list[dict[str, Any]]
    eliminated: list[dict[str, Any]]
    signal_policy: dict[str, str]
    escape_hatch: dict[str, Any] | None = None


class RoutedJobResponse(PitwallModel):
    """Job submission response shared semantically by REST/MCP/CLI/TUI."""

    workload_id: str
    state: str
    provider_id: str
    external_job_id: str | None
    plan: dict[str, Any]
    cost: dict[str, Any]


class JobEventPageResponse(PitwallModel):
    """Bounded persisted lifecycle events with explicit stream support state."""

    workload_id: str
    plan_id: str | None
    events: list[dict[str, Any]]
    streaming_supported: bool
    unavailable_reason: str


__all__ = [
    "JobEventPageResponse",
    "RoutePlanResponse",
    "RoutePreviewRequest",
    "RoutedJobResponse",
]
