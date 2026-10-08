"""Health tool for the MCP surface: real dependency checks, reported as booleans."""

from __future__ import annotations

import asyncio

from pitwall.db import get_pool
from pitwall.providers.registry import get_default_registry
from pitwall.redis_env import optional_redis_from_env

_CHECK_TIMEOUT_S = 3.0


async def _database_reachable() -> bool:
    async def probe() -> bool:
        pool = await get_pool()
        async with pool.acquire() as connection:
            return bool(await connection.fetchval("SELECT 1") == 1)

    try:
        return await asyncio.wait_for(probe(), timeout=_CHECK_TIMEOUT_S)
    except Exception:  # reason: any failure means the database is not reachable
        return False


async def _redis_reachable() -> bool:
    async def probe() -> bool:
        async with optional_redis_from_env() as client:
            if client is None:
                return False
            return bool(await client.ping())

    try:
        return await asyncio.wait_for(probe(), timeout=_CHECK_TIMEOUT_S)
    except Exception:  # reason: any failure means Redis is not reachable
        return False


def _provider_registry_loaded() -> bool:
    try:
        return bool(get_default_registry().ids)
    except Exception:  # reason: a registry that fails to build is not loaded
        return False


async def pitwall_health() -> dict[str, bool]:
    """Report whether the database and Redis answer and the provider registry is loaded.

    Returns ``database``, ``redis``, and ``provider_registry`` booleans plus ``ok``, which is true
    only when every check passes. Redis is unreachable when ``REDIS_URL`` is unset.
    """

    database, redis = await asyncio.gather(_database_reachable(), _redis_reachable())
    registry = _provider_registry_loaded()
    return {
        "ok": database and redis and registry,
        "database": database,
        "redis": redis,
        "provider_registry": registry,
    }


__all__ = ["pitwall_health"]
