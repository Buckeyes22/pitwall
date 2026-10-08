"""GET /v1/capabilities exposes served_model_id; PATCH accepts it (hermetic)."""

from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Capability, Lease, LeaseEndpoints, LeaseReadiness

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _capability(served: str | None) -> Capability:
    return Capability(
        id="cap_llm_serve",
        name="llm.serve-test",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="per_second",
        source=CapabilitySource.API,
        served_model_id=served,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _active_lease() -> Lease:
    probe = datetime(2026, 5, 28, 12, 0, 34, tzinfo=UTC)
    return Lease(
        id="lease-serve-1",
        provider_id="prov_serve",
        runpod_pod_id="pod-serve-1",
        state=LeaseState.ACTIVE,
        created_at=_NOW,
        expires_at=datetime(2026, 5, 28, 14, 0, tzinfo=UTC),
        renewal_policy=LeaseRenewalPolicy.ACTIVITY,
        endpoints=LeaseEndpoints(http={"8000": "https://pod-serve-1.example.invalid"}),
        readiness=LeaseReadiness(
            runtime_seen_at=probe,
            port_mappings_seen_at=probe,
            probe_passed_at=probe,
            probe_method="runpod_proxy",
        ),
        ready_at=probe,
        last_traffic_at=datetime(2026, 5, 28, 12, 7, tzinfo=UTC),
        idle_timeout_min=20,
        max_usd_per_hour=Decimal("2.5000"),
    )


@pytest.fixture(autouse=True)
def clear_app_module():
    for key in [k for k in sys.modules if k.startswith("pitwall.api")]:
        del sys.modules[key]
    yield
    for key in [k for k in sys.modules if k.startswith("pitwall.api")]:
        del sys.modules[key]


def _import_app():
    old = os.environ.copy()
    os.environ.update(
        {
            "RUNPOD_API_KEY": "test-key",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )
    for key in ("PITWALL_API_TOKEN", "PITWALL_INBOUND_RATE_LIMIT"):
        os.environ.pop(key, None)
    os.environ["PITWALL_ADMIN_SECRET"] = "test-admin-secret"
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _setup(repo: AsyncMock, lease_repo: AsyncMock | None = None):
    app_mod = _import_app()
    from pitwall.api.capability_routes import _lease_repo, _pool, _provider_repo, _repo

    app_mod.app.dependency_overrides[_repo] = lambda: repo
    provider_repo = AsyncMock()
    provider_repo.list.return_value = []
    app_mod.app.dependency_overrides[_provider_repo] = lambda: provider_repo
    app_mod.app.dependency_overrides[_pool] = lambda: MagicMock()
    if lease_repo is None:
        configured_lease_repo = AsyncMock()
        configured_lease_repo.latest_active_for_capability.return_value = None
        configured_lease_repo.latest_active_for_capabilities.return_value = {}
    else:
        configured_lease_repo = lease_repo
    app_mod.app.dependency_overrides[_lease_repo] = lambda: configured_lease_repo
    return app_mod


@pytest.mark.anyio
async def test_get_capability_exposes_served_model_id() -> None:
    repo = AsyncMock()
    repo.get_by_name.return_value = _capability("org/model")
    app_mod = _setup(repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/capabilities/llm.serve-test")
    assert response.status_code == 200
    assert response.json()["served_model_id"] == "org/model"


@pytest.mark.anyio
async def test_get_capability_served_model_id_is_null_when_absent() -> None:
    repo = AsyncMock()
    repo.get_by_name.return_value = _capability(None)
    app_mod = _setup(repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/capabilities/llm.serve-test")
    assert response.json()["served_model_id"] is None


@pytest.mark.anyio
async def test_get_capability_exposes_active_lease_automation_metadata() -> None:
    repo = AsyncMock()
    repo.get_by_name.return_value = _capability("org/model")
    lease_repo = AsyncMock()
    lease_repo.latest_active_for_capability.return_value = _active_lease()
    app_mod = _setup(repo, lease_repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        body = (await client.get("/v1/capabilities/llm.serve-test")).json()
    assert body["active_lease"] == {
        "lease_id": "lease-serve-1",
        "state": "active",
        "expires_at": "2026-05-28T14:00:00+00:00",
    }
    assert body["idle_timeout_min"] == 20
    assert body["last_traffic_at"] == "2026-05-28T12:07:00Z"
    assert body["renewal_policy"] == "activity"
    assert body["max_usd_per_hour"] == "2.5000"
    lease_repo.latest_active_for_capability.assert_awaited_once_with("cap_llm_serve")


@pytest.mark.anyio
async def test_capability_automation_metadata_is_null_without_active_lease() -> None:
    repo = AsyncMock()
    repo.get_by_name.return_value = _capability(None)
    app_mod = _setup(repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        body = (await client.get("/v1/capabilities/llm.serve-test")).json()
    assert body["active_lease"] is None
    assert body["idle_timeout_min"] is None
    assert body["last_traffic_at"] is None
    assert body["renewal_policy"] is None
    assert body["max_usd_per_hour"] is None


@pytest.mark.anyio
async def test_list_capabilities_batches_and_projects_automation_metadata() -> None:
    repo = AsyncMock()
    first, second = _capability("org/first"), _capability("org/second")
    second = second.model_copy(update={"id": "cap_llm_second", "name": "llm.second"})
    repo.list.return_value = [first, second]
    lease_repo = AsyncMock()
    lease_repo.latest_active_for_capabilities.return_value = {first.id: _active_lease()}
    app_mod = _setup(repo, lease_repo)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        body = (await client.get("/v1/capabilities")).json()
    lease_repo.latest_active_for_capabilities.assert_awaited_once_with([first.id, second.id])
    assert body["items"][0]["active_lease"]["lease_id"] == "lease-serve-1"
    assert body["items"][0]["idle_timeout_min"] == 20
    assert body["items"][0]["last_traffic_at"] == "2026-05-28T12:07:00+00:00"
    assert body["items"][0]["renewal_policy"] == "activity"
    assert body["items"][0]["max_usd_per_hour"] == "2.5000"
    assert body["items"][1]["active_lease"] is None
    assert {
        body["items"][1][name]
        for name in (
            "idle_timeout_min",
            "last_traffic_at",
            "renewal_policy",
            "max_usd_per_hour",
        )
    } == {None}


@pytest.mark.anyio
async def test_patch_capability_accepts_served_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock()
    repo.get.return_value = _capability(None)
    repo.patch.return_value = _capability("org/model")
    app_mod = _setup(repo)
    import pitwall.api.capability_routes as routes

    monkeypatch.setattr(routes, "insert_audit", AsyncMock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.patch(
            "/v1/admin/capabilities/cap_llm_serve",
            json={"served_model_id": "org/model"},
            headers={"X-Pitwall-Secret": "test-admin-secret"},
        )
    assert response.status_code == 200
    assert repo.patch.call_args.kwargs["served_model_id"] == "org/model"
    assert response.json()["served_model_id"] == "org/model"
