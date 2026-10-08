"""Task 10: ``gw/`` model-id proxy pinning.

A request body whose ``model`` starts with ``gw/`` is looked up in the
``model_id_map`` table, pinned to the mapped provider, and the outbound body
``model`` field is rewritten to the provider's ``gateway.model_id`` before
egress. Unmapped ``gw/*`` ids surface as ``provider_not_found`` (404).
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_gate import BudgetAdmission
from pitwall.cost.budget_limits import BudgetLimits
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
            {"read-token": ["read"], "spend-token": ["read", "spend"]}
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


def _capability() -> Capability:
    return Capability(
        id="cap_coding",
        name="coding.chat",
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
    model_id: str = "glm-4.7-flash",
    base_url: str = "http://127.0.0.1:20130/v1",
    name: str = "gw-beta-b1",
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_coding",
        name=name,
        adapter_id="openai_gateway",
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"mode": "zero"},
            "openai_base_url": base_url,
            "gateway": {
                "base_url": base_url,
                "model_id": model_id,
                "catalog": {
                    "free_type": "keyless",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                },
            },
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
        self.list_model_ids_calls = 0

    async def list_all(self) -> tuple[QuotaRecord, ...]:
        return tuple(self._records)

    async def list_model_ids(self) -> tuple[ModelIdMapping, ...]:
        self.list_model_ids_calls += 1
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


class _AdmittingBudgetGate:
    monthly_budget_usd = Decimal("100")
    per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **_: Any) -> BudgetAdmission:
        return BudgetAdmission(workload_id="wkl_proxy_test", is_new=True)


class _RecordingWorkloadRepo:
    async def guarded_transition(self, *_: Any, **__: Any) -> object:
        return object()

    async def insert(self, *_: Any, **__: Any) -> object:
        return object()

    async def patch(self, *_: Any, **__: Any) -> object:
        return object()


_CHAT_COMPLETION_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1234567890,
    "model": "glm-4.7-flash",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture(autouse=True)
def _clear_app_modules() -> None:
    _purge_api_modules()
    yield
    _purge_api_modules()


def _build_app(
    *,
    providers: list[Provider] | None,
    capability: Capability | None = None,
    quota_repo: _FakeQuotaRepo | None = None,
) -> Any:
    env = _scoped_env()
    mod = _import_app(env)
    mock_capability_repo = AsyncMock()
    mock_capability_repo.get_by_name.return_value = capability or _capability()
    mock_provider_repo = AsyncMock()
    mock_provider_repo.list.return_value = providers or []
    mock_provider_repo.get = AsyncMock(
        side_effect=lambda provider_id: next(
            (p for p in (providers or []) if p.id == provider_id), None
        )
    )

    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    mod.app.dependency_overrides[_capability_repo] = lambda: mock_capability_repo
    mod.app.dependency_overrides[_provider_repo] = lambda: mock_provider_repo
    mod.app.dependency_overrides[_budget_gate] = lambda: _AdmittingBudgetGate()
    mod.app.dependency_overrides[_workload_repo] = lambda: _RecordingWorkloadRepo()
    mod.app.state.pool = MagicMock()
    repo = quota_repo or _FakeQuotaRepo()
    mod.app.state.quota_repository = repo

    real_routing_service_constructor = (
        mod._routing_service_factory if hasattr(mod, "_routing_service_factory") else None
    )
    if real_routing_service_constructor is None:
        # Build a fully mocked ProductionRoutingService so the quota snapshot
        # is supplied by the test fixture, not the MagicMock pool.
        from pitwall.config import get_settings
        from pitwall.routing.production import ProductionRoutingService
        from pitwall.routing.quota import QuotaSnapshot

        async def _stub_quota_snapshot() -> QuotaSnapshot:
            records = await repo.list_all()
            return QuotaSnapshot(records=records)

        service = ProductionRoutingService(
            mod.app.state.pool,
            settings=get_settings(),
            capability_repository=mock_capability_repo,
            provider_repository=mock_provider_repo,
            budget_gate=_AdmittingBudgetGate(),
        )
        service._load_quota_snapshot = _stub_quota_snapshot  # type: ignore[method-assign]  # reason: test double replaces the quota loader
        mod.app.state.production_routing_service = service

    return mod


@pytest.fixture
def gw_provider() -> Provider:
    return _provider()


async def test_gw_model_id_pins_provider_and_rewrites_model(
    gw_provider: Provider,
) -> None:
    quota_record = QuotaRecord(
        provider_id="prov_gw",
        pool_key="",
        free_type="keyless",
        window_start=None,
        reset_at=None,
        budget_units=None,
        used_units=Decimal("0"),
        tos_verdict="ok",
        evidence={},
    )
    repo = _FakeQuotaRepo(
        records=[quota_record],
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")],
    )
    mod = _build_app(providers=[gw_provider], quota_repo=repo)
    upstream_url = "http://127.0.0.1:20130/v1/chat/completions"
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE)

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post(upstream_url).mock(side_effect=handler)
        transport = httpx.ASGITransport(app=mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/openai/coding.chat/v1/chat/completions",
                json={"model": "gw/glm-flash", "messages": []},
                headers={"Authorization": "Bearer spend-token"},
            )

    assert response.status_code == 200, response.text
    assert upstream_call.called
    assert response.headers["X-Pitwall-Provider-ID"] == "prov_gw"
    assert captured_body["model"] == "glm-4.7-flash"


async def test_unmapped_gw_model_id_returns_provider_not_found() -> None:
    quota_record = QuotaRecord(
        provider_id="prov_gw",
        pool_key="",
        free_type="keyless",
        window_start=None,
        reset_at=None,
        budget_units=None,
        used_units=Decimal("0"),
        tos_verdict="ok",
        evidence={},
    )
    mod = _build_app(
        providers=[_provider()],
        quota_repo=_FakeQuotaRepo(records=[quota_record], model_ids=[]),
    )
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/openai/coding.chat/v1/chat/completions",
            json={"model": "gw/does-not-exist", "messages": []},
            headers={"Authorization": "Bearer spend-token"},
        )
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "provider_not_found"


async def test_non_gw_model_id_is_not_rewritten(gw_provider: Provider) -> None:
    quota_record = QuotaRecord(
        provider_id="prov_gw",
        pool_key="",
        free_type="keyless",
        window_start=None,
        reset_at=None,
        budget_units=None,
        used_units=Decimal("0"),
        tos_verdict="ok",
        evidence={},
    )
    repo = _FakeQuotaRepo(
        records=[quota_record],
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")],
    )
    mod = _build_app(providers=[gw_provider], quota_repo=repo)
    upstream_url = "http://127.0.0.1:20130/v1/chat/completions"
    captured_body: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE)

    with respx.mock(assert_all_called=False) as router:
        router.post(upstream_url).mock(side_effect=handler)
        transport = httpx.ASGITransport(app=mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/openai/coding.chat/v1/chat/completions",
                json={"model": "glm-4.7-flash", "messages": []},
                headers={"Authorization": "Bearer spend-token"},
            )
    assert response.status_code == 200, response.text
    assert captured_body["model"] == "glm-4.7-flash"


async def test_mapped_provider_with_non_mapping_gateway_config_is_provider_not_found() -> None:
    broken = _provider()
    broken.config["gateway"] = None
    mod = _build_app(
        providers=[broken],
        quota_repo=_FakeQuotaRepo(
            records=[],
            model_ids=[ModelIdMapping("gw/broken", "coding.chat", broken.id)],
        ),
    )
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/openai/coding.chat/v1/chat/completions",
            json={"model": "gw/broken", "messages": []},
            headers={"Authorization": "Bearer spend-token"},
        )
    assert response.status_code == 404
    assert response.json()["error"] == "provider_not_found"
