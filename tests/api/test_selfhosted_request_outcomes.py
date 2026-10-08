from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from pitwall.api.routes.openai import classify_upstream_outcome, upstream_timeout
from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_gate import BudgetAdmission
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.resolver.exceptions import NoHealthyProviderError
from pitwall.resolver.service import select_stage12_provider
from pitwall.routing import RoutingRequest
from tests.fakes.swapper import FakeSwapperApp, FakeSwapperModel

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
PATH = "/v1/openai/llm.local/v1/chat/completions"


def capability() -> Capability:
    return Capability(
        id="cap-local",
        name="llm.local",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="per_request",
        source=CapabilitySource.API,
        created_at=NOW,
        updated_at=NOW,
    )


def provider() -> Provider:
    return Provider(
        id="prov-selfhosted",
        capability_id="cap-local",
        name="self-hosted-endpoint",
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config={
            "openai_base_url": "http://localhost:9292/v1",
            "api_key_env": "PITWALL_SELFHOSTED_API_KEY_ENV",
            "per_request": "0.001",
            "self_hosted": {
                "readiness": {"kind": "llama-swap"},
                "cold_start_timeout_s": 600,
                "models": [{"id": "<model-id>", "tool_calling": "disabled"}],
            },
            "self_hosted_state": {"resident": ["<model-id>"]},
        },
        priority=0,
        health_status="healthy",
        updated_at=NOW,
    )


class Gate:
    monthly_budget_usd = Decimal("100")
    per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **kwargs: object) -> BudgetAdmission:
        del kwargs
        return BudgetAdmission(workload_id="wkl-local", is_new=True)


class RedisWindow:
    def __init__(self) -> None:
        self.members: dict[str, dict[str, float]] = {}

    async def zadd(self, key: str, values: dict[str, float]) -> int:
        bucket = self.members.setdefault(key, {})
        before = len(bucket)
        bucket.update(values)
        return len(bucket) - before

    async def zremrangebyscore(self, key: str, minimum: float, maximum: float) -> int:
        bucket = self.members.setdefault(key, {})
        removed = [name for name, score in bucket.items() if minimum <= score <= maximum]
        for name in removed:
            del bucket[name]
        return len(removed)

    async def zcard(self, key: str) -> int:
        return len(self.members.setdefault(key, {}))

    async def zrange(
        self,
        key: str,
        start: int,
        stop: int,
        *,
        withscores: bool,
    ) -> list[tuple[str, float]]:
        assert start == 0
        assert stop == -1
        assert withscores
        return sorted(self.members.setdefault(key, {}).items(), key=lambda item: item[1])

    async def expire(self, key: str, seconds: int) -> bool:
        del key, seconds
        return True


@pytest.fixture
def proxy_app(monkeypatch: pytest.MonkeyPatch):
    from pitwall.api import app as app_module
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    monkeypatch.setenv("RUNPOD_API_KEY", "test-bearer")
    current = provider()
    capability_repo = AsyncMock()
    capability_repo.get_by_name.return_value = capability()
    provider_repo = AsyncMock()

    def resolved_providers(**_: object) -> list[Provider]:
        try:
            resolution = select_stage12_provider(
                RoutingRequest(capability_name="llm.local", capability_id="cap-local"),
                [current],
                capability=capability(),
                now=NOW,
            )
        except NoHealthyProviderError:
            return []
        return list(resolution.eligible_providers)

    provider_repo.list.side_effect = resolved_providers

    async def patch_provider(provider_id: str, **fields: object) -> Provider:
        nonlocal current
        assert provider_id == current.id
        current = current.model_copy(update=fields)
        return current

    provider_repo.patch.side_effect = patch_provider
    workload_repo = AsyncMock()
    app_module.app.dependency_overrides[_capability_repo] = lambda: capability_repo
    app_module.app.dependency_overrides[_provider_repo] = lambda: provider_repo
    app_module.app.dependency_overrides[_workload_repo] = lambda: workload_repo
    app_module.app.dependency_overrides[_budget_gate] = Gate
    app_module.app.state.pool = object()
    app_module.app.state.redis = None
    yield SimpleNamespace(app=app_module.app, repo=provider_repo, current=lambda: current)
    app_module.app.dependency_overrides.clear()


def upstream_client(fake: FakeSwapperApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake),
        base_url="http://localhost:9292",
    )


