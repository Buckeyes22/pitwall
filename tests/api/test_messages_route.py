"""Task 11: ``POST /v1/messages`` route tests.

Non-stream execution is stubbed at ``ProductionRoutingService`` (pattern:
``tests/api/test_inference_contract.py``); streaming goes through the OpenAI
proxy relay with an ``httpx.MockTransport`` upstream (pattern:
``tests/api/test_openai_proxy.py``). Also pins the SPEND scope rule and the
Anthropic error envelope (``QuotaExhausted`` -> 429 ``rate_limit_error``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import importlib
import json
import os
import sys
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType, WorkloadState
from pitwall.core.models import Capability, Provider, Workload
from pitwall.cost.budget_gate import BudgetAdmission, BudgetRejected, BudgetSnapshot
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.db.quota_repository import ModelIdMapping
from pitwall.providers.gateway import QuotaExhausted
from pitwall.routing.production import RouteExecutionResult
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
_PLAN_ID = "plan_0123456789abcdef0123456789abcdef"


def _purge_api_modules() -> None:
    for key in [key for key in sys.modules if key.startswith("pitwall.api")]:
        del sys.modules[key]


@pytest.fixture(autouse=True)
def _clear_app_modules() -> None:
    _purge_api_modules()
    yield
    _purge_api_modules()


def _scoped_env() -> dict[str, str]:
    return {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "PITWALL_API_SCOPED_TOKENS": json.dumps(
            {"read-token": ["read"], "spend-token": ["read", "spend"]}
        ),
    }


def _import_app(env: dict[str, str]) -> Any:
    old = os.environ.copy()
    os.environ.update(env)
    for key in (
        "RUNPOD_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "PITWALL_ADMIN_SECRET",
        "PITWALL_API_TOKEN",
        "PITWALL_INBOUND_RATE_LIMIT",
    ):
        if key not in env:
            os.environ.pop(key, None)
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _capability() -> Capability:
    return Capability(
        id="cap_coding",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="zero",
        source=CapabilitySource.YAML,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(
    *,
    provider_id: str = "prov_gw",
    model_id: str = "glm-4.7-flash",
    base_url: str = "http://127.0.0.1:20130/v1",
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_coding",
        name="gw-beta-b1",
        adapter_id="openai_gateway",
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"mode": "zero"},
            "supports_streaming": True,
            "openai_base_url": base_url,
            "gateway": {
                "base_url": base_url,
                "model_id": model_id,
                "catalog": {"free_type": "keyless", "tos": "ok"},
            },
        },
        priority=50,
        enabled=True,
        health_status="healthy",
        updated_at=_NOW,
    )


class _FakeQuotaRepo:
    def __init__(self, *, model_ids: list[ModelIdMapping] | None = None) -> None:
        self._model_ids = list(model_ids or [])

    async def list_model_ids(self) -> tuple[ModelIdMapping, ...]:
        return tuple(self._model_ids)


class _Plan:
    plan_id = _PLAN_ID
    capability_name = "coding.chat"
    selected_provider_id = "prov_gw"

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "capability_name": self.capability_name,
            "selected_provider_id": self.selected_provider_id,
            "fallback_chain": [self.selected_provider_id],
        }


def _workload() -> Workload:
    return Workload(
        id="wkl_messages",
        capability_id="cap_coding",
        provider_id="prov_gw",
        type="inference",
        state=WorkloadState.COMPLETED,
        submitted_at=_NOW,
        completed_at=_NOW,
        result={"routed": True},
    )


_OPENAI_OK_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1234567890,
    "model": "glm-4.7-flash",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
}


class _RoutingServiceStub:
    def __init__(self, output: Any = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._output = output if output is not None else _OPENAI_OK_RESPONSE
        self._error = error

    async def execute_sync(self, **kwargs: Any) -> RouteExecutionResult:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return RouteExecutionResult(workload=_workload(), plan=_Plan(), output=self._output)


class _Gate:
    """Budget gate double: admits (or rejects) and records every admission call."""

    monthly_budget_usd = Decimal("100")
    per_request_max_usd = Decimal("10")

    def __init__(self, *, reject: BudgetRejected | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._reject = reject

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **kwargs: object) -> BudgetAdmission:
        self.calls.append(kwargs)
        if self._reject is not None:
            raise self._reject
        return BudgetAdmission(workload_id="wkl_messages_stream", is_new=True)


class _WorkloadRepo:
    """Workload repository double recording each state transition."""

    def __init__(self) -> None:
        self.transitions: list[dict[str, Any]] = []
        self.terminal = asyncio.Event()

    async def attach_route_plan(self, *args: object, **kwargs: object) -> None:
        return None

    async def guarded_transition(
        self,
        workload_id: str,
        from_states: set[str],
        to_state: Any,
        *,
        patch: dict[str, Any] | None = None,
    ) -> object:
        await asyncio.sleep(0)
        state = getattr(to_state, "value", to_state)
        self.transitions.append(
            {"workload_id": workload_id, "to_state": state, "patch": patch or {}}
        )
        if state in {"completed", "failed"}:
            self.terminal.set()
        return object()

    def states(self) -> list[str]:
        return [item["to_state"] for item in self.transitions]


def _budget_rejected() -> BudgetRejected:
    return BudgetRejected(
        "monthly_budget",
        BudgetSnapshot(
            monthly_budget_usd=Decimal("1.000000"),
            per_request_max_usd=Decimal("1.000000"),
            mtd_spend_usd=Decimal("1.000000"),
            estimate_usd=Decimal("0.250000"),
            budget_remaining_usd=Decimal("0.000000"),
        ),
    )


def _anthropic_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "coding.chat",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    body.update(overrides)
    return body


def _build_app(
    *,
    quota_repo: _FakeQuotaRepo | None = None,
    capability: Capability | None = None,
    providers: list[Provider] | None = None,
    service: Any = None,
    gate: _Gate | None = None,
    workload_repo: _WorkloadRepo | None = None,
) -> Any:
    mod = _import_app(_scoped_env())
    mock_capability_repo = AsyncMock()
    mock_capability_repo.get_by_name.return_value = (
        capability if capability is not None else _capability()
    )
    mock_provider_repo = AsyncMock()
    mock_provider_repo.list.return_value = providers if providers is not None else [_provider()]
    mock_provider_repo.get = AsyncMock(
        side_effect=lambda provider_id: next(
            (p for p in (providers or []) if p.id == provider_id), None
        )
    )

    from pitwall.api.routes.messages import (
        _capability_repo,
        _provider_repo,
        _routing_service,
    )
    from pitwall.api.routes.openai import _budget_gate, _workload_repo

    mod.app.dependency_overrides[_capability_repo] = lambda: mock_capability_repo
    mod.app.dependency_overrides[_provider_repo] = lambda: mock_provider_repo
    mod.app.state.test_gate = gate if gate is not None else _Gate()
    mod.app.state.test_workload_repo = (
        workload_repo if workload_repo is not None else _WorkloadRepo()
    )
    mod.app.dependency_overrides[_budget_gate] = lambda: mod.app.state.test_gate
    mod.app.dependency_overrides[_workload_repo] = lambda: mod.app.state.test_workload_repo
    mod.app.state.pool = MagicMock()
    mod.app.state.quota_repository = quota_repo if quota_repo is not None else _FakeQuotaRepo()
    if service is not None:
        mod.app.dependency_overrides[_routing_service] = lambda: service
    return mod


async def test_non_stream_executes_through_routing_service_and_returns_anthropic_envelope() -> None:
    service = _RoutingServiceStub()
    mod = _build_app(service=service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(system="be terse", stop_sequences=["END"], temperature=0.2),
            headers={
                "Authorization": "Bearer spend-token",
                "anthropic-version": "2023-06-01",
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["anthropic-version"] == "2023-06-01"
    body = response.json()
    assert body["type"] == "message" and body["role"] == "assistant"
    assert body["model"] == "coding.chat"
    assert body["content"] == [{"type": "text", "text": "ok"}]
    assert body["stop_reason"] == "end_turn"
    assert body["usage"] == {"input_tokens": 2, "output_tokens": 1}
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["capability_id"] == "coding.chat"
    assert call["provider_id"] is None
    payload = call["payload"]
    assert payload["messages"][0] == {"role": "system", "content": "be terse"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}
    assert payload["max_tokens"] == 64 and payload["stop"] == ["END"]


async def test_gw_model_pins_provider_via_model_id_map() -> None:
    service = _RoutingServiceStub()
    quota_repo = _FakeQuotaRepo(
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")]
    )
    mod = _build_app(quota_repo=quota_repo, service=service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(model="gw/glm-flash"),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "gw/glm-flash"
    assert service.calls[0]["capability_id"] == "coding.chat"
    assert service.calls[0]["provider_id"] == "prov_gw"


async def test_unmapped_gw_model_returns_not_found_envelope() -> None:
    mod = _build_app(service=_RoutingServiceStub())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(model="gw/does-not-exist"),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "type": "error",
        "error": {"type": "not_found_error", "message": "model not found: gw/does-not-exist"},
    }


async def test_quota_exhausted_maps_to_rate_limit_error_429() -> None:
    service = _RoutingServiceStub(
        error=QuotaExhausted(
            429, "monthly quota exhausted", reason="quota_exhausted", reset_at=None
        )
    )
    mod = _build_app(service=service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "quota" in body["error"]["message"]


async def test_missing_max_tokens_returns_invalid_request_error_400() -> None:
    mod = _build_app(service=_RoutingServiceStub())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json={"model": "coding.chat", "messages": []},
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "max_tokens" in body["error"]["message"]


async def test_messages_requires_spend_scope() -> None:
    service = _RoutingServiceStub()
    mod = _build_app(service=service)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        read_only = await client.post(
            "/v1/messages",
            json=_anthropic_body(),
            headers={"Authorization": "Bearer read-token"},
        )
        spend = await client.post(
            "/v1/messages",
            json=_anthropic_body(),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert read_only.status_code == 403
    assert read_only.json()["required_scope"] == "spend"
    assert spend.status_code == 200


_UPSTREAM_SSE_CHUNKS = [
    b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n',
    b"data: [DONE]\n\n",
]


async def test_stream_relays_through_proxy_upstream_with_anthropic_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(_UPSTREAM_SSE_CHUNKS),
        )

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler),
        base_url="http://127.0.0.1:20130",
    )
    quota_repo = _FakeQuotaRepo(
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")]
    )
    mod = _build_app(quota_repo=quota_repo)
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(model="gw/glm-flash", stream=True),
            headers={
                "Authorization": "Bearer spend-token",
                "anthropic-version": "2023-06-01",
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["anthropic-version"] == "2023-06-01"
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "glm-4.7-flash"
    events = [
        line.split("event: ", 1)[1].split("\n", 1)[0]
        for line in response.text.split("\n\n")
        if "event: " in line
    ]
    assert events == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert '"text":"he"' in response.text and '"text":"y"' in response.text
    assert '"stop_reason":"end_turn"' in response.text


async def test_stream_upstream_failure_returns_overloaded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dial failed", request=request)

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(failing_handler),
        base_url="http://127.0.0.1:20130",
    )
    mod = _build_app()
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(stream=True),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "overloaded_error"


async def test_stream_by_capability_name_rewrites_model_per_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6: the capability-name form must never forward the capability name upstream."""
    seen_models: list[str] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        seen_models.append(body["model"])
        if body["model"] == "glm-4.7-flash":
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(_UPSTREAM_SSE_CHUNKS),
        )

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler),
        base_url="http://127.0.0.1:20130",
    )
    first = _provider(provider_id="prov_gw", model_id="glm-4.7-flash")
    second = _provider(provider_id="prov_gw2", model_id="qwen/qwen3-free")
    second.priority = 60
    mod = _build_app(providers=[second, first])
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(model="coding.chat", stream=True),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 200, response.text
    assert seen_models == ["glm-4.7-flash", "qwen/qwen3-free"]
    assert "coding.chat" not in seen_models
    assert response.headers["X-Pitwall-Provider-ID"] == "prov_gw2"
    assert '"text":"he"' in response.text


