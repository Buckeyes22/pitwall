from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("pitwall.leases.activity")

_TRAFFIC_KEY = "pitwall:lease:{lease_id}:last_traffic_at"
_TRAFFIC_TTL_S = 7 * 24 * 60 * 60
_warned_redis_failures: set[str] = set()


@dataclass(frozen=True, slots=True)
class TrafficRead:
    """One Redis traffic read, distinguishing absence from unavailability."""

    available: bool
    seen_at: dt.datetime | None


def _key(lease_id: str) -> str:
    return _TRAFFIC_KEY.format(lease_id=lease_id)


async def stamp_lease_traffic(
    redis: Any,
    lease_id: str,
    *,
    now: dt.datetime,
) -> None:
    if redis is None:
        return
    value = now.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    try:
        await redis.set(_key(lease_id), value, ex=_TRAFFIC_TTL_S)
    except Exception:  # reason: Redis traffic stamps are best-effort on the proxy hot path
        if lease_id not in _warned_redis_failures:
            _warned_redis_failures.add(lease_id)
            log.warning("lease traffic stamp unavailable: lease=%s", lease_id)


async def read_lease_traffic(redis: Any, lease_id: str) -> TrafficRead:
    if redis is None:
        return TrafficRead(available=False, seen_at=None)
    try:
        raw = await redis.get(_key(lease_id))
    except Exception:  # reason: absent Redis traffic must leave a lease treated as busy
        return TrafficRead(available=False, seen_at=None)
    if raw is None:
        return TrafficRead(available=True, seen_at=None)
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return TrafficRead(available=False, seen_at=None)
    if not isinstance(raw, str):
        return TrafficRead(available=False, seen_at=None)
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return TrafficRead(available=False, seen_at=None)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return TrafficRead(available=False, seen_at=None)
    return TrafficRead(available=True, seen_at=parsed.astimezone(dt.UTC))


__all__ = ["TrafficRead", "read_lease_traffic", "stamp_lease_traffic"]
