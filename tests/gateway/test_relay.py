"""Relay cases ported from the Node gateway tests (routes, shim, hardening) plus the deadline fixes.

Every upstream is an in-process ``httpx.MockTransport``; nothing here touches the network.
Deadline tests run on a virtual-clock event loop so a 30 s+ stream takes no real time.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Any

import httpx
import pytest

from pitwall.gateway.relay import (
    Deadlines,
    RelayResult,
    RouteTarget,
    UpstreamStreamAborted,
    relay_chat,
    relay_embeddings,
)

SSE = {"content-type": "text/event-stream"}
JSON_HEADERS = {"content-type": "application/json"}
DEADLINES = Deadlines(first_byte_s=5.0, idle_s=5.0)
BODY = {"model": "ignored", "messages": [{"role": "user", "content": "hi"}]}
KEY_B = "k-b"  # pragma: allowlist secret
UPSTREAM_KEY = "upstream-secret"  # pragma: allowlist secret
PLAIN_KEY = "hunter2-not-key-shaped"  # pragma: allowlist secret
KEY = f"sk-test-{'x' * 24}"


class VirtualClockLoop(asyncio.SelectorEventLoop):
    """Event loop whose clock jumps to the next timer instead of sleeping."""

    def __init__(self) -> None:
        super().__init__()
        self._virtual_now = 0.0
        real_select = self._selector.select

        def select(timeout: float | None = None) -> Any:
            if timeout:
                self._virtual_now += timeout
            return real_select(0)

        self._selector.select = select  # type: ignore[method-assign]  # reason: virtual clock test double

    def time(self) -> float:
        return self._virtual_now


def run_virtual[T](coro: Callable[[], Coroutine[Any, Any, T]], *, limit_s: float = 600.0) -> T:
    loop = VirtualClockLoop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro(), limit_s))
    finally:
        loop.close()


class Upstream:
    """An in-process fake upstream that records requests and tracks stream closure."""

    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: Callable[[], AsyncIterator[bytes]] | None = None,
        json_body: object | None = None,
        raw_body: bytes | None = None,
        stall_before_response: bool = False,
    ) -> None:
        self.status = status
        self.headers = headers or JSON_HEADERS
        self.chunks = chunks
        self.json_body = json_body if json_body is not None else {"id": "x", "choices": []}
        self.raw_body = raw_body
        self.stall_before_response = stall_before_response
        self.requests: list[httpx.Request] = []
        self.closed = asyncio.Event()
        self.handler_cancelled = asyncio.Event()

    def body(self, index: int = -1) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.requests[index].content)
        return loaded

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.stall_before_response:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.handler_cancelled.set()
                raise
        upstream = self

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                assert upstream.chunks is not None
                async for chunk in upstream.chunks():
                    yield chunk

            async def aclose(self) -> None:
                upstream.closed.set()

        if self.chunks is not None:
            return httpx.Response(self.status, headers=self.headers, stream=Stream())
        if self.raw_body is not None:
            return httpx.Response(self.status, headers=self.headers, content=self.raw_body)
        return httpx.Response(self.status, headers=self.headers, content=json.dumps(self.json_body))


def chunks_of(*items: bytes) -> Callable[[], AsyncIterator[bytes]]:
    async def gen() -> AsyncIterator[bytes]:
        for item in items:
            yield item

    return gen


async def collect(result: RelayResult) -> tuple[bytes, BaseException | None]:
    """Drain a streamed result; returns the bytes seen and the abort exception, if any."""
    assert result.stream is not None
    seen = b""
    try:
        async for chunk in result.stream:
            seen += chunk
    except UpstreamStreamAborted as exc:
        return seen, exc
    finally:
        await result.aclose()
    return seen, None


def error_events(data: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in data.decode().split("\n\n"):
        if block.startswith("event: error"):
            payload = [ln for ln in block.split("\n") if ln.startswith("data: ")][0][6:]
            events.append(json.loads(payload))
    return events


def target(base_url: str = "http://upstream.test/v1", **kwargs: Any) -> RouteTarget:
    return RouteTarget(base_url=base_url, **kwargs)


# --- routes.test.ts / shim.test.ts request shaping ------------------------------------------


async def test_each_route_reaches_its_own_upstream_with_its_own_model_and_key() -> None:
    # Source: routes.test.ts "each route reaches its own upstream with its own model id and key"
    up_a, up_b = Upstream(), Upstream()
    async with up_a.client() as ca, up_b.client() as cb:
        await relay_chat(BODY, target("http://upstream-a.test/v1", model_id="m-a"), DEADLINES, ca)
        await relay_chat(
            BODY,
            target("http://upstream-b.test/v1", model_id="m-b", api_key=KEY_B),
            DEADLINES,
            cb,
        )
    assert str(up_a.requests[0].url) == "http://upstream-a.test/v1/chat/completions"
    assert "authorization" not in up_a.requests[0].headers
    assert up_a.body()["model"] == "m-a"
    assert str(up_b.requests[0].url) == "http://upstream-b.test/v1/chat/completions"
    assert up_b.requests[0].headers["authorization"] == "Bearer k-b"
    assert up_b.body()["model"] == "m-b"


async def test_body_is_forwarded_and_model_kept_without_a_pin() -> None:
    # Source: shim.test.ts "POST /v1/chat/completions forwards to upstream with the OpenAI body"
    up = Upstream()
    async with up.client() as client:
        result = await relay_chat({"model": "m", "messages": []}, target(), DEADLINES, client)
    assert result.status == 200
    assert up.body()["model"] == "m"
    assert up.requests[0].headers["content-type"] == "application/json"


async def test_upstream_bearer_credential_is_forwarded_and_never_in_the_url() -> None:
    # Source: shim.test.ts "forwards an optional upstream bearer credential without exposing it in the URL"
    up = Upstream()
    async with up.client() as client:
        await relay_chat(BODY, target(api_key=UPSTREAM_KEY), DEADLINES, client)
    assert up.requests[0].headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert UPSTREAM_KEY not in str(up.requests[0].url)


async def test_no_authorization_header_without_an_upstream_key() -> None:
    # Source: shim.test.ts "does not forward the inbound bearer token upstream when no upstream key is configured"
    up = Upstream()
    async with up.client() as client:
        await relay_chat(BODY, target(), DEADLINES, client)
    assert "authorization" not in up.requests[0].headers


async def test_embeddings_forward_the_upstream_credential() -> None:
    # Source: shim.test.ts "forwards the upstream bearer credential on embeddings too"
    up = Upstream()
    async with up.client() as client:
        result = await relay_embeddings(
            {"model": "embed", "input": "hello"},
            target("http://upstream.invalid/v1", api_key=UPSTREAM_KEY),
            DEADLINES,
            client,
        )
    assert result.status == 200
    assert str(up.requests[0].url) == "http://upstream.invalid/v1/embeddings"
    assert up.requests[0].headers["authorization"] == f"Bearer {UPSTREAM_KEY}"
    assert UPSTREAM_KEY not in str(up.requests[0].url)


async def test_openai_body_is_forwarded_byte_for_byte_in_shape() -> None:
    # Source: hardening.test.ts "inbound shape projections > openai inbound is forwarded byte-for-byte in OpenAI shape"
    up = Upstream()
    body = {
        "model": "fixture",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "vendor_extra": {"keep": True},
    }
    async with up.client() as client:
        await relay_chat(body, target("http://upstream.invalid"), DEADLINES, client)
    assert str(up.requests[0].url) == "http://upstream.invalid/chat/completions"
    assert up.body() == body


async def test_responses_body_reaches_upstream_unchanged() -> None:
    # Source: hardening.test.ts "inbound shape projections > responses inbound is passed through unchanged to the executor"
    up = Upstream()
    body = {"model": "fixture", "input": "fixture"}
    async with up.client() as client:
        await relay_chat(body, target("http://upstream.invalid"), DEADLINES, client)
    assert up.body() == body
    assert "messages" not in up.body()


async def test_upstream_429_keeps_rate_limit_headers_and_drops_cookies() -> None:
    # Source: routes.test.ts "an upstream 429 keeps its rate-limit reset headers for the broker's quota lockout"
    up = Upstream(
        status=429,
        headers={
            **JSON_HEADERS,
            "retry-after": "30",
            "x-ratelimit-reset-requests": "30s",
            "x-ratelimit-remaining-requests": "0",
            "set-cookie": "session=upstream-private",
        },
        json_body={"error": {"message": "rate limited"}},
    )
    async with up.client() as client:
        result = await relay_chat(BODY, target(), DEADLINES, client)
    assert result.status == 429
    assert result.headers["retry-after"] == "30"
    assert result.headers["x-ratelimit-reset-requests"] == "30s"
    assert result.headers["x-ratelimit-remaining-requests"] == "0"
    assert "set-cookie" not in result.headers


# --- shim.test.ts SSE relay -------------------------------------------------------------------


async def test_sse_chunks_are_relayed_incrementally() -> None:
    # Source: shim.test.ts "relays upstream SSE chunks incrementally and preserves backpressure-safe framing"
    release = asyncio.Event()

    async def gen() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        await release.wait()
        yield b"data: second\n\ndata: [DONE]\n\n"

    up = Upstream(headers=SSE, chunks=gen)
    async with up.client() as client:
        result = await relay_chat({**BODY, "stream": True}, target(), DEADLINES, client)
        assert result.status == 200
        assert result.headers["content-type"].startswith("text/event-stream")
        assert result.stream is not None
        assert await anext(result.stream) == b"data: first\n\n"
        release.set()
        assert b"data: second" in await anext(result.stream)
        await result.aclose()


async def test_stream_failure_sends_a_terminal_error_event_then_aborts() -> None:
    # Source: shim.test.ts "closes a partial SSE response when the upstream stream fails"
    # Inverted for E-01: the client now gets one terminal SSE error event before the abort.
    async def gen() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        raise httpx.ReadError("fixture upstream failure")

    up = Upstream(headers=SSE, chunks=gen)
    async with up.client() as client:
        result = await relay_chat({**BODY, "stream": True}, target(), DEADLINES, client)
        seen, exc = await collect(result)
    assert isinstance(exc, UpstreamStreamAborted)
    assert seen.startswith(b"data: first\n\n")
    events = error_events(seen)
    assert len(events) == 1
    assert events[0]["error"]["code"] == "upstream_stream_failed"
    assert up.closed.is_set()


async def test_client_disconnect_mid_stream_cancels_the_upstream_reader() -> None:
    # Source: shim.test.ts "cancelling an idle client cancels the upstream reader"
    async def gen() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        await asyncio.Event().wait()

    up = Upstream(headers=SSE, chunks=gen)
    async with up.client() as client:
        result = await relay_chat({**BODY, "stream": True}, target(), DEADLINES, client)
        assert result.stream is not None
        await anext(result.stream)
        await result.aclose()
        assert up.closed.is_set()


async def test_stream_ends_cleanly_after_the_terminal_chunk() -> None:
    # Source: shim.test.ts "streams the terminal chunk and ends the response cleanly after upstream completion"
    up = Upstream(headers=SSE, chunks=chunks_of(b"data: one\n\n", b"data: [DONE]\n\n"))
    async with up.client() as client:
        result = await relay_chat({**BODY, "stream": True}, target(), DEADLINES, client)
        seen, exc = await collect(result)
    assert exc is None
    assert seen == b"data: one\n\ndata: [DONE]\n\n"
    assert error_events(seen) == []


async def test_large_sse_body_is_relayed_byte_exact() -> None:
    # Source: shim.test.ts "relays a large SSE body byte-exact under slow client reads"
    lines = [f"data: chunk-{i} {'x' * 4096}\n\n".encode() for i in range(512)]
    up = Upstream(headers=SSE, chunks=chunks_of(*lines))
    async with up.client() as client:
        result = await relay_chat({**BODY, "stream": True}, target(), DEADLINES, client)
        assert result.stream is not None
        seen = b""
        async for chunk in result.stream:
            seen += chunk
            await asyncio.sleep(0)
        await result.aclose()
    assert seen == b"".join(lines)


# --- shim.test.ts disconnect before the upstream responds --------------------------------------


@pytest.mark.parametrize("relay", [relay_chat, relay_embeddings])
async def test_client_disconnect_before_upstream_responds_aborts_the_request(
    relay: Callable[..., Awaitable[RelayResult]],
) -> None:
    # Source: shim.test.ts "client disconnect before the upstream responds aborts the upstream request"
    # and "client disconnect before an embeddings response aborts the upstream request"
    up = Upstream(stall_before_response=True)
    async with up.client() as client:
        pending = asyncio.ensure_future(relay(BODY, target(), DEADLINES, client))
        for _ in range(100):
            if up.requests:
                break
            await asyncio.sleep(0.01)
        assert up.requests
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert up.handler_cancelled.is_set()


# --- hardening.test.ts / shim.test.ts error redaction (review finding #16) --------------------


async def _relayed(
    status: int, body: object, content_type: str = "application/json"
) -> RelayResult:
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    up = Upstream(status=status, headers={"content-type": content_type}, raw_body=raw)
    async with up.client() as client:
        return await relay_chat(BODY, target(), DEADLINES, client)


async def test_auth_error_echoing_the_authorization_header_never_relays_the_key() -> None:
    # Source: shim.test.ts "an auth error echoing the Authorization header never relays the key"
    out = await _relayed(
        401,
        {
            "error": {
                "message": f"invalid key; received Authorization: Bearer {KEY}",
                "type": "auth",
            }
        },
    )
    assert out.status == 401
    assert KEY.encode() not in out.body
    assert isinstance(json.loads(out.body)["error"]["message"], str)


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_sse_error_status_is_bounded_and_redacted(status: int) -> None:
    out = await _relayed(status, f"data: Bearer {KEY}\n\n", "text/event-stream")
    assert out.stream is None
    assert out.status == status
    assert out.headers["content-type"] == "application/json"
    assert KEY.encode() not in out.body


async def test_oversized_sse_error_body_is_bounded_and_never_relays_the_key() -> None:
    cap = 1000
    raw = f"data: Bearer {KEY}\n\n".encode() + b"x" * (cap * 5)
    up = Upstream(status=500, headers=SSE, raw_body=raw)
    async with up.client() as client:
        out = await relay_chat(BODY, target(max_body_bytes=cap), DEADLINES, client)
    assert out.stream is None
    assert out.status == 502
    assert out.headers["content-type"] == "application/json"
    assert json.loads(out.body)["error"]["code"] == "upstream_body_too_large"
    assert len(out.body) < cap
    assert KEY.encode() not in out.body


async def test_validation_error_echoing_an_api_key_never_relays_the_key() -> None:
    # Source: shim.test.ts "a validation error echoing an api key never relays the key"
    out = await _relayed(400, {"error": {"message": "bad request", "echoed": {"api_key": KEY}}})
    assert out.status == 400
    assert KEY.encode() not in out.body


async def test_clean_validation_error_keeps_the_wording_a_client_recovers_from() -> None:
    # Source: shim.test.ts "a clean validation error keeps the wording a client recovers from"
    message = "thinking is not supported for this model"
    out = await _relayed(400, {"error": {"message": message, "type": "invalid_request_error"}})
    assert out.status == 400
    assert json.loads(out.body)["error"]["message"] == message


async def test_non_json_server_error_is_redacted() -> None:
    # Source: shim.test.ts "a non-JSON server error is redacted"
    out = await _relayed(502, f"upstream proxy failed for Bearer {KEY}", "text/plain")
    assert out.status == 502
    assert KEY.encode() not in out.body
    assert json.loads(out.body)["error"]["message"]


async def test_successful_body_is_relayed_byte_for_byte() -> None:
    # Source: shim.test.ts "a successful body is relayed byte for byte"
    body = {"id": "ok", "object": "chat.completion", "choices": [], "note": f"key-shaped {KEY}"}
    raw = json.dumps(body, separators=(",", ":")).encode()
    up = Upstream(raw_body=raw)
    async with up.client() as client:
        out = await relay_chat(BODY, target(), DEADLINES, client)
    assert out.status == 200
    assert out.body == raw


async def test_the_configured_upstream_key_is_redacted_from_error_bodies() -> None:
    # Source: shim.test.ts "telemetry never echoes the configured upstream credential" (same redaction contract)
    up = Upstream(
        status=500,
        raw_body=f"upstream said: {PLAIN_KEY}".encode(),
        headers={"content-type": "text/plain"},
    )
    async with up.client() as client:
        out = await relay_chat(BODY, target(api_key=PLAIN_KEY), DEADLINES, client)
    assert PLAIN_KEY.encode() not in out.body


# --- spec fixes: deadlines (E-01), body cap (E-07) --------------------------------------------


def test_idle_deadline_sends_error_event_and_aborts() -> None:
    # Source: E-01 (spec fix; no TS test). Stalls after one chunk; the old shim ended cleanly.
    async def scenario() -> None:
        async def gen() -> AsyncIterator[bytes]:
            yield b"data: first\n\n"
            await asyncio.Event().wait()

        up = Upstream(headers=SSE, chunks=gen)
        async with up.client() as client:
            result = await relay_chat(
                {**BODY, "stream": True}, target(), Deadlines(first_byte_s=5.0, idle_s=2.0), client
            )
            started = asyncio.get_running_loop().time()
            seen, exc = await collect(result)
            waited = asyncio.get_running_loop().time() - started
        assert isinstance(exc, UpstreamStreamAborted)
        assert seen.startswith(b"data: first\n\n")
        events = error_events(seen)
        assert len(events) == 1
        assert events[0]["error"]["code"] == "upstream_idle_timeout"
        assert b"[DONE]" not in seen
        assert 2.0 <= waited < 3.0
        assert up.closed.is_set()

    run_virtual(scenario)


def test_first_byte_deadline() -> None:
    # Source: E-01 (spec fix; no TS test). Headers arrive, then no chunk within first_byte_s.
    async def scenario() -> None:
        async def gen() -> AsyncIterator[bytes]:
            await asyncio.Event().wait()
            yield b""  # pragma: no cover

        up = Upstream(headers=SSE, chunks=gen)
        async with up.client() as client:
            result = await relay_chat(
                {**BODY, "stream": True}, target(), Deadlines(first_byte_s=3.0, idle_s=60.0), client
            )
            seen, exc = await collect(result)
        assert isinstance(exc, UpstreamStreamAborted)
        events = error_events(seen)
        assert len(events) == 1
        assert events[0]["error"]["code"] == "upstream_first_byte_timeout"
        assert up.closed.is_set()

    run_virtual(scenario)


def test_first_byte_deadline_before_response_headers_is_a_504() -> None:
    # Source: shim.ts catch block (abort -> 504 gateway_timeout), now bounded by first_byte_s.
    async def scenario() -> None:
        up = Upstream(stall_before_response=True)
        async with up.client() as client:
            result = await relay_chat(
                BODY, target(), Deadlines(first_byte_s=3.0, idle_s=60.0), client
            )
        assert result.status == 504
        assert json.loads(result.body)["error"]["code"] == "gateway_timeout"
        assert up.handler_cancelled.is_set()

    run_virtual(scenario)


def test_long_healthy_stream_not_cut() -> None:
    # Source: E-01 (spec fix; no TS test). 40 s of regular chunks; the old 30 s total timer cut it.
    async def scenario() -> None:
        async def gen() -> AsyncIterator[bytes]:
            for i in range(40):
                await asyncio.sleep(1.0)
                yield f"data: {i}\n\n".encode()
            yield b"data: [DONE]\n\n"

        up = Upstream(headers=SSE, chunks=gen)
        async with up.client() as client:
            loop = asyncio.get_running_loop()
            started = loop.time()
            result = await relay_chat(
                {**BODY, "stream": True}, target(), Deadlines(first_byte_s=5.0, idle_s=5.0), client
            )
            seen, exc = await collect(result)
            elapsed = loop.time() - started
        assert elapsed > 30.0
        assert exc is None
        assert seen.endswith(b"data: [DONE]\n\n")
        assert error_events(seen) == []

    run_virtual(scenario)


async def test_client_disconnect_cancels_upstream() -> None:
    # Source: Review Focus 4; shim.test.ts disconnect cases. No task or connection remains afterwards.
    before = set(asyncio.all_tasks())
    released = asyncio.Event()

    async def gen() -> AsyncIterator[bytes]:
        yield b"data: first\n\n"
        await released.wait()
        yield b"data: never\n\n"

    up = Upstream(headers=SSE, chunks=gen)
    client = up.client()
    result = await relay_chat({**BODY, "stream": True}, target(), DEADLINES, client)
    assert result.stream is not None
    stream = result.stream

    async def consume() -> None:
        async for _ in stream:
            pass

    consumer = asyncio.ensure_future(consume())
    for _ in range(100):
        await asyncio.sleep(0.01)
        if consumer.done():
            break
    # The server's disconnect handler cancels the task that is relaying the stream.
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await result.aclose()
    assert up.closed.is_set()
    await client.aclose()
    await asyncio.sleep(0)
    assert asyncio.all_tasks() <= before | {asyncio.current_task()}


async def test_non_sse_body_capped() -> None:
    # Source: E-07 (spec fix; no TS test). Upstream keeps sending; the relay stops at the cap.
    sent: list[int] = []

    async def gen() -> AsyncIterator[bytes]:
        for i in range(50):
            sent.append(i)
            yield b"x" * 400

    up = Upstream(headers=JSON_HEADERS, chunks=gen)
    async with up.client() as client:
        result = await relay_chat(BODY, target(max_body_bytes=1000), DEADLINES, client)
    assert result.status == 502
    assert json.loads(result.body)["error"]["code"] == "upstream_body_too_large"
    assert "1000" in json.loads(result.body)["error"]["message"]
    assert len(sent) < 10
    assert up.closed.is_set()


async def test_non_sse_body_within_cap_is_relayed() -> None:
    # Source: E-07 (spec fix); the cap is inclusive.
    up = Upstream(raw_body=b"y" * 1000)
    async with up.client() as client:
        result = await relay_chat(BODY, target(max_body_bytes=1000), DEADLINES, client)
    assert result.status == 200
    assert result.body == b"y" * 1000


async def test_declared_content_length_over_the_cap_is_refused_without_reading() -> None:
    # Source: E-07 (spec fix); content-length is a fast path, the byte count stays authoritative.
    up = Upstream(headers={**JSON_HEADERS, "content-length": "5000"}, raw_body=b"z" * 5000)
    async with up.client() as client:
        result = await relay_chat(BODY, target(max_body_bytes=1000), DEADLINES, client)
    assert result.status == 502
    assert json.loads(result.body)["error"]["code"] == "upstream_body_too_large"


async def test_transport_failure_is_a_502_envelope() -> None:
    # Source: shim.ts catch block (non-abort failure -> structured error, no stack trace)
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await relay_chat(BODY, target(), DEADLINES, client)
    assert result.status == 502
    assert json.loads(result.body)["error"]["type"]
    assert "Traceback" not in result.body.decode()
