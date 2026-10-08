"""REST adapter for the shared provider-operations service.

The handlers are all read-only; a live provider request occurs only on the
explicit availability endpoint or when ``probe=true`` is supplied to health.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from pitwall.api.exceptions import ProviderNotFound
from pitwall.api.provider_operations_schemas import (
    ProviderOperationAvailabilityResponse,
    ProviderOperationDescriptorListResponse,
    ProviderOperationDescriptorResponse,
    ProviderOperationHealthResponse,
)
from pitwall.api.schemas.params import OptionalStrQuery, PathId
from pitwall.providers.service import ProviderDescriptor, ProviderOperationsService

provider_operations_router = APIRouter(tags=["provider-operations"])

_LIMIT = Annotated[int, Query(ge=1, le=100)]
_CAPABILITY_ID = Annotated[OptionalStrQuery, Query(min_length=1, max_length=255)]


def _service(request: Request) -> ProviderOperationsService:
    configured = getattr(request.app.state, "provider_operations_service", None)
    if configured is not None:
        if not isinstance(configured, ProviderOperationsService):
            raise RuntimeError("app.state.provider_operations_service has an invalid type")
        return configured
    pool: Any | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return ProviderOperationsService(pool)


def _list_payload(descriptors: tuple[ProviderDescriptor, ...]) -> dict[str, object]:
    items = [descriptor.as_dict() for descriptor in descriptors]
    return {"items": items, "total": len(items)}


@provider_operations_router.get(
    "/v1/provider-ops/descriptors",
    response_model=ProviderOperationDescriptorListResponse,
)
async def list_provider_descriptors(
    capability_id: _CAPABILITY_ID = None,
    enabled_only: bool = False,
    limit: _LIMIT = 100,
    service: ProviderOperationsService = Depends(_service),
) -> dict[str, object]:
    """List persisted safe descriptors without contacting provider APIs."""
    return _list_payload(
        await service.list_descriptors(
            capability_id=capability_id,
            enabled_only=enabled_only,
            limit=limit,
        )
    )


@provider_operations_router.get(
    "/v1/provider-ops/descriptors/{provider_id}",
    response_model=ProviderOperationDescriptorResponse,
)
async def describe_provider(
    provider_id: PathId,
    service: ProviderOperationsService = Depends(_service),
) -> dict[str, object]:
    """Return one safe persisted provider descriptor without live egress."""
    descriptor = await service.describe(provider_id)
    if descriptor is None:
        raise ProviderNotFound(provider_id)
    return descriptor.as_dict()


@provider_operations_router.get(
    "/v1/provider-ops/{provider_id}/availability",
    response_model=ProviderOperationAvailabilityResponse,
)
async def provider_availability(
    provider_id: PathId,
    limit: _LIMIT = 100,
    service: ProviderOperationsService = Depends(_service),
) -> dict[str, object]:
    """Perform one bounded, explicit provider availability read."""
    availability = await service.availability(provider_id, limit=limit)
    if availability is None:
        raise ProviderNotFound(provider_id)
    return availability.as_dict()


@provider_operations_router.get(
    "/v1/provider-ops/{provider_id}/health",
    response_model=ProviderOperationHealthResponse,
)
async def provider_health(
    provider_id: PathId,
    probe: bool = False,
    service: ProviderOperationsService = Depends(_service),
) -> dict[str, object]:
    """Read persisted health, optionally making one explicit bounded live probe."""
    health = await service.health(provider_id, probe=probe)
    if health is None:
        raise ProviderNotFound(provider_id)
    return health.as_dict()


__all__ = [
    "describe_provider",
    "list_provider_descriptors",
    "provider_availability",
    "provider_health",
    "provider_operations_router",
]
