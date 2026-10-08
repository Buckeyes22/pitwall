from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pitwall.leases.activity import TrafficRead, read_lease_traffic, stamp_lease_traffic

_NOW = datetime(2026, 8, 28, 12, 0, 0, 123456, tzinfo=UTC)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.values[key] = value
        self.expiries[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class BrokenRedis:
    async def set(self, key: str, value: str, *, ex: int) -> bool:
        raise ConnectionError("redis unavailable")

    async def get(self, key: str) -> str | None:
        raise ConnectionError("redis unavailable")


@pytest.mark.anyio
async def test_stamp_and_read_round_trip_utc_with_seven_day_ttl() -> None:
    redis = FakeRedis()

    await stamp_lease_traffic(redis, "lease-traffic", now=_NOW)

    key = "pitwall:lease:lease-traffic:last_traffic_at"
    assert redis.values[key] == "2026-08-28T12:00:00.123456Z"
    assert redis.expiries[key] == 7 * 24 * 60 * 60
    assert await read_lease_traffic(redis, "lease-traffic") == TrafficRead(
        available=True,
        seen_at=_NOW,
    )


@pytest.mark.anyio
async def test_redis_unavailable_logs_once_and_returns_no_stamp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = BrokenRedis()

    await stamp_lease_traffic(redis, "lease-broken", now=_NOW)
    seen = await read_lease_traffic(redis, "lease-broken")

    assert seen == TrafficRead(available=False, seen_at=None)
    messages = [record.message for record in caplog.records]
    assert messages.count("lease traffic stamp unavailable: lease=lease-broken") == 1


@pytest.mark.anyio
async def test_read_rejects_naive_or_invalid_cached_values() -> None:
    redis = FakeRedis()
    key = "pitwall:lease:lease-invalid:last_traffic_at"
    for value in ("not-a-date", "2026-08-28T12:00:00"):
        redis.values[key] = value
        assert await read_lease_traffic(redis, "lease-invalid") == TrafficRead(
            available=False,
            seen_at=None,
        )


@pytest.mark.anyio
async def test_reachable_redis_without_stamp_is_available() -> None:
    assert await read_lease_traffic(FakeRedis(), "lease-never-stamped") == TrafficRead(
        available=True,
        seen_at=None,
    )
