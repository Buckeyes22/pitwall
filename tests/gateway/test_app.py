"""Gateway server cases ported from shim.test.ts, routes.test.ts, and hardening.test.ts."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from pitwall.gateway import app as app_module
from pitwall.gateway.app import GatewayApp, serve
from pitwall.gateway.config import ConfigError, GatewayConfig, resolve_config
from pitwall.gateway.routes_table import GatewayRoute
from tests.gateway.test_app_support import (
    AUTH,
    CHAT,
    JSON_HEADERS,
    ROUTE_KEY,
    UPSTREAM_SECRET,
    Captured,
    FakeUpstream,
    chunks_of,
    eventually,
    json_response,
    running_gateway,
)

STREAM_CHAT = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
KEY = f"sk-test-{'x' * 24}"


async def stream_chat(gateway: Any) -> httpx.Response:
    request = gateway.http.build_request(
        "POST",
        "/v1/chat/completions",
        content=json.dumps(STREAM_CHAT),
        headers={**AUTH, **JSON_HEADERS},
    )
    response: httpx.Response = await gateway.http.send(request, stream=True)
    return response


# --- shim.test.ts "shim routing" ---------------------------------------------------------------


@pytest.mark.parity
async def test_post_chat_completions_forwards_to_upstream_with_the_openai_body() -> None:
    # Source: shim.test.ts "shim routing > POST /v1/chat/completions forwards to upstream with the OpenAI body"
    async with running_gateway() as gateway:
        response = await gateway.chat()
    assert response.status_code == 200
    assert len(gateway.upstream.captured) == 1
    assert gateway.upstream.last_body()["model"] == "m"


@pytest.mark.parity
async def test_relays_sse_chunks_incrementally_with_backpressure_safe_framing() -> None:
    # Source: shim.test.ts "shim routing > relays upstream SSE chunks incrementally and preserves backpressure-safe framing"
    release = asyncio.Event()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        await release.wait()
        yield b"data: second\n\ndata: [DONE]\n\n"

    upstream = FakeUpstream()
    upstream.responder = lambda _captured: upstream.sse(chunks)
    async with running_gateway(upstream) as gateway:
        response = await stream_chat(gateway)
        try:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            reader = response.aiter_raw()
            assert await anext(reader) == b"data: first\n\n"
            release.set()
            assert b"data: second" in await anext(reader)
        finally:
            release.set()
            await response.aclose()


@pytest.mark.parity
async def test_closes_a_partial_sse_response_when_the_upstream_stream_fails() -> None:
    # Source: shim.test.ts "shim routing > closes a partial SSE response when the upstream stream fails"
    fail = asyncio.Event()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        await fail.wait()
        raise httpx.ReadError("fixture upstream failure")

    upstream = FakeUpstream()
    upstream.responder = lambda _captured: upstream.sse(chunks)
    async with running_gateway(upstream) as gateway:
        response = await stream_chat(gateway)
        seen = b""
        reader = response.aiter_raw()
        seen += await anext(reader)
        fail.set()
        with pytest.raises(httpx.RemoteProtocolError):
            async for chunk in reader:
                seen += chunk
        await response.aclose()
    # The connection was aborted, not closed cleanly, and the client saw a terminal error event.
    assert b"upstream_stream_failed" in seen


@pytest.mark.parity
async def test_cancelling_an_idle_client_cancels_the_upstream_reader() -> None:
    # Source: shim.test.ts "shim routing > cancelling an idle client cancels the upstream reader"
    async def chunks() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        await asyncio.Event().wait()

    upstream = FakeUpstream()
    upstream.responder = lambda _captured: upstream.sse(chunks)
    async with running_gateway(upstream) as gateway:
        response = await stream_chat(gateway)
        await anext(response.aiter_raw())
        await response.aclose()
        await eventually(upstream.stream_closed.is_set)


@pytest.mark.parity
async def test_client_disconnect_before_the_upstream_responds_aborts_the_upstream_request() -> None:
    # Source: shim.test.ts "shim routing > client disconnect before the upstream responds aborts the upstream request"
    async def stall(_captured: Captured) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    upstream = FakeUpstream(stall)
    async with running_gateway(upstream) as gateway:
        pending = asyncio.create_task(gateway.chat())
        await eventually(lambda: bool(upstream.captured))
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await eventually(upstream.handler_cancelled.is_set)


@pytest.mark.parity
async def test_streams_the_terminal_chunk_and_ends_the_response_cleanly() -> None:
    # Source: shim.test.ts "shim routing > streams the terminal chunk and ends the response cleanly after upstream completion"
    upstream = FakeUpstream()
    upstream.responder = lambda _captured: upstream.sse(
        chunks_of(b"data: one\n\n", b"data: [DONE]\n\n")
    )
    async with running_gateway(upstream) as gateway:
        response = await stream_chat(gateway)
        text = b"".join([chunk async for chunk in response.aiter_raw()])
        await response.aclose()
    assert response.status_code == 200
    assert text == b"data: one\n\ndata: [DONE]\n\n"


@pytest.mark.parity
async def test_client_disconnect_before_an_embeddings_response_aborts_the_upstream_request() -> (
    None
):
    # Source: shim.test.ts "shim routing > client disconnect before an embeddings response aborts the upstream request"
    async def stall(_captured: Captured) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    upstream = FakeUpstream(stall)
    async with running_gateway(upstream) as gateway:
        pending = asyncio.create_task(
            gateway.post("/v1/embeddings", {"model": "embed", "input": "hi"})
        )
        await eventually(lambda: bool(upstream.captured))
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await eventually(upstream.handler_cancelled.is_set)


@pytest.mark.parity
async def test_relays_a_large_sse_body_byte_exact_under_slow_client_reads() -> None:
    # Source: shim.test.ts "shim routing > relays a large SSE body byte-exact under slow client reads"
    lines = [f"data: chunk-{i} {'x' * 4096}\n\n".encode() for i in range(512)]
    upstream = FakeUpstream()
    upstream.responder = lambda _captured: upstream.sse(chunks_of(*lines))
    async with running_gateway(upstream) as gateway:
        response = await stream_chat(gateway)
        received = b""
        async for chunk in response.aiter_raw():
            received += chunk
            await asyncio.sleep(0.001)
        await response.aclose()
    assert response.status_code == 200
    assert received == b"".join(lines)


# --- shim.test.ts "upstream error relay redaction" ---------------------------------------------


async def relayed(
    status: int, body: object, content_type: str = "application/json"
) -> httpx.Response:
    upstream = FakeUpstream(lambda _captured: json_response(body, status, content_type))
    async with running_gateway(upstream) as gateway:
        return await gateway.chat()


@pytest.mark.parity
async def test_an_auth_error_echoing_the_authorization_header_never_relays_the_key() -> None:
    # Source: shim.test.ts "upstream error relay redaction > an auth error echoing the Authorization header never relays the key"
    out = await relayed(
        401,
        {
            "error": {
                "message": f"invalid key; received Authorization: Bearer {KEY}",
                "type": "auth",
            }
        },
    )
    assert out.status_code == 401
    assert KEY not in out.text
    assert isinstance(out.json()["error"]["message"], str)


@pytest.mark.parity
async def test_a_validation_error_echoing_an_api_key_never_relays_the_key() -> None:
    # Source: shim.test.ts "upstream error relay redaction > a validation error echoing an api key never relays the key"
    out = await relayed(400, {"error": {"message": "bad request", "echoed": {"api_key": KEY}}})
    assert out.status_code == 400
    assert KEY not in out.text


@pytest.mark.parity
async def test_a_clean_validation_error_keeps_the_wording_a_client_recovers_from() -> None:
    # Source: shim.test.ts "upstream error relay redaction > a clean validation error keeps the wording a client recovers from"
    message = "thinking is not supported for this model"
    out = await relayed(400, {"error": {"message": message, "type": "invalid_request_error"}})
    assert out.status_code == 400
    assert out.json()["error"]["message"] == message


@pytest.mark.parity
async def test_a_non_json_server_error_is_redacted() -> None:
    # Source: shim.test.ts "upstream error relay redaction > a non-JSON server error is redacted"
    out = await relayed(502, f"upstream proxy failed for Bearer {KEY}", "text/plain")
    assert out.status_code == 502
    assert KEY not in out.text


@pytest.mark.parity
async def test_a_successful_body_is_relayed_byte_for_byte() -> None:
    # Source: shim.test.ts "upstream error relay redaction > a successful body is relayed byte for byte"
    body = {"id": "ok", "object": "chat.completion", "choices": [], "note": f"key-shaped {KEY}"}
    out = await relayed(200, body)
    assert out.status_code == 200
    assert out.text == json.dumps(body)


# --- routes.test.ts ----------------------------------------------------------------------------


def two_routes() -> dict[str, GatewayRoute]:
    return {
        "gw-a": GatewayRoute("http://upstream-a.test/v1", "m-a"),
        "gw-b": GatewayRoute(
            "http://upstream-b.test/v1",
            "m-b",
            "PITWALL_GATEWAY_KEY_B",
            ROUTE_KEY,
            True,
        ),
    }


def keyless_b() -> dict[str, GatewayRoute]:
    routes = two_routes()
    routes["gw-b"] = GatewayRoute(
        "http://upstream-b.test/v1", "m-b", "PITWALL_GATEWAY_KEY_B", None, True
    )
    return routes


async def route_chat(gateway: Any, route: str | None = None) -> httpx.Response:
    headers = {} if route is None else {"x-pitwall-route": route}
    return await gateway.chat({"model": "ignored", "messages": CHAT["messages"]}, **headers)


@pytest.mark.parity
async def test_each_route_reaches_its_own_upstream_with_its_own_model_id_and_key() -> None:
    # Source: routes.test.ts "each route reaches its own upstream with its own model id and key"
    async with running_gateway(routes=two_routes()) as gateway:
        assert (await route_chat(gateway, "gw-a")).status_code == 200
        assert (await route_chat(gateway, "gw-b")).status_code == 200
    calls = [
        (c.url, c.headers.get("authorization"), c.json()["model"])
        for c in gateway.upstream.captured
    ]
    assert calls == [
        ("http://upstream-a.test/v1/chat/completions", None, "m-a"),
        ("http://upstream-b.test/v1/chat/completions", "Bearer k-b", "m-b"),
    ]


@pytest.mark.parity
async def test_a_missing_route_header_is_refused_without_an_upstream_call() -> None:
    # Source: routes.test.ts "a missing route header is refused without an upstream call"
    async with running_gateway(routes=two_routes()) as gateway:
        response = await route_chat(gateway)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "route_required"
    assert gateway.upstream.captured == []


@pytest.mark.parity
async def test_an_unknown_route_is_404() -> None:
    # Source: routes.test.ts "an unknown route is 404"
    async with running_gateway(routes=two_routes()) as gateway:
        response = await route_chat(gateway, "gw-nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "route_not_found"
    assert gateway.upstream.captured == []


@pytest.mark.parity
async def test_a_keyed_route_without_its_key_is_503_and_never_contacts_the_upstream() -> None:
    # Source: routes.test.ts "a keyed route without its key is 503 and never contacts the upstream"
    async with running_gateway(routes=keyless_b()) as gateway:
        response = await route_chat(gateway, "gw-b")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_key_missing"
    assert gateway.upstream.captured == []


@pytest.mark.parity
def test_boot_fails_closed_with_neither_a_route_table_nor_an_upstream_url() -> None:
    # Source: routes.test.ts "boot fails closed with neither a route table nor an upstream URL"
    with pytest.raises(ConfigError, match="PITWALL_GATEWAY_ROUTES or PITWALL_GATEWAY_UPSTREAM_URL"):
        resolve_config({"PITWALL_GATEWAY_TOKEN": "t"}, bind="127.0.0.1", port="0")


@pytest.mark.parity
async def test_an_upstream_429_keeps_its_rate_limit_reset_headers_for_the_quota_lockout() -> None:
    # Source: routes.test.ts "an upstream 429 keeps its rate-limit reset headers for the broker's quota lockout"
    limited = httpx.Response(
        429,
        headers={
            "content-type": "application/json",
            "retry-after": "30",
            "x-ratelimit-reset-requests": "30s",
            "x-ratelimit-remaining-requests": "0",
            "set-cookie": "session=upstream-private",
        },
        content='{"error":{"message":"rate limited"}}',
    )
    upstream = FakeUpstream(lambda _captured: limited)
    async with running_gateway(upstream, routes=two_routes()) as gateway:
        response = await route_chat(gateway, "gw-a")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.headers["x-ratelimit-reset-requests"] == "30s"
    assert response.headers["x-ratelimit-remaining-requests"] == "0"
    assert "set-cookie" not in response.headers


# --- hardening.test.ts "hardening" -------------------------------------------------------------

METHOD_CASES = [
    ("/v1/chat/completions", "POST"),
    ("/v1/embeddings", "POST"),
    ("/v1/models", "GET"),
    ("/health", "GET"),
    ("/internal/telemetry", "GET"),
]


@pytest.mark.parity
@pytest.mark.parametrize(("path", "allowed"), METHOD_CASES)
async def test_route_enforces_its_documented_method_over_http(path: str, allowed: str) -> None:
    # Source: hardening.test.ts "hardening > %s enforces its documented %s method over HTTP"
    upstream = FakeUpstream()
    async with running_gateway(upstream) as gateway:
        for method in ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]:
            if method == allowed:
                continue
            response = await gateway.http.request(method, path, headers=AUTH)
            assert response.status_code == 405, f"{method} {path}"
            assert response.headers["allow"] == allowed
            request_id = response.headers["x-request-id"]
            assert len(request_id) == 36
            if method == "HEAD":
                assert response.text == ""
            else:
                error = response.json()["error"]
                assert error["type"] == "invalid_request_error"
                assert error["request_id"] == request_id
        assert upstream.captured == []
        kwargs: dict[str, Any] = {"headers": {**AUTH, **JSON_HEADERS}}
        if allowed == "POST":
            kwargs["content"] = json.dumps({"model": "fixture", "messages": [], "input": "fixture"})
        response = await gateway.http.request(allowed, path, **kwargs)
        assert response.status_code == 200
    expected = 1 if allowed == "POST" or path == "/v1/models" else 0
    assert len(upstream.captured) == expected


@pytest.mark.parity
async def test_unknown_paths_remain_structured_404_for_unsupported_methods() -> None:
    # Source: hardening.test.ts "hardening > unknown paths remain structured 404 for unsupported methods"
    async with running_gateway() as gateway:
        response = await gateway.http.put("/not-a-route", headers=AUTH)
    assert response.status_code == 404
    assert "allow" not in response.headers
    assert response.json()["error"]["code"] == "not_found"
    assert gateway.upstream.captured == []


@pytest.mark.parity
def test_boot_fails_closed_without_the_gateway_token() -> None:
    # Source: hardening.test.ts "hardening > boot fails closed without PITWALL_GATEWAY_TOKEN"
    with pytest.raises(ConfigError, match="PITWALL_GATEWAY_TOKEN"):
        resolve_config({})


@pytest.mark.parity
def test_cli_entrypoint_attempts_boot_without_a_token_so_failure_is_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Source: hardening.test.ts "hardening > CLI entrypoint attempts boot without token so failure is non-zero"
    monkeypatch.delenv("PITWALL_GATEWAY_TOKEN", raising=False)
    assert serve() == 1
    assert "PITWALL_GATEWAY_TOKEN is required" in capsys.readouterr().err


@pytest.mark.parity
async def test_bodies_over_one_mebibyte_are_413() -> None:
    # Source: hardening.test.ts "hardening > bodies over 1 MiB are 413"
    huge = "x" * (1024 * 1024 + 1)
    async with running_gateway() as gateway:
        response = await gateway.chat(
            {"model": "m", "messages": [{"role": "user", "content": huge}]}
        )
    assert response.status_code == 413
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert gateway.upstream.captured == []


@pytest.mark.parity
def test_non_loopback_bind_is_refused() -> None:
    # Source: hardening.test.ts "hardening > non-loopback bind is refused"
    with pytest.raises(ConfigError, match="loopback"):
        resolve_config({"PITWALL_GATEWAY_TOKEN": "t"}, bind="0.0.0.0")


@pytest.mark.parity
async def test_claude_shaped_body_is_translated_to_openai_shape_upstream() -> None:
    # Source: hardening.test.ts "hardening > claude-shaped body is translated to openai-shape upstream"
    body = {
        "model": "m",
        "max_tokens": 8,
        "system": "terse",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    }
    async with running_gateway() as gateway:
        response = await gateway.chat(body, **{"x-pitwall-inbound-shape": "claude"})
    assert response.status_code == 200
    assert gateway.upstream.last_body()["messages"][0] == {"role": "system", "content": "terse"}


# --- lane 3.3 additions ------------------------------------------------------------------------


def test_refuses_non_loopback_bind(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "t")
    monkeypatch.setenv("PITWALL_GATEWAY_UPSTREAM_URL", "http://upstream.invalid/v1")

    def no_server(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("uvicorn must not start on a refused bind")

    monkeypatch.setattr("uvicorn.run", no_server)
    for bind in ("0.0.0.0", "192.168.1.5", "::", "example.com"):
        assert serve(bind=bind, port="0") == 1
        assert "loopback" in capsys.readouterr().err
    with pytest.raises(ConfigError, match="(?i)loopback"):
        GatewayApp(GatewayConfig(token="t", bind="0.0.0.0", upstream_base_url="http://u.invalid"))


async def test_413_message_reports_configured_cap() -> None:
    body = {"model": "m", "messages": [{"role": "user", "content": "y" * 200}]}
    async with running_gateway(body_cap_bytes=64) as gateway:
        declared = await gateway.chat(body)

        async def undeclared() -> AsyncIterator[bytes]:
            payload = json.dumps(body).encode()
            for start in range(0, len(payload), 32):
                yield payload[start : start + 32]

        chunked = await gateway.http.post(
            "/v1/chat/completions", content=undeclared(), headers={**AUTH, **JSON_HEADERS}
        )
        embeddings = await gateway.post("/v1/embeddings", body)
    for response in (declared, chunked, embeddings):
        assert response.status_code == 413
        assert "64 byte cap" in response.json()["error"]["message"]
        assert "1 MiB" not in response.json()["error"]["message"]
    assert gateway.upstream.captured == []


async def test_body_at_the_cap_is_accepted() -> None:
    body = json.dumps({"model": "m", "messages": []}).encode()
    async with running_gateway(body_cap_bytes=len(body)) as gateway:
        response = await gateway.http.post(
            "/v1/chat/completions", content=body, headers={**AUTH, **JSON_HEADERS}
        )
    assert response.status_code == 200


async def test_a_malformed_host_header_is_not_a_server_error() -> None:
    # E-07: the request path is never rebuilt from the Host header.
    async with running_gateway() as gateway:
        for host in ("[bad", "a b c", "::::", "evil.example:notaport"):
            response = await gateway.get("/health", host=host)
            assert response.status_code in (200, 400), host


async def test_trailing_slashes_route_like_the_bare_path() -> None:
    async with running_gateway() as gateway:
        assert (await gateway.get("/health/")).status_code == 200
        assert (await gateway.get("/v1/models///")).status_code == 200


async def test_every_response_carries_a_request_id_matching_the_error_body() -> None:
    async with running_gateway() as gateway:
        ok = await gateway.get("/health")
        refused = await gateway.http.get("/health")
    assert len(ok.headers["x-request-id"]) == 36
    assert refused.json()["error"]["request_id"] == refused.headers["x-request-id"]


async def test_upstream_failure_bodies_carry_the_gateway_request_id() -> None:
    async def down(_captured: Captured) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with running_gateway(FakeUpstream(down)) as gateway:
        response = await gateway.chat()
    assert response.status_code == 502
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]


async def test_upstream_stall_before_headers_is_a_504() -> None:
    async def stall(_captured: Captured) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with running_gateway(FakeUpstream(stall), upstream_timeout_s=0.05) as gateway:
        response = await gateway.chat()
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "gateway_timeout"


async def test_error_bodies_never_echo_the_gateway_token_or_upstream_key() -> None:
    upstream = FakeUpstream(
        lambda _c: json_response({"error": {"message": f"echo {UPSTREAM_SECRET}"}}, 500)
    )
    async with running_gateway(upstream, upstream_api_key=UPSTREAM_SECRET) as gateway:
        response = await gateway.chat()
    assert response.status_code == 500
    assert UPSTREAM_SECRET not in response.text


async def test_lifespan_closes_the_client_it_created() -> None:
    config = GatewayConfig(token="t", upstream_base_url="http://upstream.invalid")
    gateway_app = app_module.create_app(config)
    sent: list[dict[str, Any]] = []
    inbox = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive() -> dict[str, Any]:
        return next(inbox)

    async def send(message: Any) -> None:
        sent.append(message)

    client = gateway_app._http_client()
    await gateway_app({"type": "lifespan"}, receive, send)
    assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert client.is_closed


def test_serve_starts_uvicorn_on_the_configured_loopback_address(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "t")
    monkeypatch.setenv("PITWALL_GATEWAY_UPSTREAM_URL", "http://upstream.invalid/v1")
    seen: dict[str, Any] = {}

    def fake_run(target: Any, **kwargs: Any) -> None:
        seen["app"], seen["kwargs"] = target, kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)
    assert serve(bind="127.0.0.1", port="20131") == 0
    assert isinstance(seen["app"], GatewayApp)
    assert (seen["kwargs"]["host"], seen["kwargs"]["port"]) == ("127.0.0.1", 20131)
    assert "listening on http://127.0.0.1:20131" in capsys.readouterr().out


# --- launcher.test.ts --------------------------------------------------------------------------


def run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


@pytest.mark.parity
def test_serve_rejects_a_missing_token_with_a_failing_exit_and_diagnostic() -> None:
    # Source: launcher.test.ts "%s rejects a missing token with a failing exit and diagnostic"
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_GATEWAY_TOKEN"}
    result = run_python("import sys; from pitwall.gateway.app import serve; sys.exit(serve())", env)
    assert result.returncode == 1
    assert "PITWALL_GATEWAY_TOKEN is required" in result.stderr
    assert result.stdout == ""


@pytest.mark.parity
def test_importing_the_module_with_a_token_does_not_start_a_server() -> None:
    # Source: launcher.test.ts "importing the compiled module with a token does not start a server"
    env = {**os.environ, "PITWALL_GATEWAY_TOKEN": "test-only"}
    result = run_python("import pitwall.gateway.app", env)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
