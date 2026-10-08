"""Feature-local MCP adapter for the persisted burn-rate read model."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pitwall.db import get_pool
from pitwall.finops.burn_rate import read_configured_burn_rate


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def pitwall_burn_rate(window_days: int = 30) -> dict[str, object]:
    """Return the shared burn-rate forecast from persisted UTC daily rollups.

    The canonical MCP registry owns public tool registration.  This handler
    only obtains the database pool and delegates the calculation to the typed
    FinOps service.
    """
    pool: Any = await get_pool()
    return (
        await read_configured_burn_rate(
            pool,
            now=_utc_now(),
            window_days=window_days,
        )
    ).to_dict()


__all__ = ["pitwall_burn_rate"]
