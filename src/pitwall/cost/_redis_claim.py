"""Owner-checked Redis claims shared by the monthly and forecast budget alerts.

A caller reserves a key with ``SET key owner EX pending NX`` before sending, then either
completes it to the sent marker or releases it. Completion and release run as ``EVAL`` scripts
that act only while the key still holds the caller's owner token, so a claim that expired and
was re-taken by another caller is never overwritten or deleted.
"""

from __future__ import annotations

import inspect
from typing import Any

COMPARE_DELETE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
COMPARE_SET_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3])
  return 1
end
return 0
"""


async def redis_value(value: Any) -> Any:
    """Await *value* when the client is async; pass it through when it is not."""
    return await value if inspect.isawaitable(value) else value


async def redis_reserve(redis_client: Any, key: str, owner: str, pending_ttl_seconds: int) -> bool:
    """Claim *key* for *owner*; false when another caller already holds it."""
    return bool(await redis_value(redis_client.set(key, owner, ex=pending_ttl_seconds, nx=True)))


async def redis_release(redis_client: Any, key: str, owner: str) -> bool:
    """Drop the claim, but only while *owner* still holds it."""
    return bool(await redis_value(redis_client.eval(COMPARE_DELETE_SCRIPT, 1, key, owner)))


async def redis_complete(
    redis_client: Any, key: str, owner: str, value: str, ttl_seconds: int
) -> bool:
    """Replace the claim with the sent marker, but only while *owner* still holds it."""
    return bool(
        await redis_value(
            redis_client.eval(COMPARE_SET_SCRIPT, 1, key, owner, value, str(ttl_seconds))
        )
    )
