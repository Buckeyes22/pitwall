"""Shared OpenAI-compatible fallback execution.

The executor stops at upstream response headers.  The caller owns the returned
response stream and must close both that response and the HTTP client after the
body is relayed.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx

from pitwall.core.models import Provider
from pitwall.providers.registry import declaration_for_provider
from pitwall.routing.openai import (
    MAX_OPENAI_ATTEMPTS,
    build_openai_url,
    openai_base_url_for_provider,
)

DEFAULT_OPENAI_FALLBACK_BUDGET_S = 5.0
# Do not forward a caller's client identity to a model endpoint.  In addition
# to leaking harness details, some hosted model proxies classify browser- or
# urllib-shaped identities as automation and reject them.  Keep one stable
# service identity for every broker-to-provider request.
PITWALL_OPENAI_PROXY_USER_AGENT = "pitwall"


@dataclass(frozen=True, slots=True)
class OpenAIProxyRequest:
    """Request data reused for every provider attempt."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    client: httpx.AsyncClient
    fallback_budget_s: float = DEFAULT_OPENAI_FALLBACK_BUDGET_S
    max_attempts: int = MAX_OPENAI_ATTEMPTS


@dataclass(frozen=True, slots=True)
class UpstreamRateLimit:
    """One provider's 429 reply: enough for the caller to record a lockout."""

    provider_id: str
    status_code: int
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class OpenAIProxyResult:
    """Selected upstream response and the providers actually attempted."""

    response: httpx.Response
    provider: Provider
    attempted_provider_ids: tuple[str, ...]
    elapsed_s: float
    rate_limited: tuple[UpstreamRateLimit, ...] = ()

    @property
    def upstream_response(self) -> httpx.Response:
        return self.response

    @property
    def provider_id(self) -> str:
        return self.provider.id

    @property
    def fallback_chain(self) -> tuple[str, ...]:
        return self.attempted_provider_ids

    @property
    def attempt_count(self) -> int:
        return len(self.attempted_provider_ids)


class OpenAIProxyExecutionError(RuntimeError):
    """Raised when no provider returns response headers."""

    def __init__(
        self,
        message: str,
        *,
        attempted_provider_ids: Sequence[str],
        cause: BaseException | None = None,
        attempted_errors: dict[str, str] | None = None,
        rate_limited: Sequence[UpstreamRateLimit] = (),
    ) -> None:
        super().__init__(message)
        self.attempted_provider_ids = tuple(attempted_provider_ids)
        self.cause = cause
        self.attempted_errors = attempted_errors or {}
        self.rate_limited = tuple(rate_limited)


async def execute_openai_with_fallback(
    request_ctx: OpenAIProxyRequest,
    providers: list[Provider],
    *,
    on_attempt: (Callable[[tuple[str, ...]], None] | Callable[[tuple[str, ...]], Awaitable[None]])
    | None = None,
) -> OpenAIProxyResult:
    """Execute an OpenAI-compatible request across a bounded provider chain.

    A provider is retried only when it fails before response body relay begins:
    an upstream 429 or 5xx arrives, or transport fails before response headers
    arrive.  Every other response, including other 4xx, is returned immediately.
    Each 429 is reported on the result so the caller can record a lockout.

    If ``on_attempt`` is provided, it will be called after each provider attempt
    with the current tuple of attempted provider IDs. This can be used to persist
    the fallback chain to a database after each attempt.
    """

    if isinstance(request_ctx.max_attempts, bool) or request_ctx.max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if request_ctx.fallback_budget_s <= 0:
        raise ValueError("fallback_budget_s must be positive")

    started_at = time.perf_counter()
    deadline = started_at + request_ctx.fallback_budget_s
    eligible_providers, skipped = _providers_with_openai_urls(
        providers,
        max_attempts=min(request_ctx.max_attempts, MAX_OPENAI_ATTEMPTS),
    )
    attempted_provider_ids: list[str] = []
    attempted_errors: dict[str, str] = dict(skipped)
    rate_limited: list[UpstreamRateLimit] = []
    last_error: BaseException | None = None

    for index, provider in enumerate(eligible_providers):
        remaining_s = deadline - time.perf_counter()
        if remaining_s <= 0:
            break

        attempted_provider_ids.append(provider.id)
        if on_attempt is not None:
            if inspect.iscoroutinefunction(on_attempt):
                await on_attempt(tuple(attempted_provider_ids))
            else:
                on_attempt(tuple(attempted_provider_ids))
        try:
            response = await _send_until_headers(
                request_ctx,
                provider,
                timeout_s=remaining_s,
            )
        except (TimeoutError, httpx.HTTPError) as exc:
            last_error = exc
            attempted_errors[provider.id] = str(exc)
            if index < len(eligible_providers) - 1 and deadline > time.perf_counter():
                continue
            break

        if response.status_code == 429:
            rate_limited.append(
                UpstreamRateLimit(provider.id, response.status_code, dict(response.headers))
            )
        if not _is_retryable_response(response):
            return OpenAIProxyResult(
                response=response,
                provider=provider,
                attempted_provider_ids=tuple(attempted_provider_ids),
                elapsed_s=time.perf_counter() - started_at,
                rate_limited=tuple(rate_limited),
            )

        attempted_errors[provider.id] = f"HTTP {response.status_code}"
        if index >= len(eligible_providers) - 1 or deadline <= time.perf_counter():
            return OpenAIProxyResult(
                response=response,
                provider=provider,
                attempted_provider_ids=tuple(attempted_provider_ids),
                elapsed_s=time.perf_counter() - started_at,
                rate_limited=tuple(rate_limited),
            )

        try:
            await response.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:  # reason: cleanup failure must not mask the fallback decision
            pass  # cleanup failure must not mask the bounded fallback decision

    message = "openai proxy upstream request failed before response headers"
    error = OpenAIProxyExecutionError(
        message,
        attempted_provider_ids=attempted_provider_ids,
        cause=last_error,
        attempted_errors=attempted_errors,
        rate_limited=rate_limited,
    )
    if last_error is not None:
        raise error from last_error
    raise error


