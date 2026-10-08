from __future__ import annotations

import json
import os
import subprocess
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import httpx
import pytest
from fastapi import FastAPI

from pitwall import serve
from pitwall.api.routes.openai import openai_proxy
from pitwall.config import PitwallSettings
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider, Workload
from pitwall.reconciler import _health_probe
from tests.api.test_routing_consumer_contract import _ASGIChunkPreservingTransport
from tests.fakes.swapper import FakeClock, FakeSwapperApp, FakeSwapperModel

pytestmark = pytest.mark.release
ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


class RedisEvents:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def publish(self, channel: str, payload: str) -> int:
        assert channel == "pitwall.leases.events"
        self.payloads.append(payload)
        return 1


class Capabilities:
    def __init__(self, registry: JourneyRegistry) -> None:
        self._registry = registry

    async def get_by_name(self, name: str) -> Capability | None:
        return self._registry.capabilities.get(name)


class Providers:
    def __init__(self, registry: JourneyRegistry) -> None:
        self._registry = registry

    async def get_by_name(self, name: str) -> Provider | None:
        return next(
            (provider for provider in self._registry.providers.values() if provider.name == name),
            None,
        )

    async def list(
        self,
        *,
        capability_id: str | None = None,
        enabled_only: bool = False,
        provider_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Provider]:
        del enabled_only, provider_type, limit, offset
        return [
            provider
            for provider in self._registry.providers.values()
            if capability_id is None or provider.capability_id == capability_id
        ]

    async def patch(self, provider_id: str, **changes: Any) -> Provider | None:
        provider = self._registry.providers.get(provider_id)
        if provider is None:
            return None
        patched = provider.model_copy(update=changes)
        self._registry.providers[provider_id] = patched
        return patched


class NoLeases:
    async def latest_active_for_provider(self, provider_id: str) -> None:
        raise AssertionError(f"self-hosted warm queried leases for {provider_id}")


class Workloads:
    async def insert(self, workload: Workload) -> Workload:
        return workload

    async def guarded_transition(
        self,
        workload_id: str,
        *,
        from_states: object,
        to_state: object,
        patch: dict[str, Any] | None = None,
    ) -> object:
        del workload_id, from_states, to_state, patch
        return object()


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


class _Connection:
    def __init__(self, registry: JourneyRegistry) -> None:
        self._registry = registry

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        del query, args
        return [self._registry.probe_row()]

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        del args
        if "FROM pitwall.budget_limits" in query:
            return None  # no runtime limits row: the configured limits apply
        return {"s": 0}

    async def fetchval(self, query: str, *args: object) -> str:
        del query
        return str(args[0])

    async def execute(self, query: str, *args: object) -> str:
        # The probe's health write: a compare-and-set on updated_at (the seventh argument).
        if len(args) == 7 and args[0] in self._registry.providers:
            provider = self._registry.providers[str(args[0])]
            if args[6] is not None and args[6] != provider.updated_at:
                return "UPDATE 0"
            state = json.loads(args[5]) if isinstance(args[5], str) else None
            config = provider.config
            if state is not None:
                config = {**config, "self_hosted_state": state}
            self._registry.providers[provider.id] = provider.model_copy(
                update={
                    "health_status": args[1],
                    "consecutive_failures": args[2],
                    "cooldown_trips": args[3],
                    "cooldown_until": args[4],
                    "config": config,
                    "updated_at": provider.updated_at + timedelta(microseconds=1),
                }
            )
        del query
        return "UPDATE 1"


class _Acquire(AbstractAsyncContextManager[_Connection]):
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback


class Pool:
    def __init__(self, registry: JourneyRegistry) -> None:
        self._connection = _Connection(registry)

    def acquire(self) -> _Acquire:
        return _Acquire(self._connection)


