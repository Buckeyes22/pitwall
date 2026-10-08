"""Claude-native ``POST /v1/messages`` surface (Decision Q1).

Translates Anthropic Messages bodies onto the production route: non-stream
requests execute through ``ProductionRoutingService.execute_sync``; streaming
requests are inspected before spend, planned, budget-admitted, and recorded as a
workload exactly like the OpenAI proxy, then relay through the admitted provider
chain and rewrite the SSE event stream.
A ``model`` that starts with ``gw/`` is pinned through the proxy
``model_id_map``; any other model is treated as a capability name.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import os
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from pitwall.api.anthropic_translate import (
    AnthropicInvalidRequest,
    StreamOutcome,
    anthropic_to_openai,
    openai_to_anthropic,
    safe_error_message,
    sse_openai_to_anthropic,
)
from pitwall.api.exceptions import (
    CapabilityDisabled,
    CapabilityNotFound,
    PreSpendPayloadRejected,
    ProviderNotFound,
    ProviderUnavailable,
)
from pitwall.api.routes.openai import (
    _admit_and_start_workload,
    _budget_gate,
    _close_upstream_client,
    _deliver_upstream_response,
    _fail_transport,
    _open_upstream_client,
    _plan_proxy_route,
    _PreparedOpenAIRequest,
    _ProxyCall,
    _workload_repo,
    record_upstream_rate_limits,
)
from pitwall.api.schemas.messages import AnthropicMessagesResponse
from pitwall.config import get_settings
from pitwall.cost.budget_gate import BudgetGate, BudgetRejected
from pitwall.db.repository import CapabilityRepository, ProviderRepository, WorkloadRepository
from pitwall.providers.errors import ProviderQuotaExhausted
from pitwall.resolver.exceptions import (
    CapabilityDisabledError,
    CapabilityNotFoundError,
    NoHealthyProviderError,
    ProviderNotFoundError,
)
from pitwall.routing.fallback import (
    DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    OpenAIProxyExecutionError,
    OpenAIProxyRequest,
    OpenAIProxyResult,
    UpstreamRateLimit,
    _is_retryable_response,
    _provider_headers,
    execute_openai_with_fallback,
)
from pitwall.routing.production import (
    PreparedRoutingPayload,
    ProductionRoutingService,
    RouteGuardrailRejected,
)
from pitwall.security.pre_spend import PreSpendDecision, get_pre_spend_inspection_service

router = APIRouter()

_MESSAGES_UPSTREAM_TIMEOUT = httpx.Timeout(330.0, connect=10.0)
_STREAM_ACCEPT = "text/event-stream"


class _UnknownModel(Exception):
    def __init__(self, model: str) -> None:
        super().__init__(f"model not found: {model}")
        self.model = model


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return pool


def _capability_repo(request: Request) -> CapabilityRepository:
    return CapabilityRepository(_pool(request))


def _provider_repo(request: Request) -> ProviderRepository:
    return ProviderRepository(_pool(request))


def _routing_service(
    request: Request,
    capability_repo: CapabilityRepository = Depends(_capability_repo),
    provider_repo: ProviderRepository = Depends(_provider_repo),
    budget_gate: BudgetGate = Depends(_budget_gate),
) -> ProductionRoutingService:
    configured = getattr(request.app.state, "production_routing_service", None)
    if configured is not None:
        return cast(ProductionRoutingService, configured)
    return ProductionRoutingService(
        _pool(request),
        settings=get_settings(),
        capability_repository=capability_repo,
        provider_repository=provider_repo,
        budget_gate=budget_gate,
    )


def _error_response(status_code: int, error_type: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )


def _error_response_for(exc: Exception) -> JSONResponse:
    """Map one route failure onto Anthropic's error envelope."""

    if isinstance(exc, AnthropicInvalidRequest):
        return _error_response(400, "invalid_request_error", str(exc))
    if isinstance(exc, ProviderQuotaExhausted):
        return _error_response(429, "rate_limit_error", str(exc))
    if isinstance(exc, _UnknownModel):
        return _error_response(404, "not_found_error", str(exc))
    if isinstance(
        exc,
        (
            CapabilityNotFoundError,
            CapabilityNotFound,
            ProviderNotFoundError,
            ProviderNotFound,
            CapabilityDisabledError,
            CapabilityDisabled,
        ),
    ):
        return _error_response(404, "not_found_error", str(exc))
    if isinstance(exc, (NoHealthyProviderError, ProviderUnavailable, OpenAIProxyExecutionError)):
        return _error_response(503, "overloaded_error", str(exc))
    if isinstance(exc, (RouteGuardrailRejected, PreSpendPayloadRejected, BudgetRejected)):
        return _error_response(400, "invalid_request_error", str(exc))
    return _error_response(500, "api_error", "internal server error")


