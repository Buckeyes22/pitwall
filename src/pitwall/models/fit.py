"""Pure hardware-fit and price arithmetic for catalogue variants."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from math import ceil
from typing import Literal

from pitwall.core.models import PitwallModel
from pitwall.models.inventory import LocalInventory
from pitwall.models.kv import kv_cache_gb
from pitwall.models.schema import Variant
from pitwall.runpod_client.graphql import RunpodGpuType

Cloud = Literal["secure", "community"]
FitVerdict = Literal["fits", "tight", "tp", "no"]
CacheState = Literal["warm", "cold", "not_checked"]
FitReason = Literal["arch", "kv_cache", "unverified"]
FitWarning = Literal["communication-bound"]


class FitOption(PitwallModel):
    """One GPU class evaluated against a catalogue variant."""

    gpu_class: str
    gpu_count: int
    vram_gb: int
    container_disk_gb: int | None = None
    headroom_gb: int | None
    fit: FitVerdict
    price_per_hour: Decimal | None
    cost_for_ttl: Decimal | None
    cloud: Cloud
    max_count: int
    warm_cache: bool = False
    cache_state: CacheState = "not_checked"
    reason: FitReason | None = None
    warnings: tuple[FitWarning, ...] = ()


def fit_options(
    variant: Variant,
    *,
    gpu_types: Sequence[RunpodGpuType],
    ttl_minutes: int,
    cloud: Cloud,
    warm_cache: bool = False,
    cache_state: CacheState = "not_checked",
) -> list[FitOption]:
    """Return the advisory hardware choices for a variant and requested TTL."""
    _require_positive_int(ttl_minutes, "ttl_minutes")
    disk_value = variant.container_disk_gb
    container_disk_gb = (
        disk_value if isinstance(disk_value, int) and not isinstance(disk_value, bool) else None
    )
    options: list[FitOption] = []
    for gpu_type in gpu_types:
        vram_gb = gpu_type.memory_in_gb
        if vram_gb is None or isinstance(vram_gb, bool) or vram_gb < 1:
            continue
        available = _cloud_max_count(gpu_type, cloud)
        if available is None:
            continue
        max_count = available
        per_gpu_price = _price(gpu_type, cloud)
        required = variant.min_vram_gb
        if required == "unverified":
            gpu_count = 1
            headroom_gb = None
            verdict: FitVerdict = "no"
        else:
            gpu_count = ceil(required / vram_gb)
            headroom_gb = gpu_count * vram_gb - required
            if gpu_count == 1:
                verdict = "tight" if 0 < headroom_gb * 10 < vram_gb else "fits"
            elif gpu_count <= max_count:
                verdict = "tp"
            else:
                verdict = "no"
        price_per_hour = per_gpu_price * gpu_count if per_gpu_price is not None else None
        cost_for_ttl = (
            price_per_hour * Decimal(ttl_minutes) / Decimal(60)
            if price_per_hour is not None
            else None
        )
        options.append(
            FitOption(
                gpu_class=gpu_type.id,
                gpu_count=gpu_count,
                vram_gb=vram_gb,
                container_disk_gb=container_disk_gb,
                headroom_gb=headroom_gb,
                fit=verdict,
                price_per_hour=price_per_hour,
                cost_for_ttl=cost_for_ttl,
                cloud=cloud,
                max_count=max_count,
                warm_cache=warm_cache,
                cache_state=cache_state,
            )
        )
    return sorted(options, key=_sort_key)


def fit_options_local(
    variant: Variant,
    *,
    inventory: LocalInventory,
    context_length: int,
    price_usd_per_hour: Decimal = Decimal(0),
) -> list[FitOption]:
    options: list[FitOption] = []
    for gpu in inventory.gpus:
        required = variant.min_vram_gb
        if required == "unverified":
            count = 1
            fit: FitVerdict = "no"
            headroom = None
            reason: FitReason | None = "unverified"
        else:
            kv_gb = kv_cache_gb(variant, context_length)
            total = Decimal(required) + kv_gb
            per_card_usable = inventory.gpu_memory_utilization * gpu.vram_gb
            count = ceil(total / per_card_usable)
            usable = per_card_usable * count
            headroom_decimal = usable - total
            headroom = int(headroom_decimal)
            arch_number = int(gpu.arch.removeprefix("sm_"))
            if arch_number < 89 and variant.format in {"fp8", "nvfp4"}:
                fit = "no"
                reason = "arch"
            elif count > gpu.count:
                fit = "no"
                reason = "kv_cache"
            elif count > 1:
                fit = "tp"
                reason = None
            else:
                fit = "tight" if headroom_decimal * 10 < usable else "fits"
                reason = None
        warnings: tuple[FitWarning, ...] = (
            ("communication-bound",) if count > 1 and not gpu.nvlink else ()
        )
        options.append(
            FitOption(
                gpu_class=gpu.name,
                gpu_count=count,
                vram_gb=int(gpu.vram_gb),
                headroom_gb=headroom,
                fit=fit,
                reason=reason,
                warnings=warnings,
                price_per_hour=price_usd_per_hour * count,
                cost_for_ttl=None,
                cloud="secure",
                max_count=gpu.count,
            )
        )
    return sorted(options, key=_sort_key)


def _cloud_max_count(gpu_type: RunpodGpuType, cloud: Cloud) -> int | None:
    """GPUs purchasable in this cloud lane, or None when there is no stock.

    An explicit zero means no stock and must never fall back to the global
    ``maxGpuCount``; only an unreported (None) count may. Fit that ignores this
    recommends cards the launch cannot buy.

    Lane openness is deliberately not consulted here. ``_live_rows`` already drops
    rows whose cloud flag is false before fit ever sees them, and the flags default
    to ``False`` on a model, so a synthetic row — every row of the fallback
    snapshot, which sets no flags — would otherwise be read as a closed lane and
    the whole advisory table would come back empty whenever live pricing is down.
    """
    cloud_max = (
        gpu_type.max_gpu_count_secure_cloud
        if cloud == "secure"
        else gpu_type.max_gpu_count_community_cloud
    )
    if isinstance(cloud_max, int) and not isinstance(cloud_max, bool):
        return cloud_max if cloud_max > 0 else None
    global_max = gpu_type.max_gpu_count
    if isinstance(global_max, int) and not isinstance(global_max, bool):
        return global_max if global_max > 0 else None
    # Nothing was reported at either level. That is not the same as no stock, so
    # keep the historical advisory default of a single card rather than hiding
    # the row: only an explicit zero means the lane cannot sell one.
    return 1


def _max_count(gpu_type: RunpodGpuType, cloud: Cloud) -> int:
    """Backwards-compatible count for callers that cannot express unavailability."""
    return _cloud_max_count(gpu_type, cloud) or 0


def _price(gpu_type: RunpodGpuType, cloud: Cloud) -> Decimal | None:
    return gpu_type.secure_price if cloud == "secure" else gpu_type.community_price


def _require_positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _sort_key(option: FitOption) -> tuple[int, int, Decimal, int]:
    verdict_rank = {"fits": 0, "tight": 1, "tp": 2, "no": 3}[option.fit]
    unpriced = option.cost_for_ttl is None
    cost = option.cost_for_ttl if option.cost_for_ttl is not None else Decimal("Infinity")
    headroom = option.headroom_gb if option.headroom_gb is not None else 2**31
    return (verdict_rank, int(unpriced), cost, headroom)


__all__ = [
    "CacheState",
    "Cloud",
    "FitOption",
    "FitReason",
    "FitVerdict",
    "FitWarning",
    "fit_options",
    "fit_options_local",
]
