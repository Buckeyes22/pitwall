from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from examples import verify_webhook
from pitwall import serve
from pitwall.api.leases import launch
from pitwall.config import PitwallSettings, get_settings
from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import (
    Capability,
    Lease,
    LeaseEndpoints,
    LeaseReadiness,
    Provider,
    WebhookSubscription,
)
from pitwall.cost.budget_gate import BudgetAdmission
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.leases.events import LEASE_EXPIRING, LEASE_READY, LEASE_RENEWED, LEASE_STOPPED
from pitwall.models.prices import GpuPriceSnapshot
from pitwall.reconciler import _lease_expiry_reconcile
from pitwall.routing.production import ProductionRoutingService
from pitwall.runpod_client.graphql import RunpodGpuType
from tests.conftest import _env_for_app, _import_app
from tests.fakes.teardown import UnlockedTeardown

_REPO_ROOT = Path(__file__).parents[2]
pytestmark = pytest.mark.release
_START = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def test_j24_harness_runs_without_database() -> None:
    result = subprocess.run(
        ["bash", "scripts/release/run-user-journeys.sh", "J24"],
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
    assert "PASS  J24" in result.stderr
    assert "hermetic serve automation" in result.stderr


@dataclass
class FrozenClock:
    now: datetime

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


class MemoryPipeline:
    """The queued-read pipeline the reconciler uses to fetch every lease's traffic stamp at once."""

    def __init__(self, redis: MemoryRedis) -> None:
        self._redis = redis
        self._keys: list[str] = []

    async def __aenter__(self) -> MemoryPipeline:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def get(self, key: str) -> MemoryPipeline:
        self._keys.append(key)
        return self

    async def execute(self) -> list[str | None]:
        return [self._redis.values.get(key) for key in self._keys]


class MemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        assert ex == 7 * 24 * 60 * 60
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    def pipeline(self, *, transaction: bool = True) -> MemoryPipeline:
        return MemoryPipeline(self)

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


class MemoryConnection:
    def __init__(self, state: JourneyState) -> None:
        self.state = state

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        lease = self.state.lease
        if lease is None or lease.state != LeaseState.ACTIVE:
            return []
        return [{**lease.model_dump(), "capability_name": self.state.capability.name}]

    async def fetchval(self, query: str, *args: object) -> bool:
        return False

    async def execute(self, query: str, *args: object) -> str:
        assert query == "SELECT pg_advisory_xact_lock($1)"
        return "SELECT 1"

    async def fetchrow(self, query: str, *args: object) -> dict[str, Any]:
        if "FROM pitwall.providers" in query and "FOR UPDATE" in query:
            # Arming and disarming lock the provider row; answer with its current state.
            provider = self.state.provider
            assert args == (provider.id,)
            return {"config": dict(provider.config), "health_status": provider.health_status}
        assert "INSERT INTO pitwall.leases" in query
        endpoints = LeaseEndpoints.model_validate_json(str(args[10]))
        readiness = (
            LeaseReadiness.model_validate_json(str(args[11])) if args[11] is not None else None
        )
        config = self.state.provider.config
        raw_cap = config.get("max_usd_per_hour")
        self.state.lease = Lease(
            id=str(args[0]),
            provider_id=str(args[1]),
            workload_id=str(args[2]) if args[2] is not None else None,
            external_resource_id=str(args[3]) if args[3] is not None else None,
            runpod_pod_id=str(args[4]),
            state=LeaseState(str(args[5])),
            created_at=args[6],
            expires_at=args[7],
            renewal_policy=LeaseRenewalPolicy(str(args[8])),
            auto_teardown_on_expiry=bool(args[9]),
            endpoints=endpoints,
            readiness=readiness,
            idle_timeout_min=int(config["idle_timeout_min"]),
            max_usd_per_hour=Decimal(str(raw_cap)) if raw_cap is not None else None,
        )
        return self.state.lease.model_dump()

    def transaction(self) -> MemoryAcquire:
        return MemoryAcquire(self)


class MemoryAcquire:
    def __init__(self, connection: MemoryConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> MemoryConnection:
        return self.connection

    async def __aexit__(self, *exc: object) -> None:
        return None


class MemoryPool:
    def __init__(self, state: JourneyState) -> None:
        self.connection = MemoryConnection(state)

    def acquire(self) -> MemoryAcquire:
        return MemoryAcquire(self.connection)


@dataclass
class JourneyState:
    capability: Capability
    provider: Provider
    lease: Lease | None
    events: list[dict[str, Any]]
    deliveries: list[tuple[bytes, str]]
    created_pods: list[str]


def _lease(clock: FrozenClock, *, lease_id: str = "lease-j24-1") -> Lease:
    ready = clock.now
    return Lease(
        id=lease_id,
        provider_id="prov-j24",
        runpod_pod_id=f"pod-{lease_id}",
        state=LeaseState.ACTIVE,
        created_at=clock.now - timedelta(minutes=45),
        expires_at=clock.now + timedelta(minutes=14),
        renewal_policy=LeaseRenewalPolicy.ACTIVITY,
        endpoints=LeaseEndpoints(http={"8000": "https://pod-j24.example.invalid"}),
        readiness=LeaseReadiness(
            runtime_seen_at=ready,
            port_mappings_seen_at=ready,
            probe_passed_at=ready,
            probe_method="runpod_proxy",
        ),
        ready_at=ready,
        idle_timeout_min=20,
        max_usd_per_hour=Decimal("2.5000"),
    )


@pytest.mark.anyio
async def test_j24_idle_stop_and_auto_serve_revival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_ENDPOINT_KEY", "test-endpoint-key")
    clock = FrozenClock(_START)
    redis = MemoryRedis()
    capability = Capability(
        id="cap-j24",
        name="llm.j24",
        version="1.0.0",
        class_="llm",
        cost_mode="per_second",
        source="api",
        served_model_id="org/j24",
        created_at=_START,
        updated_at=_START,
    )
    provider = Provider(
        id="prov-j24",
        capability_id=capability.id,
        name="serve-llm.j24",
        provider_type="pod_lease",
        config={
            "active_pod_id": "pod-lease-j24-1",
            "active_lease_id": "lease-j24-1",
            "openai_proxy_port": 8000,
            "gpu_class": "NVIDIA L4",
            "gpu_types": ["NVIDIA L4"],
            "gpu_count": 1,
            "engine": "vllm",
            "image": "example/j24:test",
            "lease_ttl_ms": 3_600_000,
            "cost": {"per_second_active": "0.0005"},
        },
        priority=0,
        enabled=True,
        health_status="healthy",
        updated_at=_START,
    )
    state = JourneyState(capability, provider, _lease(clock), [], [], [])

    class Capabilities:
        async def get_by_name(self, name: str) -> Capability | None:
            return state.capability if name == state.capability.name else None

        async def get(self, capability_id: str) -> Capability | None:
            if capability_id == state.capability.id:
                return state.capability
            return None

        async def patch(self, capability_id: str, **changes: Any) -> Capability:
            state.capability = state.capability.model_copy(update=changes)
            return state.capability

    class Providers:
        async def get_by_name(self, name: str) -> Provider | None:
            return state.provider

        async def get(self, provider_id: str) -> Provider | None:
            return state.provider if provider_id == state.provider.id else None

        async def get_many(self, provider_ids: Iterable[str]) -> dict[str, Provider]:
            return {pid: state.provider for pid in provider_ids if pid == state.provider.id}

        async def list(self, **kwargs: Any) -> list[Provider]:
            return [state.provider]

        async def patch(self, provider_id: str, **changes: Any) -> Provider:
            state.provider = state.provider.model_copy(update=changes)
            return state.provider

    class Leases(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease | None:
            if state.lease is not None and state.lease.id == lease_id:
                return state.lease
            return None

        async def capability_name(self, lease_id: str) -> str | None:
            assert state.lease is not None and lease_id == state.lease.id
            return state.capability.name

        async def latest_active_for_provider(self, provider_id: str) -> Lease | None:
            return state.lease

        async def latest_active_for_capability(self, capability_id: str) -> Lease | None:
            return state.lease

        async def list_active_with_idle_timeout(self) -> list[Lease]:
            return [state.lease] if state.lease is not None else []

        async def list_active_for_activity_control(self) -> list[Lease]:
            return [state.lease] if state.lease is not None else []

        async def record_traffic(self, lease_id: str, *, seen_at: datetime) -> None:
            assert state.lease is not None
            state.lease = state.lease.model_copy(update={"last_traffic_at": seen_at})

        async def renew(self, lease_id: str, **kwargs: Any) -> SimpleNamespace:
            assert state.lease is not None
            # Activity renewal reserves the extension at the provider's settlement rate
            # (RunPod per_second_active) under the budget lock, like an operator renewal.
            extension_usd = kwargs["extension_usd"]
            assert extension_usd == (Decimal("0.0005") * kwargs["extends_minutes"] * 60).quantize(
                Decimal("0.000001")
            )
            await kwargs["budget_check"](MemoryConnection(state), extension_usd)
            renewed = state.lease.model_copy(
                update={
                    "expires_at": state.lease.expires_at
                    + timedelta(minutes=kwargs["extends_minutes"])
                }
            )
            state.lease = renewed
            return SimpleNamespace(lease=renewed)

        async def update_state(self, lease_id: str, value: str) -> Lease:
            assert state.lease is not None
            state.lease = state.lease.model_copy(update={"state": LeaseState(value)})
            return state.lease

        async def update_readiness(self, lease_id: str, readiness: LeaseReadiness) -> None:
            assert state.lease is not None and lease_id == state.lease.id
            state.lease = state.lease.model_copy(update={"readiness": readiness})

        async def mark_ready(self, lease_id: str, *, ready_at: datetime) -> Lease:
            assert state.lease is not None and lease_id == state.lease.id
            state.lease = state.lease.model_copy(update={"ready_at": ready_at})
            return state.lease

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            assert state.lease is not None
            closed = state.lease.model_copy(
                update={
                    "state": LeaseState(changes["state"]),
                    "cost_accrued_usd": changes["cost_accrued_usd"],
                    "terminated_at": changes["terminated_at"],
                    "terminated_reason": changes["terminated_reason"],
                }
            )
            state.lease = None
            return closed

    monkeypatch.setattr(serve, "CapabilityRepository", lambda pool: Capabilities())
    monkeypatch.setattr(serve, "ProviderRepository", lambda pool: Providers())
    monkeypatch.setattr(serve, "LeaseRepository", lambda pool: Leases())

    app_mod = _import_app(_env_for_app())
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    app_mod.app.state.pool = object()
    app_mod.app.state.redis = redis
    app_mod.app.dependency_overrides[_capability_repo] = Capabilities
    app_mod.app.dependency_overrides[_provider_repo] = Providers
    budget_gate = AsyncMock()
    budget_gate.monthly_budget_usd = Decimal("100")
    budget_gate.per_request_max_usd = Decimal("10")
    budget_gate.effective_limits.return_value = BudgetLimits(
        Decimal("100"), Decimal("10"), "environment"
    )
    budget_gate.current_mtd_spend.return_value = Decimal("0")
    app_mod.app.dependency_overrides[_budget_gate] = lambda: budget_gate
    workload_repo = AsyncMock()
    workload_repo.insert.side_effect = lambda workload: workload
    workload_repo.guarded_transition.return_value = object()
    app_mod.app.dependency_overrides[_workload_repo] = lambda: workload_repo
    monkeypatch.setattr(
        app_mod.app.state,
        "production_routing_service",
        ProductionRoutingService(
            app_mod.app.state.pool,
            settings=PitwallSettings(),
            capability_repository=Capabilities(),
            provider_repository=Providers(),
            budget_gate=budget_gate,
        ),
        raising=False,
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return clock.now

    import pitwall.api.routes.openai as openai_routes

    monkeypatch.setattr(
        openai_routes,
        "dt",
        SimpleNamespace(datetime=FrozenDateTime, UTC=UTC),
    )

    with respx.mock:
        respx.post("https://pod-lease-j24-1-8000.proxy.runpod.net/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "pong"}}]},
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.j24/v1/chat/completions",
                json={
                    "model": "org/j24",
                    "messages": [{"role": "user", "content": "ping"}],
                },
            )
    assert response.status_code == 200, response.text
    assert redis.values["pitwall:lease:lease-j24-1:last_traffic_at"] == (
        _START.isoformat().replace("+00:00", "Z")
    )

    import pitwall.api.leases.teardown as teardown
    import pitwall.leases.events as lease_events
    import pitwall.leases.mutations as lease_mutations
    import pitwall.reconciler as reconciler
    import pitwall.webhook_dispatcher.dispatcher as webhook_dispatcher
    import pitwall.webhook_dispatcher.signer as signer

    pool = MemoryPool(state)
    encryption_key = base64.urlsafe_b64encode(bytes(range(32))).decode()
    monkeypatch.setenv(
        "PITWALL_WEBHOOK_ENCRYPTION_KEYS",
        json.dumps({"j24": encryption_key}),
    )
    monkeypatch.setenv("PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY", "j24")
    monkeypatch.setenv("PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST", "127.0.0.1:8765")
    get_settings.cache_clear()

    subscription = WebhookSubscription(
        id="24",
        consumer=state.capability.name,
        webhook_url="http://127.0.0.1:8765/pitwall",
        hmac_secret="j24-signing-secret",
        event_types=[LEASE_READY, LEASE_RENEWED, LEASE_STOPPED, LEASE_EXPIRING],
        created_at=_START,
        updated_at=_START,
    )

    class Subscriptions:
        async def list_for_dispatch(
            self, *, consumer: str, event_type: str
        ) -> list[WebhookSubscription]:
            if consumer == subscription.consumer and event_type in subscription.event_types:
                return [subscription]
            return []

    async def capture_outbound_webhook(
        target: Any,
        body: bytes,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> int:
        assert target.url == subscription.webhook_url
        assert timeout_seconds == 30.0
        signature = headers["X-Pitwall-Signature"]
        assert verify_webhook.verify(body, signature, "j24-signing-secret")
        event = json.loads(body)
        assert headers["X-Pitwall-Delivery-ID"] == event["delivery_id"]
        state.events.append(event)
        state.deliveries.append((body, signature))
        return 204

    monkeypatch.setattr(lease_events, "_webhook_config_warning_logged", False)
    monkeypatch.setattr(
        lease_events,
        "dt",
        SimpleNamespace(datetime=FrozenDateTime, UTC=UTC),
    )
    monkeypatch.setattr(
        lease_events,
        "WebhookSubscriptionRepository",
        lambda _pool, _cipher: Subscriptions(),
    )
    monkeypatch.setattr(
        webhook_dispatcher,
        "post_resolved_webhook",
        capture_outbound_webhook,
    )
    monkeypatch.setattr(
        signer,
        "time",
        SimpleNamespace(time=lambda: clock.now.timestamp()),
    )
    monkeypatch.setattr(
        verify_webhook,
        "time",
        SimpleNamespace(time=lambda: clock.now.timestamp()),
    )
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=Decimal("1.00"),
            ),
        ),
        checked_at=_START,
        source="live",
    )

    monkeypatch.setattr(reconciler, "LeaseRepository", lambda pool: Leases())
    monkeypatch.setattr(reconciler, "ProviderRepository", lambda pool: Providers())
    # Renewal reads the lease's provider to price the extension it reserves.
    monkeypatch.setattr(lease_mutations, "ProviderRepository", lambda pool: Providers())
    monkeypatch.setattr(reconciler, "CapabilityRepository", lambda pool: Capabilities())
    monkeypatch.setattr(
        reconciler,
        "load_gpu_price_snapshot",
        AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(reconciler.CloudKillSwitch, "ensure_disengaged", AsyncMock())
    budget_gate = AsyncMock()
    monkeypatch.setattr(reconciler, "BudgetGate", lambda pool: budget_gate)
    renewal_gate = AsyncMock()
    monkeypatch.setattr(lease_mutations, "BudgetGate", lambda pool: renewal_gate)
    monkeypatch.setattr(teardown, "LeaseRepository", lambda pool: Leases())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda pool: Providers())
    monkeypatch.setattr(teardown, "CapabilityRepository", lambda pool: Capabilities())
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
    monkeypatch.setattr(teardown, "terminate_pod", AsyncMock())
    await _lease_expiry_reconcile(
        {
            "db_pool": pool,
            "redis": redis,
            "now": lambda: clock.now,
        }
    )
    assert state.events[-1]["event"] == LEASE_RENEWED
    assert state.events[-1]["data"]["renewed_by"] == "activity"
    renewal_gate.check_available.assert_awaited_once()

    clock.advance(minutes=21)
    await _lease_expiry_reconcile(
        {
            "db_pool": pool,
            "redis": redis,
            "now": lambda: clock.now,
        }
    )
    assert state.events[-1]["event"] == LEASE_STOPPED
    assert state.events[-1]["data"]["reason"] == "idle"
    assert state.lease is None

    async def fake_admit_lease_launch(*_args: Any, **_kwargs: Any) -> BudgetAdmission:
        return BudgetAdmission(workload_id="wkl-j24-revive", is_new=True)

    async def fake_prepare_lease_launch(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            template=SimpleNamespace(
                template_id="template-j24",
                template_name="j24",
                image_ref="example/j24:test",
            ),
            workload=SimpleNamespace(
                name="j24",
                capability="llm.j24",
                gpu_types=["NVIDIA L4"],
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
        pod_id = "pod-lease-j24-2"
        state.created_pods.append(pod_id)
        callback = kwargs["pre_readiness_callback"]
        await asyncio.to_thread(callback, {"id": pod_id})
        readiness = LeaseReadiness(
            runtime_seen_at=clock.now,
            port_mappings_seen_at=clock.now,
            probe_passed_at=clock.now,
            probe_method="runpod_proxy",
        )
        return {
            "id": pod_id,
            "name": "j24-revived",
            "readiness": readiness.model_dump(mode="json"),
        }

    monkeypatch.setattr(launch, "LeaseRepository", lambda pool: Leases())
    monkeypatch.setattr(launch, "ProviderRepository", lambda pool: Providers())
    monkeypatch.setattr(launch, "insert_audit", AsyncMock())
    monkeypatch.setattr(launch, "admit_lease_launch", fake_admit_lease_launch)
    monkeypatch.setattr(launch, "prepare_lease_launch", fake_prepare_lease_launch)
    monkeypatch.setattr(launch, "_lease_id_for_launch", lambda _provider: "lease-j24-2")
    monkeypatch.setattr(launch, "create_pod_with_fallback", fake_create_pod_with_fallback)
    monkeypatch.setattr(launch, "_create_pod_with_fallback", fake_create_pod_with_fallback)
    monkeypatch.setattr(
        launch,
        "dt",
        SimpleNamespace(datetime=FrozenDateTime, timedelta=timedelta, UTC=UTC),
    )
    monkeypatch.setattr(serve, "load_gpu_price_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(serve.CloudKillSwitch, "ensure_disengaged", AsyncMock())
    with respx.mock:
        revived_models = respx.get("https://pod-lease-j24-2-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "org/j24"}]})
        )
        result = await serve.serve_model(
            pool,
            serve.ServeRequest(
                capability_name="llm.j24",
                model="org/j24",
                gpu_class="NVIDIA L4",
                image="example/j24:test",
                idle_timeout_min=20,
                max_usd_per_hour=Decimal("2.5000"),
                ttl_minutes=60,
            ),
            base_url="http://test",
            settings=PitwallSettings(),
            redis=redis,
        )
    assert result.created is True
    assert result.lease_id == "lease-j24-2"
    assert len(revived_models.calls) == 1
    assert state.created_pods == ["pod-lease-j24-2"]
    assert state.provider.config["active_lease_id"] == "lease-j24-2"
    assert state.provider.config["active_pod_id"] == "pod-lease-j24-2"
    assert state.events[-1]["event"] == LEASE_READY
    assert state.events[-1]["data"]["created"] is True

    with respx.mock:
        revived_chat = respx.post(
            "https://pod-lease-j24-2-8000.proxy.runpod.net/v1/chat/completions"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "revived pong"}}]},
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            revived_response = await client.post(
                "/v1/openai/llm.j24/v1/chat/completions",
                json={
                    "model": "org/j24",
                    "messages": [{"role": "user", "content": "ping again"}],
                },
            )
    assert revived_response.status_code == 200, revived_response.text
    assert revived_response.json()["choices"][0]["message"]["content"] == "revived pong"
    assert len(revived_chat.calls) == 1
    assert redis.values["pitwall:lease:lease-j24-2:last_traffic_at"] == (
        clock.now.isoformat().replace("+00:00", "Z")
    )
    assert [event["event"] for event in state.events] == [
        LEASE_RENEWED,
        LEASE_STOPPED,
        LEASE_READY,
    ]
    assert len(state.deliveries) == len(state.events)
    for event in state.events:
        assert set(event) == {
            "version",
            "event",
            "delivery_id",
            "occurred_at",
            "capability",
            "data",
        }
        assert event["version"] == "1"
        assert event["capability"] == "llm.j24"
        assert event["occurred_at"].endswith("Z")
