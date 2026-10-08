"""OpenAI proxy behavior for serve-armed pod_lease providers (hermetic)."""

from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType, WorkloadState
from pitwall.core.models import Capability, Provider, Workload
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.routing.fallback import PITWALL_OPENAI_PROXY_USER_AGENT, _provider_headers

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_llm_serve",
        name="llm.serve-test",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="per_second",
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _pod_lease_provider(*, armed: bool, health_status: str) -> Provider:
    config: dict[str, object] = {
        "ports": {"http": [8000]},
        "openai_proxy_port": 8000,
        "cost": {"per_second_active": "0.002"},
    }
    if armed:
        config["active_pod_id"] = "pod-serve-1"
        config["active_lease_id"] = "lease-serve-1"
    return Provider(
        id="prov_serve",
        capability_id="cap_llm_serve",
        name="serve-llm.serve-test",
        provider_type=ProviderType.POD_LEASE,
        config=config,
        priority=0,
        enabled=True,
        health_status=health_status,
        updated_at=_NOW,
    )


def test_pod_lease_proxy_uses_endpoint_key_and_never_control_plane_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "control-plane-secret")
    monkeypatch.setenv("PITWALL_ENDPOINT_KEY", "endpoint-secret")
    provider = _pod_lease_provider(armed=True, health_status="healthy")
    provider = provider.model_copy(
        update={"config": {**provider.config, "api_key_env": "PITWALL_ENDPOINT_KEY"}}
    )

    outbound = _provider_headers(
        {"Authorization": "Bearer consumer-secret", "X-Request-ID": "request-1"},
        provider,
    )

    assert outbound["authorization"] == "Bearer endpoint-secret"
    assert outbound["X-Request-ID"] == "request-1"
    assert outbound["user-agent"] == PITWALL_OPENAI_PROXY_USER_AGENT
    assert "Python-urllib" not in repr(outbound)
    assert "control-plane-secret" not in repr(outbound)


def test_proxy_uses_stable_identity_instead_of_forwarding_client_user_agent() -> None:
    outbound = _provider_headers(
        {"User-Agent": "Python-urllib/3.14", "Authorization": "Bearer consumer-secret"},
        _pod_lease_provider(armed=True, health_status="healthy"),
    )

    assert outbound["user-agent"] == PITWALL_OPENAI_PROXY_USER_AGENT
    assert "Python-urllib/3.14" not in outbound.values()


def test_legacy_pod_lease_never_falls_back_to_control_plane_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "control-plane-secret")
    outbound = _provider_headers(
        {"Authorization": "Bearer consumer-secret"},
        _pod_lease_provider(armed=True, health_status="healthy"),
    )
    assert "authorization" not in {key.lower() for key in outbound}


def test_pod_lease_custom_control_plane_ref_is_also_never_forwarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_RUNPOD_CONTROL_KEY", "control-plane-secret")
    provider = _pod_lease_provider(armed=True, health_status="healthy").model_copy(
        update={"credential_ref": "CUSTOM_RUNPOD_CONTROL_KEY"}
    )
    outbound = _provider_headers({"Authorization": "Bearer consumer-secret"}, provider)
    assert "authorization" not in {key.lower() for key in outbound}
    assert "control-plane-secret" not in repr(outbound)


@pytest.fixture(autouse=True)
def clear_app_module():
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
    for key in ("PITWALL_ADMIN_SECRET", "PITWALL_API_TOKEN", "PITWALL_INBOUND_RATE_LIMIT"):
        os.environ.pop(key, None)
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _setup(provider: Provider) -> tuple[object, AsyncMock, AsyncMock]:
    app_mod = _import_app()
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    capability_repo = AsyncMock()
    capability_repo.get_by_name.return_value = _capability()
    provider_repo = AsyncMock()
    provider_repo.list.return_value = [provider]
    budget_gate = AsyncMock()
    budget_gate.monthly_budget_usd = Decimal("100")
    budget_gate.per_request_max_usd = Decimal("10")
    budget_gate.effective_limits.return_value = BudgetLimits(
        Decimal("100"), Decimal("10"), "environment"
    )
    budget_gate.current_mtd_spend.return_value = Decimal("0")
    workload_repo = AsyncMock()
    workload_repo.insert.side_effect = lambda workload: workload
    workload_repo.guarded_transition.return_value = MagicMock()
    app_mod.app.dependency_overrides[_capability_repo] = lambda: capability_repo
    app_mod.app.dependency_overrides[_provider_repo] = lambda: provider_repo
    app_mod.app.dependency_overrides[_budget_gate] = lambda: budget_gate
    app_mod.app.dependency_overrides[_workload_repo] = lambda: workload_repo
    app_mod.app.state.pool = MagicMock()
    return app_mod, budget_gate, workload_repo


