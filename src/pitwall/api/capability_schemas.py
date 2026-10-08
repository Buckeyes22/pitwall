"""Pydantic v2 schemas for the Capability API surface."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from pitwall.core.enums import (
    CapabilityClass,
    CapabilityHint,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    ResultDelivery,
)
from pitwall.core.models import (
    JsonObject,
    NonNegativeInt,
    PitwallModel,
    RequiredStoredText,
    StoredJsonObject,
    StoredText,
)


class CapabilityDefaultsCreate(PitwallModel):
    """Defaults sub-object for capability creation."""

    execution_timeout_ms: NonNegativeInt = 60_000
    ttl_ms: NonNegativeInt = 300_000
    result_delivery: ResultDelivery = ResultDelivery.SYNC


class CapabilityCreate(PitwallModel):
    """Request body for POST /v1/admin/capabilities."""

    name: RequiredStoredText
    version: RequiredStoredText
    class_: CapabilityClass = Field(
        validation_alias="class",
        serialization_alias="class",
    )
    description: StoredText | None = None
    input_schema: StoredJsonObject = Field(default_factory=dict)
    output_schema: StoredJsonObject = Field(default_factory=dict)
    defaults: CapabilityDefaultsCreate = Field(default_factory=CapabilityDefaultsCreate)
    cost_mode: CostMode
    hints_supported: list[CapabilityHint] = Field(default_factory=list)
    source: CapabilitySource = CapabilitySource.API
    served_model_id: StoredText | None = None


class CapabilityPatch(PitwallModel):
    """Request body for PATCH /v1/admin/capabilities/{id}.

    All fields are optional — only supplied fields are merged.
    """

    name: RequiredStoredText | None = None
    version: RequiredStoredText | None = None
    class_: CapabilityClass | None = Field(
        default=None,
        validation_alias="class",
        serialization_alias="class",
    )
    description: StoredText | None = None
    input_schema: StoredJsonObject | None = None
    output_schema: StoredJsonObject | None = None
    defaults: CapabilityDefaultsCreate | None = None
    cost_mode: CostMode | None = None
    hints_supported: list[CapabilityHint] | None = None
    served_model_id: StoredText | None = None


class CapabilityListFilter(PitwallModel):
    """Query parameters for GET /v1/capabilities."""

    class_: CapabilityClass | None = Field(
        default=None,
        validation_alias="class",
        serialization_alias="class",
    )
    cost_mode: CostMode | None = None
    source: CapabilitySource | None = None
    enabled: bool | None = None


class CapabilityReadiness(PitwallModel):
    kind: Literal["llama-swap", "openai-models", "http-health"]
    state: Literal["ready", "starting", "absent"]


class CapabilityColdStart(PitwallModel):
    p50: float
    p95: float


class CapabilityResponse(PitwallModel):
    """Response body for GET /v1/capabilities/{name}."""

    id: str
    name: str
    version: str
    class_: CapabilityClass = Field(
        validation_alias="class",
        serialization_alias="class",
    )
    description: str | None = None
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject = Field(default_factory=dict)
    defaults: CapabilityDefaultsCreate = Field(default_factory=CapabilityDefaultsCreate)
    cost_mode: CostMode
    hints_supported: list[CapabilityHint] = Field(default_factory=list)
    source: CapabilitySource = CapabilitySource.API
    last_applied_yaml_hash: str | None = None
    served_model_id: str | None = None
    active_lease: dict[str, str] | None = None
    idle_timeout_min: int | None = None
    last_traffic_at: datetime | None = None
    renewal_policy: LeaseRenewalPolicy | None = None
    max_usd_per_hour: Decimal | None = None
    provider_kind: Literal["self_hosted"] | None = None
    readiness: CapabilityReadiness | None = None
    resident: bool | None = None
    cold_start_s: CapabilityColdStart | None = None
    slot_group: str | None = None
    exclusive: bool | None = None
    context_length: int | None = None
    tool_calling: Literal["enabled", "disabled", "unknown"] | None = None
    idle_unload_s: int | None = None
    enabled: bool = True
    created_at: str
    updated_at: str


__all__ = [
    "CapabilityCreate",
    "CapabilityColdStart",
    "CapabilityDefaultsCreate",
    "CapabilityListFilter",
    "CapabilityPatch",
    "CapabilityReadiness",
    "CapabilityResponse",
]
