from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from pitwall.models.catalogue import Catalogue
from pitwall.models.prices import GpuPriceSnapshot
from pitwall.models.schema import ModelDossier
from pitwall.runpod_client.graphql import RunpodGpuType

models_route = pytest.importorskip("pitwall.api.routes.models")


def _catalogue() -> Catalogue:
    dossier = ModelDossier.model_validate(
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
                    "min_vram_gb": 22,
                    "context": 32768,
                    "container_disk_gb": 40,
                    "startup_min": 15,
                    "flags": [],
                    "env": {},
                    "recommended_gpu_classes": ["NVIDIA GeForce RTX 4090"],
                    "tool_call_parser": None,
                    "reasoning_parser": None,
                    "confidence": "medium",
                    "sources": ["https://example.test/model"],
                }
            ],
            "confidence": {"overall": "medium", "notes": "source checked"},
            "accessed": "2026-08-27",
            "body": "# Model\n",
        }
    )
    return Catalogue((dossier,))


async def _price_snapshot(cloud: str = "secure") -> GpuPriceSnapshot:
    return GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType.model_validate(
                {
                    "id": "NVIDIA GeForce RTX 4090",
                    "memoryInGb": 24,
                    "securePrice": None,
                    "communityPrice": None,
                }
            ),
        ),
        checked_at=datetime.now(UTC),
        source="fallback",
    )


@pytest.fixture
async def client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(models_route.model_catalogue_router)
    app.dependency_overrides[models_route._catalogue] = _catalogue
    app.dependency_overrides[models_route._price_snapshot] = _price_snapshot
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.anyio
async def test_catalogue_list_and_double_dash_detail(client: httpx.AsyncClient) -> None:
    listed = await client.get("/v1/models/catalogue")
    detail = await client.get("/v1/models/catalogue/org--model")
    assert listed.status_code == 200
    assert listed.json()["models"][0]["default_variant"] == "bf16"
    assert detail.status_code == 200
    assert detail.json()["model_id"] == "org/model"
    assert detail.json()["body"] == "# Model\n"


@pytest.mark.anyio
async def test_fit_route_returns_all_rows_and_fallback_state(client: httpx.AsyncClient) -> None:
    response = await client.get(
        "/v1/models/catalogue/org--model/fit",
        params={"variant": "bf16", "ttl_minutes": 60, "cloud": "community"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["variant"] == "bf16"
    assert payload["price_source"] == "fallback"
    assert payload["price_age_seconds"] >= 0
    assert payload["price_stale"] is False
    assert payload["options"][0]["price_per_hour"] is None


async def test_fit_route_serializes_tight_verdict(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/models/catalogue/org--model/fit")
    assert response.status_code == 200
    assert response.json()["options"][0]["fit"] == "tight"


async def test_fit_route_reports_warm_only_for_matching_provider_volume(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cast(Any, client._transport).app.state.pool = object()
    repository = SimpleNamespace(
        get_by_name=AsyncMock(
            return_value=SimpleNamespace(
                config={
                    "network_volume_id": "volume-cache",
                    "warm_cache": {
                        "variant": "bf16",
                        "verified_at": "2026-08-28T12:00:00+00:00",
                        "volume_id": "volume-cache",
                    },
                }
            )
        )
    )
    monkeypatch.setattr(models_route, "ProviderRepository", lambda pool: repository)

    omitted = await client.get("/v1/models/catalogue/org--model/fit")
    explicit = await client.get("/v1/models/catalogue/org--model/fit", params={"variant": "bf16"})
    repository.get_by_name.return_value.config["warm_cache"]["variant"] = "fp8"
    other_variant = await client.get("/v1/models/catalogue/org--model/fit")
    repository.get_by_name.return_value.config["warm_cache"]["variant"] = "bf16"
    repository.get_by_name.return_value.config["network_volume_id"] = "volume-other"
    other_volume = await client.get("/v1/models/catalogue/org--model/fit")

    assert omitted.status_code == explicit.status_code == 200
    assert omitted.json()["options"][0]["warm_cache"] is True
    assert explicit.json()["options"][0]["warm_cache"] is True
    assert other_variant.json()["options"][0]["warm_cache"] is False
    assert other_volume.json()["options"][0]["warm_cache"] is False


@pytest.mark.anyio
async def test_unknown_model_and_variant_are_mapped(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/models/catalogue/missing--model")).status_code == 404
    response = await client.get(
        "/v1/models/catalogue/org--model/fit", params={"variant": "missing"}
    )
    assert response.status_code == 422
    assert "unknown variant" in response.json()["detail"]
