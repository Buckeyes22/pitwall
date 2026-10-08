from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from pitwall import serve
from pitwall.api.exceptions import ServeWarmFailed
from pitwall.config import PitwallSettings
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.providers.selfhosted.profile import self_hosted_profile
from pitwall.providers.selfhosted.readiness import ReadinessObservation, oracle_for
from tests.fakes.swapper import FakeClock, FakeSwapperApp, FakeSwapperModel

NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


class RedisEvents:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def publish(self, channel: str, payload: str) -> int:
        assert channel == "pitwall.leases.events"
        self.payloads.append(payload)
        return 1


def capability() -> Capability:
    return Capability(
        id="cap_selfhosted",
        name="llm.selfhosted",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        source=CapabilitySource.API,
        created_at=NOW,
        updated_at=NOW,
        served_model_id="<model-id>",
    )


def provider() -> Provider:
    return Provider(
        id="prov_selfhosted",
        capability_id="cap_selfhosted",
        name="selfhosted",
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config={
            "base_url": "http://localhost:9292/v1",
            "api_key_env": "PITWALL_TEST_SELFHOSTED_KEY",
            "self_hosted": {
                "readiness": {"kind": "llama-swap"},
                "cold_start_timeout_s": 12,
                "warmup": {"prompt": "ping", "max_tokens": 1},
                "models": [{"id": "<model-id>"}],
            },
        },
        priority=0,
        source=CapabilitySource.API,
        updated_at=NOW,
    )


class Capabilities:
    async def get_by_name(self, name: str) -> Capability | None:
        return capability() if name == "llm.selfhosted" else None


class Providers:
    def __init__(self, current: Provider | None = None) -> None:
        self.current = current or provider()
        self.patches: list[dict[str, Any]] = []

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
        return [self.current] if capability_id == "cap_selfhosted" else []

    async def patch(self, provider_id: str, **changes: Any) -> Provider | None:
        if provider_id != self.current.id:
            return None
        self.patches.append(changes)
        self.current = self.current.model_copy(update=changes)
        return self.current


class NoLeases:
    async def latest_active_for_provider(self, provider_id: str) -> None:
        raise AssertionError(f"self-hosted warm queried leases for {provider_id}")


async def run_serve(
    monkeypatch: pytest.MonkeyPatch,
    swapper: FakeSwapperApp,
    redis: RedisEvents,
    provider_repo: Providers | None = None,
) -> serve.ServeResult:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "test-bearer")
    monkeypatch.setattr(serve, "CapabilityRepository", lambda pool: Capabilities())
    providers = provider_repo or Providers()
    monkeypatch.setattr(serve, "ProviderRepository", lambda pool: providers)
    monkeypatch.setattr(serve, "LeaseRepository", lambda pool: NoLeases())
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=swapper),
        base_url="http://localhost:9292",
    )
    try:
        result = await serve.serve_model(
            object(),
            serve.ServeRequest(capability_name="llm.selfhosted"),
            base_url="http://test",
            settings=PitwallSettings(),
            redis=redis,
            http_client=client,
        )
        assert isinstance(result, serve.ServeResult)
        return result
    finally:
        await client.aclose()


@pytest.mark.anyio
async def test_cold_then_resident_warm_publishes_nullable_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = FakeSwapperApp(
        clock=FakeClock(auto_advance=True),
        api_key="test-bearer",
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=4)},
    )
    redis = RedisEvents()

    cold = await run_serve(monkeypatch, swapper, redis)
    profile = self_hosted_profile(provider())
    assert profile is not None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=swapper),
        base_url="http://localhost:9292",
    ) as client:
        observed = await oracle_for(profile).observe(
            client=client,
            base_url="http://localhost:9292/v1",
            headers={"Authorization": "Bearer test-bearer"},
            model_id="<model-id>",
        )
    assert observed.state == "ready"
    assert cold.model_dump()["provider_kind"] == "self_hosted"
    assert cold.lease_id is None and cold.created is True
    event = json.loads(redis.payloads[-1])
    assert event["event"] == "lease.ready"
    assert event["data"]["lease_id"] is None
    assert event["data"]["served_model_id"] == "<model-id>"

    resident = await run_serve(monkeypatch, swapper, redis)
    assert resident.created is False and resident.lease_id is None


