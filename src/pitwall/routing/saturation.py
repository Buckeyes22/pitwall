"""Steady-cadence signals for repeated consumer-authored upstream 4xx responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class SaturationSignal:
    provider_id: str
    token_fingerprint: str
    capability_name: str
    count: int
    window_s: int
    observed_at: datetime
    rate_per_s: float


class SaturationDetector:
    def __init__(self, redis: Any, *, window_s: int = 60, threshold: int = 30) -> None:
        if window_s < 1 or threshold < 1:
            raise ValueError("window_s and threshold must be positive")
        self._redis = redis
        self._window_s = window_s
        self._threshold = threshold

    async def observe_4xx(
        self,
        *,
        provider_id: str,
        token_fingerprint: str,
        capability_name: str,
        now: datetime,
    ) -> SaturationSignal | None:
        """Observe a client error and signal only a steady run.

        A run is steady when it contains at least ``threshold`` observations
        and every adjacent gap is no more than
        ``(window_s / threshold) * 2``. A larger gap starts a new run.
        """

        observed_at = now.astimezone(UTC)
        key = f"pitwall:saturation:{provider_id}:{token_fingerprint}:{capability_name}"
        score = observed_at.timestamp()
        member = f"{score:.6f}:{uuid4().hex}"
        await self._redis.zremrangebyscore(key, float("-inf"), score - self._window_s)
        await self._redis.zadd(key, {member: score})
        await self._redis.expire(key, self._window_s + 1)
        observations = await self._redis.zrange(key, 0, -1, withscores=True)
        scores = [float(item[1]) for item in observations]
        steady_count = _longest_steady_run(
            scores,
            maximum_gap_s=(self._window_s / self._threshold) * 2,
        )
        if steady_count < self._threshold:
            return None
        return SaturationSignal(
            provider_id=provider_id,
            token_fingerprint=token_fingerprint,
            capability_name=capability_name,
            count=steady_count,
            window_s=self._window_s,
            observed_at=observed_at,
            rate_per_s=steady_count / self._window_s,
        )


def _longest_steady_run(scores: list[float], *, maximum_gap_s: float) -> int:
    longest = 0
    current = 0
    previous: float | None = None
    for score in sorted(scores):
        if previous is None or score - previous <= maximum_gap_s:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = score
    return longest