async def test_stream_to_the_fork_names_the_route_and_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(_UPSTREAM_SSE_CHUNKS),
        )

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler),
        base_url="http://127.0.0.1:20130",
    )
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "fork-tok")
    quota_repo = _FakeQuotaRepo(
        model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")]
    )
    mod = _build_app(quota_repo=quota_repo)
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(model="gw/glm-flash", stream=True),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 200, response.text
    assert captured["x-pitwall-route"] == "gw-beta-b1"
    assert captured["authorization"] == "Bearer fork-tok"
    assert captured["x-pitwall-compression"] == "off"


def _counting_upstream(
    monkeypatch: pytest.MonkeyPatch, *, content: bytes | None = None
) -> list[httpx.Request]:
    """Route every upstream call to a mock transport; return the list of calls it saw."""

    seen: list[httpx.Request] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=content if content is not None else b"".join(_UPSTREAM_SSE_CHUNKS),
        )

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler),
        base_url="http://127.0.0.1:20130",
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )
    return seen


async def _post_stream(mod: Any, **overrides: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        return await client.post(
            "/v1/messages",
            json=_anthropic_body(stream=True, **overrides),
            headers={"Authorization": "Bearer spend-token"},
        )


async def test_stream_rejected_by_the_budget_gate_never_reaches_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _Gate(reject=_budget_rejected())
    mod = _build_app(gate=gate)
    seen = _counting_upstream(monkeypatch)

    response = await _post_stream(mod)

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert len(gate.calls) == 1
    assert seen == []


async def test_stream_with_a_credential_shaped_secret_is_rejected_before_egress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _Gate()
    mod = _build_app(gate=gate)
    seen = _counting_upstream(monkeypatch)
    import pitwall.api.routes.messages as messages_route

    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(messages_route, "get_pre_spend_inspection_service", lambda: guardrail)
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    response = await _post_stream(mod, messages=[{"role": "user", "content": f"use {secret}"}])

    assert response.status_code == 400, response.text
    assert response.json()["type"] == "error"
    assert secret not in response.text
    assert gate.calls == []
    assert seen == []


async def test_admitted_stream_records_one_workload_and_settles_it_when_the_stream_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _Gate()
    repo = _WorkloadRepo()
    mod = _build_app(gate=gate, workload_repo=repo)
    seen = _counting_upstream(monkeypatch)

    response = await _post_stream(mod)

    assert response.status_code == 200, response.text
    assert response.headers["X-Pitwall-Workload-ID"] == "wkl_messages_stream"
    assert len(seen) == 1
    assert len(gate.calls) == 1
    assert repo.states() == ["running", "completed"]
    assert repo.transitions[-1]["patch"]["output_bytes"] > 0


async def test_stream_client_disconnect_records_the_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BlockingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.waiting = asyncio.Event()
            self.closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
            self.waiting.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True

    stream = _BlockingStream()
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        ),
        base_url="http://127.0.0.1:20130",
    )
    repo = _WorkloadRepo()
    mod = _build_app(workload_repo=repo)
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )
    body = json.dumps(_anthropic_body(stream=True)).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/messages",
        "raw_path": b"/v1/messages",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer spend-token"),
        ],
        "client": ("test", 1234),
        "server": ("test", 80),
    }
    request_sent = False
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await stream.waiting.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await mod.app(scope, receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200
    assert repo.terminal.is_set()
    assert stream.closed
    assert repo.states() == ["running", "failed"]
    assert repo.transitions[-1]["patch"]["error"]["error"] == "provider_stream_cancelled"


@pytest.mark.parametrize("stream", [False, True])
async def test_unsupported_content_block_is_refused_before_budget_or_provider_work(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    service = _RoutingServiceStub()
    gate = _Gate()
    mod = _build_app(service=service, gate=gate)
    seen = _counting_upstream(monkeypatch)
    image = {"type": "image", "source": {"type": "url", "url": "https://example.test/a.png"}}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json=_anthropic_body(messages=[{"role": "user", "content": [image]}], stream=stream),
            headers={"Authorization": "Bearer spend-token"},
        )

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "image" in body["error"]["message"]
    assert service.calls == [] and gate.calls == [] and seen == []


async def test_stream_upstream_error_frame_reaches_the_client_as_an_error_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _build_app()
    _counting_upstream(
        monkeypatch,
        content=(
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
            b'data: {"error":{"message":"overloaded","type":"overloaded_error"}}\n\n'
        ),
    )

    response = await _post_stream(mod)

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "message_stop" not in response.text


async def test_stream_upstream_429_records_the_cooldown_like_the_openai_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.routing import lockout
    from pitwall.routing.lockout import LockoutTable

    table = LockoutTable()
    monkeypatch.setattr(lockout, "get_lockout_table", lambda: table)

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["model"] == "glm-4.7-flash":
            return httpx.Response(429, headers={"retry-after": "60"})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"".join(_UPSTREAM_SSE_CHUNKS),
        )

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler), base_url="http://127.0.0.1:20130"
    )
    first = _provider(provider_id="prov_gw", model_id="glm-4.7-flash")
    second = _provider(provider_id="prov_gw2", model_id="qwen/qwen3-free")
    second.priority = 60
    mod = _build_app(providers=[first, second])
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client", lambda timeout: upstream_client
    )

    response = await _post_stream(mod)

    assert response.status_code == 200, response.text
    assert response.headers["X-Pitwall-Provider-ID"] == "prov_gw2"
    key = lockout.model_lockout_key(first)
    assert key is not None
    now = dt.datetime.now(dt.UTC)
    assert table.is_locked(key, now=now + dt.timedelta(seconds=30))
    assert not table.is_locked(key, now=now + dt.timedelta(seconds=90))
    second_key = lockout.model_lockout_key(second)
    assert second_key is not None and not table.is_locked(second_key, now=now)


