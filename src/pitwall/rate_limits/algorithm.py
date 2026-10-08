"""Token-bucket admission logic for RunPod endpoint operations."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pitwall.api.exceptions import RateLimited
from pitwall.rate_limits.retry_after import (
    DEFAULT_MAX_RETRY_AFTER_DELAY_S,
    parse_retry_after,
)

REFILL_WINDOW_S = 10.0

SleepFunc = Callable[[float], Awaitable[object]]


def refill_tokens(
    *,
    tokens: float,
    capacity: int,
    elapsed_s: float,
    refill_window_s: float = REFILL_WINDOW_S,
) -> float:
    """Refill token count using a full-bucket refill window."""

    if capacity <= 0:
        raise ValueError("capacity must be > 0")
    if tokens < 0:
        raise ValueError("tokens must be >= 0")
    if elapsed_s < 0:
        raise ValueError("elapsed_s must be >= 0")
    if not math.isfinite(refill_window_s):
        raise ValueError("refill_window_s must be finite")
    if refill_window_s <= 0:
        raise ValueError("refill_window_s must be > 0")

    refill_rate = capacity / refill_window_s
    return min(float(capacity), tokens + elapsed_s * refill_rate)


def seconds_until_available(
    *,
    tokens: float,
    capacity: int,
    tokens_needed: float = 1.0,
    refill_window_s: float = REFILL_WINDOW_S,
) -> float:
    """Return seconds until ``tokens_needed`` can be consumed."""

    if capacity <= 0:
        raise ValueError("capacity must be > 0")
    if tokens < 0:
        raise ValueError("tokens must be >= 0")
    if tokens_needed <= 0:
        raise ValueError("tokens_needed must be > 0")
    if not math.isfinite(refill_window_s):
        raise ValueError("refill_window_s must be finite")
    if refill_window_s <= 0:
        raise ValueError("refill_window_s must be > 0")

    if tokens >= tokens_needed:
        return 0.0
    if tokens_needed > capacity:
        return math.inf

    refill_rate = capacity / refill_window_s
    return (tokens_needed - tokens) / refill_rate


@dataclass
class TokenBucket:
    """In-memory token bucket useful for deterministic algorithm tests."""

    capacity: int
    tokens: float | None = None
    last_refilled_at_s: float | None = None
    refill_window_s: float = REFILL_WINDOW_S

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if not math.isfinite(self.refill_window_s) or self.refill_window_s <= 0:
            raise ValueError("refill_window_s must be a finite number > 0")
        if self.tokens is None:
            self.tokens = float(self.capacity)
        if self.tokens < 0:
            raise ValueError("tokens must be >= 0")
        self.tokens = min(float(self.capacity), self.tokens)
        if self.last_refilled_at_s is None:
            self.last_refilled_at_s = time.monotonic()

    def refill(self, *, now_s: float | None = None) -> float:
        """Refill in-place and return the current token count."""

        current_s = time.monotonic() if now_s is None else now_s
        elapsed_s = max(0.0, current_s - self._last_refilled_at_s())
        self.tokens = refill_tokens(
            tokens=self._tokens(),
            capacity=self.capacity,
            elapsed_s=elapsed_s,
            refill_window_s=self.refill_window_s,
        )
        self.last_refilled_at_s = current_s
        return self.tokens

    def resize(self, capacity: int, *, now_s: float | None = None) -> None:
        """Apply a dynamic capacity change after refilling to ``now_s``."""

        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self.refill(now_s=now_s)
        self.capacity = capacity
        self.tokens = min(float(capacity), self._tokens())

    def try_consume(
        self,
        tokens: float = 1.0,
        *,
        now_s: float | None = None,
    ) -> bool:
        """Consume tokens if available after refilling to ``now_s``."""

        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        self.refill(now_s=now_s)
        if self._tokens() < tokens:
            return False
        self.tokens = self._tokens() - tokens
        return True

    def retry_after_s(self, tokens: float = 1.0) -> float:
        """Return seconds until ``tokens`` can be consumed."""

        return seconds_until_available(
            tokens=self._tokens(),
            capacity=self.capacity,
            tokens_needed=tokens,
            refill_window_s=self.refill_window_s,
        )

    def _tokens(self) -> float:
        if self.tokens is None:
            raise RuntimeError("TokenBucket was not initialized")
        return self.tokens

    def _last_refilled_at_s(self) -> float:
        if self.last_refilled_at_s is None:
            raise RuntimeError("TokenBucket was not initialized")
        return self.last_refilled_at_s


__all__ = [
    "DEFAULT_MAX_RETRY_AFTER_DELAY_S",
    "REFILL_WINDOW_S",
    "RateLimited",
    "SleepFunc",
    "TokenBucket",
    "refill_tokens",
    "parse_retry_after",
    "seconds_until_available",
]