@pytest.mark.parametrize(
    ("status", "exc", "well_formed", "response_body", "expected"),
    [
        (200, None, True, b"", "healthy"),
        (503, None, True, b"", "failure"),
        (None, httpx.ReadTimeout("late"), True, b"", "failure"),
        (401, None, True, b"", "misconfigured"),
        (403, None, True, b"", "misconfigured"),
        (429, None, True, b'{"error":"concurrency limit"}', "capacity"),
        (400, None, True, b'{"error":"plain bad request"}', "client_error"),
        (
            400,
            None,
            True,
            b'{"error":"\\"AUTO\\" TOOL CHOICE REQUIRES --tool-call-parser"}',
            "misconfigured",
        ),
        (400, None, False, b'{"error":"--tool-call-parser"}', "client_error"),
    ],
)
def test_classify_upstream_outcome(
    status: int | None,
    exc: BaseException | None,
    well_formed: bool,
    response_body: bytes,
    expected: str,
) -> None:
    assert (
        classify_upstream_outcome(
            status_code=status,
            exc=exc,
            request_was_well_formed=well_formed,
            response_body=response_body,
        )
        == expected
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("model", "status"),
    [
        (
            FakeSwapperModel(
                id="<model-id>", response_status=413, response_body={"error": "large"}
            ),
            413,
        ),
        (
            FakeSwapperModel(
                id="<model-id>", response_status=422, response_body={"error": "invalid"}
            ),
            422,
        ),
        (
            FakeSwapperModel(
                id="<model-id>",
                response_status=400,
                response_body={"error": "plain bad request"},
            ),
            400,
        ),
    ],
)
async def test_real_proxy_client_4xx_preserves_provider_and_real_resolver_selection(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    model: FakeSwapperModel,
    status: int,
) -> None:
    initial = proxy_app.current()
    fake = FakeSwapperApp(catalogue={model.id: model}, api_key="test-bearer")
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client(fake),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH,
            headers={"authorization": "Bearer test-bearer"},
            json={"model": model.id, "messages": []},
        )

    assert response.status_code == status
    assert proxy_app.current() == initial
    resolved = select_stage12_provider(
        RoutingRequest(capability_name="llm.local", capability_id="cap-local"),
        [proxy_app.current()],
        capability=capability(),
        now=NOW,
    )
    assert resolved.selected_provider_id == initial.id


@pytest.mark.anyio
async def test_real_proxy_unknown_model_404_preserves_provider_and_resolver_selection(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = proxy_app.current()
    known = FakeSwapperModel(id="known-model")
    fake = FakeSwapperApp(catalogue={known.id: known}, api_key="test-bearer")
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client(fake),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH,
            headers={"authorization": "Bearer test-bearer"},
            json={"model": "<model-id>", "messages": []},
        )

    assert response.status_code == 404
    assert proxy_app.current() == initial
    resolved = select_stage12_provider(
        RoutingRequest(capability_name="llm.local", capability_id="cap-local"),
        [proxy_app.current()],
        capability=capability(),
        now=NOW,
    )
    assert resolved.selected_provider_id == initial.id


@pytest.mark.anyio
async def test_real_proxy_classifies_5xx_and_2xx(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeSwapperModel(id="<model-id>", fail_start_after_s=0)
    fake = FakeSwapperApp(catalogue={model.id: model}, api_key="test-bearer")
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client(fake),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        for _ in range(2):
            response = await client.post(
                PATH,
                headers={"authorization": "Bearer test-bearer"},
                json={"model": "<model-id>", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert response.status_code == 503
    assert proxy_app.current().health_status == "healthy"
    assert proxy_app.current().consecutive_failures == 2

    fake.configure_edit(FakeSwapperModel(id="<model-id>"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH,
            headers={"authorization": "Bearer test-bearer"},
            json={"model": "<model-id>", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert response.status_code == 200
    assert proxy_app.current().health_status == "healthy"
    assert proxy_app.current().consecutive_failures == 0

    fake.configure_edit(FakeSwapperModel(id="<model-id>", fail_start_after_s=0))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app),
        base_url="http://localhost:8000",
    ) as client:
        for _ in range(3):
            response = await client.post(
                PATH,
                headers={"authorization": "Bearer test-bearer"},
                json={"model": "<model-id>", "messages": []},
            )
            assert response.status_code == 503
    assert proxy_app.current().health_status == "unhealthy"
    assert proxy_app.current().consecutive_failures == 3


@pytest.mark.anyio
async def test_real_proxy_timeout_counts_toward_cooldown(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("upstream timeout", request=request)

    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: httpx.AsyncClient(transport=TimeoutTransport(), timeout=timeout),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app),
        base_url="http://localhost:8000",
    ) as client:
        for _ in range(3):
            response = await client.post(
                PATH,
                headers={"authorization": "Bearer test-bearer"},
                json={"model": "<model-id>", "messages": []},
            )
            assert response.status_code == 503

    assert proxy_app.current().health_status == "unhealthy"
    assert proxy_app.current().consecutive_failures == 3
    assert proxy_app.current().cooldown_until is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("bearer", "payload", "status"),
    [
        ("bad-bearer", {"model": "<model-id>", "messages": []}, 401),
        (
            "test-bearer",
            {
                "model": "<model-id>",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "ping"}}],
                "tool_choice": "auto",
            },
            400,
        ),
    ],
)
async def test_real_proxy_marks_deterministic_4xx_misconfigured_without_cooldown(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    bearer: str,
    payload: dict[str, object],
    status: int,
) -> None:
    model = FakeSwapperModel(id="<model-id>", tool_calling=False)
    fake = FakeSwapperApp(catalogue={model.id: model}, api_key="test-bearer")
    monkeypatch.setenv("RUNPOD_API_KEY", bearer)
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client(fake),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH, headers={"authorization": f"Bearer {bearer}"}, json=payload
        )
    assert response.status_code == status
    assert proxy_app.current().health_status == "misconfigured"
    assert proxy_app.current().cooldown_until is None
    assert proxy_app.current().consecutive_failures == 0


