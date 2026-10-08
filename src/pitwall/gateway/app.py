"""The gateway ASGI app and ``serve`` entry point (port of the Node gateway shim.ts).

Five routes (chat completions, models, embeddings, health, telemetry), bearer authentication and a
per-token rate limit shared by all of them, request-shape validation, inbound translation,
compression, and a relay to the routed upstream. The app is written directly against ASGI so a
truncated upstream stream can abort the connection instead of closing it normally.

Executors (per-model handlers that replace the upstream relay) are injected into
:func:`create_app`; there is no module-level registry.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from pitwall.gateway.auth import GatewayAuth
from pitwall.gateway.compression import CompressionPolicy, compress_request
from pitwall.gateway.config import (
    ConfigError,
    GatewayConfig,
    parse_compression_header,
    resolve_config,
)
from pitwall.gateway.relay import (
    DEFAULT_MAX_BODY_BYTES,
    Deadlines,
    RelayResult,
    RouteTarget,
    UpstreamStreamAborted,
    relay_chat,
    relay_embeddings,
)
from pitwall.gateway.routes_table import RouteRefusal, resolve_route
from pitwall.gateway.translation import INBOUND_SHAPES, InboundShape, translate_inbound
from pitwall.routing.fallback import PITWALL_OPENAI_PROXY_USER_AGENT
from pitwall.routing.openai import build_openai_url
from pitwall.security.redaction import redact_text, safe_url_label

logger = logging.getLogger(__name__)

SERVICE_NAME = "pitwall-gateway"
_ERROR_MESSAGE_LIMIT = 4096

Message = MutableMapping[str, Any]
Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


@dataclass(frozen=True)
class ExecutorContext:
    """What an executor receives for one chat request."""

    body: dict[str, Any]
    inbound_shape: InboundShape
    policy: CompressionPolicy
    target: RouteTarget
    deadlines: Deadlines
    client: httpx.AsyncClient
    request_id: str


ChatExecutor = Callable[[ExecutorContext], Awaitable[RelayResult]]


class GatewayStreamAborted(RuntimeError):
    """Raised after a stream's terminal error event so the server aborts the connection."""


