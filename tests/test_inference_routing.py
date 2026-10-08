"""Hermetic tests for POST /v1/inference routing."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.config import PitwallSettings
from pitwall.core.enums import ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.db.repository import CapabilityRepository, ProviderRepository
from pitwall.routing.production import ProductionRoutingService

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_embedding_bge_m3",
        name="embedding.bge-m3",
        version="1.0.0",
        class_="embedding",
        cost_mode="per_request",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(provider_id: str, *, priority: int, health_status: str = "healthy") -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_embedding_bge_m3",
        name=provider_id,
        provider_type=ProviderType.SERVERLESS_LB,
        runpod_endpoint_id=f"{provider_id}-endpoint",
        priority=priority,
        enabled=True,
        health_status=health_status,
        config={"per_request": "0.001"},
        updated_at=_NOW,
    )


class _Budget:
    def __init__(self) -> None:
        self.monthly_budget_usd = Decimal("100")
        self.per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: object | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")


@pytest.fixture()
def api_client() -> tuple[object, AsyncMock, AsyncMock]:
    old = os.environ.copy()
    os.environ.update(
        {
            "RUNPOD_API_KEY": "test-key",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )

    for mod in list(sys.modules):
        if mod.startswith("pitwall.api"):
            del sys.modules[mod]

    from pitwall.api.app import app
    from pitwall.api.routes.inference import _routing_service

    capability_repo = AsyncMock(spec=CapabilityRepository)
    provider_repo = AsyncMock(spec=ProviderRepository)
    pool = MagicMock()
    app.state.pool = pool
    service = ProductionRoutingService(
        pool,
        settings=PitwallSettings(),
        capability_repository=capability_repo,
        provider_repository=provider_repo,
        budget_gate=_Budget(),
    )
    app.dependency_overrides[_routing_service] = lambda: service

    yield app, capability_repo, provider_repo

    app.dependency_overrides.clear()
    if hasattr(app.state, "pool"):
        delattr(app.state, "pool")
    os.environ.clear()
    os.environ.update(old)
    for mod in list(sys.modules):
        if mod.startswith("pitwall.api"):
            del sys.modules[mod]


@pytest.mark.anyio
async def test_inference_dry_run_routes_by_capability_name_to_priority_one_provider(
    api_client: tuple[object, AsyncMock, AsyncMock],
) -> None:
    app, capability_repo, provider_repo = api_client
    capability = _capability()
    capability_repo.get_by_name.return_value = capability
    capability_repo.get.return_value = None
    provider_repo.list.return_value = [
        _provider("prov_unhealthy", priority=1, health_status="unhealthy"),
        _provider("prov_priority_2", priority=2),
        _provider("prov_priority_1", priority=1),
    ]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/inference",
            json={
                "capability": "embedding.bge-m3",
                "texts": ["hello"],
                "dry_run": True,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workload_id"].startswith("dry_run_inference_")
    plan = body["result"]["plan"]
    assert plan["capability_id"] == "cap_embedding_bge_m3"
    assert plan["selected_provider_id"] == "prov_priority_1"
    assert plan["fallback_chain"] == [
        "prov_priority_1",
        "prov_priority_2",
    ]
    provider_repo.list.assert_awaited_once_with(
        capability_id="cap_embedding_bge_m3",
        enabled_only=False,
        limit=100,
        offset=0,
    )


@pytest.mark.anyio
async def test_inference_unknown_explicit_provider_returns_404(
    api_client: tuple[object, AsyncMock, AsyncMock],
) -> None:
    app, capability_repo, provider_repo = api_client
    capability_repo.get_by_name.return_value = _capability()
    capability_repo.get.return_value = None
    provider_repo.get.return_value = None

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/v1/inference",
            json={
                "capability": "embedding.bge-m3",
                "provider_id": "prov_missing",
                "dry_run": True,
            },
        )

    assert resp.status_code == 404
    assert resp.json() == {"error": "provider_not_found", "id": "prov_missing"}
    provider_repo.get.assert_awaited_once_with("prov_missing")
    provider_repo.list.assert_not_awaited()
