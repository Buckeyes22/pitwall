"""Upstream relay for the gateway (port of the executor and relay half of the Node gateway shim.ts).

``relay_chat`` and ``relay_embeddings`` POST one OpenAI-shaped body to the routed upstream and hand
back a :class:`RelayResult`. Non-SSE responses come back fully read (size-capped, credentials
redacted from errors). SSE responses come back as ``stream``, an async iterator the server writes to
the client:

* the first chunk must arrive within ``Deadlines.first_byte_s`` of the request start, and every
  later chunk within ``Deadlines.idle_s`` of the previous one; a healthy stream is never cut by a
  total-time limit;
* on a deadline or an upstream failure the iterator yields one terminal SSE error event and then
  raises :class:`UpstreamStreamAborted`, so the server aborts the connection instead of closing it
  normally. A truncated stream never ends with a normal close;
* closing the iterator (or the result) closes the upstream response, so a client disconnect leaves
  no upstream connection open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from pitwall.routing.fallback import PITWALL_OPENAI_PROXY_USER_AGENT
from pitwall.routing.openai import build_openai_url
from pitwall.security.redaction import redact_text

DEFAULT_MAX_BODY_BYTES = 16 * 1024 * 1024
_ERROR_MESSAGE_LIMIT = 4096
# Only these upstream headers reach the client; the broker's quota lockout reads reset timing here.
_RELAYED_HEADER_PREFIXES = ("x-ratelimit-", "ratelimit")
_PASSTHROUGH_EXCLUDED = frozenset({401, 403, 407})
_INTERNAL_LEAK = re.compile(r"\sat\s/|node_modules|omniroute/|Traceback", re.IGNORECASE)
_CREDENTIAL_LEAK = re.compile(
    r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9._-]{8,}"
    r"|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|secret)"
    r"""\\?["']?\s*[:=]\s*\\?["']?[^"'\\,\s}]{6,}""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteTarget:
    """One upstream: where to send, which credential, which model id to pin."""

    base_url: str
    api_key: str | None = None
    model_id: str | None = None
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES


@dataclass(frozen=True)
class Deadlines:
    """Streaming deadlines in seconds: request start to first chunk, then between chunks."""

    first_byte_s: float
    idle_s: float

    def __post_init__(self) -> None:
        if self.first_byte_s <= 0 or self.idle_s <= 0:
            raise ValueError("deadlines must be positive")


class UpstreamStreamAborted(Exception):
    """Raised by ``RelayResult.stream`` after its terminal error event: abort, do not close cleanly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class RelayResult:
    """A relayed upstream response: ``body`` for non-SSE, ``stream`` for SSE."""

    status: int
    headers: dict[str, str]
    body: bytes = b""
    stream: AsyncIterator[bytes] | None = None
    _response: httpx.Response | None = field(default=None, repr=False)

    async def aclose(self) -> None:
        """Close the stream and the upstream response. Safe to call more than once."""
        stream = self.stream
        if isinstance(stream, AsyncGenerator):
            # A RuntimeError means another task is still inside the generator; closing the
            # response below ends it.
            with contextlib.suppress(RuntimeError):
                await stream.aclose()
        if self._response is not None:
            await self._response.aclose()


async def relay_chat(
    request_body: dict[str, Any],
    target: RouteTarget,
    deadlines: Deadlines,
    client: httpx.AsyncClient,
    *,
    request_id: str | None = None,
) -> RelayResult:
    """POST ``request_body`` to the target's ``/chat/completions``."""
    return await _relay("chat/completions", request_body, target, deadlines, client, request_id)


async def relay_embeddings(
    request_body: dict[str, Any],
    target: RouteTarget,
    deadlines: Deadlines,
    client: httpx.AsyncClient,
    *,
    request_id: str | None = None,
) -> RelayResult:
    """POST ``request_body`` to the target's ``/embeddings``."""
    return await _relay("embeddings", request_body, target, deadlines, client, request_id)