async def _resolve_model_target(
    request_model: str, quota_repository: Any
) -> tuple[str, str | None]:
    """Return ``(capability_name, pinned_provider_id)`` for one requested model."""

    if not request_model.startswith("gw/"):
        return request_model, None
    list_model_ids = getattr(quota_repository, "list_model_ids", None)
    if callable(list_model_ids):
        for mapping in await list_model_ids():
            if mapping.model_id == request_model:
                return mapping.capability, mapping.provider
    raise _UnknownModel(request_model)


def _gateway_model_id(provider: Any) -> str | None:
    gateway = provider.config.get("gateway") if isinstance(provider.config, Mapping) else None
    if isinstance(gateway, Mapping):
        model_id = gateway.get("model_id")
        if isinstance(model_id, str):
            return model_id
    return None


def _openai_body_for(provider: Any, openai_body: Mapping[str, Any]) -> bytes:
    """Serialise the body for one provider with the model it actually serves.

    Mirrors ``GatewayProvider.infer``: an ``openai_gateway`` provider replaces
    ``model`` with ``config.gateway.model_id``; any other provider receives the
    body unchanged.
    """

    body = dict(openai_body)
    gateway_model = _gateway_model_id(provider)
    if gateway_model is not None:
        body["model"] = gateway_model
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


async def _execute_stream_chain(
    *,
    openai_body: Mapping[str, Any],
    providers_chain: list[Any],
    client: httpx.AsyncClient,
    attempted: list[str] | None = None,
) -> OpenAIProxyResult:
    """Attempt each provider with its own request body under one fallback budget.

    ``execute_openai_with_fallback`` reuses a single body across a chain, which is
    wrong when providers serve different model ids; this walks the chain one provider
    at a time with the same retry rule (5xx or transport failure before headers moves
    on, anything else is returned) and the same overall deadline. A caller that passes
    ``attempted`` sees each provider id appended before its request goes out, so a
    cancellation can be recorded against the providers that were actually tried.
    """

    started_at = time.perf_counter()
    deadline = started_at + DEFAULT_OPENAI_FALLBACK_BUDGET_S
    if attempted is None:
        attempted = []
    errors: dict[str, str] = {}
    last_error: BaseException | None = None
    last_result: OpenAIProxyResult | None = None
    rate_limited: list[UpstreamRateLimit] = []
    for index, provider in enumerate(providers_chain):
        remaining_s = deadline - time.perf_counter()
        if remaining_s <= 0:
            break
        if last_result is not None:
            # Close the retryable response only now that another attempt will go out;
            # at the deadline it is returned open, like the OpenAI route does.
            # A cleanup failure must not mask the fallback decision.
            with contextlib.suppress(Exception):
                await last_result.response.aclose()
            last_result = None
        attempted.append(provider.id)
        try:
            result = await execute_openai_with_fallback(
                OpenAIProxyRequest(
                    method="POST",
                    path="chat/completions",
                    headers={"content-type": "application/json", "accept": _STREAM_ACCEPT},
                    body=_openai_body_for(provider, openai_body),
                    client=client,
                    fallback_budget_s=remaining_s,
                    max_attempts=1,
                ),
                [provider],
            )
        except OpenAIProxyExecutionError as exc:
            last_error = exc
            errors.update(exc.attempted_errors)
            rate_limited.extend(exc.rate_limited)
            continue
        rate_limited.extend(result.rate_limited)
        if not _is_retryable_response(result.response) or index == len(providers_chain) - 1:
            return OpenAIProxyResult(
                response=result.response,
                provider=result.provider,
                attempted_provider_ids=tuple(attempted),
                elapsed_s=time.perf_counter() - started_at,
                rate_limited=tuple(rate_limited),
            )
        errors[provider.id] = f"HTTP {result.response.status_code}"
        last_result = result
    if last_result is not None:
        return replace(
            last_result,
            attempted_provider_ids=tuple(attempted),
            elapsed_s=time.perf_counter() - started_at,
            rate_limited=tuple(rate_limited),
        )
    error = OpenAIProxyExecutionError(
        "messages stream: every provider failed before response headers",
        attempted_provider_ids=attempted,
        cause=last_error,
        attempted_errors=errors,
        rate_limited=rate_limited,
    )
    if last_error is not None:
        raise error from last_error
    raise error


