"""Transport schemas for the shared provider-operations read surface.

The domain service owns the values and their safe serialization.  These
schemas only make the REST contract explicit for OpenAPI consumers.
"""

from __future__ import annotations

from pydantic import Field

from pitwall.core.models import JsonObject, PitwallModel


class ProviderOperationDescriptorResponse(PitwallModel):
    """Safe persisted provider descriptor; credential values are never included."""

    provider_id: str
    name: str
    adapter_id: str
    credential_ref: str
    credential_configured: bool
    provider_type: str
    enabled: bool
    persisted_health: str
    capabilities: list[str]
    pricing_kind: str
    config: JsonObject


class ProviderOperationDescriptorListResponse(PitwallModel):
    """Bounded descriptor listing envelope."""

    items: list[ProviderOperationDescriptorResponse]
    total: int = Field(ge=0, le=100)


class ProviderOperationAvailabilityEntryResponse(PitwallModel):
    """One Decimal-preserving provider availability item."""

    resource_id: str
    kind: str
    available: bool | None
    region: str | None
    accelerator: str | None
    accelerator_count: int | None
    pricing: dict[str, str]
    attributes: JsonObject


class ProviderOperationAvailabilityResponse(PitwallModel):
    """Bounded, explicit live availability observation."""

    provider_id: str
    status: str
    # The service's JSON-stable UTC representation is intentionally passed
    # through unchanged so REST matches MCP and CLI byte-for-byte.
    observed_at: str
    source_contract: str | None
    items: list[ProviderOperationAvailabilityEntryResponse]
    error_code: str | None


class ProviderOperationHealthResponse(PitwallModel):
    """Persisted provider health with an optional live probe result."""

    provider_id: str
    persisted_health: str
    live_status: str
    # See ``ProviderOperationAvailabilityResponse.observed_at``.
    observed_at: str
    availability_count: int | None
    error_code: str | None


__all__ = [
    "ProviderOperationAvailabilityEntryResponse",
    "ProviderOperationAvailabilityResponse",
    "ProviderOperationDescriptorListResponse",
    "ProviderOperationDescriptorResponse",
    "ProviderOperationHealthResponse",
]
