"""Tests for budget threshold alerts (80% notification)."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.cost.alerts import (
    check_and_send_budget_alert,
)
from pitwall.cost.budget_gate import MONTH_SPEND_AT_SQL
from pitwall.cost.budget_limits import BUDGET_LIMITS_SQL
from pitwall.cost.notifications import NotificationResult

pytestmark = pytest.mark.anyio


class _FakeRedis:
    """Async like the redis.asyncio client the reconciler passes; a sync fake hid finding #6.

    ``SET`` honours ``NX`` (null reply when the key exists) and ``EVAL`` runs the two
    owner-checked scripts, so claims behave as they do against Redis. Every call yields
    first, so concurrent callers interleave.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._expiry: dict[str, int] = {}

    async def exists(self, key: str) -> int:
        await asyncio.sleep(0)
        return 1 if key in self._data else 0

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        await asyncio.sleep(0)
        if nx and key in self._data:
            return None
        self._data[key] = value
        if ex is not None:
            self._expiry[key] = ex
        return True

    async def eval(self, script: str, _numkeys: int, key: str, *args: str) -> int:
        await asyncio.sleep(0)
        if self._data.get(key) != args[0]:
            return 0
        if "redis.call('del'" in script:
            del self._data[key]
            self._expiry.pop(key, None)
            return 1
        self._data[key] = args[1]
        self._expiry[key] = int(args[2])
        return 1


class _FakeNotifier:
    def __init__(self, result: NotificationResult | None = None) -> None:
        self.result = result or NotificationResult(ok=True, email_id="test_email_123")
        self.sent: list[dict[str, str]] = []

    def send(self, *, subject: str, body: str) -> NotificationResult:
        self.sent.append({"subject": subject, "body": body})
        return self.result


def _mock_pool(spend: Decimal, runtime_budget: Decimal | None = None) -> MagicMock:
    pool = MagicMock()
    conn = MagicMock()

    async def mock_fetchrow(sql: str, *args: object) -> dict[str, object] | None:
        if sql == MONTH_SPEND_AT_SQL:
            return {"s": spend}
        if sql == BUDGET_LIMITS_SQL:
            return None if runtime_budget is None else {"monthly_budget_usd": runtime_budget}
        raise AssertionError(f"unexpected query: {sql}")

    conn.fetchrow = mock_fetchrow

    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq)
    pool.conn = conn
    return pool


@pytest.fixture
def set_env_budget_alert() -> None:
    def _set(
        budget: str = "100.0",
    ) -> None:
        os.environ["PITWALL_MONTHLY_BUDGET_USD"] = budget

    return _set


@pytest.fixture(autouse=True)
def cleanup_env_budget_alert() -> None:
    yield
    for var in (
        "PITWALL_MONTHLY_BUDGET_USD",
        "RESEND_API_KEY",
        "PITWALL_ALERT_FROM",
        "PITWALL_ALERT_TO",
        "RESEND_SENDER_EMAIL",
        "RESEND_BUDGET_ALERT_EMAIL",
    ):
        os.environ.pop(var, None)