@pytest.mark.anyio
async def test_warm_timeout_has_pinned_503_body(monkeypatch: pytest.MonkeyPatch) -> None:
    swapper = FakeSwapperApp(
        clock=FakeClock(),
        api_key="test-bearer",
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=13)},
    )
    with pytest.raises(ServeWarmFailed) as caught:
        await run_serve(monkeypatch, swapper, RedisEvents())
    assert caught.value.status_code == 503
    assert caught.value.to_response_body() == {
        "error": "warm_failed",
        "provider_id": "prov_selfhosted",
        "model_id": "<model-id>",
        "state": "starting",
    }


@pytest.mark.anyio
async def test_cold_warm_persists_elapsed_time_and_probed_tool_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = FakeSwapperApp(
        clock=FakeClock(auto_advance=True),
        api_key="test-bearer",
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=4)},
    )
    providers = Providers()

    await run_serve(monkeypatch, swapper, RedisEvents(), providers)

    state = providers.current.config["self_hosted_state"]
    assert isinstance(state, dict)
    assert state["cold_start_s"]["p50"] > 0
    assert state["cold_start_s"]["p95"] == state["cold_start_s"]["p50"]
    assert state["tool_calling"] == "enabled"
    cold_start = state["cold_start_s"]

    await run_serve(monkeypatch, swapper, RedisEvents(), providers)

    resident_state = providers.current.config["self_hosted_state"]
    assert isinstance(resident_state, dict)
    assert resident_state["cold_start_s"] == cold_start
    assert resident_state["tool_calling"] == "enabled"


@pytest.mark.anyio
async def test_http_health_created_is_unknown_but_llama_swap_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    http_provider = provider().model_copy(
        update={
            "config": {
                **provider().config,
                "self_hosted": {
                    **provider().config["self_hosted"],
                    "readiness": {"kind": "http-health", "path": "/health"},
                },
            }
        }
    )
    providers = Providers(http_provider)
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "test-bearer")
    monkeypatch.setattr(serve, "CapabilityRepository", lambda pool: Capabilities())
    monkeypatch.setattr(serve, "ProviderRepository", lambda pool: providers)
    monkeypatch.setattr(serve, "LeaseRepository", lambda pool: NoLeases())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://localhost:9292"
    ) as client:
        result = await serve.serve_model(
            object(),
            serve.ServeRequest(capability_name="llm.selfhosted"),
            base_url="http://test",
            settings=PitwallSettings(),
            redis=RedisEvents(),
            http_client=client,
        )

    assert isinstance(result, serve.ServeResult)
    assert result.created is None


@pytest.mark.anyio
async def test_timeout_diagnostic_oracle_is_independently_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingOracle:
        def __init__(self) -> None:
            self.calls = 0

        async def observe(self, **kwargs: object) -> ReadinessObservation:
            del kwargs
            self.calls += 1
            if self.calls == 1:
                return ReadinessObservation(
                    state="starting", models={}, observed_at=NOW, latency_ms=0
                )
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    profile = self_hosted_profile(provider())
    assert profile is not None
    profile = profile.model_copy(update={"cold_start_timeout_s": 0.01})
    monkeypatch.setattr(serve, "oracle_for", lambda candidate: HangingOracle())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ServeWarmFailed) as caught:
            await asyncio.wait_for(
                serve.warm_self_hosted(
                    provider=provider(),
                    profile=profile,
                    capability=capability(),
                    client=client,
                    settings=PitwallSettings(),
                ),
                timeout=0.25,
            )

    assert caught.value.status_code == 503