class _Refusal(Exception):
    """A request refused with a structured error envelope."""

    def __init__(
        self,
        status: int,
        message: str,
        type_: str,
        code: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.type = type_
        self.code = code
        self.headers = dict(headers or {})


class _ClientGone(Exception):
    """The client disconnected; there is nobody to answer."""


@dataclass
class _Request:
    scope: Scope
    receive: Receive
    _send: Send
    request_id: str
    headers: dict[str, str]
    extra_headers: dict[str, str] = field(default_factory=dict)
    started: bool = False

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
        await self._send(message)

    async def start(self, status: int, headers: Mapping[str, str]) -> None:
        merged = {**headers, "x-request-id": self.request_id, **self.extra_headers}
        await self.send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in merged.items()],
            }
        )

    async def respond(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        await self.start(status, {**headers, "content-length": str(len(body))})
        await self.send({"type": "http.response.body", "body": body, "more_body": False})


def _read_headers(scope: Scope) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in scope.get("headers", []):
        headers.setdefault(name.decode("latin-1").lower(), value.decode("latin-1"))
    return headers


async def _wait_disconnect(receive: Receive) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


async def _cancel(*tasks: asyncio.Future[Any]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _model_entry(
    model_id: str, created: float = 0, owned_by: str = "pitwall-gateway"
) -> dict[str, Any]:
    return {"id": model_id, "object": "model", "created": created, "owned_by": owned_by}


def _parse_upstream_models(payload: object) -> list[dict[str, Any]] | None:
    """Strictly project an upstream ``/models`` payload; ``None`` when it is not a model list."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    models: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            return None
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            return None
        created = entry.get("created")
        owned_by = entry.get("owned_by")
        models.append(
            _model_entry(
                model_id,
                created
                if isinstance(created, int | float) and not isinstance(created, bool)
                else 0,
                owned_by if isinstance(owned_by, str) and owned_by else "upstream",
            )
        )
    return models


async def _default_executor(ctx: ExecutorContext) -> RelayResult:
    return await relay_chat(
        ctx.body, ctx.target, ctx.deadlines, ctx.client, request_id=ctx.request_id
    )


class GatewayApp:
    """The gateway ASGI application."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        executors: Mapping[str, ChatExecutor] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._executors: dict[str, ChatExecutor] = dict(executors or {})
        self._auth = GatewayAuth(config.token, config.rate_limit_rpm)
        self._client = client
        self._owns_client = client is None
        self._deadlines = Deadlines(config.upstream_timeout_s, config.upstream_timeout_s)
        routes = config.routes or {}
        self._secrets = tuple(
            secret
            for secret in (
                config.upstream_api_key,
                *(route.api_key for route in routes.values()),
            )
            if secret
        )
        # path -> (allowed method, handler, counts against the rate limit)
        self._routes: dict[str, tuple[str, Callable[[_Request], Awaitable[None]], bool]] = {
            "/v1/chat/completions": ("POST", self._chat, True),
            "/v1/models": ("GET", self._models, True),
            "/v1/embeddings": ("POST", self._embeddings, True),
            "/health": ("GET", self._health, False),
            "/internal/telemetry": ("GET", self._telemetry, True),
        }

    # ---- ASGI ---------------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
        elif scope["type"] == "http":
            await self._handle_http(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self._owns_client and self._client is not None:
                    await self._client.aclose()
                    self._client = None
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # The relay owns every deadline, so the client carries no timeout of its own.
            self._client = httpx.AsyncClient(timeout=None)
        return self._client

    async def _handle_http(self, scope: Scope, receive: Receive, send: Send) -> None:
        req = _Request(scope, receive, send, str(uuid.uuid4()), _read_headers(scope))
        try:
            await self._dispatch(req)
        except _Refusal as refusal:
            await self._refuse(req, refusal)
        except _ClientGone:
            return
        except Exception:  # reason: last-resort boundary; the body carries no exception text
            logger.error("gateway request %s failed", req.request_id, exc_info=True)
            if req.started:
                raise
            await self._refuse(
                req,
                _Refusal(500, "Internal gateway error.", "api_error", "internal_server_error"),
            )

    async def _refuse(self, req: _Request, refusal: _Refusal) -> None:
        if req.started:
            return
        error = {
            "message": redact_text(refusal.message, secrets=self._secrets)[:_ERROR_MESSAGE_LIMIT],
            "type": refusal.type,
            "code": refusal.code,
            "request_id": req.request_id,
        }
        await req.respond(
            refusal.status,
            {**refusal.headers, "content-type": "application/json"},
            json.dumps({"error": error}).encode(),
        )

    async def _dispatch(self, req: _Request) -> None:
        path = str(req.scope["path"]).rstrip("/") or "/"
        method = str(req.scope["method"]).upper()
        entry = self._routes.get(path)
        if entry is None:
            raise _Refusal(404, f"Unknown route: {path}", "invalid_request_error", "not_found")
        allowed, handler, limited = entry
        if method != allowed:
            raise _Refusal(
                405,
                "Method not allowed.",
                "invalid_request_error",
                "method_not_allowed",
                {"allow": allowed},
            )
        token = self._auth.authenticate(req.headers.get("authorization"))
        if token is None:
            raise _Refusal(
                401,
                "Missing or invalid bearer token.",
                "authentication_error",
                "invalid_api_key",
            )
        if limited:
            decision = self._auth.consume(token)
            if not decision.allowed:
                raise _Refusal(
                    429,
                    "Rate limit exceeded.",
                    "rate_limit_error",
                    "rate_limit_exceeded",
                    {"retry-after": str(decision.retry_after_s)},
                )
            req.extra_headers["x-ratelimit-remaining"] = str(decision.remaining)
        await handler(req)

    # ---- request reading ----------------------------------------------------------------

    def _too_large(self) -> _Refusal:
        return _Refusal(
            413,
            f"Request body exceeds the {self.config.body_cap_bytes} byte cap.",
            "invalid_request_error",
            "payload_too_large",
        )

    async def _read_body(self, req: _Request) -> bytes:
        cap = self.config.body_cap_bytes
        declared = req.headers.get("content-length", "")
        # The declared length is only a hint that lets an oversized body be refused unread.
        if declared.isdigit() and int(declared) > cap:
            raise self._too_large()
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await req.receive()
            if message["type"] == "http.disconnect":
                raise _ClientGone
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > cap:
                raise self._too_large()
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)

    async def _read_object(self, req: _Request) -> dict[str, Any]:
        raw = await self._read_body(req)
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise _Refusal(
                400, "Request body is not valid JSON.", "invalid_request_error", "bad_request"
            ) from None
        if not isinstance(parsed, dict):
            raise _Refusal(
                400, "Request body must be a JSON object.", "invalid_request_error", "bad_request"
            )
        return parsed

    @staticmethod
    def _read_shape(req: _Request) -> InboundShape:
        raw = req.headers.get("x-pitwall-inbound-shape")
        normalized = (raw or "openai").lower()
        for shape in INBOUND_SHAPES:
            if shape == normalized:
                return shape
        raise _Refusal(
            400,
            f"Unsupported x-pitwall-inbound-shape: {raw}",
            "invalid_request_error",
            "unsupported_endpoint",
        )

    def _target(self, req: _Request) -> RouteTarget:
        routes = self.config.routes
        if routes:
            resolved = resolve_route(routes, req.headers.get("x-pitwall-route"))
            if isinstance(resolved, RouteRefusal):
                raise _Refusal(resolved.status, resolved.message, resolved.type, resolved.code)
            return resolved
        base_url = self.config.upstream_base_url
        if base_url is None:
            raise RuntimeError("config guarantees an upstream when no route table is loaded")
        return RouteTarget(base_url=base_url, api_key=self.config.upstream_api_key)

    # ---- relay plumbing -----------------------------------------------------------------

    async def _until_disconnect[T](self, req: _Request, work: Awaitable[T]) -> T:
        """Await ``work``, cancelling it (and so the upstream request) if the client leaves."""
        task = asyncio.ensure_future(work)
        watcher = asyncio.ensure_future(_wait_disconnect(req.receive))
        try:
            await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            await _cancel(task, watcher)
            raise
        if task.done():
            await _cancel(watcher)
            return task.result()
        await _cancel(task, watcher)
        raise _ClientGone

    async def _run_relay(self, req: _Request, work: Awaitable[RelayResult]) -> None:
        result = await self._until_disconnect(req, work)
        try:
            await self._write_result(req, result)
        finally:
            await result.aclose()

    async def _write_result(self, req: _Request, result: RelayResult) -> None:
        if result.stream is None:
            await req.respond(result.status, result.headers, result.body)
            return
        await req.start(result.status, result.headers)
        pump = asyncio.ensure_future(self._pump(req, result.stream))
        watcher = asyncio.ensure_future(_wait_disconnect(req.receive))
        try:
            await asyncio.wait({pump, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if pump.done():
                await _cancel(watcher)
                pump.result()
            else:
                await _cancel(pump, watcher)
        except BaseException:
            await _cancel(pump, watcher)
            raise

    @staticmethod
    async def _pump(req: _Request, stream: Any) -> None:
        try:
            async for chunk in stream:
                if chunk:
                    await req.send({"type": "http.response.body", "body": chunk, "more_body": True})
        except UpstreamStreamAborted as exc:
            # The terminal SSE error event is already on the wire; abort rather than close.
            raise GatewayStreamAborted(exc.code) from exc
        await req.send({"type": "http.response.body", "body": b"", "more_body": False})

    # ---- routes -------------------------------------------------------------------------

    async def _chat(self, req: _Request) -> None:
        shape = self._read_shape(req)
        policy = parse_compression_header(req.headers.get("x-pitwall-compression"))
        body = await self._read_object(req)
        if "messages" in body:
            messages = body["messages"]
            if not isinstance(messages, list):
                raise _Refusal(
                    400,
                    "messages must be an array of objects.",
                    "invalid_request_error",
                    "bad_request",
                )
            if not all(isinstance(entry, dict) for entry in messages):
                raise _Refusal(
                    400, "messages entries must be objects.", "invalid_request_error", "bad_request"
                )
        try:
            outbound = translate_inbound(body, shape)
        except ValueError as exc:
            raise _Refusal(400, str(exc), "invalid_request_error", "bad_request") from None
        outbound = compress_request(outbound, policy)
        target = self._target(req)
        if target.model_id is not None:
            outbound = {**outbound, "model": target.model_id}
        model = outbound.get("model")
        executor = self._executors.get(model if isinstance(model, str) else "", _default_executor)
        context = ExecutorContext(
            body=outbound,
            inbound_shape=shape,
            policy=policy,
            target=target,
            deadlines=self._deadlines,
            client=self._http_client(),
            request_id=req.request_id,
        )
        await self._run_relay(req, executor(context))

    async def _embeddings(self, req: _Request) -> None:
        body = await self._read_object(req)
        target = self._target(req)
        await self._run_relay(
            req,
            relay_embeddings(
                body,
                target,
                self._deadlines,
                self._http_client(),
                request_id=req.request_id,
            ),
        )

    async def _models(self, req: _Request) -> None:
        if self.config.routes:
            # With a route table the catalog is the table: no upstream is contacted.
            ids = dict.fromkeys([*self._executors, *self.config.routes])
            await self._json(req, {"object": "list", "data": [_model_entry(i) for i in ids]})
            return
        upstream = await self._until_disconnect(req, self._fetch_upstream_models())
        merged = {model_id: _model_entry(model_id) for model_id in self._executors}
        for model in upstream:
            merged.setdefault(model["id"], model)
        await self._json(req, {"object": "list", "data": list(merged.values())})

    async def _fetch_upstream_models(self) -> list[dict[str, Any]]:
        """List the single upstream's models; every failure is a structured 502 or 504."""
        base_url = self.config.upstream_base_url
        if base_url is None:
            raise RuntimeError("config guarantees an upstream when no route table is loaded")
        headers = {"accept": "application/json", "user-agent": PITWALL_OPENAI_PROXY_USER_AGENT}
        if self.config.upstream_api_key:
            headers["authorization"] = f"Bearer {self.config.upstream_api_key}"
        client = self._http_client()
        request = client.build_request(
            "GET", build_openai_url(base_url, "models"), headers=headers, timeout=None
        )
        try:
            async with asyncio.timeout(self.config.upstream_timeout_s):
                response = await client.send(request, stream=True)
                try:
                    if not response.is_success:
                        raise _Refusal(
                            502,
                            f"Upstream model listing failed with HTTP {response.status_code}.",
                            "server_error",
                            "bad_gateway",
                        )
                    payload = await self._read_json_capped(response)
                finally:
                    await response.aclose()
        except TimeoutError:
            raise _Refusal(
                504, "Upstream model listing timed out.", "gateway_timeout", "gateway_timeout"
            ) from None
        except httpx.HTTPError as exc:
            raise _Refusal(
                502,
                f"Upstream model listing failed: {type(exc).__name__}",
                "server_error",
                "bad_gateway",
            ) from None
        models = _parse_upstream_models(payload)
        if models is None:
            raise _Refusal(
                502, "Upstream returned a malformed model list.", "server_error", "bad_gateway"
            )
        return models

    @staticmethod
    async def _read_json_capped(response: httpx.Response) -> object:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > DEFAULT_MAX_BODY_BYTES:
                return None
            chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except ValueError:
            return None

    async def _health(self, req: _Request) -> None:
        await self._json(req, {"status": "ok", "service": SERVICE_NAME})

    async def _telemetry(self, req: _Request) -> None:
        config = self.config
        base_url = config.upstream_base_url
        await self._json(
            req,
            {
                "service": SERVICE_NAME,
                "bind": config.bind,
                "port": config.port,
                "body_cap_bytes": config.body_cap_bytes,
                "rate_limit_rpm": config.rate_limit_rpm,
                "upstream_base_url": safe_url_label(base_url) if base_url else None,
                "executor_count": len(self._executors),
            },
        )

    @staticmethod
    async def _json(req: _Request, payload: object, status: int = 200) -> None:
        await req.respond(
            status, {"content-type": "application/json"}, json.dumps(payload).encode()
        )


def create_app(
    config: GatewayConfig,
    *,
    executors: Mapping[str, ChatExecutor] | None = None,
    client: httpx.AsyncClient | None = None,
) -> GatewayApp:
    """Build the gateway app. ``executors`` and ``client`` are the injection points for tests."""
    return GatewayApp(config, executors=executors, client=client)


def serve(*, bind: str | None = None, port: str | None = None) -> int:
    """Run the gateway under uvicorn; returns a process exit code."""
    try:
        config = resolve_config(os.environ, bind=bind, port=port)
    except ConfigError as exc:
        print(f"pitwall-gateway failed to start: {exc}", file=sys.stderr)
        return 1
    import uvicorn  # reason: deferred so importing this module never loads the server

    host = f"[{config.bind}]" if ":" in config.bind else config.bind
    print(f"pitwall-gateway listening on http://{host}:{config.port}", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(
            create_app(config),
            host=config.bind,
            port=config.port,
            log_level="warning",
            lifespan="on",
            server_header=False,
        )
    return 0
