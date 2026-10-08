"""Budget threshold alert plumbing — notification only, no admission control."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import asyncpg

from pitwall.cost._redis_claim import redis_complete, redis_release, redis_reserve
from pitwall.cost.budget_gate import month_to_date_spend
from pitwall.cost.budget_limits import BUDGET_LIMITS_SQL
from pitwall.cost.notifications import NotificationResult, Notifier, send_notification_safely

if TYPE_CHECKING:
    import httpx

log = logging.getLogger("pitwall.cost.alerts")

BUDGET_USD_ENV = "PITWALL_MONTHLY_BUDGET_USD"

_BUDGET_ALERT_KEY_PREFIX = "pitwall:budget-alert"
_BUDGET_ALERT_TTL_SECONDS = 45 * 24 * 60 * 60
_BUDGET_ALERT_PENDING_TTL_SECONDS = 15 * 60


def _alert_key(month: str, threshold_pct: int) -> str:
    return f"{_BUDGET_ALERT_KEY_PREFIX}:{month}:{threshold_pct}"


@dataclass(frozen=True)
class BudgetAlertResult:
    threshold_pct: int
    mtd_spend_usd: Decimal
    monthly_budget_usd: Decimal
    budget_pct: float
    email_sent: bool
    email_id: str | None
    error: str | None
    skipped_duplicate: bool


async def check_and_send_budget_alert(
    pool: asyncpg.Pool,
    redis_client: Any,
    *,
    now: datetime | None = None,
    http_client: httpx.AsyncClient | None = None,
    notifier: Notifier | None = None,
) -> BudgetAlertResult:
    """Check if monthly spend crossed 80% of budget and send alert if needed.

    Deduplication: uses Redis key ``pitwall:budget-alert:YYYY-MM:80``.  A check
    claims the key with ``SET NX`` (a short pending TTL) before sending, so
    concurrent checks send once; a successful send turns the claim into the sent
    marker with a 45-day TTL, and a failed send releases it so a later check can
    retry.  This function does NOT kill in-flight work; it is purely
    notification-side effects.

    Args:
        pool: asyncpg connection pool for querying workloads.
        redis_client: Redis client for deduplication key management.
        now: Current UTC datetime (defaults to now). Used for testing.
        http_client: Deprecated; kept for compatibility with older callers.
        notifier: Optional notifier override for tests or custom transports.

    Returns:
        BudgetAlertResult with alert status and details.
    """
    current_time = now or datetime.now(UTC)
    current_month = current_time.strftime("%Y-%m")
    threshold_pct = 80

    budget_usd = await _monthly_budget(pool)
    if budget_usd <= 0:
        return BudgetAlertResult(
            threshold_pct=threshold_pct,
            mtd_spend_usd=Decimal("0"),
            monthly_budget_usd=budget_usd,
            budget_pct=0.0,
            email_sent=False,
            email_id=None,
            error="Monthly budget is not positive",
            skipped_duplicate=False,
        )

    mtd_spend = await _compute_mtd_spend(pool, current_time)

    budget_pct = float(mtd_spend / budget_usd * 100) if budget_usd else 0.0

    if budget_pct < threshold_pct:
        return BudgetAlertResult(
            threshold_pct=threshold_pct,
            mtd_spend_usd=mtd_spend,
            monthly_budget_usd=budget_usd,
            budget_pct=budget_pct,
            email_sent=False,
            email_id=None,
            error=None,
            skipped_duplicate=False,
        )

    alert_key = _alert_key(current_month, threshold_pct)
    owner = "pending:" + secrets.token_hex(16)
    if not await redis_reserve(redis_client, alert_key, owner, _BUDGET_ALERT_PENDING_TTL_SECONDS):
        log.info(
            "Budget alert for %d%% threshold already sent or in flight this month, skipping",
            threshold_pct,
        )
        return BudgetAlertResult(
            threshold_pct=threshold_pct,
            mtd_spend_usd=mtd_spend,
            monthly_budget_usd=budget_usd,
            budget_pct=budget_pct,
            email_sent=False,
            email_id=None,
            error=None,
            skipped_duplicate=True,
        )

    if http_client is not None:
        log.debug("Ignoring deprecated http_client argument; notifier transport is configured")

    send_result = _send_budget_notification(
        mtd_spend=mtd_spend,
        budget_usd=budget_usd,
        budget_pct=budget_pct,
        notifier=notifier,
    )

    if send_result.ok:
        completed = await redis_complete(
            redis_client,
            alert_key,
            owner,
            send_result.email_id or "ok",
            _BUDGET_ALERT_TTL_SECONDS,
        )
        if completed:
            log.info(
                "Set budget alert dedup key %s with TTL %d seconds",
                alert_key,
                _BUDGET_ALERT_TTL_SECONDS,
            )
        else:
            log.warning("budget alert reservation expired before completion: key=%s", alert_key)
    else:
        await redis_release(redis_client, alert_key, owner)

    return BudgetAlertResult(
        threshold_pct=threshold_pct,
        mtd_spend_usd=mtd_spend,
        monthly_budget_usd=budget_usd,
        budget_pct=budget_pct,
        email_sent=send_result.ok,
        email_id=send_result.email_id,
        error=send_result.error,
        skipped_duplicate=False,
    )


async def _compute_mtd_spend(pool: asyncpg.Pool, current_time: datetime) -> Decimal:
    async with pool.acquire() as conn:
        return await month_to_date_spend(conn, at=current_time)


def _get_monthly_budget() -> Decimal:
    value = os.environ.get(BUDGET_USD_ENV)
    if not value:
        raise ValueError(f"{BUDGET_USD_ENV} environment variable is not set")
    return Decimal(value)


async def _monthly_budget(pool: asyncpg.Pool) -> Decimal:
    """The budget the gate enforces: a runtime limits row, else the environment value."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(BUDGET_LIMITS_SQL)
    return _get_monthly_budget() if row is None else Decimal(str(row["monthly_budget_usd"]))


def _send_budget_notification(
    mtd_spend: Decimal,
    budget_usd: Decimal,
    budget_pct: float,
    notifier: Notifier | None = None,
) -> NotificationResult:
    subject = f"[Pitwall] Budget alert: {budget_pct:.1f}% of monthly budget"
    body = (
        f"Budget Alert: 80% threshold crossed\n\n"
        f"Spend: ${mtd_spend:.2f}\n"
        f"Budget: ${budget_usd:.2f}\n"
        f"Percent: {budget_pct:.1f}%\n"
        f"Threshold: 80%"
    )

    return send_notification_safely(
        notifier=notifier,
        subject=subject,
        body=body,
        context="monthly budget alert",
    )


__all__ = [
    "BudgetAlertResult",
    "check_and_send_budget_alert",
]
