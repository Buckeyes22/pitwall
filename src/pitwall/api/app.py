"""Pitwall REST API app — fail-closed boot and health endpoints.

This module must be imported after required env vars are set.
Refuses to boot (os.EX_CONFIG) if DATABASE_URL or REDIS_URL are unset at
import time. RUNPOD_API_KEY is only required when the first RunPod operation runs. PITWALL_ADMIN_SECRET enables
admin routes; without it, /v1/admin/* fails closed at the middleware.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import os
import time
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pitwall.api.admin.audit_capability import router as audit_capability_router
from pitwall.api.admin.emergency import router as emergency_router
from pitwall.api.capability_routes import router as capability_router
from pitwall.api.exceptions import install_api_error_handler
from pitwall.api.lifespan import api_lifespan
from pitwall.api.provider_routes import router as provider_router
from pitwall.api.routes import (
    burn_rate_router,
    cost_router,
    gateway_models_router,
    guardrail_router,
    inference_router,
    lease_router,
    model_catalogue_router,
    onboarding_router,
    provider_operations_router,
    quotas_router,
    routing_router,
    runpod_market_router,
    runpod_resources_router,
    serve_router,
    volume_file_router,
    webhook_subscription_router,
)
from pitwall.api.routes.budget import router as budget_router
from pitwall.api.routes.jobs import router as jobs_router
from pitwall.api.routes.messages import router as messages_router
from pitwall.api.routes.openai import router as openai_router
from pitwall.api.routes.volume_files import install_volume_file_error_handler
from pitwall.api.scopes import (
    ALL_API_SCOPES,
    GRANTED_SCOPES_KEY,
    LEASE_MUTATION_SCOPE,
    READ_SCOPE,
    SERVER_ADMIN_SCOPE,
    SPEND_SCOPE,
    WEBHOOK_ADMIN_SCOPE,
    parse_scoped_tokens,
)
from pitwall.config import (
    parse_inbound_rate_limit,
    require_credentials_for_bind,
    require_runtime_env,
)
from pitwall.cost import BudgetRejected
from pitwall.cost.budget_gate import BudgetNotConfigured
from pitwall.rate_limits import TokenBucket
from pitwall.security.redaction import configure_logging_redaction

configure_logging_redaction()
log = logging.getLogger("pitwall.api.app")

require_runtime_env("api")
require_credentials_for_bind(
    "api",
    os.environ.get("PITWALL_API_HOST", "127.0.0.1"),
    ("PITWALL_API_TOKEN", "PITWALL_ADMIN_SECRET"),
)

_PITWALL_ADMIN_SECRET = os.environ.get("PITWALL_ADMIN_SECRET") or None
_PITWALL_API_TOKEN = os.environ.get("PITWALL_API_TOKEN") or None


_PUBLIC_HEALTH_PATHS = frozenset(
    {
        "/health",
        "/healthz",
        "/readyz",
        "/v1/health",
    }
)


class InboundRateLimitConfig:
    """Opt-in inbound request limit as a token bucket."""

    def __init__(self, *, requests: int, window_s: float) -> None:
        if requests <= 0:
            raise ValueError("requests must be > 0")
        if not math.isfinite(window_s) or window_s <= 0:
            raise ValueError("window_s must be a finite number > 0")
        self.requests = requests
        self.window_s = window_s


def _is_public_health_path(path: str) -> bool:
    return path in _PUBLIC_HEALTH_PATHS


def _headers(scope: Scope) -> dict[bytes, bytes]:
    headers: dict[bytes, bytes] = {}
    for key, value in scope.get("headers", []):
        headers[key.lower()] = value
    return headers


def _bearer_token(scope: Scope) -> str | None:
    try:
        header_value = _headers(scope).get(b"authorization", b"").decode("utf-8")
    except UnicodeDecodeError:
        return None  # not a token any configured (UTF-8) token could match
    scheme, separator, token = header_value.partition(" ")
    if separator != " " or scheme.lower() != "bearer":
        return None
    return token


def _required_scope(method: str, path: str) -> str:
    """Map a request to its least-privilege API scope."""

    method = method.upper()
    if path.startswith("/v1/admin/runpod/") and method in {"GET", "HEAD"}:
        return READ_SCOPE
    if path == "/v1/admin/quotas/refresh" and method == "POST":
        return SERVER_ADMIN_SCOPE
    if path == "/v1/admin" or path.startswith("/v1/admin/"):
        return SERVER_ADMIN_SCOPE
    if path == "/v1/webhook-subscriptions" or path.startswith("/v1/webhook-subscriptions/"):
        return WEBHOOK_ADMIN_SCOPE
    if path == "/v1/inference" or path == "/v1/messages" or path.startswith("/v1/openai/"):
        return SPEND_SCOPE
    if path == "/v1/serve" and method == "POST":
        return SPEND_SCOPE
    if path == "/v1/jobs" and method == "POST":
        return SPEND_SCOPE
    if path.startswith("/v1/jobs/") and method in {"POST", "PATCH", "DELETE"}:
        return SPEND_SCOPE
    if path == "/v1/leases" and method == "POST":
        return SPEND_SCOPE
    if path.startswith("/v1/leases/") and method in {"POST", "PATCH", "DELETE"}:
        return LEASE_MUTATION_SCOPE
    return READ_SCOPE


class BearerTokenAuthorizer:
    """Constant-time bearer-token lookup with explicit scope grants."""

    def __init__(self, master_token: str | None, scoped_tokens_json: str | None) -> None:
        entries: list[tuple[str, frozenset[str]]] = []
        if master_token is not None:
            entries.append((master_token, ALL_API_SCOPES))
        if scoped_tokens_json:
            entries.extend(self._parse_scoped_tokens(scoped_tokens_json))
        # Compared as bytes: hmac.compare_digest raises TypeError on non-ASCII str.
        self._entries = tuple((token.encode("utf-8"), scopes) for token, scopes in entries)

    @property
    def enabled(self) -> bool:
        return bool(self._entries)

    def scopes_for(self, token: str | None) -> frozenset[str] | None:
        if token is None:
            return None
        try:
            presented = token.encode("utf-8")
        except UnicodeEncodeError:
            return None  # lone surrogates: no configured token can match
        matched: frozenset[str] | None = None
        for candidate, scopes in self._entries:
            if hmac.compare_digest(presented, candidate):
                matched = scopes
        return matched

    @staticmethod
    def _parse_scoped_tokens(raw: str) -> list[tuple[str, frozenset[str]]]:
        return parse_scoped_tokens(raw)


try:
    _API_AUTHORIZER = BearerTokenAuthorizer(
        _PITWALL_API_TOKEN,
        os.environ.get("PITWALL_API_SCOPED_TOKENS"),
    )
except ValueError as exc:
    log.critical("invalid API authorization configuration: %s", exc)
    raise SystemExit(os.EX_CONFIG) from exc


def _parse_inbound_rate_limit(value: str | None) -> InboundRateLimitConfig | None:
    parsed = parse_inbound_rate_limit(value)
    if parsed is None:
        return None
    requests, window_s = parsed
    return InboundRateLimitConfig(requests=requests, window_s=window_s)


def _load_inbound_rate_limit_config() -> InboundRateLimitConfig | None:
    raw_value = os.environ.get("PITWALL_INBOUND_RATE_LIMIT", "120/60s")
    try:
        return _parse_inbound_rate_limit(raw_value)
    except (TypeError, ValueError) as exc:
        log.critical("invalid PITWALL_INBOUND_RATE_LIMIT configuration: %s", exc)
        raise SystemExit(os.EX_CONFIG) from exc


_INBOUND_RATE_LIMIT_CONFIG = _load_inbound_rate_limit_config()
try:
    _MAX_BODY_BYTES = int(os.environ.get("PITWALL_API_MAX_BODY_BYTES", str(8 * 1024 * 1024)))
except ValueError as exc:
    raise SystemExit(os.EX_CONFIG) from exc
if _MAX_BODY_BYTES <= 0:
    raise SystemExit(os.EX_CONFIG)


class RequestBodyLimitMiddleware:
    """Bound every HTTP request body before application parsing or provider calls."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        raw_length = _headers(scope).get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                await self._reject(scope, receive, send, 400, "invalid content length")
                return
            if content_length < 0 or content_length > self._max_body_bytes:
                await self._reject(scope, receive, send, 413, "request body too large")
                return

        # Read and bound the request before invoking a route.  This makes the
        # decision safe for chunked requests too: a handler can never start a
        # response and then discover that a later body chunk exceeds the cap.
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self._max_body_bytes:
                await self._reject(scope, receive, send, 413, "request body too large")
                return
            more_body = bool(message.get("more_body", False))

        replayed = False

        async def bounded_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, bounded_receive, send)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"error": "request_rejected", "detail": detail},
        )
        await response(scope, receive, send)


