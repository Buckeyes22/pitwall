"""Auth, upstream credential handling, and rate limiting (upstream-auth.test.ts, shim.test.ts)."""

from __future__ import annotations

import hmac
from typing import Any

import pytest

from pitwall.gateway.auth import GatewayAuth, read_bearer_token
from tests.gateway.test_app_support import (
    AUTH,
    CHAT,
    UPSTREAM_SECRET,
    FakeUpstream,
    running_gateway,
)

INBOUND = "inbound-test-secret"  # pragma: allowlist secret
UPSTREAM_KEY = "upstream-test-secret"  # pragma: allowlist secret
ROUTES: list[tuple[str, dict[str, Any]]] = [
    ("/v1/chat/completions", {**CHAT, "input": "hi"}),
    ("/v1/embeddings", {**CHAT, "input": "hi"}),
]


@pytest.mark.parity
@pytest.mark.parametrize(("path", "body"), ROUTES)
async def test_inference_routes_use_only_the_separately_configured_upstream_credential(
    path: str, body: dict[str, Any]
) -> None:
    # Source: upstream-auth.test.ts "%s uses only the separately configured upstream credential"
    for upstream_key in (None, UPSTREAM_KEY):
        upstream = FakeUpstream()
        async with running_gateway(
            upstream, token=INBOUND, upstream_api_key=upstream_key
        ) as gateway:
            headers = {"authorization": f"Bearer {INBOUND}"}
            response = await gateway.http.post(path, json=body, headers=headers)
            assert response.status_code == 200
            expected = f"Bearer {upstream_key}" if upstream_key else None
            assert upstream.captured[0].headers.get("authorization") == expected
            telemetry = await gateway.http.get("/internal/telemetry", headers=headers)
            assert UPSTREAM_KEY not in telemetry.text
            assert INBOUND not in telemetry.text


@pytest.mark.parity
async def test_upstream_bearer_credential_is_forwarded_without_exposing_it_in_the_url() -> None:
    # Source: shim.test.ts "forwards an optional upstream bearer credential without exposing it in the URL"
    upstream = FakeUpstream()
    async with running_gateway(upstream, upstream_api_key=UPSTREAM_SECRET) as gateway:
        assert (await gateway.chat()).status_code == 200
    assert upstream.captured[0].headers["authorization"] == f"Bearer {UPSTREAM_SECRET}"
    assert UPSTREAM_SECRET not in upstream.captured[0].url


@pytest.mark.parity
async def test_upstream_bearer_credential_is_forwarded_on_embeddings_too() -> None:
    # Source: shim.test.ts "forwards the upstream bearer credential on embeddings too"
    upstream = FakeUpstream()
    async with running_gateway(
        upstream,
        upstream_api_key=UPSTREAM_SECRET,
        upstream_base_url="http://upstream.invalid/v1",
    ) as gateway:
        response = await gateway.post("/v1/embeddings", {"model": "embed", "input": "hello"})
        assert response.status_code == 200
    assert len(upstream.captured) == 1
    assert upstream.captured[0].url == "http://upstream.invalid/v1/embeddings"
    assert upstream.captured[0].headers["authorization"] == f"Bearer {UPSTREAM_SECRET}"
    assert UPSTREAM_SECRET not in upstream.captured[0].url


@pytest.mark.parity
async def test_inbound_bearer_token_is_not_forwarded_without_an_upstream_key() -> None:
    # Source: shim.test.ts "does not forward the inbound bearer token upstream when no upstream key is configured"
    upstream = FakeUpstream()
    async with running_gateway(upstream) as gateway:
        assert (await gateway.chat()).status_code == 200
    assert len(upstream.captured) == 1
    assert "authorization" not in upstream.captured[0].headers


@pytest.mark.parity
async def test_telemetry_never_echoes_the_configured_upstream_credential() -> None:
    # Source: shim.test.ts "telemetry never echoes the configured upstream credential"
    async with running_gateway(
        upstream_api_key=UPSTREAM_SECRET,
        upstream_base_url="http://upstream.invalid/v1",
    ) as gateway:
        response = await gateway.get("/internal/telemetry")
    assert response.status_code == 200
    assert "upstream_base_url" in response.text
    assert UPSTREAM_SECRET not in response.text


@pytest.mark.parity
async def test_get_health_returns_200_with_the_service_identifier() -> None:
    # Source: shim.test.ts "GET /health returns 200 with the service identifier"
    async with running_gateway() as gateway:
        response = await gateway.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pitwall-gateway"


@pytest.mark.parity
async def test_anonymous_health_is_401() -> None:
    # Source: shim.test.ts "anonymous /health is 401"
    async with running_gateway() as gateway:
        response = await gateway.http.get("/health")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.parity
async def test_telemetry_returns_401_without_a_bearer_token() -> None:
    # Source: shim.test.ts "GET /internal/telemetry returns 401 without a bearer token"
    async with running_gateway() as gateway:
        response = await gateway.http.get("/internal/telemetry")
    assert response.status_code == 401


