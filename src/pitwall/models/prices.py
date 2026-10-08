"""Cached RunPod GPU-price snapshots with a canonical VRAM fallback."""

from __future__ import annotations

import logging
import threading
import time as _time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, Protocol

from pitwall.config import PitwallSettings, load_settings_from_env
from pitwall.core.models import PitwallModel
from pitwall.models.fit import Cloud
from pitwall.runpod_client.gpu import GPU_VRAM_GB
from pitwall.runpod_client.graphql import RunpodGpuType, RunpodGraphQLClient

if TYPE_CHECKING:
    from pitwall.runpod_market import RunpodMarketRead, RunpodMarketService

_CACHE_TTL_SECONDS = 300.0
time = SimpleNamespace(monotonic=_time.monotonic)
log = logging.getLogger("pitwall.models.prices")


class GpuTypesClient(Protocol):
    """The minimal GraphQL client interface required for a price snapshot."""

    async def gpu_types(self) -> list[RunpodGpuType]: ...


class GpuPriceSnapshot(PitwallModel):
    """A selected-cloud GPU-type snapshot and its provenance."""

    gpu_types: tuple[RunpodGpuType, ...]
    checked_at: datetime
    source: Literal["live", "fallback"]

    def age_seconds(self, now: datetime | None = None) -> int:
        """Return elapsed wall-clock seconds since this snapshot was checked."""
        current = now or datetime.now(UTC)
        return max(0, int((current - self.checked_at).total_seconds()))


class GpuPriceFreshness(PitwallModel):
    """Age and policy status for a price snapshot."""

    age_seconds: int
    source: Literal["live", "fallback"]
    stale: bool


def gpu_price_freshness(
    snapshot: GpuPriceSnapshot,
    *,
    max_age_s: int | None,
    now: datetime | None = None,
) -> GpuPriceFreshness:
    """Return snapshot provenance and whether an enabled policy rejects its age."""
    current = now or datetime.now(UTC)
    age_seconds = max(0, int((current - snapshot.checked_at).total_seconds()))
    return GpuPriceFreshness(
        age_seconds=age_seconds,
        source=snapshot.source,
        stale=max_age_s is not None and (snapshot.source == "fallback" or age_seconds > max_age_s),
    )


@dataclass(frozen=True, slots=True)
class _CachedSnapshot:
    monotonic_at: float
    snapshot: GpuPriceSnapshot


_CACHE: dict[Cloud, _CachedSnapshot] = {}
_CACHE_LOCK = threading.RLock()


def _cached_snapshot(cloud: Cloud) -> GpuPriceSnapshot | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cloud)
        if cached is not None and now - cached.monotonic_at <= _CACHE_TTL_SECONDS:
            return cached.snapshot
    return None


def _store_snapshot(cloud: Cloud, snapshot: GpuPriceSnapshot) -> None:
    with _CACHE_LOCK:
        _CACHE[cloud] = _CachedSnapshot(monotonic_at=time.monotonic(), snapshot=snapshot)


async def gpu_price_snapshot(client: GpuTypesClient, *, cloud: Cloud) -> GpuPriceSnapshot:
    """Return a cloud-filtered, five-minute cached GPU price snapshot."""
    cached = _cached_snapshot(cloud)
    if cached is not None:
        return cached
    try:
        rows = await client.gpu_types()
    except (
        Exception
    ) as exc:  # reason: client failures deliberately return the public fallback snapshot
        log.warning(
            "gpu price snapshot fallback: error_type=%s error=%s",
            type(exc).__name__,
            str(exc),
            extra={"error_type": type(exc).__name__, "error_message": str(exc)},
        )
        snapshot = _fallback_snapshot()
    else:
        snapshot = GpuPriceSnapshot(
            gpu_types=tuple(_live_rows(rows, cloud)),
            checked_at=datetime.now(UTC),
            source="live",
        )
    _store_snapshot(cloud, snapshot)
    return snapshot


async def gpu_type_snapshot(client: GpuTypesClient, *, cloud: Cloud) -> list[RunpodGpuType]:
    """Return the GPU types from the cached selected-cloud snapshot."""
    return list((await gpu_price_snapshot(client, cloud=cloud)).gpu_types)


async def load_gpu_price_snapshot(
    *,
    cloud: Cloud,
    settings: PitwallSettings | None = None,
    market_service: RunpodMarketService | None = None,
) -> GpuPriceSnapshot:
    """Load through RP-01's shared snapshot when available, with static fallback."""

    if market_service is not None:
        return gpu_price_snapshot_from_market(await market_service.read(), cloud=cloud)
    if settings is None:
        cached = _cached_snapshot(cloud)
        if cached is not None:
            return cached
        from pitwall.runpod_market import build_configured_runpod_market_service

        owned_market = build_configured_runpod_market_service()
        try:
            snapshot = gpu_price_snapshot_from_market(await owned_market.read(), cloud=cloud)
        finally:
            await owned_market.aclose()
        _store_snapshot(cloud, snapshot)
        return snapshot
    resolved_settings = settings or load_settings_from_env()
    if not resolved_settings.runpod_api_key:
        return _fallback_snapshot()
    client = RunpodGraphQLClient.from_settings(resolved_settings)
    try:
        return await gpu_price_snapshot(client, cloud=cloud)
    finally:
        await client.aclose()


def gpu_price_snapshot_from_market(
    snapshot: RunpodMarketRead,
    *,
    cloud: Cloud,
) -> GpuPriceSnapshot:
    """Adapt the shared RunPod market read to existing hardware-fit input.

    Missing GraphQL pricing retains the established static, unpriced fallback.
    REST catalogue fields are deliberately not treated as GraphQL prices.
    """

    rows = snapshot.gpu_types_for_fit(cloud=cloud)
    if not rows:
        return _fallback_snapshot()
    discovery = next(
        (item for item in snapshot.components if item.name == "graphql_discovery"),
        None,
    )
    return GpuPriceSnapshot(
        gpu_types=rows,
        checked_at=(
            discovery.observed_at
            if discovery is not None and discovery.observed_at is not None
            else snapshot.refresh_attempted_at
        ),
        source="live",
    )


def reset_price_cache() -> None:
    """Clear all cached snapshots, primarily for tests and explicit refreshes."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _live_rows(rows: Sequence[RunpodGpuType], cloud: Cloud) -> list[RunpodGpuType]:
    cloud_field = "secure_cloud" if cloud == "secure" else "community_cloud"
    selected = [row for row in rows if getattr(row, cloud_field)]
    return sorted(
        (
            row.model_copy(update={"memory_in_gb": GPU_VRAM_GB.get(row.id)})
            if row.memory_in_gb is None
            else row
            for row in selected
        ),
        key=lambda row: row.id,
    )


def _fallback_snapshot() -> GpuPriceSnapshot:
    return GpuPriceSnapshot(
        gpu_types=tuple(
            RunpodGpuType(
                id=name,
                memoryInGb=vram_gb,
                securePrice=None,
                communityPrice=None,
            )
            for name, vram_gb in sorted(GPU_VRAM_GB.items())
        ),
        checked_at=datetime.now(UTC),
        source="fallback",
    )


__all__ = [
    "GpuPriceSnapshot",
    "GpuPriceFreshness",
    "GpuTypesClient",
    "gpu_price_snapshot",
    "gpu_price_freshness",
    "gpu_price_snapshot_from_market",
    "gpu_type_snapshot",
    "load_gpu_price_snapshot",
    "reset_price_cache",
]