class AdminSecretMiddleware:
    def __init__(self, app: ASGIApp, secret: str | None) -> None:
        self._app = app
        # Compared as bytes: hmac.compare_digest raises TypeError on non-ASCII str,
        # and a header is arbitrary bytes, so a str compare let one crash the gate.
        self._secret = secret.encode("utf-8") if secret is not None else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            runpod_read = path.startswith("/v1/admin/runpod/") and scope.get(
                "method", "GET"
            ).upper() in {"GET", "HEAD"}
            if (path == "/v1/admin" or path.startswith("/v1/admin/")) and not runpod_read:
                secret = self._secret
                if secret is None:
                    response = JSONResponse(
                        status_code=401,
                        content={
                            "detail": (
                                "admin routes disabled: PITWALL_ADMIN_SECRET is not configured"
                            )
                        },
                    )
                    await response(scope, receive, send)
                    return
                headers: dict[bytes, bytes] = {}
                for key, value in scope.get("headers", []):
                    headers[key] = value
                header_val = headers.get(b"x-pitwall-secret", b"")
                if not hmac.compare_digest(header_val, secret):
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "invalid or missing X-Pitwall-Secret"},
                    )
                    await response(scope, receive, send)
                    return
        await self._app(scope, receive, send)


class ApiTokenMiddleware:
    def __init__(self, app: ASGIApp, authorizer: BearerTokenAuthorizer) -> None:
        self._app = app
        self._authorizer = authorizer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self._authorizer.enabled:
            # Loopback development without tokens: the caller holds every scope.
            scope[GRANTED_SCOPES_KEY] = ALL_API_SCOPES
        if scope["type"] == "http" and self._authorizer.enabled:
            path = scope.get("path", "")
            if not _is_public_health_path(path):
                token = _bearer_token(scope)
                granted_scopes = self._authorizer.scopes_for(token)
                if granted_scopes is None:
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "invalid or missing bearer token"},
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                    await response(scope, receive, send)
                    return
                scope[GRANTED_SCOPES_KEY] = granted_scopes
                required_scope = _required_scope(scope.get("method", "GET"), path)
                if required_scope not in granted_scopes:
                    response = JSONResponse(
                        status_code=403,
                        content={
                            "detail": "bearer token lacks required scope",
                            "required_scope": required_scope,
                        },
                    )
                    await response(scope, receive, send)
                    return
        await self._app(scope, receive, send)


