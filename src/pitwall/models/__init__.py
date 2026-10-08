"""Public model-catalogue exports for Pitwall.

Core domain models and enums live in ``pitwall.core.models`` and ``pitwall.core.enums``.
"""

from __future__ import annotations

from pitwall.models.catalogue import Catalogue, load_catalogue, reload
from pitwall.models.errors import CatalogueError, UnknownModel, UnknownVariant
from pitwall.models.fit import (
    Cloud,
    FitOption,
    FitReason,
    FitVerdict,
    FitWarning,
    fit_options,
    fit_options_local,
)
from pitwall.models.inventory import LocalGpu, LocalInventory, load_inventory
from pitwall.models.kv import kv_cache_gb
from pitwall.models.lookup import CatalogueLookup, VariantInfo
from pitwall.models.prices import (
    GpuPriceFreshness,
    GpuPriceSnapshot,
    gpu_price_freshness,
    gpu_price_snapshot,
    gpu_type_snapshot,
    load_gpu_price_snapshot,
    reset_price_cache,
)
from pitwall.models.schema import Companion, Evidence, ModelDossier, Variant

__all__ = [
    "Catalogue",
    "CatalogueError",
    "CatalogueLookup",
    "Cloud",
    "Companion",
    "Evidence",
    "FitOption",
    "FitReason",
    "FitVerdict",
    "FitWarning",
    "GpuPriceSnapshot",
    "GpuPriceFreshness",
    "gpu_price_freshness",
    "LocalGpu",
    "LocalInventory",
    "ModelDossier",
    "UnknownModel",
    "UnknownVariant",
    "Variant",
    "VariantInfo",
    "load_catalogue",
    "fit_options",
    "fit_options_local",
    "gpu_price_snapshot",
    "gpu_type_snapshot",
    "load_gpu_price_snapshot",
    "load_inventory",
    "kv_cache_gb",
    "reload",
    "reset_price_cache",
]
