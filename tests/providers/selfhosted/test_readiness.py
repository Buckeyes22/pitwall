from __future__ import annotations

import asyncio

import httpx
import pytest

from pitwall.providers.selfhosted.profile import ReadinessConfig, SelfHostedProfile
from pitwall.providers.selfhosted.readiness import (
    HttpHealthOracle,
    LlamaSwapOracle,
    OpenAIModelsOracle,
    oracle_for,
)
from tests.fakes.swapper import FakeClock, FakeSwapperApp, FakeSwapperModel

_BASE_URL = "http://localhost:9292/v1"
_HEADERS = {"Authorization": "Bearer <api-key>"}


def _app(*, clock: FakeClock | None = None) -> FakeSwapperApp:
    return FakeSwapperApp(
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=10)},
        api_key="<api-key>",
        clock=clock,
    )


def _client(app: FakeSwapperApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost:9292",
    )


@pytest.mark.anyio
async def test_llama_swap_oracle_observes_starting_then_ready() -> None:
    clock = FakeClock()
    app = _app(clock=clock)
    async with _client(app) as client:
        request = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers=_HEADERS,
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        starting = await LlamaSwapOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id="<model-id>",
        )
        assert starting.state == "starting"
        assert starting.models == {"<model-id>": "starting"}

        clock.advance(10)
        assert (await request).status_code == 200
        ready = await LlamaSwapOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id="<model-id>",
        )
        assert ready.state == "ready"
        assert ready.models == {"<model-id>": "ready"}
        assert ready.latency_ms >= 0


@pytest.mark.anyio
async def test_openai_models_is_absent_for_empty_catalogue() -> None:
    app = FakeSwapperApp(catalogue={}, api_key="<api-key>")
    async with _client(app) as client:
        observation = await OpenAIModelsOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id="<model-id>",
        )
    assert observation.state == "absent"
    assert observation.models == {}


@pytest.mark.anyio
async def test_openai_models_documented_weakness_marks_starting_model_ready() -> None:
    clock = FakeClock()
    app = _app(clock=clock)
    async with _client(app) as client:
        request = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers=_HEADERS,
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        observation = await OpenAIModelsOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id="<model-id>",
        )
        assert observation.state == "ready"
        assert observation.models == {"<model-id>": "ready"}
        clock.advance(10)
        assert (await request).status_code == 200


@pytest.mark.anyio
async def test_http_health_is_process_liveness_without_model_claims() -> None:
    app = _app()
    async with _client(app) as client:
        observation = await HttpHealthOracle("/v1/models").observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id="<model-id>",
        )
    assert observation.state == "ready"
    assert observation.models == {}


@pytest.mark.anyio
async def test_oracles_map_bad_bearer_and_transport_error() -> None:
    app = _app()
    async with _client(app) as client:
        unauthorized = await LlamaSwapOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers={"Authorization": "Bearer <wrong-key>"},
            model_id="<model-id>",
        )
    assert unauthorized.state == "unauthorized"

    async def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("endpoint unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        unreachable = await OpenAIModelsOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id=None,
        )
    assert unreachable.state == "unreachable"


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "<model-id>", "state": "ready"},
        [{"model": "<model-id>", "state": "ready"}, 7],
        [{"model": 7, "state": "ready"}],
        [{"model": "<model-id>", "state": 7}],
    ],
)
@pytest.mark.anyio
async def test_llama_swap_oracle_rejects_malformed_success_payload(payload: object) -> None:
    app = FakeSwapperApp(
        catalogue={},
        api_key="<api-key>",
        malformed_running_payload=payload,
    )
    async with _client(app) as client:
        observation = await LlamaSwapOracle().observe(
            client=client,
            base_url=_BASE_URL,
            headers=_HEADERS,
            model_id="<model-id>",
        )
    assert observation.state == "unreachable"
    assert observation.models == {}


def test_oracle_factory_defaults_and_dispatches_all_profile_kinds() -> None:
    assert isinstance(oracle_for(None), OpenAIModelsOracle)
    assert isinstance(
        oracle_for(SelfHostedProfile(readiness=ReadinessConfig(kind="llama-swap"))),
        LlamaSwapOracle,
    )
    assert isinstance(
        oracle_for(SelfHostedProfile(readiness=ReadinessConfig(kind="openai-models"))),
        OpenAIModelsOracle,
    )
    oracle = oracle_for(
        SelfHostedProfile(readiness=ReadinessConfig(kind="http-health", path="/v1/models"))
    )
    assert isinstance(oracle, HttpHealthOracle)