def _providers_with_openai_urls(
    providers: Sequence[Provider],
    *,
    max_attempts: int,
) -> tuple[tuple[Provider, ...], dict[str, str]]:
    eligible: list[Provider] = []
    skipped: dict[str, str] = {}
    for provider in providers:
        if len(eligible) >= max_attempts:
            break
        declaration = declaration_for_provider(provider)
        if declaration is not None and not declaration.openai_compatible:
            continue
        if openai_base_url_for_provider(provider) is None:
            continue
        if declaration is not None and declaration.proxy_skip_reason is not None:
            reason = declaration.proxy_skip_reason(provider)
            if reason is not None:
                skipped[provider.id] = reason
                continue
        eligible.append(provider)
    return tuple(eligible), skipped


async def _send_until_headers(
    request_ctx: OpenAIProxyRequest,
    provider: Provider,
    *,
    timeout_s: float,
) -> httpx.Response:
    base_url = openai_base_url_for_provider(provider)
    if base_url is None:
        raise ValueError(f"provider {provider.id!r} does not have an OpenAI base URL")

    content = request_ctx.body
    declaration = declaration_for_provider(provider)
    if declaration is not None and declaration.proxy_rewrite_body is not None and content:
        content = declaration.proxy_rewrite_body(content, provider)
    request = request_ctx.client.build_request(
        method=request_ctx.method,
        url=build_openai_url(base_url, request_ctx.path),
        headers=_provider_headers(request_ctx.headers, provider),
        content=content,
    )
    async with asyncio.timeout(timeout_s):
        return await request_ctx.client.send(request, stream=True)


def _provider_headers(headers: Mapping[str, str], provider: Provider) -> dict[str, str]:
    """Replace any consumer credential with the selected provider credential."""
    outbound = {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"authorization", "user-agent"}
    }
    outbound["user-agent"] = PITWALL_OPENAI_PROXY_USER_AGENT
    declaration = declaration_for_provider(provider)
    if declaration is not None and declaration.proxy_outbound_headers is not None:
        declared = declaration.proxy_outbound_headers(provider, outbound)
        if declared is not None:
            return declared
    credential_ref = provider.credential_ref
    credential = os.environ.get(credential_ref, "").strip()
    if credential:
        outbound["authorization"] = f"Bearer {credential}"
    return outbound


def _is_retryable_response(response: httpx.Response) -> bool:
    return response.status_code == 429 or 500 <= response.status_code < 600


__all__ = [
    "DEFAULT_OPENAI_FALLBACK_BUDGET_S",
    "OpenAIProxyExecutionError",
    "OpenAIProxyRequest",
    "OpenAIProxyResult",
    "PITWALL_OPENAI_PROXY_USER_AGENT",
    "UpstreamRateLimit",
    "execute_openai_with_fallback",
]