class JourneyRegistry:
    def __init__(
        self,
        capabilities: dict[str, Capability],
        providers: list[Provider],
        upstream_transport: httpx.AsyncBaseTransport,
    ) -> None:
        self.capabilities = capabilities
        self.providers = {provider.id: provider for provider in providers}
        self.upstream_transport = upstream_transport
        self.pool = Pool(self)

    @classmethod
    def with_self_hosted_provider(
        cls,
        upstream_transport: httpx.AsyncBaseTransport,
    ) -> Self:
        capabilities = {
            "llm.selfhosted": _capability("cap_selfhosted", "llm.selfhosted", "<model-id>"),
            "llm.exclusive": _capability("cap_exclusive", "llm.exclusive", "<exclusive-model-id>"),
        }
        provider = Provider(
            id="prov_selfhosted",
            capability_id="cap_selfhosted",
            name="selfhosted",
            credential_ref="PITWALL_TEST_SELFHOSTED_KEY",
            provider_type=ProviderType.PUBLIC_ENDPOINT,
            config={
                "base_url": "http://localhost:9292/v1",
                "openai_base_url": "http://localhost:9292/v1",
                "api_key_env": "PITWALL_TEST_SELFHOSTED_KEY",
                "self_hosted": {
                    "readiness": {"kind": "llama-swap"},
                    "cold_start_timeout_s": 12,
                    "warmup": {"prompt": "ping", "max_tokens": 1},
                    "models": [
                        {"id": "<model-id>", "slot_group": "gpu0"},
                        {"id": "<exclusive-model-id>", "exclusive": True},
                    ],
                },
            },
            priority=0,
            source=CapabilitySource.API,
            updated_at=NOW,
        )
        exclusive_provider = provider.model_copy(
            update={
                "id": "prov-exclusive",
                "capability_id": "cap_exclusive",
                "name": "selfhosted-exclusive",
                "health_status": "healthy",
            }
        )
        return cls(capabilities, [provider, exclusive_provider], upstream_transport)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def capabilities(pool: object) -> Capabilities:
            del pool
            return Capabilities(self)

        def providers(pool: object) -> Providers:
            del pool
            return Providers(self)

        def leases(pool: object) -> NoLeases:
            del pool
            return NoLeases()

        def workloads(pool: object) -> Workloads:
            del pool
            return Workloads()

        monkeypatch.setattr(serve, "CapabilityRepository", capabilities)
        monkeypatch.setattr(serve, "ProviderRepository", providers)
        monkeypatch.setattr(serve, "LeaseRepository", leases)
        monkeypatch.setattr("pitwall.api.routes.openai.CapabilityRepository", capabilities)
        monkeypatch.setattr("pitwall.api.routes.openai.ProviderRepository", providers)
        monkeypatch.setattr("pitwall.api.routes.openai.WorkloadRepository", workloads)
        monkeypatch.setattr(
            "pitwall.api.routes.openai._new_upstream_client",
            lambda timeout: httpx.AsyncClient(
                transport=self.upstream_transport,
                base_url="http://localhost:9292",
                timeout=timeout,
            ),
        )

    def health_status(self) -> str:
        return self.provider().health_status

    def provider(self) -> Provider:
        return self.providers["prov_selfhosted"]

    def probe_row(self) -> dict[str, Any]:
        provider = self.provider()
        row = provider.model_dump(mode="python")
        row["provider_type"] = provider.provider_type.value
        row["source"] = provider.source.value
        row["served_model_id"] = self.capabilities["llm.selfhosted"].served_model_id
        return row

    async def proxy_chat(
        self,
        *,
        client: httpx.AsyncClient,
        capability: str,
        model: str,
    ) -> httpx.Response:
        app = FastAPI()
        app.state.pool = self.pool
        app.state.redis = None
        app.add_api_route(
            "/v1/openai/{capability}/v1/{path:path}",
            openai_proxy,
            methods=["POST"],
        )
        async with httpx.AsyncClient(
            transport=_ASGIChunkPreservingTransport(app),
            base_url="http://test",
        ) as proxy_client:
            response = await proxy_client.post(
                f"/v1/openai/{capability}/v1/chat/completions",
                headers={"Authorization": "Bearer test-bearer"},
                json={"model": model, "messages": [{"role": "user", "content": "ping"}]},
            )
            await response.aread()
        await _health_probe(
            {
                "db_pool": self.pool,
                "settings": PitwallSettings(),
                "http_client": client,
                "now": lambda: NOW,
            }
        )
        return response


def _capability(capability_id: str, name: str, model_id: str) -> Capability:
    return Capability(
        id=capability_id,
        name=name,
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        source=CapabilitySource.API,
        created_at=NOW,
        updated_at=NOW,
        served_model_id=model_id,
    )


def test_j25_harness_runs_without_database() -> None:
    result = subprocess.run(
        ["bash", "scripts/release/run-user-journeys.sh", "J25"],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": "", "REDIS_URL": ""},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS  J25" in result.stderr


@pytest.mark.anyio
async def test_j25_probe_warm_chat_evict_and_rewarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(auto_advance=True)
    swapper = FakeSwapperApp(
        clock=clock,
        api_key="test-bearer",
        catalogue={
            "<model-id>": FakeSwapperModel(id="<model-id>", slot_group="gpu0", load_delay_s=2),
            "<exclusive-model-id>": FakeSwapperModel(
                id="<exclusive-model-id>", exclusive=True, load_delay_s=2
            ),
        },
    )
    transport = httpx.ASGITransport(app=swapper)
    states: list[str] = ["registered"]
    registry = JourneyRegistry.with_self_hosted_provider(transport)
    redis = RedisEvents()
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "test-bearer")
    registry.install(monkeypatch)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost:9292",
    ) as client:
        await _health_probe(
            {
                "db_pool": registry.pool,
                "redis": redis,
                "settings": PitwallSettings(),
                "http_client": client,
                "now": lambda: NOW,
            }
        )
        states.append(registry.health_status())
        first = await serve.serve_model(
            registry.pool,
            serve.ServeRequest(capability_name="llm.selfhosted"),
            base_url="http://test",
            settings=PitwallSettings(),
            redis=redis,
            http_client=client,
        )
        states.append("warmed" if first.created else "resident")
        chat = await registry.proxy_chat(
            client=client,
            capability="llm.selfhosted",
            model="<model-id>",
        )
        assert chat.status_code == 200
        states.append("proxied")
        exclusive = await registry.proxy_chat(
            client=client,
            capability="llm.exclusive",
            model="<exclusive-model-id>",
        )
        assert exclusive.status_code == 200
        # Residency admission lived only in the removed planner; the observable outcome is the
        # swapper's own running set: the exclusive model evicted the first one.
        running = await client.get("/running", headers={"authorization": "Bearer test-bearer"})
        assert [item["model"] for item in running.json()] == ["<exclusive-model-id>"]
        states.append("evicted")
        second = await serve.serve_model(
            registry.pool,
            serve.ServeRequest(capability_name="llm.selfhosted"),
            base_url="http://test",
            settings=PitwallSettings(),
            redis=redis,
            http_client=client,
        )
        assert second.created is True
        states.append("rewarmed")
    assert states == [
        "registered",
        "healthy",
        "warmed",
        "proxied",
        "evicted",
        "rewarmed",
    ]
    events = [json.loads(item) for item in redis.payloads]
    assert [item["data"]["lease_id"] for item in events] == [None, None]
