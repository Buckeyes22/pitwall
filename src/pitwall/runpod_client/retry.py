"""The single HTTP retry helper shared by every RunPod client.

Each caller states which failures are safe to replay through a ``RetryPolicy``. A failure the
policy does not allow is never replayed: an exception is re-raised at once and a response is
returned to the caller as an ambiguous failure, because the request may already have taken effect.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from pitwall.rate_limits.retry_after import (
    DEFAULT_MAX_RETRY_AFTER_DELAY_S,
    parse_retry_after,
)

SleepFunc = Callable[[float], Awaitable[object]]
ClockFunc = Callable[[], dt.datetime]
ExceptionPredicate = Callable[[httpx.HTTPError], bool]
StatusPredicate = Callable[[int], bool]

DEFAULT_RETRY_DELAYS: tuple[float, ...] = (1.0, 3.0, 9.0)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def retry_any_transport_failure(exc: httpx.HTTPError) -> bool:
    """Replay any transport-level failure; only for requests that are safe to repeat."""
    return True


def retry_connect_failure(exc: httpx.HTTPError) -> bool:
    """Replay only failures where the request provably never reached the server."""
    return isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)


def retry_rate_limit_only(status_code: int) -> bool:
    """Replay only 429, which the server rejected before doing any work."""
    return status_code == 429


def retry_rate_limit_or_server_error(status_code: int) -> bool:
    """Replay 429 and 5xx; only for requests that are safe to repeat."""
    return status_code == 429 or status_code >= 500


def retry_never(exc: httpx.HTTPError) -> bool:
    """Never replay a transport failure."""
    return False


@dataclass(frozen=True)
class RetryPolicy:
    """Delays between attempts plus the explicit safe-to-retry predicates."""

    retry_exception: ExceptionPredicate
    retry_status: StatusPredicate
    delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS
    max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_DELAY_S
    sleep: SleepFunc = field(default=asyncio.sleep)
    clock: ClockFunc = field(default=utc_now)

    def configured_delay(self, attempt_index: int) -> float:
        if attempt_index >= len(self.delays):
            return 0.0
        return self.delays[attempt_index]

    def rate_limit_delay(self, response: httpx.Response, attempt_index: int) -> float:
        delay = parse_retry_after(
            response.headers.get("Retry-After"),
            now=self.clock(),
            max_delay_s=self.max_retry_after_s,
        )
        return delay if delay is not None else self.configured_delay(attempt_index)


def rest_retry_policy(method: str) -> RetryPolicy:
    """One immediate replay of a RunPod REST call, only where the method is idempotent.

    Non-idempotent methods replay only a 429; transport failures and 5xx are ambiguous.
    """
    if method.upper() in {"GET", "DELETE", "PATCH"}:
        return RetryPolicy(
            retry_exception=retry_any_transport_failure,
            retry_status=retry_rate_limit_or_server_error,
            delays=(0.0,),
        )
    return RetryPolicy(
        retry_exception=retry_never,
        retry_status=retry_rate_limit_only,
        delays=(0.0,),
    )


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    policy: RetryPolicy,
) -> httpx.Response:
    """Send a request, replaying only failures the policy allows.

    Returns the last response when it is not replayable or the attempts are exhausted; the
    caller decides how to treat its status. Raises the transport exception when it is not
    replayable or the attempts are exhausted.
    """
    attempts = len(policy.delays) + 1
    for attempt_index in range(attempts):
        is_last = attempt_index == attempts - 1
        try:
            response = await send()
        except httpx.HTTPError as exc:
            if is_last or not policy.retry_exception(exc):
                raise
            delay = policy.configured_delay(attempt_index)
        else:
            if is_last or not policy.retry_status(response.status_code):
                return response
            delay = (
                policy.rate_limit_delay(response, attempt_index)
                if response.status_code == 429
                else policy.configured_delay(attempt_index)
            )
        if delay:
            await policy.sleep(delay)
    raise RuntimeError("retry loop ended without a response")  # pragma: no cover


__all__ = [
    "ClockFunc",
    "DEFAULT_RETRY_DELAYS",
    "RetryPolicy",
    "SleepFunc",
    "retry_any_transport_failure",
    "rest_retry_policy",
    "retry_connect_failure",
    "retry_never",
    "retry_rate_limit_only",
    "retry_rate_limit_or_server_error",
    "send_with_retry",
    "utc_now",
]