_MAX_RATE_BUCKETS = 10_000


class InboundRateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        config: InboundRateLimitConfig | None,
        authorizer: BearerTokenAuthorizer,
        max_buckets: int = _MAX_RATE_BUCKETS,
    ) -> None:
        self._app = app
        self._config = config
        self._authorizer = authorizer
        self._max_buckets = max_buckets
        # Least recently used first; bounded by ``max_buckets`` and idle expiry.
        self._buckets: OrderedDict[tuple[str, str | None], tuple[TokenBucket, float]] = (
            OrderedDict()
        )
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._config is None:
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "")
        if _is_public_health_path(path):
            await self._app(scope, receive, send)
            return

        try:
            retry_after_s = await self._retry_after_s(scope)
        except Exception:  # reason: abuse-control failure must not admit paid work
            log.exception("inbound rate limiter failed closed")
            response = JSONResponse(
                status_code=503,
                content={"error": "rate_limiter_unavailable"},
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return

        if retry_after_s is not None:
            response = JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded"},
                headers={"Retry-After": str(retry_after_s)},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)

    async def _retry_after_s(self, scope: Scope) -> int | None:
        config = self._config
        if config is None:
            return None

        key = self._bucket_key(scope)
        now_s = time.monotonic()
        async with self._lock:
            self._evict(now_s, config.window_s)
            entry = self._buckets.get(key)
            if entry is None:
                bucket = TokenBucket(
                    capacity=config.requests,
                    refill_window_s=config.window_s,
                    last_refilled_at_s=now_s,
                )
            else:
                bucket = entry[0]
            self._buckets[key] = (bucket, now_s)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max_buckets:
                self._buckets.popitem(last=False)

            if bucket.try_consume(1.0, now_s=now_s):
                return None

            return max(1, math.ceil(bucket.retry_after_s(1.0)))

    def _evict(self, now_s: float, window_s: float) -> None:
        """Drop buckets idle for a full refill window; they equal a fresh bucket."""
        while self._buckets:
            oldest_key = next(iter(self._buckets))
            _bucket, last_seen_s = self._buckets[oldest_key]
            if now_s - last_seen_s <= window_s:
                break
            del self._buckets[oldest_key]

    def _bucket_key(self, scope: Scope) -> tuple[str, str | None]:
        client = scope.get("client")
        client_ip = "unknown"
        if isinstance(client, tuple) and client:
            client_ip = str(client[0])

        token_digest: str | None = None
        token = _bearer_token(scope)
        if self._authorizer.scopes_for(token) is not None and token is not None:
            token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return client_ip, token_digest


