"""Consumer webhook dispatcher with signed delivery and bounded retries.

Dispatches completion payloads to registered consumer webhook endpoints with
HMAC-SHA256 signing and exponential backoff retry semantics. Delivery failures
are recorded separately from workload state to avoid polluting workload state
with transient delivery issues.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import http.client
import json
import logging
import random
import uuid
from typing import Any

from pitwall.config import get_settings
from pitwall.webhook_dispatcher.security import (
    ResolvedWebhookTarget,
    WebhookTargetRejected,
    post_resolved_webhook,
    resolve_webhook_target,
)
from pitwall.webhook_dispatcher.signer import sign

log = logging.getLogger("pitwall.webhook_dispatcher")

DEFAULT_RETRY_DELAYS = (0.0, 1.0, 3.0, 9.0)
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 4


_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429})


class DeliveryOutcome:
    """Result of a webhook delivery attempt."""

    def __init__(
        self,
        success: bool,
        attempt: int,
        status_code: int | None = None,
        error_message: str | None = None,
        next_retry_at: dt.datetime | None = None,
        delivery_id: str | None = None,
        retryable: bool = True,
    ) -> None:
        self.success = success
        self.attempt = attempt
        self.status_code = status_code
        self.error_message = error_message
        self.next_retry_at = next_retry_at
        self.delivery_id = delivery_id
        self.retryable = retryable

    @property
    def state(self) -> str:
        if self.success:
            return "delivered"
        if self.should_retry:
            return "retry_scheduled"
        return "terminal_failure"

    @property
    def should_retry(self) -> bool:
        """True when the failure class is worth another attempt and attempts remain."""
        if self.success or not self.retryable or self.attempt >= MAX_ATTEMPTS:
            return False
        return (
            self.status_code is None
            or self.status_code in _RETRYABLE_STATUS_CODES
            or self.status_code >= 500
        )


def _prepare_delivery(
    payload: dict[str, Any],
    hmac_secret: str | None,
    delivery_id: str | None,
) -> tuple[bytes, dict[str, str], str]:
    """Serialize the payload canonically and build the signed request headers."""
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    payload_delivery_id = payload.get("delivery_id")
    if delivery_id is None and isinstance(payload_delivery_id, str):
        delivery_id = payload_delivery_id
    delivery_id = delivery_id or str(uuid.uuid4())
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "X-Pitwall-Delivery-ID": delivery_id,
    }
    if hmac_secret:
        headers["X-Pitwall-Signature"] = sign(body, hmac_secret)
    return body, headers, delivery_id


def _next_retry_at(
    attempt: int,
    retry_delays: tuple[float, ...],
    now: dt.datetime,
) -> dt.datetime | None:
    """When the attempt after ``attempt`` is due, or None when the schedule is spent."""
    delays = retry_delays[:MAX_ATTEMPTS]
    if attempt >= len(delays):
        return None
    delay = delays[attempt]
    return now + dt.timedelta(seconds=delay + random.uniform(0, delay * 0.2))


async def _post_to_any_address(
    target: ResolvedWebhookTarget,
    body: bytes,
    headers: dict[str, str],
    timeout_seconds: float,
) -> int:
    """POST to each resolved address in order; only an unreachable set raises."""
    last_error: Exception | None = None
    addresses = getattr(target, "addresses", None) or (None,)
    for address in addresses:
        pinned = target if address is None else dataclasses.replace(target, addresses=(address,))
        try:
            return await post_resolved_webhook(pinned, body, headers, timeout_seconds)
        except (TimeoutError, OSError, http.client.HTTPException, WebhookTargetRejected) as exc:
            last_error = exc
            log.info("webhook address %s failed; trying the next resolved address", address)
    raise last_error or OSError("webhook target resolved to no addresses")


async def attempt_delivery(
    webhook_url: str,
    payload: dict[str, Any],
    hmac_secret: str | None,
    *,
    attempt: int = 1,
    retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    delivery_id: str | None = None,
    loopback_allowlist: tuple[str, ...] | None = None,
    now: dt.datetime | None = None,
) -> DeliveryOutcome:
    """Make exactly one delivery attempt; never sleeps.

    A retryable failure carries ``next_retry_at`` taken from ``retry_delays`` so the
    caller can persist it and redeliver later. ``attempt`` is 1-based.
    """
    effective_allowlist = (
        get_settings().pitwall_webhook_loopback_allowlist
        if loopback_allowlist is None
        else loopback_allowlist
    )
    body, headers, delivery_id = _prepare_delivery(payload, hmac_secret, delivery_id)
    failure = DeliveryOutcome(
        success=False,
        attempt=attempt,
        error_message="Webhook delivery failed",
        delivery_id=delivery_id,
    )
    try:
        target = await resolve_webhook_target(webhook_url, loopback_allowlist=effective_allowlist)
    except WebhookTargetRejected:
        failure.error_message = "Webhook target rejected by egress policy"
        failure.retryable = False
        return failure
    try:
        status_code = await _post_to_any_address(target, body, headers, timeout_seconds)
    except TimeoutError, OSError, http.client.HTTPException, WebhookTargetRejected:
        failure.error_message = "Webhook delivery transport failure"
    else:
        failure.status_code = status_code
        if 200 <= status_code < 300:
            return DeliveryOutcome(
                success=True,
                attempt=attempt,
                status_code=status_code,
                delivery_id=delivery_id,
            )
        if status_code not in _RETRYABLE_STATUS_CODES and status_code < 500:
            failure.error_message = f"Non-retryable HTTP status: {status_code}"
            return failure
        failure.error_message = f"Retryable HTTP status: {status_code}"
    if attempt < MAX_ATTEMPTS:
        failure.next_retry_at = _next_retry_at(
            attempt, retry_delays, now or dt.datetime.now(dt.UTC)
        )
    return failure


async def dispatch_completion(
    workload_id: str,
    consumer: str,
    payload: dict[str, Any],
    subscriptions: list[tuple[int, str, str | None]],
    retry_delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Make one delivery attempt to each registered webhook subscription.

    Failures that are retryable carry ``next_retry_at`` for the caller to persist.

    Args:
        workload_id: The workload ID this completion is for.
        consumer: The consumer identifier.
        payload: The completion payload to send.
        subscriptions: List of (subscription_id, webhook_url, hmac_secret) tuples.
        retry_delays: Tuple of delays between attempts in seconds.
        timeout_seconds: HTTP request timeout.

    Returns:
        Dict with dispatch results keyed by subscription_id.
    """
    results: dict[str, Any] = {}

    for subscription_id, webhook_url, hmac_secret in subscriptions:
        sub_id_str = str(subscription_id)
        delivery_id = str(uuid.uuid4())
        event = build_completion_event(
            workload_id=workload_id,
            consumer=consumer,
            payload=payload,
            delivery_id=delivery_id,
        )
        outcome = await attempt_delivery(
            webhook_url,
            event,
            hmac_secret,
            retry_delays=retry_delays,
            timeout_seconds=timeout_seconds,
            delivery_id=delivery_id,
        )
        results[sub_id_str] = {
            "success": outcome.success,
            "attempt": outcome.attempt,
            "status_code": outcome.status_code,
            "error_message": outcome.error_message,
            "next_retry_at": outcome.next_retry_at.isoformat() if outcome.next_retry_at else None,
            "delivery_id": outcome.delivery_id,
            "state": outcome.state,
        }

    return results


def build_completion_event(
    *,
    workload_id: str,
    consumer: str,
    payload: dict[str, Any],
    delivery_id: str,
    occurred_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build the versioned public completion envelope."""

    timestamp = occurred_at or dt.datetime.now(dt.UTC)
    return {
        "version": "1",
        "event": "workload.completed",
        "delivery_id": delivery_id,
        "occurred_at": timestamp.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
        "workload_id": workload_id,
        "consumer": consumer,
        "data": payload,
    }


__all__ = [
    "DEFAULT_RETRY_DELAYS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "DeliveryOutcome",
    "attempt_delivery",
    "build_completion_event",
    "dispatch_completion",
]
