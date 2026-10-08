"""The serverless-LB embedding client never sends the RunPod key to a Pitwall origin."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pitwall.config import get_settings
from pitwall.providers import InferenceRequest, ProviderOperationContext, RunPodProvider
from pitwall.runpod_client.serverless_lb import ServerlessLBClient
from tests.providers.test_runpod_adapter import _capability, _credentials, _provider_record

RUNPOD_KEY = "runpod-secret-key"  # pragma: allowlist secret
PITWALL_TOKEN = "pitwall-api-token"  # pragma: allowlist secret
_EMBED_BODY = {"dense": [[0.0]], "sparse": [{}], "model": "test"}


def _record_requests(monkeypatch: pytest.MonkeyPatch, *, via_pitwall: bool) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/inference":
            return httpx.Response(200, json={"result": _EMBED_BODY})
        return httpx.Response(200, json=_EMBED_BODY)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    monkeypatch.setenv("PITWALL_EMBEDDING_VIA_PITWALL", "true" if via_pitwall else "false")
    monkeypatch.setenv("PITWALL_BASE_URL", "https://pitwall.example")
    monkeypatch.delenv("PITWALL_API_TOKEN", raising=False)
    get_settings.cache_clear()
    return seen


@pytest.mark.anyio
async def test_adapter_infer_goes_to_runpod_even_when_pitwall_mode_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker request must not loop back into the broker with the RunPod key."""
    seen = _record_requests(monkeypatch, via_pitwall=True)
    provider_record = _provider_record().model_copy(update={"runpod_endpoint_id": "endpoint-123"})

    await RunPodProvider().infer(
        InferenceRequest(
            context=ProviderOperationContext(pool="pool"),
            capability=_capability(),
            provider_record=provider_record,
            credentials=_credentials(),
            payload={"texts": ["hello"]},
        )
    )

    assert [(request.url.host, request.url.path) for request in seen] == [
        ("endpoint-123.api.runpod.ai", "/embed")
    ]
    assert seen[0].headers["authorization"].startswith("Bearer ")


@pytest.mark.anyio
async def test_pitwall_mode_never_sends_the_runpod_key_to_the_pitwall_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_requests(monkeypatch, via_pitwall=True)

    client = ServerlessLBClient(lb_base_url="https://embed.example", api_key=RUNPOD_KEY)
    try:
        await client.embed(["hello"])
    finally:
        await client.aclose()

    assert [str(request.url) for request in seen] == ["https://pitwall.example/v1/inference"]
    assert "authorization" not in seen[0].headers
    assert RUNPOD_KEY not in str(seen[0].headers)


@pytest.mark.anyio
async def test_pitwall_mode_sends_the_pitwall_api_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_requests(monkeypatch, via_pitwall=True)
    monkeypatch.setenv("PITWALL_API_TOKEN", PITWALL_TOKEN)
    get_settings.cache_clear()

    client = ServerlessLBClient(lb_base_url="https://embed.example", api_key=RUNPOD_KEY)
    try:
        await client.embed(["hello"])
    finally:
        await client.aclose()

    assert seen[0].headers["authorization"] == f"Bearer {PITWALL_TOKEN}"
    assert RUNPOD_KEY not in str(seen[0].headers)


@pytest.mark.anyio
async def test_explicit_direct_mode_ignores_the_pitwall_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _record_requests(monkeypatch, via_pitwall=True)

    client = ServerlessLBClient(
        lb_base_url="https://embed.example", api_key=RUNPOD_KEY, via_pitwall=False
    )
    try:
        await client.embed(["hello"])
    finally:
        await client.aclose()

    assert [str(request.url) for request in seen] == ["https://embed.example/embed"]
    assert seen[0].headers["authorization"] == f"Bearer {RUNPOD_KEY}"