app = FastAPI(title="Pitwall API", version="1", lifespan=api_lifespan)

# Each add_middleware call wraps the previous ones, so the last call runs first:
# rate limit, bearer auth, admin secret, then the body cap innermost. Nothing
# buffers a request body until the caller has been authenticated.
app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=_MAX_BODY_BYTES)
app.add_middleware(AdminSecretMiddleware, secret=_PITWALL_ADMIN_SECRET)
app.add_middleware(ApiTokenMiddleware, authorizer=_API_AUTHORIZER)
app.add_middleware(
    InboundRateLimitMiddleware,
    config=_INBOUND_RATE_LIMIT_CONFIG,
    authorizer=_API_AUTHORIZER,
)

if _PITWALL_ADMIN_SECRET:
    log.info("admin routes enabled: PITWALL_ADMIN_SECRET is configured")
else:
    log.warning("admin routes disabled: PITWALL_ADMIN_SECRET is not configured")

if _API_AUTHORIZER.enabled:
    log.info("scoped bearer authorization enabled")
else:
    log.warning("API authorization disabled for loopback development")

if _INBOUND_RATE_LIMIT_CONFIG:
    log.info(
        "inbound rate limiter enabled: %s requests per %s seconds",
        _INBOUND_RATE_LIMIT_CONFIG.requests,
        _INBOUND_RATE_LIMIT_CONFIG.window_s,
    )

app.include_router(capability_router)
app.include_router(provider_router)
app.include_router(audit_capability_router)
app.include_router(emergency_router)
app.include_router(lease_router)
app.include_router(inference_router)
app.include_router(messages_router)
app.include_router(serve_router)
app.include_router(jobs_router)
app.include_router(routing_router)
app.include_router(model_catalogue_router)
app.include_router(provider_operations_router)
app.include_router(burn_rate_router)
app.include_router(cost_router)
app.include_router(budget_router)
app.include_router(guardrail_router)
app.include_router(volume_file_router)
app.include_router(runpod_market_router)
app.include_router(runpod_resources_router)
app.include_router(onboarding_router)
app.include_router(openai_router)
app.include_router(webhook_subscription_router)
app.include_router(quotas_router)
app.include_router(gateway_models_router)
install_volume_file_error_handler(app)


install_api_error_handler(app)


@app.exception_handler(BudgetRejected)
async def budget_rejected_exception_handler(_request: Request, exc: BudgetRejected) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())


