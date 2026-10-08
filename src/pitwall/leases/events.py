from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from typing import Any, Literal, cast

import asyncpg

from pitwall.core.models import Capability, Lease, WebhookEventType
from pitwall.db.repository import (
    WebhookDeliveryFailureRepository,
    WebhookSubscriptionRepository,
)
from pitwall.webhook_dispatcher.dispatcher import attempt_delivery
from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher

LEASE_READY = "lease.ready"
LEASE_RENEWED = "lease.renewed"
LEASE_STOPPED = "lease.stopped"
LEASE_EXPIRING = "lease.expiring"
LEASE_EVENT_CHANNEL = "pitwall.leases.events"
StopReason = Literal[
    "ttl",
    "idle",
    "operator",
    "kill_switch",
    "budget",
    "max_lifetime",
    "provider_failure",
]

log = logging.getLogger("pitwall.leases.events")
# Logging latch only: the missing-configuration warning is emitted once per process.
# It must NEVER gate delivery — configuration can appear later, and one keyless
# publish must not disable lifecycle webhooks for every subsequent event.
_webhook_config_warning_logged = False


def _occurred_at(now: dt.datetime | None = None) -> str:
    value = now or dt.datetime.now(dt.UTC)
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _lease_envelope(
    *,
    event_type: str,
    capability_name: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": "1",
        "event": event_type,
        "delivery_id": str(uuid.uuid4()),
        "occurred_at": _occurred_at(),
        "capability": capability_name,
        "data": data,
    }


def build_lease_ready_event(
    *,
    lease: Lease | None,
    capability: Capability | None = None,
    capability_name: str | None = None,
    served_model_id: str,
    variant: str | None,
    proxy_base_url: str,
    created: bool | None,
) -> dict[str, Any]:
    name = capability.name if capability is not None else capability_name
    if name is None:
        raise ValueError("capability or capability_name is required")
    return _lease_envelope(
        event_type=LEASE_READY,
        capability_name=name,
        data={
            "capability": name,
            "lease_id": lease.id if lease is not None else None,
            "served_model_id": served_model_id,
            "variant": variant,
            "expires_at": lease.expires_at.isoformat() if lease is not None else None,
            "proxy_base_url": proxy_base_url,
            "idle_timeout_min": lease.idle_timeout_min if lease is not None else None,
            "created": created,
        },
    )


def build_lease_renewed_event(
    *,
    lease: Lease,
    capability_name: str,
    renewed_by: Literal["operator", "activity"],
) -> dict[str, Any]:
    return _lease_envelope(
        event_type=LEASE_RENEWED,
        capability_name=capability_name,
        data={
            "capability": capability_name,
            "lease_id": lease.id,
            "expires_at": lease.expires_at.isoformat(),
            "renewed_by": renewed_by,
        },
    )


def build_lease_stopped_event(
    *,
    lease: Lease,
    capability_name: str,
    reason: StopReason,
) -> dict[str, Any]:
    return _lease_envelope(
        event_type=LEASE_STOPPED,
        capability_name=capability_name,
        data={
            "capability": capability_name,
            "lease_id": lease.id,
            "reason": reason,
        },
    )


def build_lease_expiring_event(
    *,
    lease: Lease,
    capability_name: str,
    minutes_left: int,
) -> dict[str, Any]:
    return _lease_envelope(
        event_type=LEASE_EXPIRING,
        capability_name=capability_name,
        data={
            "capability": capability_name,
            "lease_id": lease.id,
            "expires_at": lease.expires_at.isoformat(),
            "minutes_left": minutes_left,
        },
    )


def _subscription_repository(pool: asyncpg.Pool) -> WebhookSubscriptionRepository:
    return WebhookSubscriptionRepository(pool, WebhookSecretCipher.from_env())


def _failure_repository(pool: asyncpg.Pool) -> WebhookDeliveryFailureRepository:
    return WebhookDeliveryFailureRepository(pool)


async def publish_lease_event(
    pool: asyncpg.Pool,
    redis: Any,
    event: dict[str, Any],
) -> None:
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
    if redis is not None:
        try:
            published = redis.publish(LEASE_EVENT_CHANNEL, payload)
            if hasattr(published, "__await__"):
                await published
        except Exception:  # reason: Redis delivery is best-effort; signed webhooks must continue
            log.warning("lease event Redis publish failed", exc_info=True)

    capability_name = event.get("capability")
    event_type = event.get("event")
    delivery_id = event.get("delivery_id")
    if not isinstance(capability_name, str):
        return
    if not isinstance(event_type, str):
        return
    if not isinstance(delivery_id, str):
        return
    typed_event = cast(WebhookEventType, event_type)
    data = event.get("data")
    lease_id = data.get("lease_id") if isinstance(data, dict) else None
    global _webhook_config_warning_logged
    try:
        repo = _subscription_repository(pool)
    except RuntimeError, ValueError:
        if not _webhook_config_warning_logged:
            _webhook_config_warning_logged = True
            log.warning(
                "lifecycle webhook delivery skipped: PITWALL_WEBHOOK_ENCRYPTION_KEYS not configured"
            )
        return
    try:
        subscriptions = await repo.list_for_dispatch(
            consumer=capability_name,
            event_type=typed_event,
        )
    except RuntimeError, ValueError:
        log.warning("lease event subscriptions unavailable", exc_info=True)
        return
    for subscription in subscriptions:
        delivery_id = str(uuid.uuid4())
        delivery_event = {**event, "delivery_id": delivery_id}
        outcome = await attempt_delivery(
            webhook_url=subscription.webhook_url,
            payload=delivery_event,
            hmac_secret=subscription.hmac_secret,
            delivery_id=delivery_id,
        )
        if outcome.success:
            continue
        data = delivery_event.get("data")
        lease_id = data.get("lease_id") if isinstance(data, dict) else None
        if not isinstance(lease_id, str):
            log.warning(
                "lease event webhook delivery failed",
                extra={
                    "subscription_id": subscription.id,
                    "delivery_id": delivery_id,
                    "event_type": event_type,
                    "error_message": outcome.error_message,
                },
            )
            continue
        subscription_id = int(subscription.id)
        await _failure_repository(pool).insert(
            lease_id,
            subscription_id,
            outcome.attempt,
            {
                "event": event_type,
                "lease_id": lease_id,
                "delivery_id": delivery_id,
                # The exact body, so the webhook retry sweep redelivers it unchanged.
                "envelope": delivery_event,
            },
            next_retry_at=outcome.next_retry_at,
            status_code=outcome.status_code,
            error_message=outcome.error_message,
        )
        log.warning(
            "lease event webhook delivery failed",
            extra={
                "subscription_id": subscription_id,
                "delivery_id": delivery_id,
                "event_type": event_type,
                "attempt": outcome.attempt,
                "status_code": outcome.status_code,
                "error_message": outcome.error_message,
            },
        )


__all__ = [
    "LEASE_READY",
    "LEASE_RENEWED",
    "LEASE_STOPPED",
    "LEASE_EXPIRING",
    "StopReason",
    "build_lease_ready_event",
    "build_lease_renewed_event",
    "build_lease_stopped_event",
    "build_lease_expiring_event",
    "publish_lease_event",
]
