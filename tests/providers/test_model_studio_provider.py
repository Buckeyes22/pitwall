"""ModelStudioProvider: streaming, usage, ceilings, gates, and 429 classification."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider as ProviderRecord
from pitwall.providers.errors import ProviderQuotaExhausted
from pitwall.providers.gateway import QuotaExhausted
from pitwall.providers.interface import (
    AvailabilityRequest,
    CredentialReference,
    InferenceRequest,
    ProviderOperationContext,
)
from pitwall.providers.model_studio.adapter import (
    ModelStudioProvider,
    ModelStudioProviderError,
    ModelStudioQuotaExhausted,
    build_chat_body,
)
from pitwall.providers.model_studio.catalog import (
    ModelStudioConfigError,
    check_model,
    provider_settings,
)

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
ACCEPT = {"MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept"}


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_STUDIO_API_KEY", "sk-sp-test")


def _record(**model_studio: Any) -> ProviderRecord:
    settings = {
        "plan": "token-plan-personal",
        "tier": "pro",
        "model": "qwen3.8-flash",
        "renews_on": "2026-09-12",
        **model_studio,
    }
    return ProviderRecord(
        id="prov_ms",
        capability_id="cap_ms",
        name="ms",
        adapter_id=ProviderAdapterId.MODEL_STUDIO,
        credential_ref="MODEL_STUDIO_API_KEY",
        provider_type=ProviderType.MODEL_STUDIO,
        config={"model_studio": settings, "cost": {"kind": "zero"}},
        priority=50,
        updated_at=NOW,
    )


def _sse(*chunks: dict[str, Any]) -> bytes:
    return (
        b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks)
        + b"data: [DONE]\n\n"
    )


def _provider(handler: Any, environ: dict[str, str] | None = None) -> ModelStudioProvider:
    return ModelStudioProvider(
        transport=httpx.MockTransport(handler),
        environ=ACCEPT if environ is None else environ,
        now=lambda: NOW,
    )


async def _infer(
    provider: ModelStudioProvider, payload: dict[str, Any], record: ProviderRecord | None = None
) -> Any:
    return await provider.infer(
        InferenceRequest(
            context=ProviderOperationContext(pool=None, now=NOW),
            capability=None,  # type: ignore[arg-type]  # reason: the adapter never reads it
            provider_record=record or _record(),
            credentials=CredentialReference(name="MODEL_STUDIO_API_KEY"),
            payload=payload,
        )
    )


@pytest.mark.anyio
async def test_streams_with_usage_and_sends_the_ceiling_as_max_completion_tokens() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(
                {
                    "model": "qwen3.8-flash",
                    "choices": [{"index": 0, "delta": {"reasoning_content": "think"}}],
                },
                {"model": "qwen3.8-flash", "choices": [{"index": 0, "delta": {"content": "Hel"}}]},
                {
                    "model": "qwen3.8-flash",
                    "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}],
                },
                {
                    "model": "qwen3.8-flash",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 7,
                        "total_tokens": 17,
                        "completion_tokens_details": {"reasoning_tokens": 5},
                    },
                },
            ),
            headers={"content-type": "text/event-stream"},
        )

    result = await _infer(
        _provider(handler), {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 900}
    )
    assert (
        seen["url"]
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert seen["auth"] == "Bearer sk-sp-test"
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {
        "include_usage": True
    }
    assert seen["body"]["max_completion_tokens"] == 900 and "max_tokens" not in seen["body"]
    assert (
        result.content,
        result.prompt_tokens,
        result.completion_tokens,
        result.total_tokens,
    ) == ("Hello", 10, 7, 17)
    assert result.raw["usage"]["prompt_tokens"] == 10


@pytest.mark.anyio
async def test_stream_without_usage_reports_unavailable_usage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "stop"}]}
            ),
        )

    result = await _infer(_provider(handler), {"messages": [], "max_tokens": 10})
    assert result.prompt_tokens is None and "usage" not in result.raw


def test_models_whose_max_tokens_include_reasoning_keep_max_tokens() -> None:
    settings = provider_settings(
        {
            "model_studio": {
                "plan": "token-plan-personal",
                "tier": "pro",
                "model": "deepseek-v4.1-flash",
            }
        }
    )
    body = build_chat_body(
        {"messages": []}, settings, check_model("token-plan-personal", "deepseek-v4.1-flash")
    )
    assert body["max_tokens"] == 393216 and "max_completion_tokens" not in body


def test_reasoning_effort_with_thinking_budget_is_refused_on_qwen38() -> None:
    settings = provider_settings(
        {"model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "qwen3.8-max"}}
    )
    record = check_model("token-plan-personal", "qwen3.8-max")
    with pytest.raises(ModelStudioConfigError, match="thinking_budget"):
        build_chat_body(
            {"messages": [], "reasoning_effort": "low", "thinking_budget": 100}, settings, record
        )
    with pytest.raises(ModelStudioConfigError, match="xhigh, medium, low"):
        build_chat_body({"messages": [], "reasoning_effort": "max"}, settings, record)


@pytest.mark.anyio
async def test_automation_gate_refuses_before_any_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may leave without acceptance")

    with pytest.raises(ModelStudioConfigError, match="automation_not_accepted"):
        await _infer(_provider(handler, {}), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_pay_as_you_go_key_on_token_plan_is_refused_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_STUDIO_API_KEY", "sk-leaky")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request")

    with pytest.raises(ModelStudioConfigError) as caught:
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
    assert caught.value.code == "key_plan_mismatch" and "leaky" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "reason", "reset"),
    [
        (
            429,
            {"error": {"code": "insufficient_quota", "message": "Allocated quota exceeded"}},
            "rate_limit_exceeded",
            NOW + dt.timedelta(seconds=60),
        ),
        (
            429,
            {"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded"},
            "rate_limit_exceeded",
            NOW + dt.timedelta(seconds=60),
        ),
        (
            429,
            {
                "error": {
                    "code": "insufficient_quota",
                    "message": "Your token-plan quota has been exhausted.",
                }
            },
            "quota_exhausted",
            dt.datetime(2026, 10, 12, tzinfo=dt.UTC),
        ),
        (
            400,
            {
                "code": "Arrearage",
                "message": "Access denied, please make sure your account is in good standing.",
            },
            "billing_state",
            NOW + dt.timedelta(hours=1),
        ),
    ],
)
async def test_errors_map_to_typed_lockouts(
    status: int, body: dict[str, Any], reason: str, reset: dt.datetime
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    with pytest.raises(ModelStudioQuotaExhausted) as caught:
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
    assert (caught.value.reason, caught.value.reset_at) == (reason, reset)


@pytest.mark.anyio
async def test_401_is_an_endpoint_pairing_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"code": "InvalidApiKey", "message": "Invalid API-key provided."}
        )

    with pytest.raises(ModelStudioProviderError, match="invalid_endpoint_pairing"):
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_availability_reads_the_openai_compatible_model_list() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # The Token Plan host answers GET /api/v1/models with 404 (live check, 2026-09-26);
        # the OpenAI-compatible list is the one it serves.
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "qwen3.8-max", "object": "model"},
                    {"id": "qwen3.8-flash", "object": "model"},
                ],
            },
        )

    result = await _provider(handler).availability(
        AvailabilityRequest(
            context=ProviderOperationContext(pool=None, now=NOW),
            provider_record=_record(),
            credentials=CredentialReference(name="MODEL_STUDIO_API_KEY"),
        )
    )
    assert (
        seen["url"]
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/models"
    )
    assert seen["auth"] == "Bearer sk-sp-test"
    assert [item.resource_id for item in result.items] == ["qwen3.8-flash"]
    assert result.observed_at == NOW


def _availability_request() -> AvailabilityRequest:
    return AvailabilityRequest(
        context=ProviderOperationContext(pool=None, now=NOW),
        provider_record=_record(),
        credentials=CredentialReference(name="MODEL_STUDIO_API_KEY"),
    )


@pytest.mark.anyio
async def test_availability_respects_automation_gate() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may leave without acceptance")

    with pytest.raises(ModelStudioConfigError, match="automation_not_accepted"):
        await _provider(handler, {}).availability(_availability_request())


@pytest.mark.anyio
async def test_availability_malformed_json_raises_typed_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    with pytest.raises(ModelStudioProviderError, match="malformed_response"):
        await _provider(handler).availability(_availability_request())


@pytest.mark.anyio
async def test_availability_non_object_json_raises_typed_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["qwen3.8-flash"])

    with pytest.raises(ModelStudioProviderError, match="malformed_response"):
        await _provider(handler).availability(_availability_request())


@pytest.mark.anyio
async def test_malformed_chunk_raises_typed_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: {not json\n\ndata: [DONE]\n\n")

    with pytest.raises(ModelStudioProviderError, match="malformed_stream"):
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_sse_error_chunk_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse({"error": {"code": "internal_error", "message": "upstream exploded"}}),
        )

    with pytest.raises(ModelStudioProviderError, match="stream_error"):
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_sse_rate_limit_error_chunk_is_a_quota_signal() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"error": {"code": "insufficient_quota", "message": "Allocated quota exceeded"}}
            ),
        )

    with pytest.raises(ModelStudioQuotaExhausted):
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_non_sse_200_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    with pytest.raises(ModelStudioProviderError, match="non_sse_response"):
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_retry_after_honoured() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "17"},
            json={"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded"},
        )

    with pytest.raises(ModelStudioQuotaExhausted) as caught:
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
    assert caught.value.reset_at == NOW + dt.timedelta(seconds=17)


@pytest.mark.anyio
async def test_quota_error_is_model_studio_type() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded"}
        )

    with pytest.raises(ProviderQuotaExhausted) as caught:
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
    assert isinstance(caught.value, ModelStudioQuotaExhausted)
    assert isinstance(caught.value, ModelStudioProviderError)
    assert not isinstance(caught.value, QuotaExhausted)
    assert issubclass(QuotaExhausted, ProviderQuotaExhausted)
