"""execute_sync_prepared cools down failing providers and backs off between attempts."""

from __future__ import annotations

import pytest
from test_production_routing import _FakeAdapter, _gateway_provider, _provider, _service

from pitwall.core.enums import ProviderAdapterId
from pitwall.core.models import Provider
from pitwall.providers.gateway import GatewayProviderError
from pitwall.providers.registry import ProviderRegistry
from pitwall.routing.production import attempt_backoff_s

pytestmark = pytest.mark.anyio

_PAYLOAD = {"messages": [], "max_output_tokens": 64}


def _two_providers() -> list[Provider]:
    return [
        _gateway_provider("prov_gw", priority=1),
        _provider("provider_together", ProviderAdapterId.TOGETHER, priority=2, latency_ms=20),
    ]


def _registry(first: _FakeAdapter, second: _FakeAdapter) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(first)
    registry.register(second)
    return registry


class _Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("upstream timed out"), GatewayProviderError(503, "unavailable")],
    ids=["timeout", "http_5xx"],
)
async def test_timeout_records_cooldown(failure: Exception) -> None:
    service, _, _ = _service(
        _two_providers(),
        _registry(_FakeAdapter("openai_gateway", sync_exception=failure), _FakeAdapter("together")),
    )
    service._sleep = _Sleeps()

    result = await service.execute_sync(capability_id="llm.chat", payload=_PAYLOAD)

    assert result.workload.provider_id == "provider_together"
    patches = service._providers.patches  # type: ignore[attr-defined]  # reason: the test repository records patches
    assert [provider_id for provider_id, _ in patches] == ["prov_gw"]
    assert patches[0][1]["consecutive_failures"] == 1


async def test_client_error_does_not_cool_the_provider_down() -> None:
    service, _, _ = _service(
        _two_providers(),
        _registry(
            _FakeAdapter("openai_gateway", sync_exception=GatewayProviderError(400, "bad")),
            _FakeAdapter("together"),
        ),
    )
    service._sleep = _Sleeps()

    await service.execute_sync(capability_id="llm.chat", payload=_PAYLOAD)

    assert service._providers.patches == []  # type: ignore[attr-defined]  # reason: the test repository records patches


async def test_backoff_between_attempts() -> None:
    sleeps = _Sleeps()
    service, _, _ = _service(
        _two_providers(),
        _registry(
            _FakeAdapter("openai_gateway", sync_exception=TimeoutError("slow")),
            _FakeAdapter("together"),
        ),
    )
    service._sleep = sleeps

    await service.execute_sync(capability_id="llm.chat", payload=_PAYLOAD)

    assert sleeps.calls == [attempt_backoff_s(1)]
    assert 0 < sleeps.calls[0] <= 2.0


async def test_no_backoff_when_the_first_attempt_succeeds() -> None:
    sleeps = _Sleeps()
    service, _, _ = _service(
        _two_providers(),
        _registry(_FakeAdapter("openai_gateway"), _FakeAdapter("together")),
    )
    service._sleep = sleeps

    await service.execute_sync(capability_id="llm.chat", payload=_PAYLOAD)

    assert sleeps.calls == []


def test_backoff_is_bounded() -> None:
    assert attempt_backoff_s(1) < attempt_backoff_s(2) <= attempt_backoff_s(10) <= 2.0
