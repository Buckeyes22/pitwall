"""FastAPI route handlers for the OpenAI-compatible pass-through surface.

Transparent proxy at ``/v1/openai/{capability}/v1/*`` that forwards bodies
verbatim, rewrites the upstream URL based on capability resolution, adds Pitwall
observability headers, and traverses the provider chain on pre-relay failures.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Literal, NoReturn, TypedDict, cast
from urllib.parse import urlencode

import anyio
import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from pitwall.api.exceptions import (
    CapabilityNotFound,
    InvalidProxyPath,
    PreSpendPayloadRejected,
    ProviderNotFound,
    ProviderUnavailable,
)
from pitwall.api.schemas.params import PathId
from pitwall.config import get_settings, load_settings_from_env
from pitwall.core.enums import ProviderType, WorkloadState
from pitwall.core.ids import ulid_new
from pitwall.core.models import Capability, Provider, Workload
from pitwall.cost.budget_gate import BudgetGate, BudgetRejected
from pitwall.cost.estimator import CostComponent, CostQuote
from pitwall.db.quota_repository import QuotaRepository
from pitwall.db.repository import CapabilityRepository, ProviderRepository, WorkloadRepository
from pitwall.leases.activity import stamp_lease_traffic
from pitwall.observability.langfuse import emit_inference_trace, start_inference_trace
from pitwall.providers.gateway import classify_429
from pitwall.providers.selfhosted.profile import self_hosted_profile, self_hosted_state
from pitwall.resolver.exceptions import CapabilityNotFoundError, NoHealthyProviderError
from pitwall.routing import lockout as _lockout
from pitwall.routing.cooldown import (
    CooldownStateMachine,
    ProviderCooldownState,
    state_from_provider,
    to_provider_patch,
)
from pitwall.routing.fallback import (
    DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    OpenAIProxyExecutionError,
    OpenAIProxyRequest,
    OpenAIProxyResult,
    UpstreamRateLimit,
    execute_openai_with_fallback,
)
from pitwall.routing.openai import (
    MAX_OPENAI_ATTEMPTS,
    OpenAIProviderChain,
    pod_lease_base_url,
    resolve_openai_provider_chain,
    validate_openai_proxy_path,
)
from pitwall.routing.production import (
    PreparedRoutingPayload,
    ProductionRoutePlan,
    ProductionRoutingService,
    RoutingOperation,
)
from pitwall.routing.saturation import SaturationDetector
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendPayloadScanResult,
    get_pre_spend_inspection_service,
)
from pitwall.workload_lifecycle import (
    transition_to_completed,
    transition_to_failed,
    transition_to_running,
)

log = logging.getLogger("pitwall.api.routes.openai")

router = APIRouter()

_OPENAI_FALLBACK_BUDGET_S = DEFAULT_OPENAI_FALLBACK_BUDGET_S
_GLOBAL_UPSTREAM_TIMEOUT_S = 330.0
_WORKLOAD_TYPE_OPENAI_PASSTHROUGH = "openai_passthrough"
_LEASE_COVERED_COST = Decimal("0")
_DETERMINISTIC_LAUNCH_ERROR_PATTERNS = (
    '"auto" tool choice requires',
    "tool-call-parser",
)
"""Case-insensitive upstream text unique to deterministic tool-launch errors."""

_OPENAI_QUERY_ALLOWLIST = frozenset(
    {"after", "before", "limit", "max_tokens", "order", "purpose", "stream"}
)
_OPENAI_HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "accept-encoding",
        "content-type",
        "idempotency-key",
        "openai-beta",
        "openai-organization",
        "openai-project",
        "user-agent",
        "x-request-id",
        "x-stainless-arch",
        "x-stainless-async",
        "x-stainless-lang",
        "x-stainless-os",
        "x-stainless-package-version",
        "x-stainless-raw-response",
        "x-stainless-read-timeout",
        "x-stainless-retry-count",
        "x-stainless-runtime",
        "x-stainless-runtime-version",
        "x-stainless-timeout",
    }
)
_OPENAI_LOCAL_HEADER_ALLOWLIST = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "host",
        "x-pitwall-drill",
        # OpenCode attaches these session identifiers to model requests. They
        # are broker-local metadata and must not be forwarded to providers.
        "x-session-affinity",
        "x-session-id",
    }
)
# HTTPX transparently decodes compressed upstream bodies before the relay sees
# them.  Do not forward metadata that describes the encoded/original transport
# body, or the downstream client will decode or frame it a second time.
_RELAY_BODY_HEADERS = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

UpstreamOutcome = Literal[
    "healthy",
    "failure",
    "misconfigured",
    "client_error",
    "capacity",
]


def _provider_attempt_component_name(provider_id: str, component_name: str) -> str:
    """Return a stable bounded cost-component name for an arbitrary provider ID."""

    provider_slug = re.sub(r"[^a-z0-9_-]+", "_", provider_id.casefold()).strip("_-")
    component_slug = re.sub(r"[^a-z0-9_-]+", "_", component_name.casefold()).strip("_-")
    provider_label = provider_slug[:6].rstrip("_-") or "source"
    component_label = component_slug[:6].rstrip("_-") or "cost"
    provider_digest = hashlib.sha256(provider_id.encode("utf-8")).hexdigest()[:30]
    component_digest = hashlib.sha256(component_name.encode("utf-8")).hexdigest()[:16]
    return f"pa_{provider_label}_{provider_digest}_{component_label}_{component_digest}"


@dataclass(frozen=True, slots=True)
class _OpenAIAttemptedCost:
    ceiling_usd: Decimal
    quote: dict[str, object]
    all_attempts_lease_covered: bool


@dataclass(frozen=True, slots=True)
class _OpenAIFallbackBudgetQuote:
    """One conservative admission quote for the exact proxy attempt chain."""

    route_plan: ProductionRoutePlan
    providers: tuple[Provider, ...]

    def _attempts(self) -> tuple[tuple[Provider, CostQuote | None], ...]:
        candidates = {candidate.provider_id: candidate for candidate in self.route_plan.attempts}
        providers = {provider.id: provider for provider in self.providers}
        expected_ids = tuple(candidate.provider_id for candidate in self.route_plan.attempts)
        if len(candidates) != len(expected_ids) or len(providers) != len(self.providers):
            raise ValueError("OpenAI fallback admission requires unique provider attempts")
        if set(providers) != set(expected_ids):
            raise ValueError("OpenAI fallback providers must match the planned attempt chain")
        return tuple(
            (
                providers[provider_id],
                None
                if _active_lease_cost_is_covered(providers[provider_id])
                else candidates[provider_id].quote,
            )
            for provider_id in expected_ids
        )

    def estimate(self) -> Decimal:
        _provider, quote = self._attempts()[0]
        return _LEASE_COVERED_COST if quote is None else quote.estimate()

    def upper_bound(self) -> Decimal:
        return sum(
            (
                _LEASE_COVERED_COST if quote is None else quote.upper_bound()
                for _provider, quote in self._attempts()
            ),
            start=Decimal("0"),
        )

    def all_attempts_lease_covered(self) -> bool:
        return all(quote is None for _provider, quote in self._attempts())

    def _serialize_attempts(
        self,
        attempts: tuple[tuple[Provider, CostQuote | None], ...],
    ) -> dict[str, object]:
        components: list[dict[str, object]] = []
        for provider, quote in attempts:
            if quote is None:
                serialized_lease: dict[str, object] = dict(
                    CostComponent(
                        name=_provider_attempt_component_name(
                            provider.id,
                            "lease_covered",
                        ),
                        unit="request",
                        rate=_LEASE_COVERED_COST,
                        ceiling_rate=_LEASE_COVERED_COST,
                        estimated_count=Decimal("1"),
                        ceiling_count=Decimal("1"),
                        estimate=_LEASE_COVERED_COST,
                        ceiling=_LEASE_COVERED_COST,
                    ).model_dump(mode="json")
                )
                components.append(serialized_lease)
                continue
            for component in quote.components:
                serialized: dict[str, object] = dict(component.model_dump(mode="json"))
                serialized["name"] = _provider_attempt_component_name(
                    provider.id,
                    component.name,
                )
                components.append(serialized)
        return {
            "model": "openai_fallback_route",
            "components": components,
            "estimate": str(self.estimate()),
            "ceiling": str(
                sum(
                    (
                        _LEASE_COVERED_COST if quote is None else quote.upper_bound()
                        for _provider, quote in attempts
                    ),
                    start=Decimal("0"),
                )
            ),
            "confidence": "bounded",
            "provenance": "production_route_plan",
            "currency": "USD",
            "assumptions": [
                "every paid fallback attempt may incur its configured provider ceiling",
                "an already-paid pod lease has zero incremental proxy cost",
            ],
        }

    def to_serializable_dict(self) -> dict[str, object]:
        return self._serialize_attempts(self._attempts())

    def attempted_cost(self, provider_ids: tuple[str, ...]) -> _OpenAIAttemptedCost:
        """Return a quote/ceiling reduced to the exact attempted prefix."""

        attempts = self._attempts()
        expected_ids = tuple(provider.id for provider, _quote in attempts)
        if not provider_ids or expected_ids[: len(provider_ids)] != provider_ids:
            raise ValueError("attempted providers must be a non-empty planned prefix")
        quote = self._serialize_attempts(attempts[: len(provider_ids)])
        return _OpenAIAttemptedCost(
            ceiling_usd=Decimal(str(quote["ceiling"])),
            quote=quote,
            all_attempts_lease_covered=all(
                attempt_quote is None for _provider, attempt_quote in attempts[: len(provider_ids)]
            ),
        )


def _active_lease_cost_is_covered(provider: Provider) -> bool:
    if provider.provider_type != ProviderType.POD_LEASE:
        return False
    lease_id = provider.config.get("active_lease_id")
    return (
        isinstance(lease_id, str)
        and bool(lease_id.strip())
        and pod_lease_base_url(provider) is not None
    )


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return pool


def _capability_repo(request: Request) -> CapabilityRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return CapabilityRepository(pool)


def _provider_repo(request: Request) -> ProviderRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return ProviderRepository(pool)


def _workload_repo(request: Request) -> WorkloadRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return WorkloadRepository(pool)


def _budget_gate(request: Request) -> BudgetGate:
    settings = load_settings_from_env()
    return BudgetGate(
        _pool(request),
        monthly_budget_usd=settings.pitwall_monthly_budget_usd,
        per_request_max_usd=settings.pitwall_per_request_max_usd,
    )


def _routing_service(
    request: Request,
    capability_repo: CapabilityRepository = Depends(_capability_repo),
    provider_repo: ProviderRepository = Depends(_provider_repo),
    budget_gate: BudgetGate = Depends(_budget_gate),
) -> ProductionRoutingService:
    """Bind routing to the proxy's injectable repositories and budget gate."""

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


