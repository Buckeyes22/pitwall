"""Hermetic forecast-alert policy tests."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest

from pitwall.cost.notifications import NotificationResult
from pitwall.finops.burn_rate import BurnRateRead, DataSufficiency
from pitwall.finops.burn_rate_alerts import check_and_send_forecast_alert

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC)
_GOLDEN_PATH = Path(__file__).parents[1] / "fixtures" / "cost" / "burn_rate_boundaries.json"


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    def exists(self, key: str) -> int:
        return int(key in self.data)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.data:
            return False
        self.data[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True

    def delete(self, key: str) -> int:
        existed = key in self.data
        self.data.pop(key, None)
        self.expiry.pop(key, None)
        return int(existed)

    def eval(self, script: str, _numkeys: int, key: str, *args: str) -> int:
        if "string.sub" in script:
            current = self.data.get(key)
            if current is None or current.startswith("pending:"):
                return 0
            return self.delete(key)
        owner = args[0]
        if self.data.get(key) != owner:
            return 0
        if "redis.call('del'" in script:
            return self.delete(key)
        value, ttl = args[1], int(args[2])
        _FakeRedis.set(self, key, value, ex=ttl)
        return 1


class _ConcurrentFakeRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self._reservation_calls = 0
        self._reservations_ready = asyncio.Event()

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx:
            self._reservation_calls += 1
            if self._reservation_calls == 2:
                self._reservations_ready.set()
            await self._reservations_ready.wait()
        return super().set(key, value, ex=ex, nx=nx)


class _PendingRaceRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.reserved = asyncio.Event()
        self.release_reservation = asyncio.Event()

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        result = super().set(key, value, ex=ex, nx=nx)
        if nx and result:
            self.reserved.set()
            await self.release_reservation.wait()
        return result


class _FakeNotifier:
    def __init__(self, result: NotificationResult | None = None) -> None:
        self.result = result or NotificationResult(ok=True, email_id="forecast_email_123")
        self.sent: list[tuple[str, str]] = []

    def send(self, *, subject: str, body: str) -> NotificationResult:
        self.sent.append((subject, body))
        return self.result


def _forecast(
    *,
    budget: str = "100",
    forecast_total: str | None = "110",
    data_sufficiency: DataSufficiency = "sufficient",
    stale: bool = False,
    already_breached: bool = False,
    at_budget: bool = False,
    projection_overflow: bool = False,
) -> BurnRateRead:
    parsed_budget = Decimal(budget)
    parsed_forecast = Decimal(forecast_total) if forecast_total is not None else None
    return BurnRateRead(
        now=_NOW,
        observation_window_start=dt.date(2026, 6, 4),
        observation_window_end=_NOW.date(),
        observation_window_days=7,
        observed_day_count=7 if data_sufficiency == "sufficient" else 1,
        spend_to_date_usd=Decimal("50"),
        daily_rate_usd=Decimal("5"),
        forecast_total_usd=parsed_forecast,
        budget_usd=parsed_budget,
        remaining_budget_usd=Decimal("50"),
        percent_consumed=Decimal("50") if parsed_budget else None,
        projected_breach_at=dt.datetime(2026, 6, 20, 12, tzinfo=dt.UTC),
        projected_breach_eta_days=Decimal("10"),
        trend="stable",
        confidence=Decimal("0.9"),
        data_sufficiency=data_sufficiency,
        stale=stale,
        last_rollup_day=_NOW.date(),
        already_breached=already_breached,
        at_budget=at_budget,
        projection_overflow=projection_overflow,
    )


async def test_forecast_crossing_sends_once_and_uses_month_scoped_ttl_dedup() -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier()

    first = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)
    second = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    key = "pitwall:forecast-alert:2026-06:100"
    assert first.crossed is True
    assert first.sent is True
    assert first.skipped_duplicate is False
    assert second.crossed is True
    assert second.sent is False
    assert second.skipped_duplicate is True
    assert redis.data[key] == "sent:forecast_email_123"
    assert redis.expiry[key] == 45 * 24 * 60 * 60
    assert len(notifier.sent) == 1


async def test_concurrent_forecast_crossings_atomically_reserve_one_notification() -> None:
    redis = _ConcurrentFakeRedis()
    notifier = _FakeNotifier()

    first, second = await asyncio.gather(
        check_and_send_forecast_alert(_forecast(), redis, notifier=notifier),
        check_and_send_forecast_alert(_forecast(), redis, notifier=notifier),
    )

    assert sum(result.sent for result in (first, second)) == 1
    assert sum(result.skipped_duplicate for result in (first, second)) == 1
    assert len(notifier.sent) == 1


async def test_crashed_delivery_lease_expires_and_later_run_reacquires() -> None:
    class SimulatedCrash(BaseException):
        pass

    class CrashingNotifier:
        def send(self, *, subject: str, body: str) -> NotificationResult:
            del subject, body
            raise SimulatedCrash

    redis = _FakeRedis()
    with pytest.raises(SimulatedCrash):
        await check_and_send_forecast_alert(
            _forecast(),
            redis,
            notifier=CrashingNotifier(),
        )

    key = "pitwall:forecast-alert:2026-06:100"
    assert redis.data[key].startswith("pending:")
    assert redis.expiry[key] == 15 * 60

    # Simulate Redis expiring the short pending lease after the crashed owner is gone.
    redis.delete(key)
    notifier = _FakeNotifier()
    retried = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    assert retried.sent is True
    assert len(notifier.sent) == 1
    assert redis.data[key] == "sent:forecast_email_123"
    assert redis.expiry[key] == 45 * 24 * 60 * 60


async def test_recovery_does_not_delete_an_inflight_crossing_reservation() -> None:
    redis = _PendingRaceRedis()
    notifier = _FakeNotifier()
    crossing = asyncio.create_task(
        check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)
    )
    await redis.reserved.wait()

    recovered = await check_and_send_forecast_alert(
        _forecast(forecast_total="89"),
        redis,
        notifier=notifier,
    )
    key = "pitwall:forecast-alert:2026-06:100"
    assert recovered.rearmed is False
    assert redis.data[key].startswith("pending:")

    redis.release_reservation.set()
    sent = await crossing
    duplicate = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    assert sent.sent is True
    assert sent.reason is None
    assert duplicate.skipped_duplicate is True
    assert len(notifier.sent) == 1


async def test_completed_delivery_id_cannot_collide_with_pending_owner_prefix() -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier(NotificationResult(ok=True, email_id="pending:provider-id"))

    sent = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)
    recovered = await check_and_send_forecast_alert(
        _forecast(forecast_total="89"),
        redis,
        notifier=notifier,
    )

    assert sent.sent is True
    assert redis.data == {}
    assert recovered.rearmed is True


async def test_lost_completion_owner_reports_sent_without_dedup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class LostOwnerRedis(_FakeRedis):
        def eval(self, script: str, _numkeys: int, key: str, *args: str) -> int:
            if "redis.call('set'" in script:
                self.delete(key)
                return 0
            return super().eval(script, _numkeys, key, *args)

    redis = LostOwnerRedis()
    with caplog.at_level(logging.WARNING, logger="pitwall.finops.burn_rate_alerts"):
        result = await check_and_send_forecast_alert(
            _forecast(),
            redis,
            notifier=_FakeNotifier(),
        )

    assert result.sent is True
    assert result.reason == "notification_sent_without_dedup"
    assert "reservation expired" in caplog.text
    assert redis.data == {}


async def test_fresh_recovery_below_90_percent_rearms_a_later_crossing() -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier()
    await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    recovered = await check_and_send_forecast_alert(
        _forecast(forecast_total="89"),
        redis,
        notifier=notifier,
    )
    recrossed = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    assert recovered.crossed is False
    assert recovered.rearmed is True
    assert recovered.reason == "recovered"
    assert recrossed.sent is True
    assert len(notifier.sent) == 2


async def test_alert_repeat_and_recovery_match_golden_sequence() -> None:
    vector = json.loads(_GOLDEN_PATH.read_text())["alert_repeat_recovery"]
    redis = _FakeRedis()
    notifier = _FakeNotifier()

    results = [
        await check_and_send_forecast_alert(
            _forecast(forecast_total=forecast_total),
            redis,
            notifier=notifier,
        )
        for forecast_total in vector["forecast_totals"]
    ]
    serialized = [
        {
            **asdict(result),
            "forecast_pct": str(result.forecast_pct) if result.forecast_pct is not None else None,
        }
        for result in results
    ]

    assert serialized == vector["expected_results"]
    assert len(notifier.sent) == vector["expected_notification_count"]
    expected_dedup = vector["expected_dedup"]
    key = expected_dedup["key"]
    assert redis.data[key] == expected_dedup["value"]
    assert redis.expiry[key] == expected_dedup["ttl_seconds"]


async def test_threshold_and_rearm_boundaries_do_not_flap() -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier()
    await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    at_threshold = await check_and_send_forecast_alert(
        _forecast(forecast_total="100"),
        redis,
        notifier=notifier,
    )
    at_rearm_threshold = await check_and_send_forecast_alert(
        _forecast(forecast_total="90"),
        redis,
        notifier=notifier,
    )

    assert at_threshold.crossed is False
    assert at_threshold.rearmed is False
    assert at_rearm_threshold.crossed is False
    assert at_rearm_threshold.rearmed is False
    assert len(notifier.sent) == 1


@pytest.mark.parametrize(
    ("forecast", "reason"),
    [
        (_forecast(budget="0", forecast_total="0"), "zero_budget"),
        (_forecast(already_breached=True), "actual_budget_exhausted"),
        (_forecast(at_budget=True), "actual_budget_exhausted"),
        (_forecast(data_sufficiency="sparse"), "insufficient_data"),
        (_forecast(stale=True), "stale_rollup"),
        (_forecast(projection_overflow=True), "projection_overflow"),
        (_forecast(forecast_total=None), "no_projection"),
    ],
)
async def test_ineligible_forecasts_never_notify(
    forecast: BurnRateRead,
    reason: str,
) -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier()

    result = await check_and_send_forecast_alert(forecast, redis, notifier=notifier)

    assert result.sent is False
    assert result.crossed is False
    assert result.reason == reason
    assert notifier.sent == []
    assert redis.data == {}


async def test_notification_contains_only_aggregate_forecast_information() -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier()

    result = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    assert result.sent is True
    subject, body = notifier.sent[0]
    assert "Forecast alert" in subject
    assert "Forecast month-end spend: $110.00" in body
    assert "Budget: $100.00" in body
    assert "2026-06-20T12:00:00Z" in body
    for prohibited in ("provider", "workload", "input", "api_key", "secret"):
        assert prohibited not in body.lower()


async def test_failed_delivery_does_not_claim_the_deduplication_key() -> None:
    redis = _FakeRedis()
    notifier = _FakeNotifier(NotificationResult(ok=False, error="delivery unavailable"))

    result = await check_and_send_forecast_alert(_forecast(), redis, notifier=notifier)

    assert result.crossed is True
    assert result.sent is False
    assert result.reason == "notification_delivery_failed"
    assert redis.data == {}


async def test_notifier_exception_is_non_reflecting_and_releases_reservation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class RaisingNotifier:
        def send(self, *, subject: str, body: str) -> NotificationResult:
            del subject, body
            raise RuntimeError("Bearer finops-secret-canary")

    redis = _FakeRedis()
    with caplog.at_level(logging.ERROR, logger="pitwall.cost.notifications"):
        result = await check_and_send_forecast_alert(
            _forecast(),
            redis,
            notifier=RaisingNotifier(),
        )

    assert result.reason == "notification_delivery_failed"
    assert "finops-secret-canary" not in caplog.text
    assert redis.data == {}
