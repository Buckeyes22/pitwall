"""Thin MCP adapters for the model catalogue services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, cast

from pydantic import Field

from pitwall.config import get_settings
from pitwall.mcp.tools.runpod_market import get_runpod_market_service
from pitwall.models import (
    Cloud,
    LocalInventory,
    fit_options,
    fit_options_local,
    gpu_price_freshness,
    load_catalogue,
    load_gpu_price_snapshot,
)

ModelId = Annotated[str, Field(description="Catalogue model ID in org/model form.")]
VariantId = Annotated[
    str | None,
    Field(description="Catalogue variant ID; omit to use the dossier default."),
]
TtlMinutes = Annotated[int, Field(description="Minutes used to estimate lease cost.")]
CloudName = Annotated[
    str,
    Field(description="RunPod cloud price class: secure or community."),
]


async def pitwall_models_list() -> dict[str, Any]:
    """List curated model dossiers and their published serving variants."""
    catalogue = load_catalogue()
    return {
        "models": [
            {
                "model_id": dossier.model_id,
                "vendor": dossier.vendor,
                "family": dossier.family,
                "openai_chat": dossier.openai_chat,
                "variant_ids": [variant.id for variant in dossier.variants],
                "default_variant": next(
                    variant.id for variant in dossier.variants if variant.default
                ),
            }
            for dossier in catalogue.models()
        ]
    }


async def pitwall_models_fit(
    model: ModelId,
    variant: VariantId = None,
    ttl_minutes: TtlMinutes = 120,
    cloud: CloudName = "secure",
    inventory: Mapping[str, Any] | None = None,
    context: int | None = None,
) -> dict[str, Any]:
    """Find canonical GPU fits and costs for a catalogue variant before launch."""
    if ttl_minutes < 1:
        raise ValueError("ttl_minutes must be >= 1")
    catalogue = load_catalogue()
    selected_variant = catalogue.dossier_variant(model, variant)
    if inventory is not None:
        local = LocalInventory.model_validate(inventory)
        context_length = context if context is not None else selected_variant.context
        if not isinstance(context_length, int):
            raise ValueError("context is required when variant context is unverified")
        options = fit_options_local(
            selected_variant,
            inventory=local,
            context_length=context_length,
        )
        return {
            "model_id": model,
            "variant": selected_variant.id,
            "confidence": selected_variant.confidence,
            "price_source": "local",
            "context_length": context_length,
            "options": [
                {
                    "gpu_class": option.gpu_class,
                    "gpu_count": option.gpu_count,
                    "vram_gb": option.vram_gb,
                    "headroom_gb": option.headroom_gb,
                    "fit": option.fit,
                    "reason": option.reason,
                    "warnings": list(option.warnings),
                    "price_per_hour": str(option.price_per_hour),
                    "cost_for_ttl": None,
                    "cloud": "local",
                    "max_count": option.max_count,
                }
                for option in options
            ],
        }
    if cloud not in {"secure", "community"}:
        raise ValueError("cloud must be secure or community")
    selected_cloud = cast(Cloud, cloud)
    snapshot = await load_gpu_price_snapshot(
        cloud=selected_cloud,
        market_service=get_runpod_market_service(),
    )
    freshness = gpu_price_freshness(snapshot, max_age_s=get_settings().pitwall_price_max_age_s)
    return {
        "model_id": model,
        "variant": selected_variant.id,
        "confidence": selected_variant.confidence,
        "price_source": snapshot.source,
        "price_checked_at": snapshot.checked_at.isoformat(),
        "price_age_seconds": freshness.age_seconds,
        "price_stale": freshness.stale,
        "options": [
            {
                "gpu_class": option.gpu_class,
                "gpu_count": option.gpu_count,
                "vram_gb": option.vram_gb,
                "headroom_gb": option.headroom_gb,
                "fit": option.fit,
                "warm_cache": option.warm_cache,
                "cache_state": option.cache_state,
                "price_per_hour": (
                    str(option.price_per_hour) if option.price_per_hour is not None else None
                ),
                "cost_for_ttl": (
                    str(option.cost_for_ttl) if option.cost_for_ttl is not None else None
                ),
                "cloud": option.cloud,
                "max_count": option.max_count,
            }
            for option in fit_options(
                selected_variant,
                gpu_types=snapshot.gpu_types,
                ttl_minutes=ttl_minutes,
                cloud=selected_cloud,
            )
        ],
    }


__all__ = ["pitwall_models_fit", "pitwall_models_list"]
