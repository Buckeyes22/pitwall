"""Read-scoped REST handlers for the packaged model catalogue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from pitwall.api.provider_schemas import warm_cache_matches
from pitwall.config import get_settings
from pitwall.db.repository import ProviderRepository
from pitwall.models import (
    Catalogue,
    Cloud,
    GpuPriceSnapshot,
    ModelDossier,
    UnknownVariant,
    fit_options,
    gpu_price_freshness,
    load_catalogue,
    load_gpu_price_snapshot,
)

model_catalogue_router = APIRouter()


def _catalogue() -> Catalogue:
    return load_catalogue()


async def _price_snapshot(request: Request, cloud: Cloud) -> GpuPriceSnapshot:
    return await load_gpu_price_snapshot(
        cloud=cloud,
        market_service=getattr(request.app.state, "runpod_market_service", None),
    )


def _model_id(path_model: str) -> str:
    return path_model.replace("--", "/", 1)


def _dossier_or_404(catalogue: Catalogue, model_id: str) -> ModelDossier:
    dossier = catalogue.get(model_id)
    if dossier is None:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id}")
    return dossier


@model_catalogue_router.get("/v1/models/catalogue")
def catalogue_list(catalogue: Annotated[Catalogue, Depends(_catalogue)]) -> dict[str, object]:
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


@model_catalogue_router.get("/v1/models/catalogue/{model}/fit")
async def catalogue_fit(
    request: Request,
    model: str,
    variant: str | None = None,
    ttl_minutes: Annotated[int, Query(ge=1)] = 120,
    cloud: Cloud = "secure",
    catalogue: Catalogue = Depends(_catalogue),
    snapshot: GpuPriceSnapshot = Depends(_price_snapshot),
) -> dict[str, object]:
    model_id = _model_id(model)
    _dossier_or_404(catalogue, model_id)
    try:
        selected_variant = catalogue.dossier_variant(model_id, variant)
    except UnknownVariant as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    freshness = gpu_price_freshness(snapshot, max_age_s=get_settings().pitwall_price_max_age_s)
    pool = getattr(request.app.state, "pool", None)
    provider = (
        await ProviderRepository(pool).get_by_name(
            f"serve-{_dossier_or_404(catalogue, model_id).pitwall.capability_name}"
        )
        if pool is not None
        else None
    )
    warm_cache = provider is not None and warm_cache_matches(
        provider.config, variant=selected_variant.id
    )
    return {
        "model_id": model_id,
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
                "price_per_hour": (
                    str(option.price_per_hour) if option.price_per_hour is not None else None
                ),
                "cost_for_ttl": str(option.cost_for_ttl)
                if option.cost_for_ttl is not None
                else None,
                "cloud": option.cloud,
                "max_count": option.max_count,
            }
            for option in fit_options(
                selected_variant,
                gpu_types=snapshot.gpu_types,
                ttl_minutes=ttl_minutes,
                cloud=cloud,
                warm_cache=warm_cache,
            )
        ],
    }


@model_catalogue_router.get("/v1/models/catalogue/{model}")
def catalogue_detail(
    model: str,
    catalogue: Annotated[Catalogue, Depends(_catalogue)],
) -> dict[str, object]:
    dossier = _dossier_or_404(catalogue, _model_id(model))
    return {**dossier.model_dump(mode="json"), "body": dossier.body}


__all__ = ["model_catalogue_router"]
