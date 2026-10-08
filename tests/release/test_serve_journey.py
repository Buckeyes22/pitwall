"""Hermetic J23 serve-model journey coverage."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from pitwall import serve
from pitwall.api.leases import launch, teardown
from pitwall.api.routes import leases as lease_routes
from pitwall.config import PitwallSettings
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
)
from pitwall.core.models import Capability, Lease, LeaseEndpoints, LeaseReadiness, Provider
from pitwall.cost.budget_gate import BudgetAdmission
from pitwall.cost.budget_limits import BudgetLimits
from tests.conftest import _env_for_app, _import_app
from tests.fakes.teardown import UnlockedTeardown

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
_REPO_ROOT = Path(__file__).parents[2]


class _AsyncContext:
    """Minimal ``async with`` wrapper around a fake connection."""

    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *exc: object) -> None:
        return None


pytestmark = pytest.mark.release


def test_j23_harness_runs_without_database() -> None:
    """J23 is self-contained, unlike database-backed release journeys."""
    result = subprocess.run(
        ["bash", "scripts/release/run-user-journeys.sh", "J23"],
        cwd=_REPO_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "",
            "REDIS_URL": "",
            # The fixture models an authenticated registry launch; use a
            # hermetic sentinel rather than depending on a developer secret.
            "PITWALL_ENDPOINT_KEY": "test-endpoint-key",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS  J23" in result.stderr


def _capability() -> Capability:
    return Capability(
        id="cap_serve_journey",
        name="llm.serve-journey",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _lease() -> Lease:
    ready = _NOW + timedelta(seconds=30)
    return Lease(
        id="lease-serve-journey",
        provider_id="prov_serve_journey",
        runpod_pod_id="pod-serve-journey",
        state=LeaseState.ACTIVE,
        created_at=_NOW,
        expires_at=_NOW + timedelta(hours=2),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        endpoints=LeaseEndpoints(http={"8000": "https://pod-serve-journey-8000.proxy.runpod.net"}),
        readiness=LeaseReadiness(
            runtime_seen_at=ready,
            port_mappings_seen_at=ready,
            probe_passed_at=ready,
            probe_method="runpod_proxy",
        ),
    )


@pytest.mark.anyio
async def test_j23_seeded_serve_dry_run_launch_proxy_and_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_ENDPOINT_KEY", "test-endpoint-key")
    states = ["seeded"]
    capability = _capability()
    provider: Provider | None = None
    lease: Lease | None = None
    pool = MagicMock()
    # The kill-switch admission reads `pitwall.kill_log` through pool.acquire(); a bare
    # MagicMock would answer with a truthy mock and refuse every launch as "engaged".
    kill_log_conn = MagicMock()
    kill_log_conn.fetchval = AsyncMock(return_value=False)
    kill_log_conn.transaction = MagicMock(return_value=_AsyncContext(None))

    async def lock_provider_row(sql: str, *args: Any) -> dict[str, Any] | None:
        # Arming and disarming lock the provider row; answer with its current state.
        assert "FROM pitwall.providers" in sql and "FOR UPDATE" in sql
        assert provider is not None and args == (provider.id,)
        return {"config": dict(provider.config), "health_status": provider.health_status}

    kill_log_conn.fetchrow = lock_provider_row
    pool.acquire = MagicMock(
        return_value=_AsyncContext(kill_log_conn),
    )

    class ServeCapabilities:
        async def get_by_name(self, name: str) -> Capability | None:
            return capability if name == capability.name else None

        async def create(self, value: Capability) -> Capability:
            return value

        async def patch(self, capability_id: str, **changes: Any) -> Capability:
            nonlocal capability
            assert capability_id == capability.id
            capability = capability.model_copy(update=changes)
            return capability

    class ServeProviders:
        async def get_by_name(self, name: str) -> Provider | None:
            return provider

        async def create(self, value: Provider) -> Provider:
            nonlocal provider
            provider = value
            return value

        async def patch(self, provider_id: str, **changes: Any) -> Provider:
            nonlocal provider
            assert provider is not None and provider_id == provider.id
            provider = provider.model_copy(update=changes)
            return provider

    class ServeLeases:
        async def latest_active_for_provider(self, provider_id: str) -> Lease | None:
            return lease

        async def get(self, lease_id: str) -> Lease | None:
            return lease

    monkeypatch.setattr(serve, "CapabilityRepository", lambda _pool: ServeCapabilities())
    monkeypatch.setattr(serve, "ProviderRepository", lambda _pool: ServeProviders())
    monkeypatch.setattr(serve, "LeaseRepository", lambda _pool: ServeLeases())

    lease_states: list[str] = []

    class LaunchLeases(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease | None:
            return lease if lease is not None and lease_id == lease.id else None

        async def capability_name(self, lease_id: str) -> str | None:
            assert lease is not None and lease_id == lease.id
            return capability.name

        async def update_state(self, lease_id: str, state: str) -> Lease | None:
            nonlocal lease
            assert lease is not None and lease_id == lease.id
            lease_states.append(state)
            lease = lease.model_copy(update={"state": state})
            return lease

        async def update_readiness(self, lease_id: str, readiness: LeaseReadiness) -> None:
            assert lease is not None and lease_id == lease.id
            assert readiness.has_active_signals

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease | None:
            nonlocal lease
            assert lease is not None and lease_id == lease.id
            lease = lease.model_copy(update=changes)
            return lease

    class LifecycleProviders:
        async def patch(self, provider_id: str, **changes: Any) -> Provider:
            return await ServeProviders().patch(provider_id, **changes)

        async def get(self, provider_id: str) -> Provider | None:
            return provider if provider is not None and provider_id == provider.id else None

    audit = AsyncMock()
    monkeypatch.setattr(launch, "LeaseRepository", lambda _pool: LaunchLeases())
    monkeypatch.setattr(launch, "ProviderRepository", lambda _pool: LifecycleProviders())
    monkeypatch.setattr(launch, "insert_audit", audit)
    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LaunchLeases())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: LifecycleProviders())
    monkeypatch.setattr(teardown, "insert_audit", audit)

    async def fake_admit_lease_launch(*_args: Any, **_kwargs: Any) -> BudgetAdmission:
        return BudgetAdmission(workload_id="wkl-serve-journey", is_new=True)

    async def fake_prepare_lease_launch(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            template=SimpleNamespace(
                template_id="template-serve-journey",
                template_name="serve-journey",
                image_ref="example/serve-journey:test",
            ),
            workload=SimpleNamespace(
                name="serve-journey",
                capability="llm.serve-journey",
                gpu_types=["NVIDIA H100 80GB HBM3"],
                gpu_count=1,
                container_disk_gb=50,
                cloud_type="ALL",
                gpu_type_priority=None,
                data_center_priority=None,
                min_vcpu=None,
                min_memory_gb=None,
                allowed_cuda_versions=None,
                ports=None,
            ),
            env={},
            network_volume_id=None,
            data_center_id=None,
            volume_attach_timeout_s=None,
            docker_start_cmd=None,
            docker_entrypoint=["sh", "-c"],
            startup_timeout_s=60,
            readiness_path="/health",
        )

    async def fake_create_pod_with_fallback(**kwargs: Any) -> dict[str, Any]:
        nonlocal lease
        assert kwargs["template_id"] == "template-serve-journey"
        assert provider is not None
        states.append("launched")
        lease = _lease().model_copy(update={"provider_id": provider.id})
        return {
            "id": lease.runpod_pod_id,
            "name": "pod-serve-journey",
            "readiness": lease.readiness.model_dump(mode="json"),
        }

    async def fake_terminate_pod(pod_id: str) -> None:
        assert pod_id == "pod-serve-journey"

    monkeypatch.setattr(launch, "admit_lease_launch", fake_admit_lease_launch)
    monkeypatch.setattr(launch, "prepare_lease_launch", fake_prepare_lease_launch)
    monkeypatch.setattr(launch, "_lease_id_for_launch", lambda _provider: "lease-serve-journey")
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create_pod_with_fallback)
    monkeypatch.setattr(launch, "_create_pod_with_fallback", fake_create_pod_with_fallback)
    monkeypatch.setattr(teardown, "terminate_pod", fake_terminate_pod)

    request = serve.ServeRequest(
        capability_name=capability.name,
        model="org/serve-journey",
        gpu_class="NVIDIA H100 80GB HBM3",
        image="example/serve-journey:test",
        rate_per_second=Decimal("0.002"),
        served_model_name="serve-journey-model",
    )
    dry_run = await serve.serve_model(
        pool,
        request.model_copy(update={"dry_run": True}),
        base_url="http://test",
        settings=PitwallSettings(),
    )
    assert dry_run.dry_run is True
    assert states == ["seeded"]
    states.append("dry-run")  # observed from the production serve_model result

    with respx.mock:
        respx.get("https://pod-serve-journey-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "serve-journey-model"}]})
        )
        launched = await serve.serve_model(
            pool, request, base_url="http://test", settings=PitwallSettings()
        )
    assert launched.lease_id == "lease-serve-journey"
    assert lease_states == ["waiting_runtime", "waiting_probe", "active"]
    assert states == ["seeded", "dry-run", "launched"]
    assert provider is not None and provider.config["active_pod_id"] == "pod-serve-journey"
    states.append("armed")  # observed from provider state after production launch

    app_mod = _import_app(_env_for_app())
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    capability_repo = AsyncMock()
    capability_repo.get_by_name.return_value = capability
    provider_repo = AsyncMock()
    provider_repo.list.return_value = [provider]
    workload_repo = AsyncMock()
    workload_repo.insert.side_effect = lambda workload: workload
    workload_repo.guarded_transition.return_value = MagicMock()
    app_mod.app.state.pool = pool
    app_mod.app.dependency_overrides[_capability_repo] = lambda: capability_repo
    app_mod.app.dependency_overrides[_provider_repo] = lambda: provider_repo
    app_mod.app.dependency_overrides[_workload_repo] = lambda: workload_repo
    budget_gate = AsyncMock()
    budget_gate.monthly_budget_usd = Decimal("100")
    budget_gate.per_request_max_usd = Decimal("10")
    budget_gate.effective_limits.return_value = BudgetLimits(
        Decimal("100"), Decimal("10"), "environment"
    )
    budget_gate.current_mtd_spend.return_value = Decimal("0")
    app_mod.app.dependency_overrides[_budget_gate] = lambda: budget_gate
    chat_body = {"model": "serve-journey-model", "messages": [{"role": "user", "content": "hi"}]}
    with respx.mock:
        chat = respx.post(
            "https://pod-serve-journey-8000.proxy.runpod.net/v1/chat/completions"
        ).mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/v1/openai/{capability.name}/v1/chat/completions", json=chat_body
            )
    assert response.status_code == 200, response.text
    assert json.loads(chat.calls[0].request.content) == chat_body

    assert lease is not None
    stopped = await lease_routes.stop_lease(lease.id, pool=pool, redis_client=None)
    assert stopped["state"] == "stopped"
    assert provider is not None and "active_pod_id" not in provider.config
    assert provider.health_status == "disarmed"  # idle, not failing (finding #2)
    states.append("disarmed")  # observed after the production stop route delegates teardown
    assert states == ["seeded", "dry-run", "launched", "armed", "disarmed"]