@pytest.mark.anyio
async def test_real_proxy_429_is_capacity_and_preserves_provider(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = proxy_app.current()
    model = FakeSwapperModel(id="<model-id>", response_status=429, response_body={"error": "busy"})
    fake = FakeSwapperApp(catalogue={model.id: model}, api_key="test-bearer")
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client(fake),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH,
            headers={"authorization": "Bearer test-bearer"},
            json={"model": model.id, "messages": []},
        )

    assert response.status_code == 429
    current = proxy_app.current()
    # Capacity is separate from health: 429 may set only the concurrency marker.
    assert current.health_status == initial.health_status
    assert current.consecutive_failures == initial.consecutive_failures
    assert current.cooldown_until == initial.cooldown_until
    assert current.cooldown_trips == initial.cooldown_trips
    assert current.config["self_hosted_state"]["concurrency_limited"] is True


@pytest.mark.anyio
async def test_real_proxy_4xx_storm_names_consumer_and_leaves_provider_healthy(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    initial = proxy_app.current()
    model = FakeSwapperModel(
        id="<model-id>",
        response_status=400,
        response_body={"error": "plain consumer request error"},
    )
    fake = FakeSwapperApp(catalogue={model.id: model}, api_key="steady-consumer")
    monkeypatch.setenv("RUNPOD_API_KEY", "steady-consumer")
    proxy_app.app.state.redis = RedisWindow()
    monkeypatch.setenv("PITWALL_SATURATION_WINDOW_S", "60")
    monkeypatch.setenv("PITWALL_SATURATION_4XX_THRESHOLD", "4")
    caplog.set_level(logging.WARNING, logger="pitwall.api.routes.openai")
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client(fake),
    )
    payload = {
        "model": "<model-id>",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "ping"}}],
        "tool_choice": "auto",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        for _ in range(4):
            response = await client.post(
                PATH,
                headers={"authorization": "Bearer steady-consumer"},
                json=payload,
            )
            assert response.status_code == 400

    assert proxy_app.current() == initial
    proxy_app.repo.patch.assert_not_awaited()
    key = "pitwall:saturation:prov-selfhosted:sha256:46af993273db:llm.local"
    assert len(proxy_app.app.state.redis.members[key]) == 4
    signal_record = next(
        record for record in caplog.records if record.message == "consumer saturation detected"
    )
    assert signal_record.token_fingerprint == "sha256:46af993273db"
    assert signal_record.capability_name == "llm.local"
    assert signal_record.rate_per_s == pytest.approx(4 / 60)


def test_upstream_timeout_uses_profile_only_for_non_resident_model() -> None:
    cold = provider()
    cold = cold.model_copy(
        update={
            "config": {
                **cold.config,
                "self_hosted": {
                    **cold.config["self_hosted"],
                    "cold_start_timeout_s": 600,
                },
                "self_hosted_state": {"resident": []},
            }
        }
    )

    assert upstream_timeout(cold, model_id="<model-id>", resident=False).read == 600
    assert upstream_timeout(cold, model_id="<model-id>", resident=True).read == 330

    ordinary = cold.model_copy(update={"config": {"per_request": "0.001"}})
    assert upstream_timeout(ordinary, model_id="<model-id>", resident=False).read == 330


@pytest.mark.anyio
async def test_first_request_longer_than_global_timeout_uses_profile_bound(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeSwapperModel(id="cold-model", load_delay_s=400)
    fake = FakeSwapperApp(
        catalogue={model.id: model},
        api_key="test-bearer",
        auto_advance=True,
    )
    observed_read_timeouts: list[float] = []

    def client_for_timeout(timeout: httpx.Timeout) -> httpx.AsyncClient:
        observed_read_timeouts.append(float(timeout.read))
        return upstream_client(fake)

    monkeypatch.setattr("pitwall.api.routes.openai._new_upstream_client", client_for_timeout)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH,
            headers={"authorization": "Bearer test-bearer"},
            json={"model": "cold-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert observed_read_timeouts == [600.0]
    assert proxy_app.current().health_status == "healthy"


@pytest.mark.anyio
async def test_warming_that_outlives_bound_records_exactly_one_failure(
    proxy_app: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("cold start exceeded", request=request)

    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: httpx.AsyncClient(transport=TimeoutTransport(), timeout=timeout),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxy_app.app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post(
            PATH,
            headers={"authorization": "Bearer test-bearer"},
            json={"model": "cold-model", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 503
    assert proxy_app.current().health_status == "warming"
    assert proxy_app.current().consecutive_failures == 1
    assert proxy_app.current().cooldown_until is None