@pytest.mark.parity
async def test_telemetry_returns_the_shim_stats_with_a_valid_token() -> None:
    # Source: shim.test.ts "GET /internal/telemetry returns the shim stats with a valid token"
    async with running_gateway() as gateway:
        response = await gateway.get("/internal/telemetry")
    body = response.json()
    assert response.status_code == 200
    assert body["service"] == "pitwall-gateway"
    assert body["bind"] == "127.0.0.1"


@pytest.mark.parity
async def test_anonymous_requests_are_401_with_the_structured_envelope() -> None:
    # Source: hardening.test.ts "hardening > anonymous requests are 401 with the structured envelope"
    async with running_gateway() as gateway:
        response = await gateway.http.post("/v1/chat/completions", content="{}")
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/chat/completions"),
        ("POST", "/v1/embeddings"),
        ("GET", "/v1/models"),
        ("GET", "/health"),
        ("GET", "/internal/telemetry"),
    ],
)
@pytest.mark.parametrize(
    "authorization", [None, "Bearer wrong", "Basic t", "Bearer", "Bearer t extra", "t", ""]
)
async def test_every_route_rejects_a_missing_or_wrong_credential(
    method: str, path: str, authorization: str | None
) -> None:
    upstream = FakeUpstream()
    headers = {} if authorization is None else {"authorization": authorization}
    async with running_gateway(upstream) as gateway:
        response = await gateway.http.request(method, path, headers=headers, content="{}")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert upstream.captured == []


@pytest.mark.parametrize(
    ("header", "token"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("  Bearer   abc  ", "abc"),
        ("Bearer a b", None),
        ("Bearer", None),
        ("Basic abc", None),
        (None, None),
    ],
)
def test_bearer_header_parsing(header: str | None, token: str | None) -> None:
    assert read_bearer_token(header) == token


def test_constant_time_compare_used(monkeypatch: pytest.MonkeyPatch) -> None:
    # E-05: one comparison site, hmac.compare_digest, never `==` on the credential.
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr("pitwall.gateway.auth.hmac.compare_digest", spy)
    auth = GatewayAuth("secret", 10)  # pragma: allowlist secret
    assert auth.authenticate("Bearer secret") == "secret"
    assert auth.authenticate("Bearer secreT") is None
    assert calls == [(b"secret", b"secret"), (b"secreT", b"secret")]


async def test_every_route_authenticates_through_the_one_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr("pitwall.gateway.auth.hmac.compare_digest", spy)
    async with running_gateway() as gateway:
        await gateway.chat()
        await gateway.post("/v1/embeddings", {"model": "e", "input": "x"})
        await gateway.get("/v1/models")
        await gateway.get("/health")
        await gateway.get("/internal/telemetry")
    assert len(calls) == 5


@pytest.mark.parametrize("path", ["/v1/models", "/internal/telemetry"])
async def test_rate_limit_applies_to_models_and_telemetry(path: str) -> None:
    # E-06: the limiter covers every authenticated route, not inference alone.
    upstream = FakeUpstream()
    async with running_gateway(upstream, rate_limit_rpm=2) as gateway:
        first = await gateway.get(path)
        second = await gateway.get(path)
        third = await gateway.get(path)
    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(third.headers["retry-after"]) >= 1


async def test_rate_limit_is_shared_across_routes_and_counts_per_token() -> None:
    async with running_gateway(rate_limit_rpm=3) as gateway:
        assert (await gateway.chat()).status_code == 200
        assert (await gateway.get("/v1/models")).status_code == 200
        remaining = await gateway.get("/internal/telemetry")
        assert remaining.headers["x-ratelimit-remaining"] == "0"
        assert (await gateway.chat()).status_code == 429
        # A rejected token never reaches a bucket: wrong credentials stay 401, not 429.
        wrong = await gateway.http.get("/v1/models", headers={"authorization": "Bearer nope"})
        assert wrong.status_code == 401


async def test_health_is_authenticated_but_not_rate_limited() -> None:
    async with running_gateway(rate_limit_rpm=1) as gateway:
        for _ in range(5):
            assert (await gateway.get("/health")).status_code == 200
        assert (await gateway.get("/v1/models")).status_code == 200
        assert (await gateway.get("/v1/models")).status_code == 429
        assert (await gateway.get("/health")).status_code == 200


async def test_rate_limited_request_never_reaches_the_upstream() -> None:
    upstream = FakeUpstream()
    async with running_gateway(upstream, rate_limit_rpm=1) as gateway:
        assert (await gateway.chat()).status_code == 200
        assert (await gateway.chat()).status_code == 429
    assert len(upstream.captured) == 1


async def test_two_apps_do_not_share_a_rate_limiter() -> None:
    async with (
        running_gateway(rate_limit_rpm=1) as first,
        running_gateway(rate_limit_rpm=1) as second,
    ):
        assert (await first.get("/v1/models")).status_code == 200
        assert (await second.get("/v1/models")).status_code == 200
        assert (await first.get("/v1/models")).status_code == 429


async def test_authorization_header_never_appears_in_error_bodies() -> None:
    async with running_gateway() as gateway:
        response = await gateway.http.get(
            "/v1/models", headers={"authorization": "Bearer wrongsecretvalue"}
        )
    assert "wrongsecretvalue" not in response.text
    assert AUTH["authorization"] not in response.text
