"""Shared harness for the gateway server tests (no tests live here).

The gateway runs under a real in-process uvicorn server on an ephemeral loopback port, so streaming,
disconnects, and connection aborts behave as they do in production. Every upstream is an in-process
``httpx.MockTransport``; nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import uvicorn

from pitwall.gateway.app import ChatExecutor, GatewayApp, create_app
from pitwall.gateway.config import GatewayConfig

JSON_HEADERS = {"content-type": "application/json"}
SSE_HEADERS = {"content-type": "text/event-stream"}
AUTH = {"authorization": "Bearer t"}
CHAT = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
COMPLETION = {"id": "x", "object": "chat.completion", "choices": []}
UPSTREAM_URL = "http://upstream.invalid"
UPSTREAM_SECRET = "upstream-secret"  # pragma: allowlist secret
ROUTE_KEY = "k-b"  # pragma: allowlist secret


@dataclass
class Captured:
    """One request the fake upstream saw."""

    url: str
    method: str
    headers: dict[str, str]
    body_text: str

    def json(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.body_text)
        return loaded


Responder = Callable[[Captured], httpx.Response | Awaitable[httpx.Response]]


def json_response(
    payload: object, status: int = 200, content_type: str = "application/json"
) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(status, headers={"content-type": content_type}, content=text)


def default_responder(captured: Captured) -> httpx.Response:
    if captured.url.endswith("/models"):
        return json_response({"object": "list", "data": []})
    return json_response(COMPLETION)


class FakeUpstream:
    """An in-process upstream: records requests, tracks stream closure and cancellation."""

    def __init__(self, responder: Responder = default_responder) -> None:
        self.responder = responder
        self.captured: list[Captured] = []
        self.stream_closed = asyncio.Event()
        self.handler_cancelled = asyncio.Event()

    def last_body(self) -> dict[str, Any]:
        return self.captured[-1].json()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        captured = Captured(
            str(request.url),
            request.method,
            {k.lower(): v for k, v in request.headers.items()},
            request.content.decode(),
        )
        self.captured.append(captured)
        try:
            outcome = self.responder(captured)
            if isinstance(outcome, httpx.Response):
                return outcome
            return await outcome
        except asyncio.CancelledError:
            self.handler_cancelled.set()
            raise

    def sse(self, chunks: Callable[[], AsyncIterator[bytes]], status: int = 200) -> httpx.Response:
        """A streaming SSE response whose body closure is recorded."""
        upstream = self

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                async for chunk in chunks():
                    yield chunk

            async def aclose(self) -> None:
                upstream.stream_closed.set()

        return httpx.Response(status, headers=SSE_HEADERS, stream=Stream())


def chunks_of(*items: bytes) -> Callable[[], AsyncIterator[bytes]]:
    async def gen() -> AsyncIterator[bytes]:
        for item in items:
            yield item

    return gen


@dataclass
class Gateway:
    """A running gateway plus an HTTP client pointed at it."""

    app: GatewayApp
    config: GatewayConfig
    base_url: str
    http: httpx.AsyncClient
    upstream: FakeUpstream = field(default_factory=FakeUpstream)

    async def post(self, path: str, body: object, **headers: str) -> httpx.Response:
        return await self.http.post(
            path, content=json.dumps(body), headers={**AUTH, **JSON_HEADERS, **headers}
        )

    async def chat(self, body: object = CHAT, **headers: str) -> httpx.Response:
        return await self.post("/v1/chat/completions", body, **headers)

    async def get(self, path: str, **headers: str) -> httpx.Response:
        return await self.http.get(path, headers={**AUTH, **headers})


def make_config(**overrides: Any) -> GatewayConfig:
    values: dict[str, Any] = {"token": "t", "bind": "127.0.0.1", "port": 0}
    if "routes" not in overrides:
        values["upstream_base_url"] = UPSTREAM_URL
    values.update(overrides)
    return GatewayConfig(**values)


async def _wait_started(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    async with asyncio.timeout(10):
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("gateway server exited before starting")
            await asyncio.sleep(0.005)


@contextlib.asynccontextmanager
async def running_gateway(
    upstream: FakeUpstream | None = None,
    *,
    executors: Mapping[str, ChatExecutor] | None = None,
    **config_overrides: Any,
) -> AsyncIterator[Gateway]:
    fake = upstream or FakeUpstream()
    config = make_config(**config_overrides)
    client = fake.client()
    app = create_app(config, executors=executors, client=client)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="critical",
            lifespan="on",
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        await _wait_started(server, task)
        port = server.servers[0].sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base_url, timeout=10) as http:
            yield Gateway(app, config, base_url, http, fake)
    finally:
        server.should_exit = True
        await task
        await client.aclose()


async def eventually(condition: Callable[[], bool], *, timeout_s: float = 5.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not condition():
            await asyncio.sleep(0.01)