def _inspect_stream_payload(openai_body: Mapping[str, Any]) -> tuple[PreparedRoutingPayload, bytes]:
    """Run the pre-spend inspection before any budget, planner, or provider work.

    Returns the attested routing payload and the redacted body that may leave the broker.
    """

    guardrail = get_pre_spend_inspection_service().inspect(dict(openai_body))
    if guardrail.decision == PreSpendDecision.BLOCK:
        raise PreSpendPayloadRejected(
            decision=guardrail.decision.value,
            findings=[finding.to_dict() for finding in guardrail.findings],
        )
    prepared = PreparedRoutingPayload.from_inspection(guardrail)
    return prepared, json.dumps(
        dict(prepared.payload), ensure_ascii=False, separators=(",", ":")
    ).encode()


def _stream_call(
    request: Request,
    *,
    capability_name: str,
    prepared: PreparedRoutingPayload,
    body: bytes,
    started_at: float,
    routing_service: ProductionRoutingService,
    provider_repo: ProviderRepository,
    workload_repo: WorkloadRepository,
    budget_gate: BudgetGate,
) -> _ProxyCall:
    """Bind one streaming Messages request to the OpenAI proxy's admission state."""

    return _ProxyCall(
        request=request,
        capability=capability_name,
        path="chat/completions",
        started_at=started_at,
        body=body,
        headers={},
        query="",
        request_was_well_formed=True,
        workload_repo=workload_repo,
        provider_repo=provider_repo,
        budget_gate=budget_gate,
        routing_service=routing_service,
        prepared=_PreparedOpenAIRequest(body=body, query="", headers={}, routing_payload=prepared),
    )


_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", "x-api-key", "api-key"})
_UPSTREAM_ERROR_READ_LIMIT = 65536
_AUTH_ERROR_MESSAGE = "upstream rejected the broker's credentials"
_ERROR_TYPE_BY_STATUS: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    503: "overloaded_error",
    529: "overloaded_error",
}


def _provider_secrets(providers: Sequence[Any]) -> tuple[str, ...]:
    """The credentials the broker sends to *providers*, for redacting upstream text."""

    secrets: set[str] = set()
    for provider in providers:
        credential = os.environ.get(provider.credential_ref, "").strip()
        if credential:
            secrets.add(credential)
        for key, value in _provider_headers({}, provider).items():
            if key.lower() in _CREDENTIAL_HEADERS and value.strip():
                secrets.add(value.strip())
                secrets.add(value.strip().removeprefix("Bearer ").strip())
    secrets.discard("")
    return tuple(sorted(secrets, key=len, reverse=True))


async def _read_bounded(body_iterator: AsyncIterator[bytes], limit: int) -> bytes:
    """Read an upstream body to its end, keeping only its first *limit* bytes."""

    kept = bytearray()
    try:
        async for chunk in body_iterator:
            kept += chunk[: max(limit - len(kept), 0)]
    finally:
        aclose = getattr(body_iterator, "aclose", None)
        if aclose is not None:
            await aclose()
    return bytes(kept)


def _upstream_error_message(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        return text
    error = parsed.get("error") if isinstance(parsed, Mapping) else None
    if isinstance(error, Mapping):
        error = error.get("message")
    if isinstance(error, str):
        return error
    message = parsed.get("message") if isinstance(parsed, Mapping) else None
    return message if isinstance(message, str) else text


def _upstream_error_response(
    status_code: int, body: bytes, secrets: Sequence[str], headers: Mapping[str, str]
) -> JSONResponse:
    """Answer a non-2xx upstream reply in Anthropic's envelope, never with its raw body."""

    error_type = _ERROR_TYPE_BY_STATUS.get(
        status_code, "api_error" if status_code >= 500 else "invalid_request_error"
    )
    message = (
        _AUTH_ERROR_MESSAGE
        if status_code in {401, 403}
        else safe_error_message(_upstream_error_message(body), secrets)
    )
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
        headers=dict(headers),
    )


async def _send_stream_upstream(
    call: _ProxyCall, provider: Any, openai_body: Mapping[str, Any]
) -> OpenAIProxyResult:
    """Walk the admitted chain until response headers; own every failure before relay."""

    attempted: list[str] = []
    try:
        result = await _execute_stream_chain(
            openai_body=openai_body,
            providers_chain=call.providers,
            client=call.client,
            attempted=attempted,
        )
    except asyncio.CancelledError:
        cancelled = next(
            (item for item in call.providers if attempted and item.id == attempted[-1]), provider
        )
        try:
            await _close_upstream_client(call.client)
        finally:
            await call.record_cancellation(cancelled, tuple(attempted))
        raise
    except OpenAIProxyExecutionError as exc:
        await _fail_transport(call, provider, exc)
        raise  # unreachable: _fail_transport always raises
    record_upstream_rate_limits(result.rate_limited, call.providers, now=dt.datetime.now(dt.UTC))
    return result


