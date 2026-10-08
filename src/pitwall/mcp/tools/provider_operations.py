"""Thin MCP adapters for the shared provider-operations read service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from mcp.shared.exceptions import MCPError
from pydantic import Field

from pitwall.api.exceptions import ProviderNotFound
from pitwall.db import get_pool
from pitwall.mcp.error_adapter import PITWALL_ERROR_CODE_BASE, adapt_error
from pitwall.providers.service import ProviderOperationsService

ProviderId = Annotated[str, Field(min_length=1, description="Persisted provider ID.")]
CapabilityId = Annotated[
    str | None,
    Field(description="Optional persisted capability ID filter."),
]
ProviderOpsLimit = Annotated[
    int,
    Field(ge=1, le=100, description="Bounded provider or availability result limit."),
]
Probe = Annotated[
    bool,
    Field(description="Make one explicit bounded live availability read when true."),
]


async def get_provider_operations_service() -> ProviderOperationsService:
    """Build the common service; credential resolution remains inside it."""
    return ProviderOperationsService(await get_pool())


async def pitwall_provider_ops_list_descriptors(
    capability_id: CapabilityId = None,
    enabled_only: bool = False,
    limit: ProviderOpsLimit = 100,
) -> dict[str, object]:
    """List safe provider descriptors without provider-network egress."""
    _optional_text(capability_id)
    _bounded_limit(limit)
    if not isinstance(enabled_only, bool):
        raise _invalid_request_error()
    return await _call(
        lambda service: _descriptor_list(
            service,
            capability_id=capability_id,
            enabled_only=enabled_only,
            limit=limit,
        )
    )


async def pitwall_provider_ops_describe(provider_id: ProviderId) -> dict[str, object]:
    """Describe one provider without provider-network egress."""
    _provider_id(provider_id)
    descriptor = await _call(lambda service: service.describe(provider_id))
    if descriptor is None:
        raise adapt_error(ProviderNotFound(provider_id))
    return descriptor.as_dict()


async def pitwall_provider_ops_availability(
    provider_id: ProviderId,
    limit: ProviderOpsLimit = 100,
) -> dict[str, object]:
    """Perform one explicit bounded provider availability read."""
    _provider_id(provider_id)
    _bounded_limit(limit)
    availability = await _call(lambda service: service.availability(provider_id, limit=limit))
    if availability is None:
        raise adapt_error(ProviderNotFound(provider_id))
    return availability.as_dict()


async def pitwall_provider_ops_health(
    provider_id: ProviderId,
    probe: Probe = False,
) -> dict[str, object]:
    """Read persisted health; only ``probe=true`` permits one live read."""
    _provider_id(provider_id)
    if not isinstance(probe, bool):
        raise _invalid_request_error()
    health = await _call(lambda service: service.health(provider_id, probe=probe))
    if health is None:
        raise adapt_error(ProviderNotFound(provider_id))
    return health.as_dict()


async def _descriptor_list(
    service: ProviderOperationsService,
    *,
    capability_id: str | None,
    enabled_only: bool,
    limit: int,
) -> dict[str, object]:
    descriptors = await service.list_descriptors(
        capability_id=capability_id,
        enabled_only=enabled_only,
        limit=limit,
    )
    items = [descriptor.as_dict() for descriptor in descriptors]
    return {"items": items, "total": len(items)}


async def _call[T](
    operation: Callable[[ProviderOperationsService], Awaitable[T]],
) -> T:
    try:
        service = await get_provider_operations_service()
        return await operation(service)
    except MCPError:
        raise
    except ValueError as exc:
        del exc
        raise _invalid_request_error() from None
    except Exception as exc:  # reason: provider and persistence detail must never cross MCP
        del exc
        raise _unavailable_error() from None


def _provider_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise _invalid_request_error()


def _optional_text(value: object) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip() or "\x00" in value):
        raise _invalid_request_error()


def _bounded_limit(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise _invalid_request_error()


def _invalid_request_error() -> MCPError:
    return MCPError(
        code=PITWALL_ERROR_CODE_BASE,
        message="invalid provider operations request",
        data={"error": "provider_operations_invalid_request"},
    )


def _unavailable_error() -> MCPError:
    return MCPError(
        code=PITWALL_ERROR_CODE_BASE,
        message="provider operations unavailable",
        data={"error": "provider_operations_unavailable"},
    )


__all__ = [
    "get_provider_operations_service",
    "pitwall_provider_ops_availability",
    "pitwall_provider_ops_describe",
    "pitwall_provider_ops_health",
    "pitwall_provider_ops_list_descriptors",
]