async def test_stream_fallback_budget_expiring_after_a_5xx_relays_it_instead_of_a_closed_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain stops at the deadline holding a retryable 503; it must be returned open."""
    import types

    import pitwall.api.routes.messages as messages_route

    now = [1000.0]
    monkeypatch.setattr(messages_route, "time", types.SimpleNamespace(perf_counter=lambda: now[0]))
    seen: list[str] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["model"])
        now[0] += 10_000.0

        async def body() -> AsyncIterator[bytes]:
            yield b'{"error":"down"}'

        return httpx.Response(503, content=body())

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler), base_url="http://127.0.0.1:20130"
    )
    first = _provider(provider_id="prov_gw", model_id="glm-4.7-flash")
    second = _provider(provider_id="prov_gw2", model_id="qwen/qwen3-free")
    second.priority = 60
    repo = _WorkloadRepo()
    mod = _build_app(providers=[first, second], workload_repo=repo)
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client", lambda timeout: upstream_client
    )

    response = await _post_stream(mod)
    await asyncio.wait_for(repo.terminal.wait(), timeout=5)

    assert seen == ["glm-4.7-flash"]
    assert response.status_code == 503, response.text
    assert "down" in response.text
    terminal = [state for state in repo.states() if state in {"completed", "failed"}]
    assert len(terminal) == 1


_BROKER_CREDENTIAL = "zq9RotatedBrokerCredentialValue0451"


def _credential_upstream(
    monkeypatch: pytest.MonkeyPatch, *, status: int, content_type: str, content: bytes
) -> list[str]:
    """Upstream double returning *content*; the list records each Authorization it received."""

    sent: list[str] = []

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("authorization", ""))
        return httpx.Response(status, headers={"content-type": content_type}, content=content)

    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(upstream_handler), base_url="http://127.0.0.1:20130"
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client", lambda timeout: upstream_client
    )
    return sent


@pytest.mark.parametrize(
    "error_type", ["invalid_request_error", "api_error", "weird_vendor_error", "overloaded_error"]
)
async def test_stream_error_frame_never_reflects_the_brokers_provider_credential(
    monkeypatch: pytest.MonkeyPatch, error_type: str
) -> None:
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", _BROKER_CREDENTIAL)
    error = {"error": {"type": error_type, "message": f"bad key {_BROKER_CREDENTIAL} given"}}
    mod = _build_app()
    sent = _credential_upstream(
        monkeypatch,
        status=200,
        content_type="text/event-stream",
        content=b'data: {"choices":[{"delta":{"content":"he"}}]}\n\ndata: '
        + json.dumps(error).encode()
        + b"\n\n",
    )
    response = await _post_stream(mod)

    assert sent == [f"Bearer {_BROKER_CREDENTIAL}"]
    assert response.status_code == 200
    assert "event: error" in response.text
    assert _BROKER_CREDENTIAL not in response.text


@pytest.mark.parametrize(
    ("status", "content_type", "content", "error_type"),
    [
        (
            401,
            "application/json",
            b'{"error":{"type":"invalid_request_error","message":"Incorrect key: %s"}}',
            "authentication_error",
        ),
        (400, "application/json", b'{"error":{"message":"bad %s"}}', "invalid_request_error"),
        (500, "text/plain", b"upstream exploded with %s", "api_error"),
    ],
)
async def test_non_2xx_upstream_body_is_an_anthropic_envelope_without_the_credential(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content_type: str,
    content: bytes,
    error_type: str,
) -> None:
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", _BROKER_CREDENTIAL)
    repo = _WorkloadRepo()
    mod = _build_app(workload_repo=repo)
    sent = _credential_upstream(
        monkeypatch,
        status=status,
        content_type=content_type,
        content=content.replace(b"%s", _BROKER_CREDENTIAL.encode()),
    )
    response = await _post_stream(mod)

    assert sent == [f"Bearer {_BROKER_CREDENTIAL}"]
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    assert _BROKER_CREDENTIAL not in response.text
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == error_type
    assert response.headers["X-Pitwall-Workload-ID"] == "wkl_messages_stream"
    await asyncio.wait_for(repo.terminal.wait(), timeout=5)
    # An error reply is not a translated stream: it never settles with an in-band reason.
    assert "provider_stream" not in json.dumps(repo.transitions, default=str)


async def test_non_2xx_upstream_error_text_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _build_app()
    _credential_upstream(monkeypatch, status=502, content_type="text/plain", content=b"x" * 200_000)
    response = await _post_stream(mod)

    assert response.status_code == 502
    assert len(response.json()["error"]["message"]) <= 4096


_PARTIAL_TEXT = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'


async def _settled_stream(
    monkeypatch: pytest.MonkeyPatch, content: bytes
) -> tuple[httpx.Response, _WorkloadRepo, MagicMock]:
    from unittest.mock import patch

    repo = _WorkloadRepo()
    trace = MagicMock()
    trace.trace_id = "trace_messages"
    mod = _build_app(workload_repo=repo)
    _counting_upstream(monkeypatch, content=content)
    with patch("pitwall.api.routes.openai.start_inference_trace", return_value=trace):
        response = await _post_stream(mod)
        await asyncio.wait_for(repo.terminal.wait(), timeout=5)
    return response, repo, trace


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            _PARTIAL_TEXT + b'data: {"error":{"type":"overloaded_error","message":"busy"}}\n\n',
            "provider_stream_error",
        ),
        (_PARTIAL_TEXT, "provider_stream_truncated"),
        (
            _PARTIAL_TEXT + b'data: {"choices":[{"delta":{"content":"\xff"}}]}\n\n',
            "provider_stream_malformed",
        ),
    ],
)
async def test_failed_stream_settles_the_workload_failed_with_a_failed_trace(
    monkeypatch: pytest.MonkeyPatch, content: bytes, reason: str
) -> None:
    response, repo, trace = await _settled_stream(monkeypatch, content)

    assert response.status_code == 200
    assert response.text.count("event: error") == 1
    assert "message_stop" not in response.text
    assert repo.states() == ["running", "failed"]
    assert repo.transitions[-1]["patch"]["error"]["error"] == reason
    trace.finish.assert_called_once()
    assert trace.finish.call_args.kwargs["status"] == "error"


async def test_stream_with_a_finish_reason_and_no_done_marker_settles_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response, repo, trace = await _settled_stream(
        monkeypatch, b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n'
    )

    assert "message_stop" in response.text and "event: error" not in response.text
    assert repo.states() == ["running", "completed"]
    assert trace.finish.call_args.kwargs["status"] == "success"
