"""Pitwall webhook receiver.

Runs as ``python -m pitwall.webhook_receiver`` on port 8082.

The RunPod callback endpoint accepts duplicate deliveries and returns 200
quickly. Durable result reconciliation is handled by downstream storage jobs;
this receiver keeps the ingress contract idempotent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pitwall.config import parse_rate_limit, require_credentials_for_bind, require_runtime_env
from pitwall.db.repository import WebhookDeliveryRepository
from pitwall.rate_limits import TokenBucket
from pitwall.security.redaction import configure_logging_redaction
from pitwall.webhook_dispatcher.signer import verify as verify_signature
from pitwall.webhook_receiver.runpod import RunPodWebhookEvent, normalize_runpod_webhook

configure_logging_redaction()
require_runtime_env("webhook")
require_credentials_for_bind(
    "webhook receiver",
    os.environ.get("PITWALL_WEBHOOK_HOST", "127.0.0.1"),
    ("PITWALL_WEBHOOK_SECRET",),
)

log = logging.getLogger("pitwall.webhook_receiver")

# Inbound authentication. Every inbound delivery must carry a valid
# X-Pitwall-Webhook-Signature (the same timestamped HMAC scheme used for
# outbound dispatch — constant-time compare, 300s replay window). The service
# refuses to start when PITWALL_WEBHOOK_SECRET is unset (see lifespan).
_WEBHOOK_SECRET = os.environ.get("PITWALL_WEBHOOK_SECRET", "")
try:
    _PREVIOUS_WEBHOOK_SECRETS = json.loads(os.environ.get("PITWALL_WEBHOOK_PREVIOUS_SECRETS", "[]"))
except json.JSONDecodeError as exc:
    raise SystemExit(os.EX_CONFIG) from exc
if not isinstance(_PREVIOUS_WEBHOOK_SECRETS, list) or not all(
    isinstance(secret, str) and secret for secret in _PREVIOUS_WEBHOOK_SECRETS
):
    raise SystemExit(os.EX_CONFIG)
_WEBHOOK_SECRETS = tuple(
    dict.fromkeys(secret for secret in (_WEBHOOK_SECRET, *_PREVIOUS_WEBHOOK_SECRETS) if secret)
)
_SIGNATURE_HEADER = "X-Pitwall-Webhook-Signature"


def require_webhook_secret() -> None:
    """Refuse to serve without an inbound webhook secret."""
    if not _WEBHOOK_SECRETS:
        raise RuntimeError(
            "PITWALL_WEBHOOK_SECRET is not set; the webhook receiver refuses to accept "
            "unauthenticated deliveries"
        )


_MAX_BODY_BYTES = int(os.environ.get("PITWALL_WEBHOOK_MAX_BODY_BYTES", str(1024 * 1024)))
if _MAX_BODY_BYTES <= 0:
    raise SystemExit(os.EX_CONFIG)


_parse_rate_limit = parse_rate_limit


try:
    _WEBHOOK_RATE_LIMIT = _parse_rate_limit(os.environ.get("PITWALL_WEBHOOK_RATE_LIMIT", "120/60s"))
except ValueError as exc:
    raise SystemExit(os.EX_CONFIG) from exc


class _RequestBodyTooLarge(RuntimeError):
    pass


class WebhookIngressGuardMiddleware:
    """Reject invalid media, oversized bodies, and abusive callers before parsing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        rate_limit: tuple[int, float],
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes
        self._rate_limit = rate_limit
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in {
            "/webhooks/runpod",
            "/runpod",
        }:
            await self._app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_type = headers.get(b"content-type", b"").decode("latin1").split(";", 1)[0]
        if content_type.strip().lower() != "application/json":
            await self._response(scope, receive, send, 415, "content type must be application/json")
            return
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                await self._response(scope, receive, send, 400, "invalid content length")
                return
            if content_length < 0 or content_length > self._max_body_bytes:
                await self._response(scope, receive, send, 413, "webhook body too large")
                return
        retry_after = await self._retry_after(scope)
        if retry_after is not None:
            response = JSONResponse(
                status_code=429,
                content={"ok": False, "detail": "webhook rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._response(scope, receive, send, 413, "webhook body too large")

    async def _retry_after(self, scope: Scope) -> int | None:
        client = scope.get("client")
        client_ip = str(client[0]) if isinstance(client, tuple) and client else "unknown"
        requests, window_s = self._rate_limit
        now_s = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(client_ip)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=requests,
                    refill_window_s=window_s,
                    last_refilled_at_s=now_s,
                )
                self._buckets[client_ip] = bucket
            if bucket.try_consume(1, now_s=now_s):
                return None
            return max(1, int(bucket.retry_after_s(1) + 0.999))

    @staticmethod
    async def _response(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(status_code=status_code, content={"ok": False, "detail": detail})
        await response(scope, receive, send)


_RUNPOD_TERMINAL_STATUSES = frozenset(
    {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMED_OUT",
        "TIMEOUT",
        "TIME_OUT",
    }
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    require_webhook_secret()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    # Shared pool factory: registers the jsonb codec (payload columns take
    # dicts) and the PgBouncer-safe statement-cache settings.
    from pitwall.db import close_pool, get_pool

    pool = await get_pool(database_url, min_size=1, max_size=4)
    app.state.pool = pool

    redis_url = os.environ.get("REDIS_URL")
    redis_settings = None
    if redis_url:
        try:
            from arq.connections import RedisSettings

            redis_settings = RedisSettings.from_dsn(redis_url)
        except Exception:  # reason: invalid REDIS_URL disables enqueue; receiver stays up
            log.warning("Failed to create Redis settings; webhook job enqueuing disabled")
    app.state.redis_settings = redis_settings
    app.state.redis_required = redis_settings is not None
    app.state.redis = (
        redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]  # reason: redis.from_url is untyped in redis-py
        if redis_settings is not None and redis_url is not None
        else None
    )

    try:
        yield
    finally:
        arq_pool = getattr(app.state, "arq_pool", None)
        if arq_pool is not None:
            await arq_pool.aclose()
        redis_client: redis.Redis | None = app.state.redis
        if redis_client is not None:
            await redis_client.aclose()
        await close_pool()


app = FastAPI(title="Pitwall Webhook Receiver", version="1", lifespan=lifespan)
app.add_middleware(
    WebhookIngressGuardMiddleware,
    max_body_bytes=_MAX_BODY_BYTES,
    rate_limit=_WEBHOOK_RATE_LIMIT,
)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "webhook-receiver"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "webhook-receiver"}


async def _postgres_health(request: Request) -> dict[str, Any]:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        return {"ok": False, "error": "unavailable"}
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:  # pragma: no cover  # reason: dependency failures are reported, not raised
        return {"ok": False, "error": "unavailable"}
    return {"ok": True}


async def _redis_health(request: Request) -> dict[str, Any]:
    redis_required = bool(getattr(request.app.state, "redis_required", False))
    redis_client: redis.Redis | None = getattr(request.app.state, "redis", None)
    if not redis_required:
        return {"ok": True, "required": False}
    if redis_client is None:
        return {"ok": False, "error": "unavailable"}
    try:
        await redis_client.ping()
    except Exception:  # pragma: no cover  # reason: dependency failures are reported, not raised
        return {"ok": False, "error": "unavailable"}
    return {"ok": True}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Return success only when configured ingestion dependencies are reachable."""

    postgres = await _postgres_health(request)
    redis_check = await _redis_health(request)
    ok = bool(postgres["ok"] and redis_check["ok"])
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "postgres": postgres, "redis": redis_check},
    )


@app.post("/webhooks/runpod", response_model=None)
@app.post("/runpod", response_model=None)
async def runpod_webhook(request: Request) -> dict[str, Any] | JSONResponse:
    body = await request.body()
    if _WEBHOOK_SECRETS:
        signature = request.headers.get(_SIGNATURE_HEADER, "")
        signature_valid = False
        for secret in _WEBHOOK_SECRETS:
            signature_valid = verify_signature(body, signature, secret) or signature_valid
        if not signature_valid:
            return JSONResponse(
                status_code=401,
                content={"ok": False, "detail": "invalid or missing webhook signature"},
            )
    try:
        decoded = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "detail": "webhook body must be valid JSON"},
        )
    if not isinstance(decoded, dict):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "detail": "webhook body must be a JSON object"},
        )
    payload = decoded

    headers = dict(request.headers)
    event: RunPodWebhookEvent | None = None
    with suppress(ValueError):
        event = normalize_runpod_webhook(payload, headers)

    if event is not None:
        key, attempt, status = event.runpod_job_id, event.attempt, event.status
    else:
        key = _fallback_idempotency_key(payload, body, headers)
        attempt, status = 1, payload.get("status", "")
    if status in _RUNPOD_TERMINAL_STATUSES:
        try:
            await _enqueue_terminal_status_job(request.app, key, status)
        except Exception:  # reason: nothing recorded yet, so RunPod's retry is processed
            log.warning(
                "Failed to enqueue terminal status job for runpod_job_id=%s status=%s",
                key,
                status,
            )
            return JSONResponse(
                status_code=503,
                content={"ok": False, "detail": "could not enqueue webhook job; retry later"},
            )
    repo = WebhookDeliveryRepository(request.app.state.pool)
    result = await repo.insert_or_skip(runpod_job_id=key, attempt=attempt, payload=payload)
    return {"ok": True, "duplicate": not result.is_new}


_ARQ_POOL_LOCK = asyncio.Lock()


async def _arq_pool(app: FastAPI) -> Any:
    """Return the process-wide Arq pool, creating it on first use."""
    pool = getattr(app.state, "arq_pool", None)
    if pool is not None:
        return pool
    async with _ARQ_POOL_LOCK:
        pool = getattr(app.state, "arq_pool", None)
        if pool is None:
            from arq import create_pool

            pool = await create_pool(app.state.redis_settings)
            app.state.arq_pool = pool
    return pool


async def _enqueue_terminal_status_job(app: FastAPI, runpod_job_id: str, status: str) -> None:
    """Enqueue the terminal-status job; raises when Redis is configured but enqueue fails."""
    if app.state.redis_settings is None:
        return
    arq_redis = await _arq_pool(app)
    # The name is the one ``WorkerSettings.functions`` registers in the reconciler.
    await arq_redis.enqueue_job(
        "_process_webhook_terminal_status",
        runpod_job_id,
        status,
        _job_id=f"webhook-terminal:{runpod_job_id}:{status}",
    )


def _fallback_idempotency_key(
    payload: dict[str, Any],
    body: bytes,
    headers: dict[str, str],
) -> str:
    for field_name in ("id", "job_id", "jobId", "runpod_job_id"):
        value = payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()

    header_value = headers.get("RunPod-Job-Id") or headers.get("X-RunPod-Job-Id")
    if header_value and header_value.strip():
        return header_value.strip()

    return hashlib.sha256(body).hexdigest()


__all__ = ["app"]
