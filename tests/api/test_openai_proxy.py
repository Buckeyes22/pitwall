"""SDK smoke test for OpenAI pass-through route.

Verify that the OpenAI SDK (openai>=1.40) can call the Pitwall proxy at
``/v1/openai/{capability}/v1/*`` when OPENAI_BASE_URL is set, using the
FastAPI test client in hermetic mode (no real network, no database).

This is an import-level smoke test — only the SDK's ability to form a
well-formed request and receive a parsed response is asserted.
"""

from __future__ import annotations

import gzip
import importlib
import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from pitwall.core.enums import CapabilityClass, CapabilitySource, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_gate import BudgetAdmission, BudgetRejected, BudgetSnapshot
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.security.pre_spend import PreSpendInspectionService

_TEST_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _make_capability(
    id: str = "cap_llm_qwen3_32b",
    name: str = "llm.qwen3-32b",
) -> Capability:
    return Capability(
        id=id,
        name=name,
        version="1.0.0",
        class_=CapabilityClass.LLM,
        description="Qwen3 32B AWQ",
        cost_mode="per_request",
        source=CapabilitySource.API,
        enabled=True,
        created_at=_TEST_NOW,
        updated_at=_TEST_NOW,
    )


def _make_provider(
    id: str = "prov_qwen3_32b",
    capability_id: str = "cap_llm_qwen3_32b",
    name: str = "qwen3-32b-awq",
    provider_type: ProviderType = ProviderType.SERVERLESS_LB,
    runpod_endpoint_id: str = "qwen3-32b-awq",
    openai_base_url: str | None = None,
    priority: int = 1,
    per_request: str = "0.001000",
) -> Provider:
    config: dict[str, object] = {"per_request": per_request}
    if openai_base_url is not None:
        config["openai_base_url"] = openai_base_url
    else:
        config["openai_base_url"] = f"https://api.runpod.ai/v2/{runpod_endpoint_id}/openai/v1"
    return Provider(
        id=id,
        capability_id=capability_id,
        name=name,
        provider_type=provider_type,
        runpod_endpoint_id=runpod_endpoint_id,
        config=config,
        priority=priority,
        enabled=True,
        health_status="healthy",
        updated_at=_TEST_NOW,
    )


def _env_for_app(**overrides: str) -> dict[str, str]:
    base: dict[str, str] = {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_app_module():
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for k in to_remove:
        del sys.modules[k]
    yield
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for k in to_remove:
        del sys.modules[k]


def _import_app(env: dict[str, str]):
    old = os.environ.copy()
    os.environ.update(env)
    for k in list(os.environ):
        if k not in env and k in (
            "RUNPOD_API_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "PITWALL_ADMIN_SECRET",
            "PITWALL_API_TOKEN",
            "PITWALL_INBOUND_RATE_LIMIT",
        ):
            del os.environ[k]
    try:
        mod = importlib.import_module("pitwall.api.app")
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old)


_CHAT_COMPLETION_RESPONSE = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "created": 1234567890,
    "model": "qwen3-32b-awq",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you today?",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    },
}


class _TrackingAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = tuple(chunks)
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _FailingAsyncByteStream(httpx.AsyncByteStream):
    def __init__(
        self, chunks: list[bytes], fail_after: int, message: str = "connection lost"
    ) -> None:
        self._chunks = tuple(chunks)
        self._fail_after = fail_after
        self._message = message
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for i, chunk in enumerate(self._chunks):
            if i >= self._fail_after:
                raise httpx.ReadError(self._message, request=MagicMock())
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _AdmittingBudgetGate:
    def __init__(self, workload_id: str = "wkl_openai_proxy_test") -> None:
        self.workload_id = workload_id
        self.calls: list[dict[str, object]] = []
        self.monthly_budget_usd = Decimal("100")
        self.per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **kwargs: object) -> BudgetAdmission:
        self.calls.append(kwargs)
        return BudgetAdmission(workload_id=self.workload_id, is_new=True)


class _RejectingBudgetGate:
    def __init__(self, exc: BudgetRejected) -> None:
        self.exc = exc
        self.calls: list[dict[str, object]] = []
        self.monthly_budget_usd = Decimal("100")
        self.per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **kwargs: object) -> BudgetAdmission:
        self.calls.append(kwargs)
        raise self.exc


