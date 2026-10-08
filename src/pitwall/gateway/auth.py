"""Bearer authentication and per-token rate limiting for the gateway.

There is one comparison site (:meth:`GatewayAuth.authenticate`, using ``hmac.compare_digest``) and
one limiter per app, so every route shares the same policy.
"""

from __future__ import annotations

import hmac
import math
import re
from dataclasses import dataclass

from pitwall.rate_limits.algorithm import TokenBucket

_BEARER = re.compile(r"Bearer\s+(\S+)", re.IGNORECASE)
RATE_WINDOW_S = 60.0


def read_bearer_token(header: str | None) -> str | None:
    """Return the credential of an ``Authorization: Bearer <token>`` header, else ``None``."""
    if header is None:
        return None
    match = _BEARER.fullmatch(header.strip())
    return match.group(1) if match else None


@dataclass(frozen=True)
class RateDecision:
    """The outcome of one rate-limit check."""

    allowed: bool
    retry_after_s: int
    remaining: int


class GatewayAuth:
    """Constant-time bearer check plus a :class:`TokenBucket` per authenticated token."""

    def __init__(self, token: str, rate_limit_rpm: int) -> None:
        self._token = token.encode()
        self._rpm = rate_limit_rpm
        self._buckets: dict[str, TokenBucket] = {}

    def authenticate(self, authorization: str | None) -> str | None:
        """Return the presented token when it matches the configured one, else ``None``."""
        presented = read_bearer_token(authorization)
        if presented is None:
            return None
        if hmac.compare_digest(presented.encode(), self._token):
            return presented
        return None

    def consume(self, token: str) -> RateDecision:
        """Spend one request from the token's bucket (``rate_limit_rpm`` per rolling minute)."""
        bucket = self._buckets.get(token)
        if bucket is None:
            bucket = self._buckets[token] = TokenBucket(
                capacity=self._rpm, refill_window_s=RATE_WINDOW_S
            )
        if bucket.try_consume():
            return RateDecision(True, 0, int(bucket.tokens or 0))
        return RateDecision(False, max(math.ceil(bucket.retry_after_s()), 1), 0)