@app.exception_handler(BudgetNotConfigured)
async def budget_not_configured_exception_handler(
    _request: Request, exc: BudgetNotConfigured
) -> JSONResponse:
    """A spend path (lease launch, renewal) on a broker without a valid budget: typed 503."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Reject malformed input without reflecting request-controlled values."""
    detail = [
        {"type": str(error.get("type", "value_error"))}
        for error in exc.errors()
        if isinstance(error, Mapping)
    ]
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request", "detail": detail},
    )


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True}


async def _postgres_health(request: Request) -> dict[str, Any]:
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        return {"ok": False, "error": "pool not configured"}
    try:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception:  # pragma: no cover  # reason: report DB health failure.
        return {"ok": False, "error": "unavailable"}
    return {"ok": True}


async def _redis_health(request: Request) -> dict[str, Any]:
    """Report the Redis client the request path actually uses."""
    redis_client: redis.Redis | None = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return {"ok": False, "error": "not_configured"}
    try:
        await redis_client.ping()
    except Exception:  # pragma: no cover  # reason: report Redis health failure.
        return {"ok": False, "error": "unavailable"}
    return {"ok": True}


@app.get("/v1/health")
async def v1_health(request: Request) -> dict[str, Any]:
    postgres = await _postgres_health(request)
    redis_check = await _redis_health(request)
    ok = postgres["ok"] and redis_check["ok"]
    return {
        "ok": ok,
        "postgres": postgres,
        "redis": redis_check,
    }


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Dependency readiness; unlike liveness, returns 503 on dependency loss."""

    postgres = await _postgres_health(request)
    redis_check = await _redis_health(request)
    ok = bool(postgres["ok"] and redis_check["ok"])
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "postgres": postgres, "redis": redis_check},
    )


def _openapi_with_auth_contract() -> dict[str, Any]:
    """Publish middleware auth plus stable error-envelope contracts."""

    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    components = schema.setdefault("components", {})
    if isinstance(components, dict):
        schemas = components.setdefault("schemas", {})
        if isinstance(schemas, dict):
            schemas["ErrorResponse"] = {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "detail": {"type": "string"},
                    "required_scope": {"type": "string"},
                },
                "additionalProperties": True,
            }
        security_schemes = components.setdefault("securitySchemes", {})
        if isinstance(security_schemes, dict):
            security_schemes["BearerAuth"] = {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "opaque scoped token",
            }
            security_schemes["AdminSecret"] = {
                "type": "apiKey",
                "in": "header",
                "name": "X-Pitwall-Secret",
            }
    paths = schema.get("paths", {})
    if isinstance(paths, dict):
        for path, path_item in paths.items():
            if not isinstance(path, str) or not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                    continue
                if not isinstance(operation, dict) or _is_public_health_path(path):
                    continue
                required_scope = _required_scope(method, path)
                operation["x-required-scope"] = required_scope
                if required_scope == SERVER_ADMIN_SCOPE:
                    operation["security"] = [{"BearerAuth": [], "AdminSecret": []}]
                else:
                    operation["security"] = [{"BearerAuth": []}]
                responses = operation.setdefault("responses", {})
                if isinstance(responses, dict):
                    for status, description in (
                        ("400", "Malformed request"),
                        ("401", "Missing or invalid authentication"),
                        ("403", "Insufficient scope"),
                        ("404", "Resource not found"),
                        ("409", "State or idempotency conflict"),
                        ("413", "Request body too large"),
                        ("422", "Request validation failed"),
                        ("429", "Rate limit exceeded"),
                        ("503", "Dependency unavailable"),
                    ):
                        response = responses.setdefault(
                            status,
                            {
                                "description": description,
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                    }
                                },
                            },
                        )
                        if status == "401" and isinstance(response, dict):
                            response.setdefault(
                                "headers",
                                {
                                    "WWW-Authenticate": {
                                        "schema": {"type": "string"},
                                        "description": "Authentication challenge",
                                    }
                                },
                            )
                        if status == "429" and isinstance(response, dict):
                            response.setdefault(
                                "headers",
                                {
                                    "Retry-After": {
                                        "schema": {"type": "string"},
                                        "description": "Seconds before retrying",
                                    }
                                },
                            )
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_auth_contract  # type: ignore[method-assign]  # reason: FastAPI supports custom OpenAPI callables.


__all__ = ["app"]