async def _stream_messages(
    *,
    request: Request,
    request_model: str,
    capability_name: str,
    pinned_provider_id: str | None,
    openai_body: dict[str, Any],
    routing_service: ProductionRoutingService,
    provider_repo: ProviderRepository,
    workload_repo: WorkloadRepository,
    budget_gate: BudgetGate,
    anthropic_version: str | None,
) -> Response:
    """Inspect, plan, admit, and record one streaming request before it reaches a provider."""

    started_at = time.perf_counter()
    openai_body["stream"] = True
    prepared, body = _inspect_stream_payload(openai_body)
    safe_body = dict(prepared.payload)
    call = _stream_call(
        request,
        capability_name=capability_name,
        prepared=prepared,
        body=body,
        started_at=started_at,
        routing_service=routing_service,
        provider_repo=provider_repo,
        workload_repo=workload_repo,
        budget_gate=budget_gate,
    )
    await _plan_proxy_route(call, pinned_provider_id)
    provider = call.providers[0]
    cost_estimate_usd = await _admit_and_start_workload(call, provider)
    call.upstream_state = provider
    await _open_upstream_client(call, provider, _MESSAGES_UPSTREAM_TIMEOUT)
    result = await _send_stream_upstream(call, provider, safe_body)
    outcome = StreamOutcome()
    if 200 <= result.response.status_code < 300:
        # Only a translated stream can fail in-band; an error reply settles as the proxy does.
        call.stream_failure = outcome.failure
    relayed = await _deliver_upstream_response(call, result, cost_estimate_usd)

    headers = {
        key: value for key, value in relayed.headers.items() if key.lower().startswith("x-pitwall-")
    }
    if anthropic_version is not None:
        headers["anthropic-version"] = anthropic_version
    status_code = result.response.status_code
    secrets = _provider_secrets(call.providers)
    body_iterator = cast(AsyncIterator[bytes], relayed.body_iterator)
    if not 200 <= status_code < 300:
        body = await _read_bounded(body_iterator, _UPSTREAM_ERROR_READ_LIMIT)
        return _upstream_error_response(status_code, body, secrets, headers)
    return StreamingResponse(
        sse_openai_to_anthropic(
            body_iterator, request_model=request_model, secrets=secrets, outcome=outcome
        ),
        status_code=status_code,
        media_type=_STREAM_ACCEPT,
        headers=headers,
    )


@router.post("/v1/messages", response_model=AnthropicMessagesResponse)
async def create_messages(
    request: Request,
    service: ProductionRoutingService = Depends(_routing_service),
    provider_repo: ProviderRepository = Depends(_provider_repo),
    workload_repo: WorkloadRepository = Depends(_workload_repo),
    budget_gate: BudgetGate = Depends(_budget_gate),
) -> Response:
    """Translate one Anthropic Messages request and execute the production route."""

    anthropic_version = request.headers.get("anthropic-version")
    try:
        raw_body_value = await request.json()
        if not isinstance(raw_body_value, Mapping):
            raise AnthropicInvalidRequest("request body must be a JSON object")
        raw_body: Mapping[str, Any] = raw_body_value
        request_model = raw_body.get("model")
        if not isinstance(request_model, str) or not request_model.strip():
            raise AnthropicInvalidRequest("model is required and must be a non-empty string")
        capability_name, pinned_provider_id = await _resolve_model_target(
            request_model,
            getattr(request.app.state, "quota_repository", None),
        )
        openai_body = anthropic_to_openai(raw_body)
        if raw_body.get("stream") is True:
            return await _stream_messages(
                request=request,
                request_model=request_model,
                capability_name=capability_name,
                pinned_provider_id=pinned_provider_id,
                openai_body=openai_body,
                routing_service=service,
                provider_repo=provider_repo,
                workload_repo=workload_repo,
                budget_gate=budget_gate,
                anthropic_version=anthropic_version,
            )

        execution = await service.execute_sync(
            capability_id=capability_name,
            payload=openai_body,
            provider_id=pinned_provider_id,
        )
        headers = {
            "X-Pitwall-Workload-ID": execution.workload.id,
            "X-Pitwall-Route-Plan-ID": execution.plan.plan_id,
        }
        if anthropic_version is not None:
            headers["anthropic-version"] = anthropic_version
        return JSONResponse(
            status_code=200,
            content=openai_to_anthropic(execution.output, request_model=request_model),
            headers=headers,
        )
    except json.JSONDecodeError as exc:
        return _error_response(400, "invalid_request_error", f"invalid JSON body: {exc.msg}")
    except Exception as exc:  # reason: the messages surface always answers in Anthropic's envelope
        return _error_response_for(exc)


__all__ = ["router"]