async def _resolve_provider_chain(
    capability_name: str,
    capability_repo: CapabilityRepository,
    provider_repo: ProviderRepository,
    *,
    model_id: str | None = None,
    quota_repository: Any | None = None,
) -> tuple[Capability, OpenAIProviderChain, list[Provider]]:
    """Resolve the OpenAI-compatible chain for *capability_name*.

    When ``model_id`` starts with ``gw/`` and a ``quota_repository`` exposes the
    proxy ``model_id_map``, the chain is pinned to the single mapped provider
    (a ``provider_not_found`` envelope is raised by the proxy when the map
    yields no entry).
    """

    capability = await capability_repo.get_by_name(capability_name)
    if capability is None:
        raise CapabilityNotFound(capability_name)

    providers = await provider_repo.list(
        capability_id=capability.id,
        enabled_only=True,
        limit=100,
    )
    pin_provider_id: str | None = None
    if isinstance(model_id, str) and model_id.startswith("gw/") and quota_repository is not None:
        list_model_ids = getattr(quota_repository, "list_model_ids", None)
        if callable(list_model_ids):
            mappings = await list_model_ids()
            for mapping in mappings:
                if mapping.model_id == model_id and mapping.capability == capability.name:
                    pin_provider_id = mapping.provider
                    break

    chain = resolve_openai_provider_chain(
        providers,
        primary_provider_id=pin_provider_id,
        max_attempts=1 if pin_provider_id is not None else MAX_OPENAI_ATTEMPTS,
    )
    if not chain.attempts:
        raise ProviderUnavailable(capability_name)
    return capability, chain, [p for p in chain.providers if isinstance(p, Provider)]


async def _close_upstream_response(
    upstream_response: httpx.Response,
    client: httpx.AsyncClient,
) -> None:
    cancellation: asyncio.CancelledError | None = None
    try:
        await upstream_response.aclose()
    except asyncio.CancelledError as exc:
        cancellation = exc
    except Exception as exc:  # reason: cleanup must not mask the routed response outcome
        log.warning("upstream response cleanup failed: type=%s", type(exc).__name__)
    try:
        await client.aclose()
    except asyncio.CancelledError as exc:
        cancellation = cancellation or exc
    except Exception as exc:  # reason: cleanup must not mask the routed response outcome
        log.warning("upstream client cleanup failed: type=%s", type(exc).__name__)
    if cancellation is not None:
        raise cancellation


async def _close_upstream_client(client: httpx.AsyncClient) -> None:
    """Close one client without replacing its routed outcome with a cleanup error."""

    try:
        await client.aclose()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # reason: cleanup detail may contain provider credentials
        log.warning("upstream client cleanup failed: type=%s", type(exc).__name__)


_SSE_STREAM_ERROR_EVENT = 'data: {"error":"upstream stream failure"}\n\n'


def _is_sse_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return "text/event-stream" in content_type


def _downstream_response_headers(response: httpx.Response) -> dict[str, str]:
    """Keep semantic headers while dropping decoded-body transport metadata."""

    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _RELAY_BODY_HEADERS
    }


