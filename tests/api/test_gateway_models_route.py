"""Task 10: /v1/gateway/models route contract.

The route joins the proxy ``model_id_map`` (Capability + Provider) with the
Provider.config["gateway"]["catalog"] evidence so consumers can render
``trains_on_prompts`` and ``tos`` privacy flags. Read scope suffices.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.db.quota_repository import ModelIdMapping
from pitwall.routing.quota import QuotaRecord

pytestmark = pytest.mark.anyio


def _purge_api_modules() -> None:
    for key in [key for key in sys.modules if key.startswith("pitwall.api")]:
        del sys.modules[key]


def _scoped_env() -> dict[str, str]:
    return {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "PITWALL_API_SCOPED_TOKENS": json.dumps(
            {"read-token": ["read"], "admin-token": ["read", "server:admin"]}
        ),
    }


def _import_app(env: dict[str, str]) -> Any:
    old = os.environ.copy()
    os.environ.update(env)
    for key in (
        "RUNPOD_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "PITWALL_ADMIN_SECRET",
        "PITWALL_API_TOKEN",
        "PITWALL_INBOUND_RATE_LIMIT",
    ):
        if key not in env:
            os.environ.pop(key, None)
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _now() -> dt.datetime:
    return dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _capability(name: str = "coding.chat") -> Capability:
    return Capability(
        id="cap_coding",
        name=name,
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="zero",
        source=CapabilitySource.YAML,
        enabled=True,
        created_at=_now(),
        updated_at=_now(),
    )


def _provider(
    *,
    provider_id: str = "prov_gw",
    name: str = "gw-beta-b1",
    catalog: dict[str, Any] | None = None,
    base_url: str = "http://127.0.0.1:20130/v1",
    model_id: str = "glm-4.7-flash",
) -> Provider:
    base_catalog: dict[str, Any] = {
        "free_type": "keyless",
        "tos": "ok",
        "hard_stop_guaranteed": True,
    }
    if catalog:
        base_catalog.update(catalog)
    return Provider(
        id=provider_id,
        capability_id="cap_coding",
        name=name,
        adapter_id="openai_gateway",
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "openai_base_url": base_url,
            "gateway": {"base_url": base_url, "model_id": model_id, "catalog": base_catalog},
        },
        priority=50,
        enabled=True,
        health_status="healthy",
        updated_at=_now(),
    )


class _FakeQuotaRepo:
    def __init__(
        self,
        records: list[QuotaRecord] | None = None,
        *,
        model_ids: list[ModelIdMapping] | None = None,
    ) -> None:
        self._records = list(records or [])
        self._model_ids = list(model_ids or [])

    async def list_all(self) -> tuple[QuotaRecord, ...]:
        return tuple(self._records)

    async def list_model_ids(self) -> tuple[ModelIdMapping, ...]:
        return tuple(self._model_ids)

    async def upsert(self, record: QuotaRecord) -> None:
        return None

    async def upsert_model_id(self, model_id: str, capability: str, provider: str) -> None:
        return None

    async def add_usage(self, provider_id: str, pool_key: str, units: Decimal) -> None:
        return None

    async def record_sample(
        self,
        provider_id: str,
        sampled_at: dt.datetime,
        used_units: Decimal,
        reset_at: dt.datetime | None,
    ) -> None:
        return None


class _FakeProviderRepo:
    def __init__(self, mapping: dict[str, Provider]) -> None:
        self._mapping = mapping

    async def get(self, provider_id: str) -> Provider | None:
        return self._mapping.get(provider_id)


@pytest.fixture(autouse=True)
def _clear_app_modules() -> None:
    _purge_api_modules()
    yield
    _purge_api_modules()


def _build_app(
    *,
    model_ids: list[ModelIdMapping],
    providers: dict[str, Provider] | None = None,
) -> Any:
    env = _scoped_env()
    mod = _import_app(env)
    mod.app.state.pool = MagicMock()
    mod.app.state.quota_repository = _FakeQuotaRepo(model_ids=model_ids)
    mod.app.state.provider_repository = _FakeProviderRepo(providers or {})
    return mod


async def test_gateway_models_lists_mapped_ids_with_privacy_flags() -> None:
    provider = _provider(
        catalog={"free_type": "keyless", "tos": "caution", "trains_on_prompts": True},
    )
    mod = _build_app(
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")],
        providers={"prov_gw": provider},
    )
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/gateway/models",
            headers={"Authorization": "Bearer read-token"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "object": "list",
        "data": [
            {
                "id": "gw/glm-flash",
                "capability": "coding.chat",
                "provider": "prov_gw",
                "trains_on_prompts": True,
                "tos": "caution",
            }
        ],
    }


async def test_gateway_models_requires_bearer_token() -> None:
    mod = _build_app(model_ids=[])
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/gateway/models")
    assert response.status_code == 401


async def test_gateway_models_skips_mappings_with_unknown_provider() -> None:
    mod = _build_app(
        model_ids=[ModelIdMapping("gw/orphan", "coding.chat", "prov_missing")],
        providers={},
    )
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/gateway/models",
            headers={"Authorization": "Bearer read-token"},
        )
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


async def test_gateway_models_defaults_trains_on_prompts_false_when_catalog_missing() -> None:
    provider = _provider()
    provider.config["gateway"]["catalog"].pop("trains_on_prompts", None)
    mod = _build_app(
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")],
        providers={"prov_gw": provider},
    )
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/gateway/models",
            headers={"Authorization": "Bearer read-token"},
        )
    assert response.status_code == 200
    [row] = response.json()["data"]
    assert row["trains_on_prompts"] is False
    assert row["tos"] == "ok"