async def _relay(
    path: str,
    request_body: dict[str, Any],
    target: RouteTarget,
    deadlines: Deadlines,
    client: httpx.AsyncClient,
    request_id: str | None,
) -> RelayResult:
    payload = dict(request_body)
    if target.model_id is not None:
        payload["model"] = target.model_id
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": PITWALL_OPENAI_PROXY_USER_AGENT,
    }
    if target.api_key:
        headers["authorization"] = f"Bearer {target.api_key}"
    # The relay owns every deadline; the client's own timeout would cut a healthy long stream.
    request = client.build_request(
        "POST",
        build_openai_url(target.base_url, path),
        headers=headers,
        content=json.dumps(payload).encode(),
        timeout=None,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        async with asyncio.timeout(deadlines.first_byte_s):
            response = await client.send(request, stream=True)
    except TimeoutError:
        return _error_result(
            504, "Upstream request timed out.", "gateway_timeout", "gateway_timeout", request_id
        )
    except httpx.HTTPError as exc:
        message = redact_text(
            f"Upstream request failed: {type(exc).__name__}", secrets=_secrets(target)
        )
        return _error_result(502, message, "api_error", "bad_gateway", request_id)

    relayed = _relayed_headers(response)
    is_stream = response.headers.get("content-type", "").lower().startswith("text/event-stream")
    # An error status never streams: _read_body bounds and redacts it like any other error.
    if is_stream and response.status_code < 400:
        remaining = max(deadlines.first_byte_s - (loop.time() - started), 0.0)
        result = RelayResult(response.status_code, relayed, _response=response)
        result.stream = _stream(response, remaining, deadlines.idle_s)
        return result
    try:
        return await _read_body(response, relayed, target, deadlines, request_id)
    finally:
        await response.aclose()


def _byte_generator(response: httpx.Response) -> AsyncGenerator[bytes]:
    # httpx implements aiter_bytes as an async generator; hold it as one so it can be closed.
    return cast(AsyncGenerator[bytes], response.aiter_bytes())


def _relayed_headers(response: httpx.Response) -> dict[str, str]:
    relayed = {"content-type": response.headers.get("content-type", "application/json")}
    for name, value in response.headers.items():
        lower = name.lower()
        if lower == "retry-after" or lower.startswith(_RELAYED_HEADER_PREFIXES):
            relayed[lower] = value
    return relayed


async def _read_body(
    response: httpx.Response,
    relayed: dict[str, str],
    target: RouteTarget,
    deadlines: Deadlines,
    request_id: str | None,
) -> RelayResult:
    cap = target.max_body_bytes
    too_large = _error_result(
        502,
        f"Upstream response exceeds the {cap} byte cap.",
        "api_error",
        "upstream_body_too_large",
        request_id,
    )
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        return too_large
    chunks: list[bytes] = []
    total = 0
    iterator = _byte_generator(response)
    try:
        while True:
            try:
                async with asyncio.timeout(deadlines.idle_s):
                    chunk = await anext(iterator)
            except StopAsyncIteration:
                break
            total += len(chunk)
            if total > cap:
                return too_large
            chunks.append(chunk)
    except TimeoutError:
        return _error_result(
            504, "Upstream response timed out.", "gateway_timeout", "gateway_timeout", request_id
        )
    except httpx.HTTPError:
        return _error_result(
            502, "Upstream response failed.", "api_error", "bad_gateway", request_id
        )
    finally:
        await iterator.aclose()
    body = b"".join(chunks)
    if response.status_code >= 400:
        return RelayResult(
            response.status_code,
            {**relayed, "content-type": "application/json"},
            _safe_error_body(response.status_code, body, target, request_id),
        )
    return RelayResult(response.status_code, relayed, body)


async def _stream(
    response: httpx.Response, first_byte_s: float, idle_s: float
) -> AsyncGenerator[bytes]:
    iterator = _byte_generator(response)
    limit = first_byte_s
    first = True
    try:
        while True:
            try:
                async with asyncio.timeout(limit):
                    chunk = await anext(iterator)
            except StopAsyncIteration:
                return
            except TimeoutError:
                code = "upstream_first_byte_timeout" if first else "upstream_idle_timeout"
                message = "Upstream sent no data before the deadline."
                yield _sse_error(code, message)
                raise UpstreamStreamAborted(code, message) from None
            except httpx.HTTPError as exc:
                message = f"Upstream stream failed: {type(exc).__name__}"
                yield _sse_error("upstream_stream_failed", message)
                raise UpstreamStreamAborted("upstream_stream_failed", message) from exc
            if chunk:
                first = False
                limit = idle_s
                yield chunk
    finally:
        await iterator.aclose()
        await response.aclose()


def _sse_error(code: str, message: str) -> bytes:
    payload = {"error": {"message": message, "type": "api_error", "code": code}}
    return f"event: error\ndata: {json.dumps(payload)}\n\n".encode()


def _secrets(target: RouteTarget) -> tuple[str, ...]:
    return (target.api_key,) if target.api_key else ()


def _error_type(status: int) -> tuple[str, str]:
    if status == 401:
        return "authentication_error", "invalid_api_key"
    if status == 429:
        return "rate_limit_error", "rate_limit_exceeded"
    if status < 500:
        return "invalid_request_error", "bad_request"
    return "api_error", "bad_gateway"


def _envelope(
    status: int, message: str, type_: str | None, code: str | None, request_id: str | None
) -> bytes:
    default_type, default_code = _error_type(status)
    error: dict[str, str] = {
        "message": message[:_ERROR_MESSAGE_LIMIT],
        "type": type_ or default_type,
        "code": code or default_code,
    }
    if request_id:
        error["request_id"] = request_id
    return json.dumps({"error": error}).encode()


def _error_result(
    status: int, message: str, type_: str, code: str, request_id: str | None
) -> RelayResult:
    return RelayResult(
        status,
        {"content-type": "application/json"},
        _envelope(status, message, type_, code, request_id),
    )


def _redact_json(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return redact_text(value, secrets=secrets)
    if isinstance(value, list):
        return [_redact_json(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _redact_json(item, secrets) for key, item in value.items()}
    return value


def _safe_error_body(
    status: int, body: bytes, target: RouteTarget, request_id: str | None
) -> bytes:
    """Never relay an upstream error byte for byte: a provider may echo the key this gateway holds.

    An eligible 4xx JSON body keeps its wording (clients recover from it) after recursive
    redaction. Every other error (auth errors, 5xx, non-JSON, or a body echoing a credential)
    becomes the gateway's own envelope carrying the redacted upstream message.
    """
    secrets = _secrets(target)
    text = body.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        parsed = None
    if (
        isinstance(parsed, dict)
        and 400 <= status <= 499
        and status not in _PASSTHROUGH_EXCLUDED
        and not _INTERNAL_LEAK.search(text)
        and not _CREDENTIAL_LEAK.search(text)
        and redact_text(text, secrets=secrets) == text
    ):
        return json.dumps(_redact_json(parsed, secrets)).encode()
    nested = parsed.get("error") if isinstance(parsed, dict) else None
    nested_message = nested.get("message") if isinstance(nested, dict) else None
    message = nested_message if isinstance(nested_message, str) else text
    return _envelope(status, redact_text(message, secrets=secrets), None, None, request_id)
