"""The OpenAI proxy gates Token Plan automation and bounds reasoning for Model Studio."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.providers.model_studio.catalog import provider_settings, rewrite_proxy_body
from pitwall.routing.fallback import (
    DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    OpenAIProxyExecutionError,
    OpenAIProxyRequest,
    execute_openai_with_fallback,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def _provider() -> Provider:
    return Provider(
        id="prov_ms",
        capability_id="cap_ms",
        name="prov_ms",
        adapter_id=ProviderAdapterId.MODEL_STUDIO,
        credential_ref="MODEL_STUDIO_API_KEY",
        provider_type=ProviderType.MODEL_STUDIO,
        config={
            "model_studio": {
                "plan": "token-plan-personal",
                "tier": "pro",
                "model": "qwen3.8-flash",
            },
            "openai_base_url": URL,
        },
        priority=1,
        enabled=True,
        health_status="healthy",
        updated_at=NOW,
    )


def _request(client: httpx.AsyncClient) -> OpenAIProxyRequest:
    return OpenAIProxyRequest(
        method="POST",
        path="chat/completions",
        headers={"content-type": "application/json", "authorization": "Bearer caller-token"},
        body=json.dumps(
            {"model": "qwen", "stream": False, "max_tokens": 50, "messages": []}
        ).encode(),
        client=client,
        fallback_budget_s=DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    )


def test_rewrite_moves_max_tokens_and_requests_usage() -> None:
    settings = provider_settings(
        {"model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "qwen3.8-flash"}}
    )
    body = json.loads(
        rewrite_proxy_body(
            json.dumps(
                {"model": "gw/anything", "stream": True, "max_tokens": 50, "messages": []}
            ).encode(),
            settings,
        )
    )
    assert body == {
        "model": "qwen3.8-flash",
        "stream": True,
        "max_completion_tokens": 50,
        "messages": [],
        "stream_options": {"include_usage": True},
    }


def test_rewrite_keeps_max_tokens_for_reasoning_inclusive_models_and_non_json() -> None:
    settings = provider_settings(
        {"model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "glm-5.3"}}
    )
    body = json.loads(rewrite_proxy_body(b'{"max_tokens": 50, "messages": []}', settings))
    assert body["max_tokens"] == 50 and "max_completion_tokens" not in body
    assert rewrite_proxy_body(b"not json", settings) == b"not json"


@respx.mock
@pytest.mark.anyio
async def test_gate_closed_provider_is_skipped_with_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_STUDIO_TOKEN_PLAN_AUTOMATION", raising=False)
    route = respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(OpenAIProxyExecutionError) as caught:
            await execute_openai_with_fallback(_request(client), [_provider()])
    assert "automation_not_accepted" in caught.value.attempted_errors["prov_ms"]
    assert not route.called


@respx.mock
@pytest.mark.anyio
async def test_accepted_provider_receives_the_rewritten_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_STUDIO_TOKEN_PLAN_AUTOMATION", "accept")
    monkeypatch.setenv("MODEL_STUDIO_API_KEY", "sk-sp-test")
    route = respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "ok"})
    )
    async with httpx.AsyncClient() as client:
        result = await execute_openai_with_fallback(_request(client), [_provider()])
    assert result.response.status_code == 200
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-sp-test"
    body = json.loads(sent.content)
    assert (body["model"], body["max_completion_tokens"]) == (
        "qwen3.8-flash",
        50,
    ) and "max_tokens" not in body
