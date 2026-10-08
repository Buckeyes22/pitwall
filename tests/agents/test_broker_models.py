"""The broker client speaks the API's own Pydantic models over httpx."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pitwall.agents import broker
from pitwall.api.capability_schemas import CapabilityResponse
from pitwall.api.schemas.serve import ServeCreate, ServeResponse
from pitwall.core.models import WebhookSubscriptionCreate, WebhookSubscriptionCreated

BASE = "http://pitwall.test"
NOW = "2026-09-29T00:00:00Z"


class _Recorder:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        self.calls: list[httpx.Request] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request(method, url, headers=kwargs.get("headers"), json=kwargs.get("json"))
        self.calls.append(request)
        return httpx.Response(self.status, json=self.body, request=request)


def _install(monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]) -> _Recorder:
    recorder = _Recorder(status, body)
    monkeypatch.setattr(httpx, "request", recorder)
    return recorder


def _capability_body() -> dict[str, Any]:
    return CapabilityResponse.model_validate(
        {
            "id": "cap-1",
            "name": "llm.x",
            "version": "1",
            "class": "llm",
            "cost_mode": "zero",
            "served_model_id": "m/1",
            "active_lease": {"lease_id": "l-1", "expires_at": "2026-09-29T02:00:00+00:00"},
            "created_at": NOW,
            "updated_at": NOW,
        }
    ).model_dump(mode="json", by_alias=True)


def _serve_body() -> dict[str, Any]:
    return ServeResponse(
        capability="llm.x",
        lease_id="l-1",
        expires_at="2026-09-29T02:00:00+00:00",
        model_id="m/1",
        proxy_base_url=f"{BASE}/v1/proxy/llm.x",
        engine="vllm",
        variant=None,
        gpu_count=1,
        workload_id=None,
        template_id=None,
        provider_id="p-1",
        dry_run=False,
        created=True,
        cost_estimate_usd=None,
    ).model_dump(mode="json")


def _subscription_body() -> dict[str, Any]:
    return WebhookSubscriptionCreated.model_validate(
        {
            "id": "s-1",
            "consumer": "pitwall-agents",
            "webhook_url": "http://127.0.0.1:8765",
            "active": True,
            "event_types": ["lease.ready"],
            "created_at": NOW,
            "updated_at": NOW,
            "signing_secret": "sekret",  # pragma: allowlist secret
        }
    ).model_dump(mode="json")


def test_request_matches_api_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = _install(monkeypatch, 200, _serve_body())
    caps = {"ttlMinutes": 90, "idleTimeoutMinutes": 15, "maxUsdPerHour": 1.5}
    broker.serve_capability(BASE, "llm.x", "tok", caps=caps)
    request = serve.calls[0]
    assert request.url.path == "/v1/serve"
    assert request.headers["authorization"] == "Bearer tok"
    parsed = ServeCreate.model_validate(json.loads(request.content))
    assert (parsed.capability, parsed.ttl_minutes, parsed.idle_timeout_min) == ("llm.x", 90, 15)
    assert str(parsed.max_usd_per_hour) == "1.5"

    sub = _install(monkeypatch, 201, _subscription_body())
    broker.create_subscription(BASE, "tok", "http://127.0.0.1:8765")
    created = WebhookSubscriptionCreate.model_validate(json.loads(sub.calls[0].content))
    assert created.webhook_url == "http://127.0.0.1:8765"
    assert set(created.event_types) == set(broker.WEBHOOK_EVENTS)
    assert sub.calls[0].url.path == "/v1/webhook-subscriptions"


def test_invalid_request_is_refused_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(monkeypatch, 200, _serve_body())
    with pytest.raises(broker.PitwallError, match="request"):
        broker.serve_capability(BASE, "llm.x", "tok", caps={"ttlMinutes": 0})
    assert recorder.calls == []


def test_response_parsed_with_api_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, 200, _capability_body())
    info = broker.fetch_capability(BASE, "llm.x", "tok")
    assert (info.name, info.served_model_id, info.lease_id) == ("llm.x", "m/1", "l-1")
    assert info.expires_at is not None

    _install(monkeypatch, 200, _serve_body())
    result = broker.serve_capability(BASE, "llm.x", "tok", caps={})
    assert result["lease_id"] == "l-1"
    assert result["proxy_base_url"].endswith("/llm.x")

    _install(monkeypatch, 201, _subscription_body())
    subscription = broker.create_subscription(BASE, "tok", "http://127.0.0.1:8765")
    assert subscription["signing_secret"] == "sekret"  # pragma: allowlist secret


def test_response_that_violates_the_api_model_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, 200, {"name": "llm.x"})
    with pytest.raises(broker.PitwallError, match="unexpected"):
        broker.fetch_capability(BASE, "llm.x", "tok")
    _install(monkeypatch, 200, {"capability": "llm.x"})
    with pytest.raises(broker.PitwallError, match="unexpected"):
        broker.serve_capability(BASE, "llm.x", "tok", caps={})
    _install(monkeypatch, 201, {"id": "s-1"})
    with pytest.raises(broker.PitwallSyncError, match="unexpected"):
        broker.create_subscription(BASE, "tok", "http://127.0.0.1:8765")


def test_refusal_and_status_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, 422, {"error": "cap_exceeded"})
    with pytest.raises(broker.ServeRefused) as refused:
        broker.serve_capability(BASE, "llm.x", "tok", caps={})
    assert refused.value.code == "cap_exceeded"
    _install(monkeypatch, 403, {})
    with pytest.raises(broker.PitwallError, match="PITWALL_AGENTS_API_TOKEN"):
        broker.fetch_capability(BASE, "llm.x", "tok")
    _install(monkeypatch, 404, {})
    with pytest.raises(broker.PitwallError, match="no capability"):
        broker.fetch_capability(BASE, "llm.x", "tok")