class _RecordingWorkloadRepo:
    def __init__(self) -> None:
        self.transitions: list[dict[str, object]] = []

    async def guarded_transition(
        self,
        workload_id: str,
        from_states: set[str],
        to_state: object,
        *,
        patch: dict[str, object] | None = None,
    ) -> object:
        self.transitions.append(
            {
                "workload_id": workload_id,
                "from_states": from_states,
                "to_state": getattr(to_state, "value", to_state),
                "patch": patch or {},
            }
        )
        return object()


def _setup_app_with_mocks(
    capability: Capability,
    provider: Provider,
    *,
    budget_gate: object | None = None,
    workload_repo: object | None = None,
):
    mock_capability_repo = AsyncMock()
    mock_capability_repo.get_by_name.return_value = capability

    mock_provider_repo = AsyncMock()
    mock_provider_repo.list.return_value = [provider]

    app_mod = _import_app(_env_for_app())
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    app_mod.app.dependency_overrides[_capability_repo] = lambda: mock_capability_repo
    app_mod.app.dependency_overrides[_provider_repo] = lambda: mock_provider_repo
    gate = budget_gate if budget_gate is not None else _AdmittingBudgetGate()
    app_mod.app.dependency_overrides[_budget_gate] = lambda: gate
    repo = workload_repo if workload_repo is not None else _RecordingWorkloadRepo()
    app_mod.app.dependency_overrides[_workload_repo] = lambda: repo

    mock_pool = MagicMock()
    app_mod.app.state.pool = mock_pool

    return app_mod, mock_capability_repo, mock_provider_repo


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


