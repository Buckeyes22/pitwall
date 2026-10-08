"""Feature-local REST reads for aggregate and workload cost history.

The router is intentionally not registered here.  Global route inventory,
authentication, and OpenAPI integration remain serialized integration work.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from pitwall.api.schemas.params import OptionalStrQuery
from pitwall.core.cost_reporting import cost_summary_read, recent_workloads_read

router = APIRouter(tags=["cost"])


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return pool


@router.get("/v1/cost/summary")
async def get_cost_summary(
    request: Request,
    capability_class: OptionalStrQuery = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> dict[str, object]:
    """Return the established aggregate JSON shape from the Decimal read model."""

    result = await cost_summary_read(
        _pool(request),
        capability_class=capability_class,
        since=since,
        until=until,
    )
    return result.to_legacy_serializable_dict()


@router.get("/v1/cost/workloads")
async def get_recent_workload_costs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    state: OptionalStrQuery = None,
    capability_id: OptionalStrQuery = None,
    provider_id: OptionalStrQuery = None,
    provider_type: OptionalStrQuery = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> dict[str, object]:
    """Return recent workloads with exact estimate/actual reconciliation detail."""

    result = await recent_workloads_read(
        _pool(request),
        capability_id=capability_id,
        provider_id=provider_id,
        provider_type=provider_type,
        state=state,
        since=since,
        until=until,
        limit=limit,
    )
    return result.to_legacy_serializable_dict()


__all__ = ["get_cost_summary", "get_recent_workload_costs", "router"]
