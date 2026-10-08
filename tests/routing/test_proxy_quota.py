"""The OpenAI proxy path fails over on 429 and records a lockout, like the adapter path."""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
import respx

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.routing import lockout
from pitwall.routing.fallback import (
    OpenAIProxyExecutionError,
    OpenAIProxyRequest,
    execute_openai_with_fallback,
)
from pitwall.routing.lockout import LockoutKey, LockoutTable

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)


def _provider(provider_id: str, host: str, *, model_id: str = "beta/b1") -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_chat",
        name=provider_id,
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=1,
        updated_at=NOW,
        enabled=True,
        health_status="healthy",
        config={
            "openai_base_url": f"https://{host}/v1",
            "gateway": {"base_url": f"https://{host}/v1", "model_id": model_id},
        },
    )


def _ctx(client: httpx.AsyncClient) -> OpenAIProxyRequest:
    return OpenAIProxyRequest(
        method="POST",
        path="chat/completions",
        headers={"content-type": "application/json"},
        body=b'{"model":"m","messages":[]}',
        client=client,
    )


@respx.mock
@pytest.mark.anyio
async def test_429_fails_over_to_next_provider() -> None:
    first = respx.post("https://a.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"}, json={"error": "slow"})
    )
    second = respx.post("https://b.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "ok"})
    )
    async with httpx.AsyncClient() as client:
        result = await execute_openai_with_fallback(
            _ctx(client), [_provider("a", "a.example.com"), _provider("b", "b.example.com")]
        )
    assert first.called and second.called
    assert result.provider_id == "b"
    assert result.attempted_provider_ids == ("a", "b")
    assert [(item.provider_id, item.status_code) for item in result.rate_limited] == [("a", 429)]
    assert result.rate_limited[0].headers["retry-after"] == "30"


@respx.mock
@pytest.mark.anyio
async def test_429_on_the_last_provider_is_returned_and_reported() -> None:
    respx.post("https://a.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"error": "slow"})
    )
    async with httpx.AsyncClient() as client:
        result = await execute_openai_with_fallback(_ctx(client), [_provider("a", "a.example.com")])
    assert result.response.status_code == 429
    assert [item.provider_id for item in result.rate_limited] == ["a"]


@respx.mock
@pytest.mark.anyio
async def test_429_then_transport_failure_reports_the_rate_limit() -> None:
    respx.post("https://a.example.com/v1/chat/completions").mock(return_value=httpx.Response(429))
    respx.post("https://b.example.com/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("down")
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(OpenAIProxyExecutionError) as excinfo:
            await execute_openai_with_fallback(
                _ctx(client), [_provider("a", "a.example.com"), _provider("b", "b.example.com")]
            )
    assert [item.provider_id for item in excinfo.value.rate_limited] == ["a"]


@respx.mock
@pytest.mark.anyio
async def test_429_records_lockout(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.api.routes.openai import record_upstream_rate_limits

    table = LockoutTable()
    monkeypatch.setattr(lockout, "get_lockout_table", lambda: table)
    respx.post("https://a.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "60"})
    )
    respx.post("https://b.example.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "ok"})
    )
    providers = [_provider("a", "a.example.com"), _provider("b", "b.example.com")]
    async with httpx.AsyncClient() as client:
        result = await execute_openai_with_fallback(_ctx(client), providers)

    record_upstream_rate_limits(result.rate_limited, providers, now=NOW)

    key = LockoutKey("a", "beta/b1")
    assert table.is_locked(key, now=NOW + dt.timedelta(seconds=30))
    assert not table.is_locked(key, now=NOW + dt.timedelta(seconds=61))
    assert not table.is_locked(LockoutKey("b", "beta/b1"), now=NOW)