async def test_below_threshold_returns_no_alert(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("50.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")

    result = await check_and_send_budget_alert(
        pool,
        redis,
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
    )

    assert result.threshold_pct == 80
    assert result.budget_pct == 50.0
    assert result.email_sent is False
    assert result.skipped_duplicate is False


async def test_at_80_percent_triggers_alert(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("80.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier()

    result = await check_and_send_budget_alert(
        pool,
        redis,
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        notifier=notifier,
    )

    assert result.threshold_pct == 80
    assert result.budget_pct == 80.0
    assert result.email_sent is True
    assert result.email_id == "test_email_123"
    assert result.skipped_duplicate is False
    assert len(notifier.sent) == 1


async def test_duplicate_alert_skipped_via_redis_dedup(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("90.00"))
    redis = _FakeRedis()
    redis._data["pitwall:budget-alert:2026-05:80"] = "existing_email_123"
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier()

    result = await check_and_send_budget_alert(
        pool,
        redis,
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        notifier=notifier,
    )

    assert result.skipped_duplicate is True
    assert result.email_sent is False
    assert notifier.sent == []


async def test_redis_key_has_45_day_ttl(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("85.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier()

    result = await check_and_send_budget_alert(
        pool,
        redis,
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        notifier=notifier,
    )

    assert result.email_sent is True
    assert redis._data["pitwall:budget-alert:2026-05:80"] == "test_email_123"
    assert redis._expiry["pitwall:budget-alert:2026-05:80"] == 45 * 24 * 60 * 60


async def test_http_error_returns_error_in_result(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("85.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier(NotificationResult(ok=False, error="401 Unauthorized"))

    result = await check_and_send_budget_alert(
        pool,
        redis,
        now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        notifier=notifier,
    )

    assert result.email_sent is False
    assert result.error is not None
    assert result.error == "notification_delivery_failed"


async def test_missing_env_raises() -> None:
    os.environ.pop("PITWALL_MONTHLY_BUDGET_USD", None)

    pool = _mock_pool(Decimal("85.00"))
    redis = _FakeRedis()

    with pytest.raises(ValueError, match="PITWALL_MONTHLY_BUDGET_USD"):
        await check_and_send_budget_alert(
            pool,
            redis,
            now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        )


async def test_second_crossing_in_a_month_is_skipped_as_duplicate(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("85.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier()
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)

    first = await check_and_send_budget_alert(pool, redis, now=now, notifier=notifier)
    second = await check_and_send_budget_alert(pool, redis, now=now, notifier=notifier)

    assert (first.email_sent, first.skipped_duplicate) == (True, False)
    assert (second.email_sent, second.skipped_duplicate) == (False, True)
    assert len(notifier.sent) == 1


@pytest.mark.parametrize(
    ("runtime_budget", "expect_sent"),
    [(Decimal("200.00"), False), (Decimal("50.00"), True)],
)
async def test_runtime_budget_limit_overrides_the_environment_budget(
    set_env_budget_alert: Any, runtime_budget: Decimal, expect_sent: bool
) -> None:
    """The alert judges spend against the budget the gate enforces, not the startup value."""
    pool = _mock_pool(Decimal("45.00"), runtime_budget=runtime_budget)
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier()

    result = await check_and_send_budget_alert(
        pool, _FakeRedis(), now=datetime(2026, 5, 28, 12, 0, tzinfo=UTC), notifier=notifier
    )

    assert result.monthly_budget_usd == runtime_budget
    assert result.email_sent is expect_sent


async def test_concurrent_checks_send_one_alert(set_env_budget_alert: Any) -> None:
    """finding r5-6: both checks passed the existence test before either recorded a send."""
    pool = _mock_pool(Decimal("90.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")
    notifier = _FakeNotifier()
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)

    first, second = await asyncio.gather(
        check_and_send_budget_alert(pool, redis, now=now, notifier=notifier),
        check_and_send_budget_alert(pool, redis, now=now, notifier=notifier),
    )

    assert len(notifier.sent) == 1
    assert sorted((r.email_sent, r.skipped_duplicate) for r in (first, second)) == [
        (False, True),
        (True, False),
    ]


async def test_a_failed_send_releases_the_claim_so_a_later_check_retries(
    set_env_budget_alert: Any,
) -> None:
    pool = _mock_pool(Decimal("90.00"))
    redis = _FakeRedis()
    set_env_budget_alert(budget="100.0")
    now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)

    failed = await check_and_send_budget_alert(
        pool,
        redis,
        now=now,
        notifier=_FakeNotifier(NotificationResult(ok=False, error="smtp down")),
    )
    assert failed.email_sent is False
    assert "pitwall:budget-alert:2026-05:80" not in redis._data

    notifier = _FakeNotifier()
    retried = await check_and_send_budget_alert(pool, redis, now=now, notifier=notifier)

    assert retried.email_sent is True
    assert len(notifier.sent) == 1