async def _relay_upstream_bytes(
    upstream_response: httpx.Response,
    client: httpx.AsyncClient,
    *,
    on_completed: Callable[[int], Awaitable[None]] | None = None,
    on_interrupted: Callable[[str, int], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    completed = False
    interruption: str | None = None
    delivered_bytes = 0
    try:
        async for chunk in upstream_response.aiter_bytes():
            delivered_bytes += len(chunk)
            yield chunk
        completed = True
    except asyncio.CancelledError:
        interruption = "provider_stream_cancelled"
        raise
    except Exception as exc:  # reason: mid-stream upstream failure: log and end stream cleanly
        interruption = "provider_stream_failed"
        log.warning("mid-stream upstream failure: type=%s", type(exc).__name__)
        if _is_sse_response(upstream_response):
            error_chunk = _SSE_STREAM_ERROR_EVENT.encode("utf-8")
            delivered_bytes += len(error_chunk)
            yield error_chunk
    finally:
        if not completed and interruption is None:
            interruption = "provider_stream_cancelled"

        # Starlette cancels the response task when the downstream disconnects.
        # Run provider close and terminal bookkeeping in one shielded child so
        # that the cancellation cannot leave the workload RUNNING forever.
        async def finalize() -> None:
            try:
                await _close_upstream_response(upstream_response, client)
            finally:
                if completed and on_completed is not None:
                    await on_completed(delivered_bytes)
                elif interruption is not None and on_interrupted is not None:
                    await on_interrupted(interruption, delivered_bytes)

        cleanup = asyncio.create_task(finalize())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # The child is deliberately shielded from this cancellation.  Wait
            # for durable failure/completion bookkeeping before propagating it.
            with anyio.CancelScope(shield=True):
                await asyncio.shield(cleanup)
            raise


async def _record_proxy_cancellation(
    workload_repo: WorkloadRepository,
    workload_id: str,
    *,
    started_at: float,
    attempted_provider_ids: tuple[str, ...],
    admission_quote: _OpenAIFallbackBudgetQuote,
    capability_name: str,
    provider: Provider,
    input_bytes: int,
) -> None:
    """Persist a safe terminal record after cancellation at any upstream await."""

    execution_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        trace_id = emit_inference_trace(
            workload_id=workload_id,
            capability_name=capability_name,
            provider_id=provider.id,
            provider_type=provider.provider_type.value,
            runpod_endpoint_id=provider.runpod_endpoint_id,
            cost_estimate_usd=float(admission_quote.upper_bound()),
            input_bytes=input_bytes,
            output_bytes=0,
            execution_ms=execution_ms,
            status="error",
            error=RuntimeError("provider call cancelled"),
        )
        attempted_cost = (
            admission_quote.attempted_cost(attempted_provider_ids)
            if attempted_provider_ids
            else None
        )
        await transition_to_failed(
            workload_repo,
            workload_id,
            execution_ms=execution_ms,
            provider_id=provider.id if attempted_provider_ids else None,
            error={
                "error": "provider_call_cancelled",
                "attempted_providers": list(attempted_provider_ids),
            },
            fallback_chain=list(attempted_provider_ids),
            langfuse_trace_id=trace_id,
            cost_ceiling_usd=(attempted_cost.ceiling_usd if attempted_cost is not None else None),
            cost_quote=attempted_cost.quote if attempted_cost is not None else None,
            cost_actual_usd=_LEASE_COVERED_COST if attempted_cost is None else None,
            cost_actual_provenance=(
                "broker:no_provider_invocation" if attempted_cost is None else None
            ),
            allow_queued=attempted_cost is None,
        )
    except Exception:  # reason: cancellation bookkeeping must not delay task cancellation
        log.warning("workload cancellation transition unavailable")


async def _record_pre_egress_failure(
    workload_repo: WorkloadRepository,
    workload_id: str,
    *,
    started_at: float,
    admission_quote: _OpenAIFallbackBudgetQuote,
    capability_name: str,
    provider: Provider,
    input_bytes: int,
    reason: str = "workload_running_transition_failed",
) -> None:
    """Release admission when RUNNING cannot be proven before provider egress."""

    execution_ms = int((time.perf_counter() - started_at) * 1000)
    try:
        trace_id = emit_inference_trace(
            workload_id=workload_id,
            capability_name=capability_name,
            provider_id=provider.id,
            provider_type=provider.provider_type.value,
            runpod_endpoint_id=provider.runpod_endpoint_id,
            cost_estimate_usd=float(admission_quote.upper_bound()),
            input_bytes=input_bytes,
            output_bytes=0,
            execution_ms=execution_ms,
            status="error",
            error=RuntimeError(reason),
        )
        await transition_to_failed(
            workload_repo,
            workload_id,
            execution_ms=execution_ms,
            error={
                "error": reason,
                "attempted_providers": [],
            },
            fallback_chain=[],
            langfuse_trace_id=trace_id,
            cost_actual_usd=_LEASE_COVERED_COST,
            cost_actual_provenance="broker:no_provider_invocation",
            allow_queued=True,
        )
    except asyncio.CancelledError:
        await _record_proxy_cancellation(
            workload_repo,
            workload_id,
            started_at=started_at,
            attempted_provider_ids=(),
            admission_quote=admission_quote,
            capability_name=capability_name,
            provider=provider,
            input_bytes=input_bytes,
        )
        raise
    except Exception:  # reason: audit failure must not authorize provider egress
        log.warning("workload pre-egress failure transition unavailable")


def _path_with_query(path: str, query: str) -> str:
    if not query:
        return path
    return f"{path}?{query}"


def _estimate_payload_from_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return {"input_bytes": len(body)}
    if isinstance(parsed, Mapping):
        return dict(parsed)
    return {"input": parsed, "input_bytes": len(body)}


async def _admit_passthrough(
    *,
    capability: Capability,
    providers: tuple[Provider, ...],
    body: bytes,
    budget_gate: BudgetGate,
    workload_repo: WorkloadRepository,
    route_plan: ProductionRoutePlan,
) -> tuple[str, _OpenAIFallbackBudgetQuote]:
    """Admit one proxied request and return its workload and exact route quote.

    The ceiling covers every paid provider in the fallback chain before any
    provider egress. A chain containing only already-paid pod leases remains a
    zero-incremental-cost ledger write and does not consult the budget gate.
    """

    if not providers:
        raise ValueError("proxy admission requires at least one provider")
    provider = providers[0]
    cost_quote = _OpenAIFallbackBudgetQuote(route_plan, providers)
    if cost_quote.all_attempts_lease_covered():
        workload = Workload(
            id=f"wkl_{ulid_new()}",
            capability_id=capability.id,
            provider_id=provider.id,
            type=_WORKLOAD_TYPE_OPENAI_PASSTHROUGH,
            state=WorkloadState.QUEUED,
            submitted_at=dt.datetime.now(dt.UTC),
            input_bytes=len(body) if body else 0,
            fallback_chain=list(route_plan.fallback_chain),
            route_plan_id=route_plan.plan_id,
            route_plan=route_plan.to_dict(),
            cost_estimate_usd=_LEASE_COVERED_COST,
            cost_ceiling_usd=_LEASE_COVERED_COST,
            cost_quote=cost_quote.to_serializable_dict(),
        )
        inserted = await workload_repo.insert(workload)
        return inserted.id, cost_quote

    admission = await budget_gate.try_launch_admission(
        capability_id=capability.id,
        provider_id=provider.id,
        estimate_usd=cost_quote,
        workload_type=_WORKLOAD_TYPE_OPENAI_PASSTHROUGH,
    )
    attach_route_plan = getattr(workload_repo, "attach_route_plan", None)
    if callable(attach_route_plan):
        await attach_route_plan(
            admission.workload_id,
            route_plan_id=route_plan.plan_id,
            route_plan=route_plan.to_dict(),
        )
    return admission.workload_id, cost_quote


def classify_upstream_outcome(
    *,
    status_code: int | None,
    exc: BaseException | None,
    request_was_well_formed: bool,
    response_body: bytes = b"",
) -> UpstreamOutcome:
    if exc is not None or status_code is None or status_code >= 500:
        return "failure"
    if 200 <= status_code < 300:
        return "healthy"
    if status_code in {401, 403}:
        return "misconfigured"
    if status_code == 429:
        return "capacity"
    if 400 <= status_code < 500:
        if status_code == 400 and request_was_well_formed:
            error_text = response_body.decode("utf-8", errors="replace").casefold()
            normalized_error_text = error_text.replace("\\", "")
            if any(
                pattern in normalized_error_text for pattern in _DETERMINISTIC_LAUNCH_ERROR_PATTERNS
            ):
                return "misconfigured"
        return "client_error"
    return "failure"


def _token_fingerprint(authorization: str | None) -> str:
    token = (authorization or "anonymous").removeprefix("Bearer ")
    digest = hashlib.sha256(token.encode()).hexdigest()[:12]
    return f"sha256:{digest}"


def _new_upstream_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


def _request_model_id(body: bytes) -> str | None:
    payload = _estimate_payload_from_body(body)
    model_id = payload.get("model")
    return model_id if isinstance(model_id, str) and model_id else None


def _model_is_resident(provider: Provider, model_id: str) -> bool:
    state = provider.config.get("self_hosted_state")
    if not isinstance(state, Mapping):
        return False
    resident = state.get("resident")
    return isinstance(resident, list) and model_id in resident


def upstream_timeout(
    provider: Provider,
    *,
    model_id: str,
    resident: bool,
) -> httpx.Timeout:
    profile = self_hosted_profile(provider)
    read_timeout = _GLOBAL_UPSTREAM_TIMEOUT_S
    if profile is not None and not resident:
        read_timeout = float(profile.cold_start_timeout_s)
    return httpx.Timeout(read_timeout, connect=10.0)


_HEALTH_WRITE_ATTEMPTS = 3


async def _record_upstream_outcome(
    *,
    provider_repo: ProviderRepository,
    provider_id: str,
    state: Provider | ProviderCooldownState,
    classification: UpstreamOutcome | Literal["warming"],
    now: dt.datetime,
) -> Provider | ProviderCooldownState:
    """Record one upstream outcome on the provider's cooldown counters.

    The next state is computed from the snapshot the request holds, so the write is a
    compare-and-set on the row's ``updated_at``; when a concurrent request or probe wrote
    first, re-read the row and recompute so no failure is lost. Returns the written row,
    which carries the ``updated_at`` a later outcome in the same request compares against.
    """
    if classification == "client_error" or classification == "capacity":
        return state_from_provider(state)
    machine = CooldownStateMachine()
    current: Provider | ProviderCooldownState = state
    if not isinstance(current, Provider):  # no row version to compare against: read one
        fresh = await provider_repo.get(provider_id)
        if isinstance(fresh, Provider):
            current = fresh
    for _ in range(_HEALTH_WRITE_ATTEMPTS):
        next_state = machine.record_request_outcome(current, classification=classification, now=now)
        expected = current.updated_at if isinstance(current, Provider) else None
        written = await provider_repo.patch(
            provider_id, **to_provider_patch(next_state), expected_updated_at=expected
        )
        if written is not None:
            return written if isinstance(written, Provider) else next_state
        fresh = await provider_repo.get(provider_id)
        if not isinstance(fresh, Provider):  # deleted since the request read it
            return next_state
        current = fresh
    log.warning("provider health write for %s kept racing; not recorded", provider_id)
    return next_state


async def _record_self_hosted_concurrency(
    repo: ProviderRepository,
    provider: Provider,
    *,
    status_code: int | None,
) -> None:
    if self_hosted_profile(provider) is None or status_code is None:
        return
    state = self_hosted_state(provider.config.get("self_hosted_state"))
    is_limited = state.get("concurrency_limited") is True
    if status_code == 429 and not is_limited:
        state["concurrency_limited"] = True
    elif 200 <= status_code < 300 and is_limited:
        state["concurrency_limited"] = False
    else:
        return
    await repo.patch(
        provider.id,
        config={**provider.config, "self_hosted_state": state},
    )


def _request_body_is_well_formed(body: bytes) -> bool:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError, UnicodeDecodeError:
        return False
    return isinstance(parsed, Mapping)


@dataclass(frozen=True, slots=True)
class _PreparedOpenAIRequest:
    body: bytes
    query: str
    headers: dict[str, str]
    routing_payload: PreparedRoutingPayload


def _require_openai_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError("OpenAI request body must be a JSON object")
    return value


def _reject_openai_metadata(rule: str) -> None:
    raise PreSpendPayloadRejected(
        decision=PreSpendDecision.BLOCK.value,
        findings=[{"rule": rule}],
    )


def _openai_metadata(request: Request) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Return allowlisted provider-bound metadata without forwarding local headers."""

    query: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        if key not in _OPENAI_QUERY_ALLOWLIST:
            _reject_openai_metadata("query_allowlist")
        query.setdefault(key, []).append(value)

    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        normalized = key.lower()
        if normalized in _OPENAI_LOCAL_HEADER_ALLOWLIST:
            continue
        if normalized not in _OPENAI_HEADER_ALLOWLIST:
            _reject_openai_metadata("header_allowlist")
        headers[normalized] = value
    return query, headers


def _prepared_openai_request(request: Request, body: bytes) -> _PreparedOpenAIRequest:
    """Inspect all provider-bound request data once and attest its routing payload."""

    query, headers = _openai_metadata(request)
    inspection_body: object = {}
    body_mapping: Mapping[str, Any] | None = None
    if body:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            inspection_body = body
        else:
            if isinstance(parsed, Mapping):
                body_mapping = parsed
                inspection_body = parsed
            else:
                inspection_body = body

    service = get_pre_spend_inspection_service()
    guardrail = service.inspect(
        {"body": inspection_body, "query": query, "headers": headers},
        validate_redacted=_require_openai_transport,
    )
    if guardrail.decision == PreSpendDecision.BLOCK:
        raise PreSpendPayloadRejected(
            decision=guardrail.decision.value,
            findings=[finding.to_dict() for finding in guardrail.findings],
        )
    safe_request = _require_openai_transport(guardrail.redacted_payload)
    safe_body_value = safe_request["body"]
    safe_query = _require_string_lists(safe_request["query"])
    safe_headers = _require_string_mapping(safe_request["headers"])

    safe_body = body
    if body_mapping is not None and safe_body_value != body_mapping:
        redacted_body = _require_openai_mapping(safe_body_value)
        safe_body = json.dumps(
            redacted_body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    routing_result: PreSpendPayloadScanResult = replace(
        guardrail,
        redacted_payload=_estimate_payload_from_body(safe_body),
    )
    return _PreparedOpenAIRequest(
        body=safe_body,
        query=urlencode(safe_query, doseq=True),
        headers=safe_headers,
        routing_payload=PreparedRoutingPayload.from_inspection(routing_result),
    )


def _require_openai_transport(value: object) -> Mapping[str, Any]:
    request = _require_openai_mapping(value)
    if set(request) != {"body", "query", "headers"}:
        raise ValueError("OpenAI transport payload has unexpected fields")
    _require_string_lists(request["query"])
    _require_string_mapping(request["headers"])
    return request


def _require_string_lists(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise ValueError("OpenAI query metadata must be an object")
    result: dict[str, list[str]] = {}
    for key, items in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(items, list)
            or not all(isinstance(item, str) for item in items)
        ):
            raise ValueError("OpenAI query metadata must contain string lists")
        result[key] = items
    return result


def _require_string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError("OpenAI header metadata must contain strings")
    return dict(value)


async def _resolve_gw_model_pin(
    request: Request,
    body: bytearray,
    *,
    capability_name: str,
) -> str | None:
    """Resolve a ``gw/*`` request body model id to a single provider id.

    Returns the provider id when the mapping exists and the body was rewritten
    in-place to the provider's ``gateway.model_id``. Returns ``None`` for any
    non-``gw/`` model id (no rewrite, no pinning). Raises ``ProviderNotFound``
    when the model id starts with ``gw/`` but the proxy map has no matching
    capability + provider binding.
    """

    payload = _estimate_payload_from_body(bytes(body))
    model_id = payload.get("model") if isinstance(payload, Mapping) else None
    if not isinstance(model_id, str) or not model_id.startswith("gw/"):
        return None

    quota_repo = getattr(request.app.state, "quota_repository", None)
    if quota_repo is None:
        pool = getattr(request.app.state, "pool", None)
        if pool is None:
            raise ProviderNotFound(model_id)
        quota_repo = QuotaRepository(pool)
    list_model_ids = getattr(quota_repo, "list_model_ids", None)
    if not callable(list_model_ids):
        raise ProviderNotFound(model_id)
    mappings = await list_model_ids()
    mapping = next(
        (
            entry
            for entry in mappings
            if entry.model_id == model_id and entry.capability == capability_name
        ),
        None,
    )
    if mapping is None:
        raise ProviderNotFound(model_id)

    overrides = getattr(request.app, "dependency_overrides", {})
    override = overrides.get(_provider_repo)
    provider_repo = override() if override is not None else _provider_repo(request)
    provider = await provider_repo.get(mapping.provider)
    if provider is None:
        raise ProviderNotFound(mapping.provider)
    gateway = provider.config.get("gateway") if isinstance(provider.config, Mapping) else None
    gateway_model_id = gateway.get("model_id") if isinstance(gateway, Mapping) else None
    if not isinstance(gateway_model_id, str) or not gateway_model_id:
        raise ProviderNotFound(mapping.provider)

    rewritten = dict(payload)
    rewritten["model"] = gateway_model_id
    new_body = json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body[:] = new_body
    return provider.id


def record_upstream_rate_limits(
    rate_limited: Sequence[UpstreamRateLimit],
    providers: Sequence[Provider],
    *,
    now: dt.datetime,
) -> None:
    """Lock out each provider model that answered 429, as the adapter path does.

    A reset time in ``Retry-After`` or ``X-RateLimit-Reset`` wins over the default backoff.
    """

    by_id = {provider.id: provider for provider in providers}
    for limit in rate_limited:
        provider = by_id.get(limit.provider_id)
        key = _lockout.model_lockout_key(provider) if provider is not None else None
        if key is None:
            continue
        reason, reset_at = classify_429(limit.status_code, "", limit.headers, now=now)
        _lockout.get_lockout_table().record_failure(key, now=now, reason=reason, reset_at=reset_at)


@dataclass
class _ProxyCall:
    """State one proxied request carries between the named steps of ``openai_proxy``."""

    request: Request
    capability: str
    path: str
    started_at: float
    body: bytes
    headers: dict[str, str]
    query: str
    request_was_well_formed: bool
    workload_repo: WorkloadRepository
    provider_repo: ProviderRepository
    budget_gate: BudgetGate
    routing_service: ProductionRoutingService
    prepared: _PreparedOpenAIRequest
    route_plan: ProductionRoutePlan = field(init=False)
    resolved_capability: Capability = field(init=False)
    providers: list[Provider] = field(init=False)
    workload_id: str = field(init=False)
    admission_quote: _OpenAIFallbackBudgetQuote = field(init=False)
    upstream_state: Provider | ProviderCooldownState = field(init=False)
    client: httpx.AsyncClient = field(init=False)
    # A caller that rewrites the relayed stream reports here whether it ended in failure:
    # the reason when it did, ``None`` when it finished. Read once the relay is exhausted.
    stream_failure: Callable[[], str | None] | None = field(default=None, init=False)

    @property
    def input_bytes(self) -> int:
        return len(self.body) if self.body else 0

    async def record_cancellation(
        self, provider: Provider, attempted_provider_ids: tuple[str, ...] = ()
    ) -> None:
        await _record_proxy_cancellation(
            self.workload_repo,
            self.workload_id,
            started_at=self.started_at,
            attempted_provider_ids=attempted_provider_ids,
            admission_quote=self.admission_quote,
            capability_name=self.capability,
            provider=provider,
            input_bytes=self.input_bytes,
        )

    async def record_pre_egress_failure(
        self, provider: Provider, reason: str | None = None
    ) -> None:
        extra = {} if reason is None else {"reason": reason}
        await _record_pre_egress_failure(
            self.workload_repo,
            self.workload_id,
            started_at=self.started_at,
            admission_quote=self.admission_quote,
            capability_name=self.capability,
            provider=provider,
            input_bytes=self.input_bytes,
            **extra,
        )

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started_at) * 1000)


async def _plan_proxy_route(call: _ProxyCall, pinned_provider_id: str | None) -> None:
    """Plan the attempt chain (or the single pinned provider) for one proxied request."""

    capability = call.capability
    try:
        if pinned_provider_id is not None:
            route_plan = await call.routing_service.preview_prepared(
                capability_id=capability,
                prepared_payload=call.prepared.routing_payload,
                operation=RoutingOperation.SYNC_INFERENCE,
                max_attempts=1,
                openai_lease_proxy=True,
                openai_attempt_order=(pinned_provider_id,),
            )
        else:
            route_plan = await call.routing_service.preview_prepared(
                capability_id=capability,
                prepared_payload=call.prepared.routing_payload,
                operation=RoutingOperation.SYNC_INFERENCE,
                max_attempts=MAX_OPENAI_ATTEMPTS,
                openai_lease_proxy=True,
            )
    except CapabilityNotFoundError as exc:
        raise CapabilityNotFound(exc.capability_name) from exc
    except BudgetRejected:
        raise  # the budget contract (HTTP 402), not a provider outage
    except NoHealthyProviderError as exc:
        log.warning(
            "openai proxy: no executable route for capability=%s pinned=%s: %s",
            capability,
            pinned_provider_id,
            getattr(exc, "eliminations", []),
        )
        hatch = getattr(exc, "escape_hatch", None)
        raise ProviderUnavailable(
            capability, escape_hatch=hatch.to_dict() if hatch is not None else None
        ) from exc
    except Exception as exc:  # reason: normalize heterogeneous planner/repository failures
        log.exception("openai proxy: planner raised")
        raise ProviderUnavailable(capability) from exc
    call.route_plan = route_plan
    call.resolved_capability = route_plan.capability_snapshot
    call.headers["x-pitwall-capability"] = capability
    call.headers["x-pitwall-trace"] = route_plan.plan_id
    providers_by_id = {provider.id: provider for provider in route_plan.provider_snapshots}
    call.providers = [providers_by_id[item.provider_id] for item in route_plan.attempts]


async def _apply_skip_primary_drill(call: _ProxyCall) -> None:
    """Replan without the first provider when the request asks for a failover drill."""

    if call.request.headers.get("x-pitwall-drill", "").lower() != "skip-primary":
        return
    if len(call.providers) <= 1:
        raise ProviderUnavailable(call.capability)
    remaining_provider_ids = tuple(provider.id for provider in call.providers[1:])
    try:
        route_plan = await call.routing_service.preview_prepared(
            capability_id=call.capability,
            prepared_payload=call.prepared.routing_payload,
            operation=RoutingOperation.SYNC_INFERENCE,
            max_attempts=MAX_OPENAI_ATTEMPTS,
            openai_lease_proxy=True,
            openai_attempt_order=remaining_provider_ids,
        )
    except BudgetRejected:
        raise
    except Exception as exc:  # reason: normalize heterogeneous drill replanning failures
        raise ProviderUnavailable(call.capability) from exc
    providers_by_id = {provider.id: provider for provider in route_plan.provider_snapshots}
    call.route_plan = route_plan
    call.providers = [providers_by_id[item.provider_id] for item in route_plan.attempts]
    call.headers["x-pitwall-trace"] = route_plan.plan_id


async def _admit_and_start_workload(call: _ProxyCall, provider: Provider) -> Decimal:
    """Admit the workload and move it to RUNNING; return the admitted cost upper bound."""

    workload_id, admission_quote = await _admit_passthrough(
        capability=call.resolved_capability,
        providers=tuple(call.providers),
        body=call.body,
        budget_gate=call.budget_gate,
        workload_repo=call.workload_repo,
        route_plan=call.route_plan,
    )
    call.workload_id = workload_id
    call.admission_quote = admission_quote
    try:
        running_workload = await transition_to_running(call.workload_repo, workload_id)
    except asyncio.CancelledError:
        await call.record_cancellation(provider)
        raise
    except Exception as exc:  # reason: persist terminal truth for any repository failure
        await call.record_pre_egress_failure(provider)
        raise ProviderUnavailable(call.capability) from exc
    if running_workload is None:
        await call.record_pre_egress_failure(provider)
        raise ProviderUnavailable(call.capability)
    return admission_quote.upper_bound()


async def _record_cold_start_warming(call: _ProxyCall, provider: Provider) -> None:
    """Mark a cold self-hosted provider as warming before the first byte goes out."""

    call.upstream_state = provider
    try:
        call.upstream_state = await _record_upstream_outcome(
            provider_repo=call.provider_repo,
            provider_id=provider.id,
            state=provider,
            classification="warming",
            now=dt.datetime.now(dt.UTC),
        )
    except asyncio.CancelledError:
        await call.record_cancellation(provider)
        raise
    except Exception:  # reason: warming telemetry must not block provider execution
        log.warning("provider warming health telemetry unavailable")


async def _open_upstream_client(
    call: _ProxyCall, provider: Provider, timeout: httpx.Timeout
) -> None:
    try:
        call.client = _new_upstream_client(timeout)
    except asyncio.CancelledError:
        await call.record_cancellation(provider)
        raise
    except Exception as exc:  # reason: third-party client setup failures need fail-closed cleanup
        await call.record_pre_egress_failure(provider, "upstream_client_initialization_failed")
        raise ProviderUnavailable(call.capability) from exc


def _chain_provider(call: _ProxyCall, provider_id: str, default: Provider) -> Provider:
    return next((item for item in call.providers if item.id == provider_id), default)


async def _send_upstream(
    call: _ProxyCall, provider: Provider, *, fallback_budget_s: float
) -> OpenAIProxyResult:
    """Run the provider chain until response headers; own every failure before relay."""

    attempted_provider_ids: tuple[str, ...] = ()

    def remember_attempts(provider_ids: tuple[str, ...]) -> None:
        nonlocal attempted_provider_ids
        attempted_provider_ids = provider_ids

    try:
        result = await execute_openai_with_fallback(
            OpenAIProxyRequest(
                method=call.request.method,
                path=_path_with_query(call.path, call.query),
                headers=call.headers,
                body=call.body,
                client=call.client,
                fallback_budget_s=fallback_budget_s,
            ),
            call.providers,
            on_attempt=remember_attempts,
        )
    except asyncio.CancelledError:
        cancelled_provider = _chain_provider(
            call, attempted_provider_ids[-1] if attempted_provider_ids else provider.id, provider
        )
        try:
            await _close_upstream_client(call.client)
        finally:
            await call.record_cancellation(cancelled_provider, attempted_provider_ids)
        raise
    except OpenAIProxyExecutionError as exc:
        await _fail_transport(call, provider, exc)
        raise  # unreachable: _fail_transport always raises
    record_upstream_rate_limits(result.rate_limited, call.providers, now=dt.datetime.now(dt.UTC))
    return result


async def _fail_transport(
    call: _ProxyCall, provider: Provider, exc: OpenAIProxyExecutionError
) -> NoReturn:
    """Every provider failed before response headers: record it and raise the stable error."""

    chain = exc.attempted_provider_ids
    failed_provider = _chain_provider(call, chain[-1] if chain else provider.id, provider)
    try:
        await _close_upstream_client(call.client)
    except asyncio.CancelledError:
        await call.record_cancellation(failed_provider, chain)
        raise
    record_upstream_rate_limits(exc.rate_limited, call.providers, now=dt.datetime.now(dt.UTC))
    try:
        await _record_upstream_outcome(
            provider_repo=call.provider_repo,
            provider_id=failed_provider.id,
            state=call.upstream_state if failed_provider.id == provider.id else failed_provider,
            classification=classify_upstream_outcome(
                status_code=None,
                exc=exc.cause or exc,
                request_was_well_formed=call.request_was_well_formed,
            ),
            now=dt.datetime.now(dt.UTC),
        )
    except asyncio.CancelledError:
        await call.record_cancellation(failed_provider, chain)
        raise
    except Exception:  # reason: health telemetry must not mask the stable provider failure
        log.warning("provider failure health telemetry unavailable")
    execution_ms = call.elapsed_ms()
    trace_id = emit_inference_trace(
        workload_id=call.workload_id,
        capability_name=call.capability,
        provider_id=failed_provider.id,
        provider_type=failed_provider.provider_type.value,
        runpod_endpoint_id=failed_provider.runpod_endpoint_id,
        cost_estimate_usd=float(call.admission_quote.upper_bound()),
        input_bytes=call.input_bytes,
        output_bytes=0,
        execution_ms=execution_ms,
        status="error",
        error=RuntimeError("provider transport failed"),
    )
    attempted_cost = call.admission_quote.attempted_cost(chain) if chain else None
    try:
        await transition_to_failed(
            call.workload_repo,
            call.workload_id,
            execution_ms=execution_ms,
            provider_id=failed_provider.id,
            error={
                "error": "provider_transport_error",
                "attempted_providers": list(chain),
                "attempted_failures": {
                    provider_id: (
                        value.lower().replace(" ", "_")
                        if value.startswith("HTTP ") and value[5:].isdigit()
                        else "transport_error"
                    )
                    for provider_id, value in exc.attempted_errors.items()
                },
            },
            fallback_chain=list(chain),
            langfuse_trace_id=trace_id,
            cost_ceiling_usd=attempted_cost.ceiling_usd if attempted_cost is not None else None,
            cost_quote=attempted_cost.quote if attempted_cost is not None else None,
            cost_actual_usd=_LEASE_COVERED_COST if attempted_cost is None else None,
            cost_actual_provenance=(
                "broker:no_provider_invocation" if attempted_cost is None else None
            ),
        )
    except asyncio.CancelledError:
        await call.record_cancellation(failed_provider, chain)
        raise
    except Exception:  # reason: failure bookkeeping must not mask the original upstream error
        log.debug("workload failed transition failed for %s", call.workload_id, exc_info=True)
    raise ProviderUnavailable(call.capability, chain=list(chain)) from exc


async def _read_error_probe(
    call: _ProxyCall,
    provider: Provider,
    result: OpenAIProxyResult,
    attempted_cost: _OpenAIAttemptedCost,
    cost_estimate_usd: Decimal,
) -> bytes:
    """Read a 400 body (needed to classify launch errors); other statuses stream untouched."""

    upstream_response = result.response
    try:
        return await upstream_response.aread() if upstream_response.status_code == 400 else b""
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        execution_ms = call.elapsed_ms()
        trace_id = emit_inference_trace(
            workload_id=call.workload_id,
            capability_name=call.capability,
            provider_id=provider.id,
            provider_type=provider.provider_type.value,
            runpod_endpoint_id=provider.runpod_endpoint_id,
            cost_estimate_usd=float(cost_estimate_usd),
            input_bytes=call.input_bytes,
            output_bytes=0,
            execution_ms=execution_ms,
            status="error",
            error=RuntimeError("provider response read failed"),
        )
        try:
            await transition_to_failed(
                call.workload_repo,
                call.workload_id,
                execution_ms=execution_ms,
                provider_id=provider.id,
                error={
                    "error": "provider_response_read_failed",
                    "attempted_providers": list(result.attempted_provider_ids),
                },
                fallback_chain=list(result.attempted_provider_ids),
                langfuse_trace_id=trace_id,
                cost_ceiling_usd=attempted_cost.ceiling_usd,
                cost_quote=attempted_cost.quote,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # reason: bookkeeping must not mask the stable provider failure
            log.debug(
                "workload response-read failure transition failed for %s",
                call.workload_id,
                exc_info=True,
            )
        raise ProviderUnavailable(
            call.capability, chain=list(result.attempted_provider_ids)
        ) from exc


async def _observe_consumer_saturation(
    call: _ProxyCall, provider: Provider, *, observed_at: dt.datetime
) -> None:
    """Feed 4xx replies to the saturation detector; telemetry only."""

    redis = getattr(call.request.app.state, "redis", None)
    if redis is None:
        return
    try:
        settings = load_settings_from_env()
        fingerprint = _token_fingerprint(call.request.headers.get("authorization"))
        signal = await SaturationDetector(
            redis,
            window_s=settings.pitwall_saturation_window_s,
            threshold=settings.pitwall_saturation_4xx_threshold,
        ).observe_4xx(
            provider_id=provider.id,
            token_fingerprint=fingerprint,
            capability_name=call.capability,
            now=observed_at,
        )
        if signal is not None:
            log.warning(
                "consumer saturation detected",
                extra={
                    "provider_id": signal.provider_id,
                    "token_fingerprint": signal.token_fingerprint,
                    "capability_name": signal.capability_name,
                    "rate_per_s": signal.rate_per_s,
                },
            )
    except Exception:  # reason: saturation telemetry must not mask the response
        log.warning("saturation telemetry unavailable", exc_info=True)


async def _record_response_telemetry(
    call: _ProxyCall,
    provider: Provider,
    upstream_response: httpx.Response,
    *,
    classification: UpstreamOutcome,
    observed_at: dt.datetime,
) -> None:
    """Record capacity, saturation, health, and lease activity for one upstream reply."""

    try:
        await _record_self_hosted_concurrency(
            call.provider_repo, provider, status_code=upstream_response.status_code
        )
    except Exception:  # reason: capacity telemetry must not mask the response
        log.warning("provider capacity telemetry unavailable", exc_info=True)
    if 400 <= upstream_response.status_code < 500:
        await _observe_consumer_saturation(call, provider, observed_at=observed_at)
    try:
        await _record_upstream_outcome(
            provider_repo=call.provider_repo,
            provider_id=provider.id,
            state=(call.upstream_state if provider.id == call.providers[0].id else provider),
            classification=classification,
            now=observed_at,
        )
    except Exception:  # reason: provider health telemetry must not mask the response
        log.warning(
            "provider health telemetry unavailable: provider=%s classification=%s",
            provider.id,
            classification,
            exc_info=True,
        )
    active_lease_id = provider.config.get("active_lease_id")
    if (
        200 <= upstream_response.status_code < 300
        and isinstance(active_lease_id, str)
        and active_lease_id
    ):
        try:
            await stamp_lease_traffic(
                getattr(call.request.app.state, "redis", None), active_lease_id, now=observed_at
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # reason: activity telemetry must not mask provider delivery
            log.warning("lease activity telemetry unavailable")


async def _record_delivered_5xx(
    call: _ProxyCall,
    provider: Provider,
    result: OpenAIProxyResult,
    attempted_cost: _OpenAIAttemptedCost,
    *,
    execution_ms: int,
    trace_id: str | None,
) -> None:
    try:
        await transition_to_failed(
            call.workload_repo,
            call.workload_id,
            execution_ms=execution_ms,
            provider_id=provider.id,
            error={
                "error": "provider_http_error",
                "status_code": result.response.status_code,
                "attempted_providers": list(result.attempted_provider_ids),
            },
            fallback_chain=list(result.attempted_provider_ids),
            langfuse_trace_id=trace_id,
            cost_ceiling_usd=attempted_cost.ceiling_usd,
            cost_quote=attempted_cost.quote,
        )
    except Exception:  # reason: failure bookkeeping must not fail the delivered response
        log.debug("workload failed transition failed for %s", call.workload_id, exc_info=True)


def _relay_callbacks(
    call: _ProxyCall,
    provider: Provider,
    result: OpenAIProxyResult,
    attempted_cost: _OpenAIAttemptedCost,
    *,
    trace: Any,
    trace_id: str | None,
) -> tuple[Callable[[int], Awaitable[None]], Callable[[str, int], Awaitable[None]]]:
    """Build the completion and interruption hooks the relay runs after the last byte."""

    lease_covered = (
        _active_lease_cost_is_covered(provider) and attempted_cost.all_attempts_lease_covered
    )

    async def record_relay_completed(delivered_bytes: int) -> None:
        failure = call.stream_failure() if call.stream_failure is not None else None
        if failure is not None:
            # The bytes arrived but the stream they carried failed: settle it as failed.
            await record_relay_interrupted(failure, delivered_bytes)
            return
        completed_ms = call.elapsed_ms()
        if trace is not None:
            trace.finish(
                status="success",
                execution_ms=completed_ms,
                input_bytes=call.input_bytes,
                output_bytes=delivered_bytes,
            )
        try:
            await transition_to_completed(
                call.workload_repo,
                call.workload_id,
                execution_ms=completed_ms,
                output_bytes=delivered_bytes,
                provider_id=provider.id,
                cost_actual_usd=_LEASE_COVERED_COST if lease_covered else None,
                cost_actual_provenance="broker:lease_covered" if lease_covered else None,
                fallback_chain=list(result.attempted_provider_ids),
                langfuse_trace_id=trace_id,
                cost_ceiling_usd=attempted_cost.ceiling_usd,
                cost_quote=attempted_cost.quote,
            )
        except asyncio.CancelledError:
            await call.record_cancellation(provider, result.attempted_provider_ids)
            raise
        except Exception:  # reason: bookkeeping must not fail the delivered response
            log.debug(
                "workload completed transition failed for %s", call.workload_id, exc_info=True
            )

    async def record_relay_interrupted(reason: str, delivered_bytes: int) -> None:
        interrupted_ms = call.elapsed_ms()
        if trace is not None:
            trace.finish(
                status="error",
                execution_ms=interrupted_ms,
                error=RuntimeError(reason),
                input_bytes=call.input_bytes,
                output_bytes=delivered_bytes,
            )
        try:
            await transition_to_failed(
                call.workload_repo,
                call.workload_id,
                execution_ms=interrupted_ms,
                output_bytes=delivered_bytes,
                provider_id=provider.id,
                error={
                    "error": reason,
                    "attempted_providers": list(result.attempted_provider_ids),
                },
                fallback_chain=list(result.attempted_provider_ids),
                langfuse_trace_id=trace_id,
                cost_ceiling_usd=attempted_cost.ceiling_usd,
                cost_quote=attempted_cost.quote,
            )
        except asyncio.CancelledError:
            await call.record_cancellation(provider, result.attempted_provider_ids)
            raise
        except Exception:  # reason: bookkeeping must not mask stream termination
            log.debug(
                "workload stream failure transition failed for %s",
                call.workload_id,
                exc_info=True,
            )

    return record_relay_completed, record_relay_interrupted


class _InferenceTraceFields(TypedDict):
    """Keyword arguments shared by ``start_inference_trace`` and ``emit_inference_trace``."""

    workload_id: str
    capability_name: str
    provider_id: str
    provider_type: str
    runpod_endpoint_id: str | None
    cost_estimate_usd: float | None
    input_bytes: int | None


async def _relay_proxy_response(
    call: _ProxyCall,
    result: OpenAIProxyResult,
    attempted_cost: _OpenAIAttemptedCost,
    cost_estimate_usd: Decimal,
) -> StreamingResponse:
    """Classify and record the upstream reply, then stream it to the consumer."""

    upstream_response = result.response
    provider = result.provider
    response_body = await _read_error_probe(
        call, provider, result, attempted_cost, cost_estimate_usd
    )
    observed_at = dt.datetime.now(dt.UTC)
    classification = classify_upstream_outcome(
        status_code=upstream_response.status_code,
        exc=None,
        request_was_well_formed=call.request_was_well_formed,
        response_body=response_body,
    )
    await _record_response_telemetry(
        call, provider, upstream_response, classification=classification, observed_at=observed_at
    )
    execution_ms = call.elapsed_ms()
    trace = None
    trace_fields: _InferenceTraceFields = {
        "workload_id": call.workload_id,
        "capability_name": call.capability,
        "provider_id": provider.id,
        "provider_type": provider.provider_type.value,
        "runpod_endpoint_id": provider.runpod_endpoint_id,
        "cost_estimate_usd": float(cost_estimate_usd),
        "input_bytes": call.input_bytes,
    }
    relay_completed: Callable[[int], Awaitable[None]] | None = None
    relay_interrupted: Callable[[str, int], Awaitable[None]] | None = None
    if upstream_response.status_code >= 500:
        trace_id = emit_inference_trace(
            **trace_fields, output_bytes=0, execution_ms=execution_ms, status="error"
        )
        await _record_delivered_5xx(
            call, provider, result, attempted_cost, execution_ms=execution_ms, trace_id=trace_id
        )
    else:
        trace = start_inference_trace(**trace_fields)
        trace_id = trace.trace_id
        relay_completed, relay_interrupted = _relay_callbacks(
            call, provider, result, attempted_cost, trace=trace, trace_id=trace_id
        )

    response_headers = _downstream_response_headers(upstream_response)
    response_headers["X-Pitwall-Workload-ID"] = call.workload_id
    response_headers["X-Pitwall-Capability"] = call.capability
    response_headers["X-Pitwall-Provider-ID"] = provider.id
    response_headers["X-Pitwall-Route-Plan-ID"] = call.route_plan.plan_id
    if isinstance(trace_id, str):
        response_headers["X-Pitwall-Trace"] = trace_id
    return StreamingResponse(
        _relay_upstream_bytes(
            upstream_response,
            call.client,
            on_completed=relay_completed,
            on_interrupted=relay_interrupted,
        ),
        status_code=upstream_response.status_code,
        headers=response_headers,
    )


async def _deliver_upstream_response(
    call: _ProxyCall, result: OpenAIProxyResult, cost_estimate_usd: Decimal
) -> StreamingResponse:
    """Relay the selected reply; close both upstream objects on any failure."""

    attempted_cost = call.admission_quote.attempted_cost(result.attempted_provider_ids)
    try:
        return await _relay_proxy_response(call, result, attempted_cost, cost_estimate_usd)
    except asyncio.CancelledError:
        try:
            await _close_upstream_response(result.response, call.client)
        finally:
            await call.record_cancellation(result.provider, result.attempted_provider_ids)
        raise
    except Exception:  # reason: post-response failures must close both upstream objects
        await _close_upstream_response(result.response, call.client)
        raise


@router.get("/v1/openai/{capability}/v1/{path:path}")
@router.post("/v1/openai/{capability}/v1/{path:path}")
@router.put("/v1/openai/{capability}/v1/{path:path}")
@router.delete("/v1/openai/{capability}/v1/{path:path}")
@router.patch("/v1/openai/{capability}/v1/{path:path}")
@router.options("/v1/openai/{capability}/v1/{path:path}")
async def openai_proxy(
    capability: PathId,
    path: PathId,
    request: Request,
    pool: Any = Depends(_pool),
    provider_repo: ProviderRepository = Depends(_provider_repo),
    workload_repo: WorkloadRepository = Depends(_workload_repo),
    budget_gate: BudgetGate = Depends(_budget_gate),
    routing_service: ProductionRoutingService = Depends(_routing_service),
) -> Response:
    started_at = time.perf_counter()
    try:
        validate_openai_proxy_path(path)
    except ValueError as exc:
        raise InvalidProxyPath(str(exc)) from exc
    prepared_request = _prepared_openai_request(request, await request.body())
    body_buffer = bytearray(prepared_request.body)
    pinned_provider_id = await _resolve_gw_model_pin(
        request, body_buffer, capability_name=capability
    )
    body = bytes(body_buffer)
    call = _ProxyCall(
        request=request,
        capability=capability,
        path=path,
        started_at=started_at,
        body=body,
        headers=prepared_request.headers,
        query=prepared_request.query,
        request_was_well_formed=_request_body_is_well_formed(body),
        workload_repo=workload_repo,
        provider_repo=provider_repo,
        budget_gate=budget_gate,
        routing_service=routing_service,
        prepared=prepared_request,
    )
    await _plan_proxy_route(call, pinned_provider_id)
    await _apply_skip_primary_drill(call)
    provider = call.providers[0] if call.providers else None
    if provider is None:
        raise ProviderUnavailable(capability)

    model_id = _request_model_id(body) or ""
    resident = _model_is_resident(provider, model_id) if model_id else True
    timeout = upstream_timeout(provider, model_id=model_id, resident=resident)
    if timeout.read is None:
        raise RuntimeError("upstream timeout must have a read deadline")
    cold_start_attempt = self_hosted_profile(provider) is not None and not resident
    cost_estimate_usd = await _admit_and_start_workload(call, provider)
    call.upstream_state = provider
    if cold_start_attempt:
        await _record_cold_start_warming(call, provider)
    await _open_upstream_client(call, provider, timeout)
    result = await _send_upstream(
        call,
        provider,
        fallback_budget_s=float(timeout.read) if cold_start_attempt else _OPENAI_FALLBACK_BUDGET_S,
    )
    return await _deliver_upstream_response(call, result, cost_estimate_usd)


__all__ = ["router"]
