from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pitwall.mcp.tools import models as models_tool
from pitwall.models.catalogue import Catalogue
from pitwall.models.prices import GpuPriceSnapshot
from pitwall.models.schema import ModelDossier
from pitwall.runpod_client.graphql import RunpodGpuType


def miniature_catalogue() -> Catalogue:
    return Catalogue(
        (
            ModelDossier.model_validate(
                {
                    "model_id": "org/model",
                    "vendor": "org",
                    "family": "Model",
                    "release_date": "2026-08-27",
                    "license": {
                        "name": "Apache-2.0",
                        "url": "https://example.test/license",
                        "gated": False,
                    },
                    "architecture": {
                        "kind": "dense",
                        "params_total_b": 7,
                        "params_active_b": 7,
                        "context_length_max": 32768,
                        "modalities": ["text"],
                        "thinking_mode": "optional",
                    },
                    "capabilities": {
                        "tool_calling": "yes",
                        "structured_outputs": "yes",
                        "vision": False,
                        "languages": "English",
                    },
                    "openai_chat": True,
                    "pitwall": {"capability_name": "llm.model", "served_model_name": "model"},
                    "variants": [
                        {
                            "id": "bf16",
                            "default": True,
                            "engine": "vllm",
                            "image": "vllm/vllm-openai:v0.12.1",
                            "repo": "org/model",
                            "file": None,
                            "format": "bf16",
                            "min_vram_gb": 20,
                            "context": 32768,
                            "container_disk_gb": 40,
                            "startup_min": 15,
                            "flags": [],
                            "env": {},
                            "recommended_gpu_classes": [],
                            "tool_call_parser": None,
                            "reasoning_parser": None,
                            "confidence": "medium",
                            "sources": ["https://example.test/model"],
                        }
                    ],
                    "confidence": {"overall": "medium", "notes": "source checked"},
                    "accessed": "2026-08-27",
                }
            ),
        )
    )


async def fallback_snapshot(
    *, cloud: str, market_service: object | None = None
) -> GpuPriceSnapshot:
    del market_service
    return GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=Decimal("0.50"),
                communityPrice=Decimal("0.40"),
            ),
        ),
        checked_at=datetime.now(UTC),
        source="fallback",
    )


@pytest.mark.anyio
async def test_models_list_delegates_to_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models_tool, "load_catalogue", miniature_catalogue)
    result = await models_tool.pitwall_models_list()
    assert result["models"][0]["model_id"] == "org/model"
    assert result["models"][0]["variant_ids"] == ["bf16"]


@pytest.mark.anyio
async def test_models_fit_delegates_to_price_and_fit_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(models_tool, "load_catalogue", miniature_catalogue)
    monkeypatch.setattr(models_tool, "load_gpu_price_snapshot", fallback_snapshot)
    result = await models_tool.pitwall_models_fit("org/model", ttl_minutes=60, cloud="secure")
    assert result["variant"] == "bf16"
    assert result["price_source"] == "fallback"
    assert result["price_age_seconds"] >= 0
    assert result["price_stale"] is False
    assert result["options"][0]["gpu_class"] == "NVIDIA L4"
    assert result["options"][0]["warm_cache"] is False
    assert result["options"][0]["cache_state"] == "not_checked"


@pytest.mark.anyio
async def test_models_fit_serializes_tight_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    catalogue = miniature_catalogue()
    dossier = catalogue.models()[0]
    adjusted = dossier.variants[0].model_copy(update={"min_vram_gb": 22})
    monkeypatch.setattr(
        models_tool,
        "load_catalogue",
        lambda: Catalogue((dossier.model_copy(update={"variants": (adjusted,)}),)),
    )
    monkeypatch.setattr(models_tool, "load_gpu_price_snapshot", fallback_snapshot)
    result = await models_tool.pitwall_models_fit("org/model", ttl_minutes=60, cloud="secure")
    assert result["options"][0]["fit"] == "tight"


@pytest.mark.anyio
async def test_models_fit_accepts_inventory_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models_tool, "load_catalogue", miniature_catalogue)
    result = await models_tool.pitwall_models_fit(
        "org/model",
        inventory={
            "gpus": [
                {
                    "name": "<gpu-name>",
                    "count": 1,
                    "vram_gb": 24,
                    "arch": "sm_86",
                    "nvlink": False,
                }
            ]
        },
        context=4096,
    )
    assert result["price_source"] == "local"
    assert result["context_length"] == 4096
    assert result["options"][0]["price_per_hour"] == "0"