@respx.mock
@pytest.mark.anyio
async def test_armed_pod_lease_proxies_to_pod_url_with_zero_cost_ledger_row() -> None:
    app_mod, budget_gate, workload_repo = _setup(
        _pod_lease_provider(armed=True, health_status="healthy")
    )
    route = respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "org/model"}]})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/openai/llm.serve-test/v1/models")

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "org/model"
    assert route.called
    budget_gate.try_launch_admission.assert_not_called()
    inserted: Workload = workload_repo.insert.call_args.args[0]
    assert inserted.provider_id == "prov_serve"
    assert inserted.type == "openai_passthrough"
    assert inserted.cost_estimate_usd == Decimal("0")
    assert inserted.cost_actual_usd is None
    completion = next(
        call
        for call in workload_repo.guarded_transition.await_args_list
        if call.kwargs["to_state"] == WorkloadState.COMPLETED
    )
    assert completion.kwargs["patch"]["cost_actual_usd"] == Decimal("0")
    assert completion.kwargs["patch"]["cost_actual_provenance"] == "broker:lease_covered"


@pytest.mark.parametrize(("status", "expected_stamps"), [(200, 1), (429, 0), (503, 0)])
@respx.mock
@pytest.mark.anyio
async def test_pod_lease_stamps_traffic_only_for_successful_upstream_response(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected_stamps: int,
) -> None:
    app_mod, _budget_gate, _workload_repo = _setup(
        _pod_lease_provider(armed=True, health_status="healthy")
    )
    upstream = respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models")
    upstream.mock(return_value=httpx.Response(status, json={"data": []}))
    stamp = AsyncMock()
    monkeypatch.setattr("pitwall.api.routes.openai.stamp_lease_traffic", stamp)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/openai/llm.serve-test/v1/models")

    assert response.status_code == status
    assert stamp.await_count == expected_stamps


@respx.mock
@pytest.mark.anyio
async def test_pod_lease_does_not_stamp_when_upstream_execution_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod, _budget_gate, _workload_repo = _setup(
        _pod_lease_provider(armed=True, health_status="healthy")
    )
    respx.get("https://pod-serve-1-8000.proxy.runpod.net/v1/models").mock(
        side_effect=httpx.ConnectError("upstream unavailable")
    )
    stamp = AsyncMock()
    monkeypatch.setattr("pitwall.api.routes.openai.stamp_lease_traffic", stamp)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/openai/llm.serve-test/v1/models")

    assert response.status_code == 503
    stamp.assert_not_awaited()


@pytest.mark.anyio
async def test_disarmed_pod_lease_returns_503_provider_unavailable() -> None:
    app_mod, _budget_gate, _workload_repo = _setup(
        _pod_lease_provider(armed=False, health_status="unhealthy")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/openai/llm.serve-test/v1/chat/completions",
            json={"model": "org/model", "messages": []},
        )
    assert response.status_code == 503
    assert response.json()["error"] == "no_providers_available"


@pytest.mark.anyio
async def test_unarmed_but_unknown_health_pod_lease_also_returns_503() -> None:
    app_mod, _budget_gate, _workload_repo = _setup(
        _pod_lease_provider(armed=False, health_status="unknown")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/openai/llm.serve-test/v1/models")
    assert response.status_code == 503
