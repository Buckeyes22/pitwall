"""Thin MCP adapters for production route preview and bounded job events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.shared.exceptions import MCPError
from pydantic import Field

from pitwall.api.exceptions import WorkloadNotFound
from pitwall.api.routes.routing import _api_error
from pitwall.config import get_settings
from pitwall.db import get_pool
from pitwall.mcp.error_adapter import PITWALL_ERROR_CODE_BASE, adapt_error
from pitwall.resolver.exceptions import ResolverError
from pitwall.routing.production import (
    ProductionRoutingService,
    RoutePlanningError,
    RoutingOperation,
)

CapabilityId = Annotated[str, Field(min_length=1, max_length=255)]
ProviderId = Annotated[str | None, Field(min_length=1, max_length=255)]
EventLimit = Annotated[int, Field(ge=1, le=100)]


async def get_production_routing_service() -> ProductionRoutingService:
    """Build the same shared service used by REST and operator transports."""

    return ProductionRoutingService(await get_pool(), settings=get_settings())


async def pitwall_preview_route(
    capability_id: CapabilityId,
    payload: dict[str, Any] | None = None,
    operation: str = "sync_inference",
    provider_id: ProviderId = None,
) -> dict[str, object]:
    """Return a non-persisting, no-egress deterministic production plan."""

    try:
        route_operation = RoutingOperation(operation)
    except ValueError:
        raise _invalid_request() from None
    return await _call(
        lambda service: _preview(
            service,
            capability_id=capability_id,
            payload=payload or {},
            operation=route_operation,
            provider_id=provider_id,
        ),
        capability_id=capability_id,
    )


async def pitwall_get_job_events(
    workload_id: Annotated[str, Field(min_length=1, max_length=255)],
    limit: EventLimit = 25,
) -> dict[str, object]:
    """Return bounded persisted lifecycle events and stream availability."""

    return await _call(
        lambda service: _events(service, workload_id=workload_id, limit=limit),
        workload_id=workload_id,
    )


async def _preview(
    service: ProductionRoutingService,
    *,
    capability_id: str,
    payload: dict[str, Any],
    operation: RoutingOperation,
    provider_id: str | None,
) -> dict[str, object]:
    plan = await service.preview(
        capability_id=capability_id,
        payload=payload,
        operation=operation,
        provider_id=provider_id,
    )
    return plan.to_dict()


async def _events(
    service: ProductionRoutingService,
    *,
    workload_id: str,
    limit: int,
) -> dict[str, object]:
    return (await service.job_events(workload_id, limit=limit)).to_dict()


async def _call[T](
    operation: Callable[[ProductionRoutingService], Awaitable[T]],
    *,
    capability_id: str | None = None,
    workload_id: str | None = None,
) -> T:
    try:
        return await operation(await get_production_routing_service())
    except MCPError:
        raise
    except LookupError as exc:
        if workload_id is None:
            raise _unavailable() from None
        del exc
        raise adapt_error(WorkloadNotFound(workload_id)) from None
    except (ResolverError, RoutePlanningError) as exc:
        # The same typed codes the REST routing routes return for these planning failures.
        raise adapt_error(_api_error(exc, capability_id or "")) from None
    except (ValueError, TypeError) as exc:
        del exc
        raise _invalid_request() from None
    except Exception as exc:  # reason: redact heterogeneous service failures at the MCP boundary
        del exc
        raise _unavailable() from None


def _unavailable() -> MCPError:
    return MCPError(
        code=PITWALL_ERROR_CODE_BASE,
        message="production routing unavailable",
        data={"error": "production_routing_unavailable"},
    )


def _invalid_request() -> MCPError:
    return MCPError(
        code=PITWALL_ERROR_CODE_BASE,
        message="invalid production routing request",
        data={"error": "production_routing_invalid_request"},
    )


@dataclass(frozen=True, slots=True)
class RoutingToolSpec:
    """Feature-local global registry addition."""

    name: str
    description: str
    handler: Callable[..., Awaitable[dict[str, object]]]


ROUTING_TOOL_SPECS: tuple[RoutingToolSpec, ...] = (
    RoutingToolSpec(
        name="pitwall_preview_route",
        description=(
            "Preview the deterministic production route and Decimal cost/latency explanation "
            "without persistence, provider egress, or spend."
        ),
        handler=pitwall_preview_route,
    ),
    RoutingToolSpec(
        name="pitwall_get_job_events",
        description=(
            "Read a bounded persisted job lifecycle page and explicit provider stream support."
        ),
        handler=pitwall_get_job_events,
    ),
)


__all__ = [
    "ROUTING_TOOL_SPECS",
    "RoutingToolSpec",
    "get_production_routing_service",
    "pitwall_get_job_events",
    "pitwall_preview_route",
]
