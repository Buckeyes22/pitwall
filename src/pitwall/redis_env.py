"""A Redis client for one-shot callers (CLI, MCP) that publish events outside the API."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis_asyncio


@asynccontextmanager
async def optional_redis_from_env() -> AsyncIterator[Any | None]:
    """Yield a client for ``REDIS_URL``, or ``None`` when it is unset; close it on exit."""

    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        yield None
        return
    client = redis_asyncio.from_url(url, decode_responses=True)  # type: ignore[no-untyped-call]  # reason: redis.from_url is untyped in redis-py
    try:
        yield client
    finally:
        await client.aclose()
