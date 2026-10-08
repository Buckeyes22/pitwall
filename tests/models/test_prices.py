from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pitwall.models import prices
from pitwall.models.prices import (
    GpuPriceSnapshot,
    gpu_price_freshness,
    gpu_price_snapshot,
    reset_price_cache,
)
from pitwall.runpod_client.gpu import GPU_VRAM_GB
from pitwall.runpod_client.graphql import RunpodGpuType


def gpu(name: str, memory: int) -> RunpodGpuType:
    return RunpodGpuType.model_validate(
        {
            "id": name,
            "memoryInGb": memory,
            "secureCloud": True,
            "communityCloud": True,
            "securePrice": "2.00",
            "communityPrice": "1.00",
            "maxGpuCount": 8,
            "maxGpuCountSecureCloud": 8,
            "maxGpuCountCommunityCloud": 4,
        }
    )


class FakeGpuClient:
    def __init__(self, rows: list[RunpodGpuType] | Exception) -> None:
        self.rows = rows
        self.calls = 0

    async def gpu_types(self) -> list[RunpodGpuType]:
        self.calls += 1
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


def test_price_freshness_reports_fresh_stale_fallback_and_unset() -> None:
    now = datetime(2026, 8, 28, tzinfo=UTC)
    live = GpuPriceSnapshot(gpu_types=(), checked_at=now, source="live")
    stale = GpuPriceSnapshot(gpu_types=(), checked_at=now - timedelta(seconds=61), source="live")
    fallback = GpuPriceSnapshot(gpu_types=(), checked_at=now, source="fallback")

    assert gpu_price_freshness(live, max_age_s=60, now=now).model_dump() == {
        "age_seconds": 0,
        "source": "live",
        "stale": False,
    }
    assert gpu_price_freshness(stale, max_age_s=60, now=now).stale is True
    assert gpu_price_freshness(fallback, max_age_s=60, now=now).stale is True
    assert gpu_price_freshness(stale, max_age_s=None, now=now).stale is False


@pytest.mark.anyio
async def test_snapshot_is_cached_for_five_minutes_per_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0.0, 0.0, 299.0, 301.0, 301.0])
    monkeypatch.setattr(prices.time, "monotonic", lambda: next(clock))
    client = FakeGpuClient([gpu("NVIDIA L4", 24)])
    reset_price_cache()
    assert (await gpu_price_snapshot(client, cloud="secure")).source == "live"
    await gpu_price_snapshot(client, cloud="secure")
    await gpu_price_snapshot(client, cloud="secure")
    assert client.calls == 2


@pytest.mark.anyio
async def test_query_failure_returns_complete_unpriced_vram_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reset_price_cache()
    snapshot = await gpu_price_snapshot(FakeGpuClient(RuntimeError("offline")), cloud="community")
    assert snapshot.source == "fallback"
    by_id = {row.id: row for row in snapshot.gpu_types}
    assert set(by_id) == set(GPU_VRAM_GB)
    assert by_id["NVIDIA GeForce RTX 4090"].memory_in_gb == 24
    assert by_id["NVIDIA GeForce RTX 4090"].community_price is None
    assert "error_type=RuntimeError error=offline" in caplog.text


async def test_market_backed_snapshot_is_cached_for_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.models.prices import load_gpu_price_snapshot, reset_price_cache

    reads = 0

    from pitwall.runpod_market import RunpodMarketService

    static_read = await RunpodMarketService(None, None, None).read()

    class FakeMarket:
        async def read(self, *, force_refresh: bool = False):
            nonlocal reads
            reads += 1
            return static_read

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "pitwall.runpod_market.build_configured_runpod_market_service", lambda: FakeMarket()
    )
    reset_price_cache()

    first = await load_gpu_price_snapshot(cloud="secure")
    second = await load_gpu_price_snapshot(cloud="secure")

    assert reads == 1
    assert first == second