@pytest.mark.anyio
async def test_openai_guardrail_blocks_before_resolution_budget_and_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _make_capability()
    provider = _make_provider()
    gate = _AdmittingBudgetGate()
    app_mod, capability_repo, provider_repo = _setup_app_with_mocks(
        capability,
        provider,
        budget_gate=gate,
    )
    import pitwall.api.routes.openai as openai_route

    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(openai_route, "get_pre_spend_inspection_service", lambda: guardrail)

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post(
            "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [
                        {
                            "role": "user",
                            "content": "use sk-abcdefghijklmnopqrstuvwxyz123456",
                        }
                    ],
                },
            )

    assert response.status_code == 422
    assert response.json()["error"] == "pre_spend_payload_rejected"
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in response.text
    capability_repo.get_by_name.assert_not_awaited()
    provider_repo.list.assert_not_awaited()
    assert gate.calls == []
    assert not upstream_call.called
    assert guardrail.status().counters.block == 1
    app_mod.app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("query", "headers"),
    [
        ("?max_tokens=sk-query-canary-1234567890abcdef", None),
        ("", {"OpenAI-Organization": "sk-header-canary-1234567890abcdef"}),
        ("?unrecognized=value", None),
    ],
)
@pytest.mark.anyio
async def test_openai_metadata_guard_blocks_before_resolution_budget_and_upstream(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    headers: dict[str, str] | None,
) -> None:
    capability = _make_capability()
    provider = _make_provider()
    gate = _AdmittingBudgetGate()
    app_mod, capability_repo, provider_repo = _setup_app_with_mocks(
        capability,
        provider,
        budget_gate=gate,
    )
    import pitwall.api.routes.openai as openai_route

    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(openai_route, "get_pre_spend_inspection_service", lambda: guardrail)
    canary = "sk-metadata-response-canary-1234567890abcdef"

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post(
            "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/v1/openai/llm.qwen3-32b/v1/chat/completions{query}",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                headers=headers,
            )

    assert response.status_code == 422
    assert response.json()["error"] == "pre_spend_payload_rejected"
    assert canary not in response.text
    assert "sk-query-canary" not in response.text
    assert "sk-header-canary" not in response.text
    capability_repo.get_by_name.assert_not_awaited()
    provider_repo.list.assert_not_awaited()
    assert gate.calls == []
    assert not upstream_call.called
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_openai_guardrail_redacts_before_budget_and_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _make_capability()
    provider = _make_provider()
    gate = _AdmittingBudgetGate()
    app_mod, _, _ = _setup_app_with_mocks(capability, provider, budget_gate=gate)
    import pitwall.api.routes.openai as openai_route

    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(openai_route, "get_pre_spend_inspection_service", lambda: guardrail)
    received_body = b""

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal received_body
        received_body = request.content
        return httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE)

    with respx.mock:
        respx.post("https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions").mock(
            side_effect=upstream
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [{"role": "user", "content": "contact ada.lovelace@example.com"}],
                },
            )

    assert response.status_code == 200
    assert b"ada.lovelace@example.com" not in received_body
    assert b"[REDACTED:email]" in received_body
    assert len(gate.calls) == 1
    assert guardrail.status().counters.redact == 1
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_openai_proxy_budget_rejection_returns_402_before_upstream_send() -> None:
    capability = _make_capability()
    provider = _make_provider(
        openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1",
        per_request="0.250000",
    )
    gate = _RejectingBudgetGate(_budget_rejected())
    app_mod, _, _ = _setup_app_with_mocks(capability, provider, budget_gate=gate)

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post(
            "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [{"role": "user", "content": "Hello!"}],
                },
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 402
    assert response.json() == _budget_rejected().to_response_body()
    assert not upstream_call.called
    assert len(gate.calls) == 1
    admission_kwargs = gate.calls[0]
    assert admission_kwargs["capability_id"] == capability.id
    assert admission_kwargs["provider_id"] == provider.id
    assert admission_kwargs["workload_type"] == "openai_passthrough"
    assert admission_kwargs["estimate_usd"].upper_bound() == Decimal("0.250000")

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_budget_rejection_does_not_mark_nonresident_provider_warming() -> None:
    capability = _make_capability()
    base_provider = _make_provider(
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        openai_base_url="http://localhost:9292/v1",
        per_request="0.250000",
    )
    provider = base_provider.model_copy(
        update={
            "config": {
                **base_provider.config,
                "api_key_env": "PITWALL_SELFHOSTED_API_KEY_ENV",
                "self_hosted": {
                    "readiness": {"kind": "llama-swap"},
                    "cold_start_timeout_s": 600,
                },
                "self_hosted_state": {"resident": []},
            }
        }
    )
    gate = _RejectingBudgetGate(_budget_rejected())
    app_mod, _, provider_repo = _setup_app_with_mocks(
        capability,
        provider,
        budget_gate=gate,
    )

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post("http://localhost:9292/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={"model": "cold-model", "messages": []},
            )

    assert response.status_code == 402
    assert not upstream_call.called
    provider_repo.patch.assert_not_awaited()
    assert provider.health_status == "healthy"
    assert provider.consecutive_failures == 0
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_provider_health_repository_failure_still_relays_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability = _make_capability()
    provider = _make_provider(openai_base_url="http://localhost:9292/v1")
    app_mod, _, provider_repo = _setup_app_with_mocks(capability, provider)
    provider_repo.patch.side_effect = RuntimeError("provider telemetry unavailable")
    stream = _TrackingAsyncByteStream([b'{"result":"ok"}'])
    upstream_response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=stream,
    )
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: upstream_response),
        base_url="http://localhost:9292",
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/openai/llm.qwen3-32b/v1/chat/completions",
            json={"model": "qwen3-32b-awq", "messages": []},
        )

    assert response.status_code == 200
    assert response.content == b'{"result":"ok"}'
    assert stream.closed
    assert upstream_response.is_closed
    assert upstream_client.is_closed
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_saturation_redis_failure_still_relays_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingRedis:
        async def zremrangebyscore(self, *args: object) -> int:
            del args
            raise RuntimeError("redis telemetry unavailable")

    capability = _make_capability()
    provider = _make_provider(openai_base_url="http://localhost:9292/v1")
    app_mod, _, _ = _setup_app_with_mocks(capability, provider)
    app_mod.app.state.redis = FailingRedis()
    stream = _TrackingAsyncByteStream([b'{"error":"plain bad request"}'])
    upstream_response = httpx.Response(
        400,
        headers={"content-type": "application/json"},
        stream=stream,
    )
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: upstream_response),
        base_url="http://localhost:9292",
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/openai/llm.qwen3-32b/v1/chat/completions",
            json={"model": "qwen3-32b-awq", "messages": []},
        )

    assert response.status_code == 400
    assert response.content == b'{"error":"plain bad request"}'
    assert stream.closed
    assert upstream_response.is_closed
    assert upstream_client.is_closed
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_redis_failure_still_relays_200_body_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingRedis:
        async def set(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("redis telemetry unavailable")

    capability = _make_capability()
    base_provider = _make_provider(openai_base_url="http://localhost:9292/v1")
    provider = base_provider.model_copy(
        update={"config": {**base_provider.config, "active_lease_id": "lease-placeholder"}}
    )
    app_mod, _, _ = _setup_app_with_mocks(capability, provider)
    app_mod.app.state.redis = FailingRedis()
    stream = _TrackingAsyncByteStream([b'{"result":"ok"}'])
    upstream_response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        stream=stream,
    )
    upstream_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: upstream_response),
        base_url="http://localhost:9292",
    )
    monkeypatch.setattr(
        "pitwall.api.routes.openai._new_upstream_client",
        lambda timeout: upstream_client,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/openai/llm.qwen3-32b/v1/chat/completions",
            json={"model": "qwen3-32b-awq", "messages": []},
        )

    assert response.status_code == 200
    assert response.content == b'{"result":"ok"}'
    assert stream.closed
    assert upstream_response.is_closed
    assert upstream_client.is_closed
    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_openai_proxy_upstream_failure_terminally_fails_admitted_workload() -> None:
    capability = _make_capability()
    provider = _make_provider(
        openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1",
        per_request="0.250000",
    )
    gate = _AdmittingBudgetGate(workload_id="wkl_admitted_proxy")
    workload_repo = _RecordingWorkloadRepo()
    app_mod, _, _ = _setup_app_with_mocks(
        capability,
        provider,
        budget_gate=gate,
        workload_repo=workload_repo,
    )

    def upstream_failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dial failed", request=request)

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post(
            "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
        ).mock(side_effect=upstream_failure)
        with patch("pitwall.api.routes.openai.emit_inference_trace"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_mod.app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                    json={
                        "model": "qwen3-32b-awq",
                        "messages": [{"role": "user", "content": "Hello!"}],
                    },
                    headers={"Content-Type": "application/json"},
                )

    assert response.status_code == 503
    assert upstream_call.called
    assert len(gate.calls) == 1
    assert gate.calls[0]["workload_type"] == "openai_passthrough"
    assert [transition["to_state"] for transition in workload_repo.transitions] == [
        "running",
        "failed",
    ]
    assert {transition["workload_id"] for transition in workload_repo.transitions} == {
        "wkl_admitted_proxy"
    }
    failed_patch = workload_repo.transitions[-1]["patch"]
    assert failed_patch["fallback_chain"] == [provider.id]
    assert failed_patch["error"] == {
        "error": "provider_transport_error",
        "attempted_providers": [provider.id],
        "attempted_failures": {provider.id: "transport_error"},
    }
    assert "dial failed" not in str(failed_patch)

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_openai_proxy_relay_closes_upstream_response_and_client() -> None:
    from pitwall.api.routes.openai import _relay_upstream_bytes

    stream = _TrackingAsyncByteStream(
        [
            b'data: {"delta":"hello"}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    upstream_response = httpx.Response(200, stream=stream)
    client = httpx.AsyncClient()

    chunks: list[bytes] = []
    async for chunk in _relay_upstream_bytes(upstream_response, client):
        chunks.append(chunk)

    assert b"".join(chunks) == b'data: {"delta":"hello"}\n\ndata: [DONE]\n\n'
    assert stream.closed
    assert upstream_response.is_closed
    assert client.is_closed


@pytest.mark.anyio
async def test_relay_emits_safe_sse_error_without_logging_exception_detail(caplog) -> None:
    from pitwall.api.routes.openai import _relay_upstream_bytes

    stream = _FailingAsyncByteStream(
        [
            b'data: {"delta":"hello"}\n\n',
            b'data: {"delta":"world"}\n\n',
        ],
        fail_after=1,
        message="relay-credential-canary",
    )
    upstream_response = httpx.Response(
        200,
        stream=stream,
        headers={"content-type": "text/event-stream"},
    )
    client = httpx.AsyncClient()

    chunks: list[bytes] = []
    async for chunk in _relay_upstream_bytes(upstream_response, client):
        chunks.append(chunk)

    data = b"".join(chunks)
    assert b'data: {"delta":"hello"}\n\n' in data
    assert b'"error"' in data
    assert b"upstream stream failure" in data
    assert stream.closed
    assert upstream_response.is_closed
    assert client.is_closed
    assert "relay-credential-canary" not in caplog.text


@pytest.mark.anyio
async def test_relay_no_sse_error_for_non_sse_response() -> None:
    from pitwall.api.routes.openai import _relay_upstream_bytes

    stream = _FailingAsyncByteStream(
        [b'{"partial":'],
        fail_after=1,
    )
    upstream_response = httpx.Response(
        200,
        stream=stream,
        headers={"content-type": "application/json"},
    )
    client = httpx.AsyncClient()

    chunks: list[bytes] = []
    async for chunk in _relay_upstream_bytes(upstream_response, client):
        chunks.append(chunk)

    data = b"".join(chunks)
    assert data == b'{"partial":'
    assert b'"error"' not in data
    assert stream.closed
    assert upstream_response.is_closed
    assert client.is_closed


@pytest.mark.anyio
async def test_streaming_sse_with_usage_frame_passes_through() -> None:
    from pitwall.api.routes.openai import _relay_upstream_bytes

    stream = _TrackingAsyncByteStream(
        [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    upstream_response = httpx.Response(
        200,
        stream=stream,
        headers={"content-type": "text/event-stream"},
    )
    client = httpx.AsyncClient()

    chunks: list[bytes] = []
    async for chunk in _relay_upstream_bytes(upstream_response, client):
        chunks.append(chunk)

    data = b"".join(chunks)
    assert b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n' in data
    assert (
        b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
        in data
    )
    assert b"data: [DONE]\n\n" in data
    assert stream.closed
    assert upstream_response.is_closed
    assert client.is_closed


@pytest.mark.anyio
async def test_relay_cleans_up_when_consumer_exits_early() -> None:
    import asyncio

    from pitwall.api.routes.openai import _relay_upstream_bytes

    stream = _TrackingAsyncByteStream(
        [
            b'data: {"delta":"chunk1"}\n\n',
            b'data: {"delta":"chunk2"}\n\n',
            b'data: {"delta":"chunk3"}\n\n',
            b"data: [DONE]\n\n",
        ]
    )
    upstream_response = httpx.Response(
        200,
        stream=stream,
        headers={"content-type": "text/event-stream"},
    )
    client = httpx.AsyncClient()

    received_chunks: list[bytes] = []

    async def consume_with_timeout():
        nonlocal received_chunks
        try:
            async with asyncio.timeout(0.01):
                async for chunk in _relay_upstream_bytes(upstream_response, client):
                    received_chunks.append(chunk)
        except TimeoutError:
            pass

    await consume_with_timeout()

    assert len(received_chunks) >= 1
    assert stream.closed
    assert upstream_response.is_closed
    assert client.is_closed


@respx.mock
@pytest.mark.anyio
async def test_openai_proxy_chat_completions_with_sdk_request_shape():
    """OpenAI SDK-shaped request to /v1/openai/{cap}/v1/chat/completions succeeds.

    This test proves OPENAI_BASE_URL compatibility by sending a request that
    mimics exactly what the OpenAI SDK would send: proper Content-Type,
    correct JSON schema for chat completions, and correct route path.
    """
    capability = _make_capability()
    provider = _make_provider(openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1")

    app_mod, _, _ = _setup_app_with_mocks(capability, provider)

    upstream_url = "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
    respx.post(upstream_url).mock(return_value=httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE))

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Hello!"},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 100,
                },
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "chatcmpl-123"
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "Hello! How can I help you today?"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 10
    assert body["usage"]["completion_tokens"] == 20
    assert body["usage"]["total_tokens"] == 30

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_openai_proxy_models_endpoint():
    """OpenAI SDK can call /v1/openai/{cap}/v1/models via proxy."""
    capability = _make_capability()
    provider = _make_provider(openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1")

    app_mod, _, _ = _setup_app_with_mocks(capability, provider)

    upstream_url = "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/models"
    models_response = {
        "object": "list",
        "data": [
            {
                "id": "qwen3-32b-awq",
                "object": "model",
                "created": 1234567890,
                "owned_by": "pitwall",
            }
        ],
    }
    respx.get(upstream_url).mock(
        return_value=httpx.Response(
            200,
            content=gzip.compress(json.dumps(models_response).encode("utf-8")),
            headers={"content-encoding": "gzip", "x-upstream": "models"},
        )
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/v1/openai/llm.qwen3-32b/v1/models",
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None
    assert response.headers["x-upstream"] == "models"
    body = response.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == "qwen3-32b-awq"

    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_openai_proxy_stream_drops_decoded_content_encoding_header() -> None:
    capability = _make_capability()
    provider = _make_provider(openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1")
    provider = provider.model_copy(
        update={"config": {**provider.config, "supports_streaming": True}}
    )
    app_mod, _, _ = _setup_app_with_mocks(capability, provider)
    upstream_url = "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
    respx.post(upstream_url).mock(
        return_value=httpx.Response(
            200,
            content=gzip.compress(b'data: {"choices":[]}\n\ndata: [DONE]\n\n'),
            headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
        )
    )

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={"model": "qwen3-32b-awq", "stream": True, "messages": []},
            )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None
    assert response.text.startswith('data: {"choices":[]}')
    assert "data: [DONE]" in response.text
    app_mod.app.dependency_overrides.clear()


@respx.mock
@pytest.mark.anyio
async def test_openai_proxy_forwards_headers_correctly(monkeypatch: pytest.MonkeyPatch):
    """OpenAI SDK headers (including custom ones) are forwarded to upstream."""
    # The upstream credential is resolved per request, so pin it for the request as well.
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    capability = _make_capability()
    provider = _make_provider(openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1")

    app_mod, _, _ = _setup_app_with_mocks(capability, provider)

    upstream_url = "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"

    async def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        content_type = request.headers.get("Content-Type", "")
        openai_model = request.headers.get("OpenAI-Organization", "")
        session_id = request.headers.get("x-session-id")
        session_affinity = request.headers.get("x-session-affinity")
        return httpx.Response(
            200,
            json={
                **{
                    "id": "chatcmpl-456",
                    "object": "chat.completion",
                    "created": 1234567890,
                    "model": "qwen3-32b-awq",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Header test passed",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 3,
                        "total_tokens": 8,
                    },
                },
                "_received_headers": {
                    "authorization": auth,
                    "content_type": content_type,
                    "openai_organization": openai_model,
                    "session_id": session_id,
                    "session_affinity": session_affinity,
                },
            },
        )

    respx.post(upstream_url).mock(side_effect=handler)

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [{"role": "user", "content": "Test"}],
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer sk-test-key",
                    "OpenAI-Organization": "my-org",
                    "x-session-id": "ses-local-only",
                    "x-session-affinity": "ses-local-only",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Header test passed"
    received = body["_received_headers"]
    assert received["authorization"] == "Bearer test-key"
    assert received["session_id"] is None
    assert received["session_affinity"] is None

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_openai_proxy_returns_404_for_unknown_capability():
    """Unknown capability name returns CapabilityNotFound (404)."""
    mock_capability_repo = AsyncMock()
    mock_capability_repo.get_by_name.return_value = None
    mock_capability_repo.get.return_value = None

    mock_provider_repo = AsyncMock()

    app_mod = _import_app(_env_for_app())
    from pitwall.api.routes.openai import _capability_repo, _provider_repo

    app_mod.app.dependency_overrides[_capability_repo] = lambda: mock_capability_repo
    app_mod.app.dependency_overrides[_provider_repo] = lambda: mock_provider_repo

    mock_pool = MagicMock()
    app_mod.app.state.pool = mock_pool

    with patch("pitwall.api.routes.openai.emit_inference_trace"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/nonexistent.capability/v1/chat/completions",
                json={
                    "model": "something",
                    "messages": [{"role": "user", "content": "Hello!"}],
                },
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 404
    body = response.json()
    assert "capability" in body.get("error", "").lower()

    app_mod.app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_openai_proxy_budget_exhausted_at_planning_returns_402_not_503() -> None:
    capability = _make_capability()
    provider = _make_provider(
        openai_base_url="https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1",
        per_request="0.250000",
    )
    gate = _AdmittingBudgetGate()
    gate.monthly_budget_usd = Decimal("0.000001")
    app_mod, _, _ = _setup_app_with_mocks(capability, provider, budget_gate=gate)

    with respx.mock(assert_all_called=False) as router:
        upstream_call = router.post(
            "https://api.runpod.ai/v2/qwen3-32b-awq/openai/v1/chat/completions"
        ).mock(return_value=httpx.Response(200, json=_CHAT_COMPLETION_RESPONSE))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/v1/openai/llm.qwen3-32b/v1/chat/completions",
                json={
                    "model": "qwen3-32b-awq",
                    "messages": [{"role": "user", "content": "Hello!"}],
                },
                headers={"Content-Type": "application/json"},
            )

    assert response.status_code == 402
    assert response.json()["error"] == "budget_rejected"
    assert response.json()["reason"] == "monthly_budget"
    assert not upstream_call.called
    assert gate.calls == []

    app_mod.app.dependency_overrides.clear()
