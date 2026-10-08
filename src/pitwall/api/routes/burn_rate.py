"""Feature-local REST adapter for the persisted burn-rate read model."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from pitwall.finops.burn_rate import read_configured_burn_rate

burn_rate_router = APIRouter()


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return pool


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@burn_rate_router.get("/v1/cost/burn-rate")
async def burn_rate(
    request: Request,
    window_days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> dict[str, object]:
    """Return the shared Decimal-preserving burn-rate schema.

    App registration is intentionally left to the serialized API integrator.
    """
    result = await read_configured_burn_rate(
        _pool(request),
        now=_utc_now(),
        window_days=window_days,
    )
    return result.to_dict()


__all__ = ["burn_rate_router"]
