"""Forecast-breach notification policy for persisted burn-rate reads.

This is notification-only.  It deliberately does not participate in budget
admission and cannot change the existing hard budget gate.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pitwall.cost._redis_claim import (
    redis_complete,
    redis_release,
    redis_reserve,
    redis_value,
)
from pitwall.cost.notifications import (
    NotificationResult,
    Notifier,
    send_notification_safely,
)
from pitwall.finops.burn_rate import BurnRateRead

log = logging.getLogger("pitwall.finops.burn_rate_alerts")

FORECAST_THRESHOLD_PCT = Decimal("100")
FORECAST_RECOVERY_REARM_PCT = Decimal("90")
_FORECAST_ALERT_KEY_PREFIX = "pitwall:forecast-alert"
_FORECAST_ALERT_TTL_SECONDS = 45 * 24 * 60 * 60
_FORECAST_ALERT_PENDING_TTL_SECONDS = 15 * 60
_PERCENT_QUANTUM = Decimal("0.1")
_REARM_COMPLETED_SCRIPT = """
local current = redis.call('get', KEYS[1])
if not current or string.sub(current, 1, 8) == 'pending:' then
  return 0
end
return redis.call('del', KEYS[1])
"""


@dataclass(frozen=True, slots=True)
class ForecastAlertResult:
    """Result of one forecast-alert evaluation.

    ``rearmed`` is true only when a previously sent alert's deduplication key
    was removed after a fresh, sufficient forecast recovered below 90%.
    """

    crossed: bool
    sent: bool
    skipped_duplicate: bool
    rearmed: bool
    forecast_pct: Decimal | None
    reason: str | None
    notification: NotificationResult | None


def _alert_key(forecast: BurnRateRead) -> str:
    return (
        f"{_FORECAST_ALERT_KEY_PREFIX}:{forecast.now.strftime('%Y-%m')}:"
        f"{int(FORECAST_THRESHOLD_PCT)}"
    )


async def _redis_rearm_completed(redis_client: Any, key: str) -> bool:
    return bool(
        await redis_value(
            redis_client.eval(
                _REARM_COMPLETED_SCRIPT,
                1,
                key,
            )
        )
    )


def _forecast_pct(forecast: BurnRateRead) -> Decimal | None:
    if forecast.budget_usd <= 0 or forecast.forecast_total_usd is None:
        return None
    return (forecast.forecast_total_usd / forecast.budget_usd * Decimal("100")).quantize(
        _PERCENT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _eligible_reason(forecast: BurnRateRead) -> str | None:
    if forecast.budget_usd <= 0:
        return "zero_budget"
    if forecast.already_breached or forecast.at_budget:
        return "actual_budget_exhausted"
    if forecast.data_sufficiency != "sufficient":
        return "insufficient_data"
    if forecast.stale:
        return "stale_rollup"
    if forecast.projection_overflow:
        return "projection_overflow"
    if forecast.forecast_total_usd is None:
        return "no_projection"
    return None


def _notification_text(forecast: BurnRateRead, forecast_pct: Decimal) -> tuple[str, str]:
    projected_breach = (
        forecast.projected_breach_at.isoformat().replace("+00:00", "Z")
        if forecast.projected_breach_at is not None
        else "unavailable"
    )
    subject = "[Pitwall] Forecast alert: monthly budget projected to breach"
    body = "\n".join(
        (
            "Forecast budget alert",
            "",
            f"Forecast month-end spend: ${forecast.forecast_total_usd:.2f}",
            f"Budget: ${forecast.budget_usd:.2f}",
            f"Projected use: {forecast_pct:.1f}%",
            f"Projected breach: {projected_breach}",
            f"Confidence: {forecast.confidence:.2f}",
            "Data: sufficient and fresh UTC daily rollups",
        )
    )
    return subject, body


async def check_and_send_forecast_alert(
    forecast: BurnRateRead,
    redis_client: Any,
    *,
    notifier: Notifier | None = None,
) -> ForecastAlertResult:
    """Notify once when a fresh, sufficient forecast first exceeds budget.

    Deduplication follows the existing Redis notification pattern with a
    month-scoped 45-day key.  A sent alert remains muted while the projected
    usage is at or above 90%.  Only a fresh, sufficient forecast below 90%
    deletes the key and re-arms a later projected-breach crossing.
    """
    reason = _eligible_reason(forecast)
    forecast_pct = _forecast_pct(forecast)
    if reason is not None:
        return ForecastAlertResult(
            crossed=False,
            sent=False,
            skipped_duplicate=False,
            rearmed=False,
            forecast_pct=forecast_pct,
            reason=reason,
            notification=None,
        )

    if forecast_pct is None:
        return ForecastAlertResult(
            crossed=False,
            sent=False,
            skipped_duplicate=False,
            rearmed=False,
            forecast_pct=None,
            reason="no_projection",
            notification=None,
        )
    key = _alert_key(forecast)
    crossed = forecast_pct > FORECAST_THRESHOLD_PCT
    if not crossed:
        rearmed = False
        if forecast_pct < FORECAST_RECOVERY_REARM_PCT:
            rearmed = await _redis_rearm_completed(redis_client, key)
            if rearmed:
                log.info("forecast alert re-armed after recovery: key=%s", key)
        return ForecastAlertResult(
            crossed=False,
            sent=False,
            skipped_duplicate=False,
            rearmed=rearmed,
            forecast_pct=forecast_pct,
            reason="recovered" if rearmed else "below_threshold",
            notification=None,
        )

    owner = "pending:" + secrets.token_hex(16)
    if not await redis_reserve(redis_client, key, owner, _FORECAST_ALERT_PENDING_TTL_SECONDS):
        return ForecastAlertResult(
            crossed=True,
            sent=False,
            skipped_duplicate=True,
            rearmed=False,
            forecast_pct=forecast_pct,
            reason=None,
            notification=None,
        )

    subject, body = _notification_text(forecast, forecast_pct)
    notification = send_notification_safely(
        notifier=notifier,
        subject=subject,
        body=body,
        context="forecast alert",
    )

    completion_reason: str | None = None
    if notification.ok:
        completed = await redis_complete(
            redis_client,
            key,
            owner,
            "sent:" + (notification.email_id or "ok"),
            _FORECAST_ALERT_TTL_SECONDS,
        )
        if completed:
            log.info("forecast alert deduplicated for month: key=%s", key)
        else:
            completion_reason = "notification_sent_without_dedup"
            log.warning("forecast alert reservation expired before completion: key=%s", key)
    else:
        await redis_release(redis_client, key, owner)

    return ForecastAlertResult(
        crossed=True,
        sent=notification.ok,
        skipped_duplicate=False,
        rearmed=False,
        forecast_pct=forecast_pct,
        reason=completion_reason or notification.error,
        notification=notification,
    )


__all__ = [
    "FORECAST_RECOVERY_REARM_PCT",
    "FORECAST_THRESHOLD_PCT",
    "ForecastAlertResult",
    "check_and_send_forecast_alert",
]
