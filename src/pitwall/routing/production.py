"""Deterministic production planning and provider-neutral execution.

The pure builder consumes immutable capability/provider snapshots.  The service
adds repository reads, pre-spend inspection, budget admission, persistence, and
the narrow provider adapter calls.  No route preview performs provider egress.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

import httpx

from pitwall.config import PitwallSettings, RoutingWeights
from pitwall.core.enums import CapabilityClass, ProviderAdapterId, ProviderType, WorkloadState
from pitwall.core.models import Capability, Provider, Workload
from pitwall.cost.budget_gate import (
    BudgetAdmission,
    BudgetGate,
    BudgetRejected,
    BudgetRejectionReason,
    BudgetSnapshot,
)
from pitwall.cost.estimator import CostComponent, CostQuote, PerTokenPricing
from pitwall.cost.usage import parse_usage_json
from pitwall.db.quota_repository import QuotaRepository
from pitwall.db.repository import CapabilityRepository, ProviderRepository, WorkloadRepository
from pitwall.observability.langfuse import emit_inference_trace
from pitwall.providers.errors import ProviderQuotaExhausted
from pitwall.providers.interface import (
    AsyncInferenceCancelRequest,
    AsyncInferenceRequest,
    AsyncInferenceStatusRequest,
    CredentialReference,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderOperationContext,
)
from pitwall.providers.registry import (
    ProviderNotRegisteredError,
    ProviderRegistry,
    UnsupportedProviderCapabilityError,
    get_default_registry,
)
from pitwall.resolver.exceptions import (
    CapabilityDisabledError,
    CapabilityNotFoundError,
    NoHealthyProviderError,
    ProviderNotFoundError,
)
from pitwall.resolver.service import CapabilityRepositoryLike, ProviderRepositoryLike
from pitwall.routing import lockout as _lockout
from pitwall.routing.cascade_seed import escape_hatch_message
from pitwall.routing.constraints import evaluate_hard_constraints
from pitwall.routing.context import PlanningContext
from pitwall.routing.cooldown import CooldownStateMachine, is_in_cooldown, to_provider_patch
from pitwall.routing.openai import (
    explicit_openai_fallback_ids,
    openai_base_url_for_provider,
    pod_lease_base_url,
)
from pitwall.routing.quota import QuotaSnapshot, quota_eligible
from pitwall.routing.types import Hints, RoutingRequest
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendInspectionService,
    PreSpendPayloadScanResult,
    get_pre_spend_inspection_service,
)
from pitwall.webhook_dispatcher.security import resolve_webhook_target

_WORKLOAD_TYPE_SYNC = "inference"
_WORKLOAD_TYPE_ASYNC = "async_job"
_JOB_STATUS_TIMEOUT_S = 15.0
_MAX_JOB_RESULT_BYTES = 1_048_576
_WORKLOAD_LOCK_NAMESPACE = 0x50495457414C4C
_PREPARED_PAYLOAD_ATTESTATION = object()

log = logging.getLogger(__name__)

_ATTEMPT_BACKOFF_BASE_S = 0.25
_ATTEMPT_BACKOFF_CAP_S = 2.0


def attempt_backoff_s(attempt_index: int) -> float:
    """Bounded exponential pause before the ``attempt_index``-th (1-based retry) provider call."""

    return float(
        min(_ATTEMPT_BACKOFF_BASE_S * 2 ** max(0, attempt_index - 1), _ATTEMPT_BACKOFF_CAP_S)
    )


def counts_toward_cooldown(exc: BaseException) -> bool:
    """A timeout or a 5xx reply is a provider health failure; quota and 4xx errors are not."""

    if isinstance(exc, ProviderQuotaExhausted):
        return False
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and not isinstance(status, bool) and 500 <= status < 600


class RoutingOperation(StrEnum):
    """Capability-specific executor selected by a production plan."""

    SYNC_INFERENCE = "sync_inference"
    ASYNC_INFERENCE = "async_inference"
    COMPUTE = "compute"


_REQUIRED_ADAPTER_CAPABILITY = {
    RoutingOperation.SYNC_INFERENCE: ProviderCapability.SYNC_INFERENCE,
    RoutingOperation.ASYNC_INFERENCE: ProviderCapability.ASYNC_INFERENCE,
    RoutingOperation.COMPUTE: ProviderCapability.COMPUTE,
}


class RoutePlanningError(RuntimeError):
    """Base class for stable production-planning failures."""


class RouteGuardrailRejected(RoutePlanningError):
    """Raised before pricing, admission, or provider egress for a blocked body."""

    def __init__(self, rule_ids: Sequence[str]) -> None:
        super().__init__("pre-spend inspection blocked the request")
        self.rule_ids = tuple(sorted(set(rule_ids)))


class JobNotCancellableError(RoutePlanningError):
    """The workload is not an async job, or its provider cannot cancel one."""


class RouteProviderInvocationError(RoutePlanningError):
    """A provider call failed; provider-controlled detail is intentionally hidden."""

    def __init__(self) -> None:
        super().__init__("selected provider invocation failed")


@dataclass(frozen=True, slots=True)
class PreparedRoutingPayload:
    """An explicit allow/redact inspection result safe for one routing call."""

    payload: Mapping[str, Any] = field(repr=False)
    decision: PreSpendDecision
    inspected_bytes: int
    payload_sha256: str
    _attestation: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._attestation is not _PREPARED_PAYLOAD_ATTESTATION:
            raise TypeError("prepared routing payload must come from from_inspection()")

    @classmethod
    def from_inspection(cls, result: PreSpendPayloadScanResult) -> PreparedRoutingPayload:
        if not isinstance(result, PreSpendPayloadScanResult):
            raise TypeError("prepared routing payload requires a pre-spend inspection result")
        if result.decision == PreSpendDecision.BLOCK:
            raise RouteGuardrailRejected(tuple(item.rule for item in result.findings))
        payload = _payload_mapping(result.redacted_payload)
        return cls(
            payload=payload,
            decision=result.decision,
            inspected_bytes=result.inspected_bytes,
            payload_sha256=hashlib.sha256(_canonical_json(payload).encode()).hexdigest(),
            _attestation=_PREPARED_PAYLOAD_ATTESTATION,
        )


class NoExecutableRouteError(NoHealthyProviderError, RoutePlanningError):
    """No provider survived capability, safety, health, capacity, and cost gates."""

    def __init__(
        self,
        capability_name: str,
        eliminations: Sequence[RouteElimination],
        *,
        cheapest_over_budget_usd: Decimal | None = None,
        escape_hatch: EscapeHatch | None = None,
    ) -> None:
        super().__init__(capability_name)
        self.eliminations = tuple(eliminations)
        # Set when a provider passed every other gate but its ceiling exceeded the budget.
        self.cheapest_over_budget_usd = cheapest_over_budget_usd
        # Set when every free pool is exhausted and an own pod would cover the gap (prong 3).
        self.escape_hatch = escape_hatch


@dataclass(frozen=True, slots=True)
class RouteElimination:
    """One safe, explicit reason that a provider cannot execute this request."""

    provider_id: str
    adapter_id: str
    reason: str
    stage: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "adapter_id": self.adapter_id,
            "stage": self.stage,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProductionRouteCandidate:
    """Eligible candidate plus Decimal objective and public cost explanation."""

    provider_id: str
    adapter_id: str
    provider_name: str
    priority: int
    rank: int
    quote: CostQuote = field(repr=False, compare=False)
    latency_ms: Decimal
    latency_signal: str
    cost_component: Decimal
    latency_component: Decimal
    objective: Decimal
    recent_error_rate: Decimal
    provider: Provider = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "adapter_id": self.adapter_id,
            "provider_name": self.provider_name,
            "priority": self.priority,
            "rank": self.rank,
            "objective": _decimal_text(self.objective),
            "score_components": {
                "cost": _decimal_text(self.cost_component),
                "latency": _decimal_text(self.latency_component),
                "latency_ms": _decimal_text(self.latency_ms),
                "latency_signal": self.latency_signal,
                "recent_error_rate": _decimal_text(self.recent_error_rate),
            },
            "cost": self.quote.to_serializable_dict(),
        }


@dataclass(frozen=True, slots=True)
class EscapeHatch:
    """Prong-3 ``pitwall serve`` proposal surfaced when every free pool is exhausted."""

    proposed_command: str
    executed: bool = False
    reason: str = "all_free_pools_exhausted"

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "proposed_command": self.proposed_command,
            "executed": self.executed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProductionRoutePlan:
    """Byte-stable, payload-free production plan shared by all transports."""

    plan_id: str
    observed_at: dt.datetime
    operation: RoutingOperation
    capability_id: str
    capability_name: str
    payload_sha256: str
    provider_constraint: str | None
    mode: str
    weights: RoutingWeights
    selected_provider_id: str
    attempts: tuple[ProductionRouteCandidate, ...]
    ranked_candidates: tuple[ProductionRouteCandidate, ...]
    eliminated: tuple[RouteElimination, ...]
    capability_snapshot: Capability = field(repr=False, compare=False)
    provider_snapshots: tuple[Provider, ...] = field(repr=False, compare=False)
    escape_hatch: EscapeHatch | None = None

    @property
    def selected(self) -> ProductionRouteCandidate:
        return self.attempts[0]

    @property
    def fallback_chain(self) -> tuple[str, ...]:
        return tuple(candidate.provider_id for candidate in self.attempts)

    @property
    def dropped_provider_reasons(self) -> dict[str, list[str]]:
        """Map each eliminated provider to its set of stage:reason strings."""

        out: dict[str, list[str]] = {}
        for item in self.eliminated:
            out.setdefault(item.provider_id, []).append(f"{item.stage}:{item.reason}")
        return out

    def to_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "plan_id": self.plan_id,
            "observed_at": self.observed_at.isoformat(),
            "operation": self.operation.value,
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "payload_sha256": self.payload_sha256,
            "provider_constraint": self.provider_constraint,
            "mode": self.mode,
            "weights": {
                "cost": _decimal_text(self.weights.cost),
                "latency": _decimal_text(self.weights.latency),
            },
            "selected_provider_id": self.selected_provider_id,
            "fallback_chain": list(self.fallback_chain),
            "attempts": [candidate.to_dict() for candidate in self.attempts],
            "ranked_candidates": [candidate.to_dict() for candidate in self.ranked_candidates],
            "eliminated": [item.to_dict() for item in self.eliminated],
            "signal_policy": {
                "missing_cost": "fail_closed",
                "explicit_capacity_unavailable": "fail_closed",
                "missing_capacity": "neutral",
                "missing_latency": "worst_known_or_zero",
                "invalid_latency": "worst_known_or_zero",
            },
        }
        if self.escape_hatch is not None:
            document["escape_hatch"] = self.escape_hatch.to_dict()
        return document

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.to_dict()).encode("utf-8")


class RoutePlanView(Protocol):
    """Minimum plan view needed by execution and transport renderers."""

    @property
    def plan_id(self) -> str: ...

    def to_dict(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class PersistedRoutePlan:
    """Validated wrapper used for idempotent reads of a stored safe plan."""

    plan_id: str
    document: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


@dataclass(frozen=True, slots=True)
class RouteExecutionResult:
    """One sync execution result carrying the exact plan/trace identity."""

    workload: Workload
    plan: RoutePlanView
    output: Any

    def to_dict(self) -> dict[str, object]:
        return {
            "workload_id": self.workload.id,
            "state": _enum_text(self.workload.state),
            "provider_id": self.workload.provider_id,
            "plan": self.plan.to_dict(),
            "result": self.output,
        }


@dataclass(frozen=True, slots=True)
class JobEventPage:
    """Bounded persisted lifecycle view; no adapter owns a second stream engine."""

    workload_id: str
    plan_id: str | None
    events: tuple[dict[str, object], ...]
    streaming_supported: bool = False
    unavailable_reason: str = "provider_event_stream_unavailable"

    def to_dict(self) -> dict[str, object]:
        return {
            "workload_id": self.workload_id,
            "plan_id": self.plan_id,
            "events": list(self.events),
            "streaming_supported": self.streaming_supported,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class JobResultPage:
    """Bounded provider-neutral result view with explicit unavailable states."""

    workload_id: str
    plan_id: str | None
    state: str
    provider_id: str | None = None
    external_job_id: str | None = None
    plan: Mapping[str, Any] | None = None
    cost_estimate_usd: Decimal | None = None
    cost_actual_usd: Decimal | None = None
    trace_id: str | None = None
    result: Any = None
    available: bool = False
    unavailable_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "workload_id": self.workload_id,
            "plan_id": self.plan_id,
            "state": self.state,
            "provider_id": self.provider_id,
            "result": self.result,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class RouteBudgetQuote:
    """Worst-case route admission across safe synchronous fallback attempts."""

    plan: ProductionRoutePlan
    fallback_spend: bool

    def estimate(self) -> Decimal:
        return self.plan.selected.quote.estimate()

    def upper_bound(self) -> Decimal:
        ceilings = tuple(candidate.quote.upper_bound() for candidate in self.plan.attempts)
        return sum(ceilings, start=Decimal("0")) if self.fallback_spend else ceilings[0]

    def to_serializable_dict(self) -> dict[str, object]:
        """Return the one persisted quote shape read back by WorkloadCostRead.

        The plan id is persisted separately in ``workloads.route_plan_id``
        (migration 0031); it appears here only as a human-readable assumption.
        """
        components = [
            CostComponent(
                name=f"provider_attempt_{candidate.rank}",
                unit="attempt",
                rate=candidate.quote.estimate(),
                ceiling_rate=candidate.quote.upper_bound(),
                estimated_count=Decimal("1"),
                ceiling_count=Decimal("1"),
                estimate=candidate.quote.estimate(),
                ceiling=candidate.quote.upper_bound(),
            ).model_dump(mode="json")
            for candidate in self.plan.attempts
        ]
        spend_assumption = (
            "all synchronous fallback attempts may incur their provider ceiling"
            if self.fallback_spend
            else "asynchronous submission stops after the first ambiguous provider response"
        )
        return {
            "model": "route_plan",
            "components": components,
            "estimate": _decimal_text(self.estimate()),
            "ceiling": _decimal_text(self.upper_bound()),
            "confidence": "bounded",
            "provenance": "production_route_plan",
            "currency": "USD",
            "assumptions": [spend_assumption, f"route plan {self.plan.plan_id}"],
        }


def build_production_plan(
    *,
    capability: Capability,
    providers: Sequence[Provider],
    payload: Mapping[str, Any],
    operation: RoutingOperation,
    registry: ProviderRegistry,
    now: dt.datetime,
    mode: str,
    weights: RoutingWeights,
    max_attempts: int,
    provider_id: str | None = None,
    budget_limit_usd: Decimal | None = None,
    openai_lease_proxy: bool = False,
    openai_attempt_order: Sequence[str] | None = None,
    context: PlanningContext | None = None,
    own_pod_usd_per_hour: Decimal | None = None,
    gpu_class: str | None = None,
    lockouts: _lockout.LockoutSnapshot | None = None,
) -> ProductionRoutePlan:
    """Build a deterministic plan from immutable snapshots without I/O.

    Model lockouts arrive as the ``lockouts`` snapshot; the caller reads the process-global
    table, so two calls with the same arguments always produce the same plan.
    """

    observed_at = _utc(now)
    capability = capability.model_copy(deep=True)
    quota_snapshot = context.quota_snapshot if context is not None else QuotaSnapshot.empty()
    lockout_snapshot = lockouts if lockouts is not None else _lockout.LockoutSnapshot()
    if mode not in {"priority", "weighted"}:
        raise ValueError("routing mode must be priority or weighted")
    if isinstance(max_attempts, bool) or not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")
    if isinstance(openai_attempt_order, str):
        raise ValueError("openai_attempt_order must be a sequence of provider IDs")
    constrained_openai_order = None if openai_attempt_order is None else tuple(openai_attempt_order)
    if constrained_openai_order is not None:
        if not openai_lease_proxy or operation != RoutingOperation.SYNC_INFERENCE:
            raise ValueError("openai_attempt_order requires the OpenAI execution profile")
        if not constrained_openai_order or len(set(constrained_openai_order)) != len(
            constrained_openai_order
        ):
            raise ValueError("openai_attempt_order must contain unique provider IDs")
    budget_limit = (
        None
        if budget_limit_usd is None
        else _non_negative_decimal(budget_limit_usd, "budget_limit_usd")
    )
    payload_snapshot = _payload_mapping(payload)
    payload_sha256 = hashlib.sha256(_canonical_json(payload_snapshot).encode()).hexdigest()
    request = RoutingRequest(
        capability_name=capability.name,
        capability_id=capability.id,
        payload_bytes=len(_canonical_json(payload_snapshot).encode()),
        hints=_hints(capability, payload_snapshot),
        stream=payload_snapshot.get("stream") is True,
    )
    snapshots = tuple(
        sorted(
            (provider.model_copy(deep=True) for provider in providers),
            key=lambda item: (item.priority, item.name, item.id),
        )
    )
    required = _REQUIRED_ADAPTER_CAPABILITY[operation]
    eligible: list[tuple[Provider, CostQuote, Decimal | None, str]] = []
    eliminated: list[RouteElimination] = []
    cheapest_over_budget: Decimal | None = None

    for provider in snapshots:
        adapter_id = provider.adapter_id.value
        if provider_id is not None and provider.id != provider_id:
            continue
        try:
            adapter = registry.lookup_for_capability(adapter_id, required)
        except ProviderNotRegisteredError, UnsupportedProviderCapabilityError:
            eliminated.append(_elimination(provider, "capability_unsupported", "capability"))
            continue
        if (
            operation is RoutingOperation.COMPUTE
            and provider.adapter_id is ProviderAdapterId.RUNPOD
            and provider.provider_type is not ProviderType.POD_LEASE
        ):
            # Only a pod_lease RunPod provider can be leased; its serverless kinds cannot.
            eliminated.append(_elimination(provider, "provider_type_unsupported", "capability"))
            continue
        gate = stage12_elimination(request, provider, capability=capability, now=observed_at)
        if gate is not None:
            eliminated.append(gate)
            continue
        if not quota_eligible(provider, quota_snapshot, observed_at)[0]:
            eliminated.append(_elimination(provider, "quota_ineligible", "quota"))
            continue
        lockout_key = _lockout.model_lockout_key(provider)
        if lockout_key is not None and lockout_snapshot.is_locked(lockout_key, now=observed_at):
            eliminated.append(_elimination(provider, "model_locked_out", "lockout"))
            continue
        incompatibility = _payload_incompatibility(provider, payload_snapshot, operation)
        if incompatibility is not None:
            eliminated.append(_elimination(provider, incompatibility, "capability"))
            continue
        if provider.config.get("capacity_available") is False:
            eliminated.append(_elimination(provider, "capacity_unavailable", "capacity"))
            continue
        if (
            openai_lease_proxy
            and operation == RoutingOperation.SYNC_INFERENCE
            and openai_base_url_for_provider(provider) is None
        ):
            eliminated.append(_elimination(provider, "transport_unsupported", "capability"))
            continue
        if (
            not openai_lease_proxy
            and _is_pod_lease_sync(provider, operation)
            and provider.runpod_endpoint_id is None
        ):
            eliminated.append(_elimination(provider, "transport_unsupported", "capability"))
            continue
        try:
            quote = CostQuote(
                pricing=adapter.pricing_model(capability, provider),
                capability=capability,
                payload=dict(payload_snapshot),
            )
            ceiling = (
                Decimal("0")
                if _lease_cost_is_covered(provider, operation, openai_lease_proxy)
                else quote.upper_bound()
            )
        except TypeError, ValueError, InvalidOperation:
            eliminated.append(_elimination(provider, "cost_unavailable", "cost"))
            continue
        if budget_limit is not None and ceiling > budget_limit:
            eliminated.append(_elimination(provider, "budget_unavailable", "budget"))
            if cheapest_over_budget is None or ceiling < cheapest_over_budget:
                cheapest_over_budget = ceiling
            continue
        latency, signal = _latency(provider)
        eligible.append((provider, quote, latency, signal))

    if provider_id is not None and not any(provider.id == provider_id for provider in snapshots):
        raise ProviderNotFoundError(provider_id)
    if not eligible:
        # No plan gets built, so the escape hatch is computed here from the eliminations alone.
        raise NoExecutableRouteError(
            capability.name,
            _sorted_eliminations(eliminated),
            cheapest_over_budget_usd=cheapest_over_budget,
            escape_hatch=_build_escape_hatch(
                ranked=(),
                eliminated=_sorted_eliminations(eliminated),
                capability=capability,
                quota_snapshot=quota_snapshot,
                now=observed_at,
                own_pod_usd_per_hour=own_pod_usd_per_hour,
                gpu_class=gpu_class,
            ),
        )

    known_latencies = tuple(item[2] for item in eligible if item[2] is not None)
    neutral_latency = max(known_latencies) if known_latencies else Decimal("0")
    candidates = [
        _candidate(
            provider,
            quote,
            latency if latency is not None else neutral_latency,
            signal if latency is not None else f"neutral_{signal}",
            operation=operation,
            openai_lease_proxy=openai_lease_proxy,
            mode=mode,
            weights=weights,
        )
        for provider, quote, latency, signal in eligible
    ]
    candidates.sort(key=_candidate_key(mode))
    ranked = tuple(
        ProductionRouteCandidate(
            provider_id=item.provider_id,
            adapter_id=item.adapter_id,
            provider_name=item.provider_name,
            priority=item.priority,
            rank=index,
            quote=item.quote,
            latency_ms=item.latency_ms,
            latency_signal=item.latency_signal,
            cost_component=item.cost_component,
            latency_component=item.latency_component,
            objective=item.objective,
            recent_error_rate=item.recent_error_rate,
            provider=item.provider,
        )
        for index, item in enumerate(candidates, start=1)
    )
    attempt_order = ranked
    if openai_lease_proxy and operation == RoutingOperation.SYNC_INFERENCE:
        candidates_by_id = {candidate.provider_id: candidate for candidate in ranked}
        if constrained_openai_order is None:
            primary_id = ranked[0].provider_id
            providers_by_id = {
                provider.id: provider for provider, _quote, _latency, _signal in eligible
            }
            explicit_ids = tuple(
                provider_id
                for provider_id in explicit_openai_fallback_ids(providers_by_id[primary_id])
                if provider_id in candidates_by_id
            )
            resolved_ids = tuple(
                dict.fromkeys(
                    (primary_id, *explicit_ids, *(candidate.provider_id for candidate in ranked))
                )
            )
        else:
            resolved_ids = tuple(
                provider_id
                for provider_id in constrained_openai_order
                if provider_id in candidates_by_id
            )
        attempt_order = tuple(candidates_by_id[provider_id] for provider_id in resolved_ids)
        if not attempt_order:
            raise NoExecutableRouteError(capability.name, _sorted_eliminations(eliminated))
    attempts = _budgeted_attempts(
        attempt_order,
        max_attempts=max_attempts,
        budget_limit_usd=budget_limit,
        zero_incremental_provider_ids=frozenset(
            provider.id
            for provider in snapshots
            if _lease_cost_is_covered(provider, operation, openai_lease_proxy)
        ),
    )
    # Emergency free descent (research §9.6) holds by construction: zero-priced
    # keyless candidates carry a zero ceiling, so they survive the budget
    # elimination above and the reservation loop in _budgeted_attempts even
    # when every metered rung is budget-blocked.
    escape_hatch = _build_escape_hatch(
        ranked=ranked,
        eliminated=_sorted_eliminations(eliminated),
        capability=capability,
        quota_snapshot=quota_snapshot,
        now=observed_at,
        own_pod_usd_per_hour=own_pod_usd_per_hour,
        gpu_class=gpu_class,
    )
    body = {
        "observed_at": observed_at.isoformat(),
        "operation": operation.value,
        "capability_id": capability.id,
        "capability_name": capability.name,
        "payload_sha256": payload_sha256,
        "provider_constraint": provider_id,
        "mode": mode,
        "weights": {"cost": _decimal_text(weights.cost), "latency": _decimal_text(weights.latency)},
        "selected_provider_id": attempts[0].provider_id,
        "fallback_chain": [candidate.provider_id for candidate in attempts],
        "ranked_candidates": [candidate.to_dict() for candidate in ranked],
        "eliminated": [item.to_dict() for item in _sorted_eliminations(eliminated)],
        "quota_snapshot": quota_snapshot.to_dict(),
    }
    plan_id = f"plan_{hashlib.sha256(_canonical_json(body).encode()).hexdigest()[:32]}"
    return ProductionRoutePlan(
        plan_id=plan_id,
        observed_at=observed_at,
        operation=operation,
        capability_id=capability.id,
        capability_name=capability.name,
        payload_sha256=payload_sha256,
        provider_constraint=provider_id,
        mode=mode,
        weights=weights,
        selected_provider_id=attempts[0].provider_id,
        attempts=attempts,
        ranked_candidates=ranked,
        eliminated=_sorted_eliminations(eliminated),
        capability_snapshot=capability,
        provider_snapshots=snapshots,
        escape_hatch=escape_hatch,
    )


class ProductionRoutingService:
    """Shared planner/executor service for REST, MCP, CLI, TUI, and proxy paths."""

    def __init__(
        self,
        pool: Any,
        *,
        settings: PitwallSettings,
        capability_repository: CapabilityRepositoryLike | None = None,
        provider_repository: ProviderRepositoryLike | None = None,
        workload_repository: WorkloadRepository | None = None,
        registry: ProviderRegistry | None = None,
        guardrails: PreSpendInspectionService | None = None,
        budget_gate: BudgetGate | None = None,
        now: Callable[[], dt.datetime] | None = None,
        own_pod_resolver: Callable[[Capability], Awaitable[tuple[Decimal | None, str | None]]]
        | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._pool = pool
        self._settings = settings
        self._capabilities = capability_repository or CapabilityRepository(pool)
        self._providers = provider_repository or ProviderRepository(pool)
        self._workloads = workload_repository or WorkloadRepository(pool)
        self._registry = registry or get_default_registry()
        self._guardrails = guardrails or get_pre_spend_inspection_service()
        self._budget = budget_gate or BudgetGate(
            pool,
            monthly_budget_usd=settings.pitwall_monthly_budget_usd,
            per_request_max_usd=settings.pitwall_per_request_max_usd,
        )
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._own_pod_resolver = own_pod_resolver or self._default_own_pod_resolver
        self._sleep = sleep or asyncio.sleep

    async def preview(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        operation: RoutingOperation = RoutingOperation.SYNC_INFERENCE,
        provider_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> ProductionRoutePlan:
        """Return a non-persisting, no-egress plan with a non-counting safety preview."""

        prepared = self._prepare_payload(payload, record=False)
        return await self.preview_prepared(
            capability_id=capability_id,
            prepared_payload=prepared,
            operation=operation,
            provider_id=provider_id,
            now=now,
        )

    async def preview_prepared(
        self,
        *,
        capability_id: str,
        prepared_payload: PreparedRoutingPayload,
        operation: RoutingOperation = RoutingOperation.SYNC_INFERENCE,
        provider_id: str | None = None,
        now: dt.datetime | None = None,
        max_attempts: int | None = None,
        openai_lease_proxy: bool = False,
        openai_attempt_order: Sequence[str] | None = None,
    ) -> ProductionRoutePlan:
        """Preview an explicitly pre-inspected payload without recording another decision."""

        guarded = _prepared_payload(prepared_payload)
        return await self._plan_safe_payload(
            capability_id=capability_id,
            payload=guarded,
            operation=operation,
            provider_id=provider_id,
            now=now,
            capability=None,
            max_attempts=max_attempts,
            openai_lease_proxy=openai_lease_proxy,
            openai_attempt_order=openai_attempt_order,
        )

    async def plan_execution(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        operation: RoutingOperation,
        provider_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> tuple[ProductionRoutePlan, dict[str, Any]]:
        """Inspect once, then plan the exact redacted payload that may reach a provider."""

        prepared = self._prepare_payload(payload, record=True)
        return await self.plan_prepared_execution(
            capability_id=capability_id,
            prepared_payload=prepared,
            operation=operation,
            provider_id=provider_id,
            now=now,
        )

    async def plan_prepared_execution(
        self,
        *,
        capability_id: str,
        prepared_payload: PreparedRoutingPayload,
        operation: RoutingOperation,
        provider_id: str | None = None,
        now: dt.datetime | None = None,
    ) -> tuple[ProductionRoutePlan, dict[str, Any]]:
        """Plan an explicitly pre-inspected payload without a duplicate guardrail decision."""

        guarded = _prepared_payload(prepared_payload)
        plan = await self._plan_safe_payload(
            capability_id=capability_id,
            payload=guarded,
            operation=operation,
            provider_id=provider_id,
            now=now,
            capability=None,
        )
        return plan, guarded

    async def execute_sync(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        provider_id: str | None = None,
        idempotency_key: str | None = None,
        now: dt.datetime | None = None,
    ) -> RouteExecutionResult:
        """Inspect, plan, reserve worst-case fallback spend, then invoke adapters."""

        return await self.execute_sync_prepared(
            capability_id=capability_id,
            prepared_payload=self._prepare_payload(payload, record=True),
            provider_id=provider_id,
            idempotency_key=idempotency_key,
            now=now,
        )

    async def capability_class(self, capability_id: str) -> CapabilityClass:
        """The class of an enabled capability referenced by name or id."""

        return CapabilityClass((await self._capability(capability_id)).class_)

    async def execute_sync_prepared(
        self,
        *,
        capability_id: str,
        prepared_payload: PreparedRoutingPayload,
        provider_id: str | None = None,
        idempotency_key: str | None = None,
        now: dt.datetime | None = None,
    ) -> RouteExecutionResult:
        """Execute one explicitly pre-inspected payload without a duplicate decision."""

        safe_payload = _prepared_payload(prepared_payload)
        requested_capability = await self._capability(capability_id)
        replay = await self._idempotency_replay(
            idempotency_key,
            safe_payload,
            capability_id=requested_capability.id,
            workload_type=_WORKLOAD_TYPE_SYNC,
            provider_id=provider_id,
        )
        if replay is not None:
            return _execution_from_replay(replay)
        plan = await self._plan_safe_payload(
            capability_id=capability_id,
            payload=safe_payload,
            operation=RoutingOperation.SYNC_INFERENCE,
            provider_id=provider_id,
            now=now,
            capability=requested_capability,
        )
        started_at = _utc(self._now())
        admitted = await self._admit_sync_plan(
            plan,
            safe_payload,
            requested_capability=requested_capability,
            provider_id=provider_id,
            idempotency_key=idempotency_key,
            started_at=started_at,
        )
        if isinstance(admitted, RouteExecutionResult):
            return admitted
        workload_id = admitted.workload_id
        providers = _provider_snapshots_by_id(plan)
        failures: list[dict[str, str]] = []
        for index, candidate in enumerate(plan.attempts):
            if index > 0:
                await self._sleep(attempt_backoff_s(index))
            provider = providers[candidate.provider_id]
            attempt_started_at = _utc(self._now())
            result = await self._attempt_sync_candidate(
                plan,
                candidate,
                provider,
                safe_payload,
                workload_id=workload_id,
                started_at=started_at,
                attempt_started_at=attempt_started_at,
                failures=failures,
            )
            if result is not None:
                return await self._complete_sync_attempt(
                    plan,
                    candidate,
                    provider,
                    result,
                    safe_payload,
                    workload_id=workload_id,
                    started_at=started_at,
                    attempt_started_at=attempt_started_at,
                )

        completed_at = _utc(self._now())
        await self._mark_failed(
            workload_id,
            completed_at=completed_at,
            execution_ms=_elapsed_ms(started_at, completed_at),
            failures=failures,
        )
        raise RoutePlanningError("all planned providers failed")

    async def _admit_sync_plan(
        self,
        plan: ProductionRoutePlan,
        safe_payload: Mapping[str, Any],
        *,
        requested_capability: Capability,
        provider_id: str | None,
        idempotency_key: str | None,
        started_at: dt.datetime,
    ) -> BudgetAdmission | RouteExecutionResult:
        """Admit the plan, or return the replay of a concurrent request with the same key."""

        capability = plan.capability_snapshot

        async def persist_admitted_plan(conn: Any, workload_id: str) -> None:
            await self._persist_new_sync_plan(
                conn,
                workload_id,
                plan=plan,
                payload=safe_payload,
                started_at=started_at,
            )

        admission = await self._budget.try_launch_admission(
            capability_id=capability.id,
            provider_id=plan.selected_provider_id,
            estimate_usd=RouteBudgetQuote(plan, fallback_spend=True),
            workload_type=_WORKLOAD_TYPE_SYNC,
            submitted_at=plan.observed_at,
            idempotency_key=idempotency_key,
            after_new_admission=persist_admitted_plan,
        )
        if admission.is_new:
            return admission
        # Lost the race to a concurrent request with the same key: hold its workload to the
        # same checks as the pre-flight replay.
        replayed = _validated_replay(
            await self._required_workload(admission.workload_id),
            safe_payload,
            capability_id=requested_capability.id,
            workload_type=_WORKLOAD_TYPE_SYNC,
            provider_id=provider_id,
            webhook_sha256=None,
        )
        return _execution_from_replay(replayed)

    async def _attempt_sync_candidate(
        self,
        plan: ProductionRoutePlan,
        candidate: ProductionRouteCandidate,
        provider: Provider,
        safe_payload: Mapping[str, Any],
        *,
        workload_id: str,
        started_at: dt.datetime,
        attempt_started_at: dt.datetime,
        failures: list[dict[str, str]],
    ) -> InferenceResult | None:
        """Call one provider; on failure record it and return ``None`` for the next attempt."""

        try:
            adapter = self._registry.lookup_inference(candidate.adapter_id)
            return await adapter.infer(
                InferenceRequest(
                    context=ProviderOperationContext(pool=self._pool, now=plan.observed_at),
                    capability=plan.capability_snapshot,
                    provider_record=provider,
                    credentials=CredentialReference(provider.credential_ref),
                    payload=safe_payload,
                )
            )
        except asyncio.CancelledError:
            completed_at = _utc(self._now())
            await self._append_route_attempt(
                workload_id,
                candidate=candidate,
                operation="inference",
                outcome="cancelled",
                started_at=attempt_started_at,
                completed_at=completed_at,
            )
            await self._mark_failed(
                workload_id,
                completed_at=completed_at,
                execution_ms=_elapsed_ms(started_at, completed_at),
                failures=[
                    {
                        "provider_id": candidate.provider_id,
                        "code": "provider_call_cancelled",
                        "type": "CancelledError",
                    }
                ],
            )
            raise
        except Exception as exc:  # reason: provider detail is intentionally not serialized
            completed_at = _utc(self._now())
            failure = _safe_provider_failure(candidate.provider_id, exc)
            lockout_key = _lockout.model_lockout_key(provider)
            if isinstance(exc, ProviderQuotaExhausted) and lockout_key is not None:
                _lockout.get_lockout_table().record_failure(
                    lockout_key,
                    now=completed_at,
                    reason=exc.reason,
                    reset_at=exc.reset_at,
                )
                failure["code"] = exc.reason
            elif counts_toward_cooldown(exc):
                await self._record_attempt_cooldown(provider, now=completed_at)
            failures.append(failure)
            await self._append_route_attempt(
                workload_id,
                candidate=candidate,
                operation="inference",
                outcome="failed",
                started_at=attempt_started_at,
                completed_at=completed_at,
            )
            return None

    async def _complete_sync_attempt(
        self,
        plan: ProductionRoutePlan,
        candidate: ProductionRouteCandidate,
        provider: Provider,
        result: InferenceResult,
        safe_payload: Mapping[str, Any],
        *,
        workload_id: str,
        started_at: dt.datetime,
        attempt_started_at: dt.datetime,
    ) -> RouteExecutionResult:
        """Persist the winning attempt and return the completed execution."""

        completed_at = _utc(self._now())
        output = _json_value(result.output)
        usage_actual = _usage_derived_actual(result, candidate)
        success_key = _lockout.model_lockout_key(provider)
        if success_key is not None:
            _lockout.get_lockout_table().record_success(success_key, now=completed_at)
        await self._append_route_attempt(
            workload_id,
            candidate=candidate,
            operation="inference",
            outcome="completed",
            started_at=attempt_started_at,
            completed_at=completed_at,
            external_job_id=result.external_job_id,
        )
        await self._mark_sync_completed(
            workload_id,
            provider=provider,
            result=output,
            external_job_id=result.external_job_id,
            completed_at=completed_at,
            execution_ms=_elapsed_ms(started_at, completed_at),
            usage_actual_usd=usage_actual,
            usage_provenance=(
                f"broker:provider_usage:{candidate.adapter_id}"
                if usage_actual is not None
                else None
            ),
        )
        await self._record_quota_usage(provider, result, candidate)
        await self._record_sync_trace(
            workload_id=workload_id,
            capability=plan.capability_snapshot,
            provider=provider,
            candidate=candidate,
            payload=safe_payload,
            output=output,
            execution_ms=_elapsed_ms(started_at, completed_at),
        )
        workload = await self._required_workload(workload_id)
        return RouteExecutionResult(workload=workload, plan=plan, output=output)

    async def _record_attempt_cooldown(self, provider: Provider, *, now: dt.datetime) -> None:
        """Count a timeout or 5xx toward the provider's cooldown before the next attempt."""

        try:
            current = await self._providers.get(provider.id) or provider
            next_state = CooldownStateMachine().record_request_outcome(
                current, classification="failure", now=now
            )
            await self._providers.patch(provider.id, **to_provider_patch(next_state))
        except Exception:  # reason: health bookkeeping must not stop the failover it feeds
            log.warning("provider cooldown write failed for %s", provider.id, exc_info=True)

    async def submit_job(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        provider_id: str | None = None,
        idempotency_key: str | None = None,
        webhook_url: str | None = None,
        now: dt.datetime | None = None,
    ) -> Workload:
        """Admit before one async submit; never retry an ambiguous provider write."""

        return await self.submit_job_prepared(
            capability_id=capability_id,
            prepared_payload=self._prepare_payload(payload, record=True),
            provider_id=provider_id,
            idempotency_key=idempotency_key,
            webhook_url=webhook_url,
            now=now,
        )

    async def submit_job_prepared(
        self,
        *,
        capability_id: str,
        prepared_payload: PreparedRoutingPayload,
        provider_id: str | None = None,
        idempotency_key: str | None = None,
        webhook_url: str | None = None,
        now: dt.datetime | None = None,
    ) -> Workload:
        """Submit one explicitly pre-inspected payload without a duplicate decision."""

        safe_payload = _prepared_payload(prepared_payload)
        webhook_sha256 = _optional_value_sha256(webhook_url)
        requested_capability = await self._capability(capability_id)
        replay = await self._idempotency_replay(
            idempotency_key,
            safe_payload,
            capability_id=requested_capability.id,
            workload_type=_WORKLOAD_TYPE_ASYNC,
            provider_id=provider_id,
            webhook_sha256=webhook_sha256,
        )
        candidate: ProductionRouteCandidate | None = None
        if replay is None:
            plan = await self._plan_safe_payload(
                capability_id=capability_id,
                payload=safe_payload,
                operation=RoutingOperation.ASYNC_INFERENCE,
                provider_id=provider_id,
                now=now,
                capability=requested_capability,
            )
            capability = plan.capability_snapshot
            providers = _provider_snapshots_by_id(plan)
            plan_document = _persisted_plan_document(plan, webhook_sha256=webhook_sha256)

            async def persist_admitted_plan(conn: Any, workload_id: str) -> None:
                await self._persist_new_async_plan(
                    conn,
                    workload_id,
                    plan=plan,
                    plan_document=plan_document,
                    payload=safe_payload,
                )

            admission = await self._budget.try_launch_admission(
                capability_id=capability.id,
                provider_id=plan.selected_provider_id,
                estimate_usd=RouteBudgetQuote(plan, fallback_spend=False),
                workload_type=_WORKLOAD_TYPE_ASYNC,
                submitted_at=plan.observed_at,
                idempotency_key=idempotency_key,
                after_new_admission=persist_admitted_plan,
            )
            if admission.is_new:
                candidate = plan.selected
                provider = providers[candidate.provider_id]
                workload_id = admission.workload_id
                observed_at = plan.observed_at
            else:
                # Lost the race to a concurrent request with the same key (see execute_sync).
                replay = _validated_replay(
                    await self._required_workload(admission.workload_id),
                    safe_payload,
                    capability_id=requested_capability.id,
                    workload_type=_WORKLOAD_TYPE_ASYNC,
                    provider_id=provider_id,
                    webhook_sha256=webhook_sha256,
                )
        if replay is not None:
            provider = await self._persisted_async_provider(replay)
            capability = requested_capability
            workload_id = replay.id
            observed_at = replay.submitted_at

        async with self._workload_lock(workload_id) as conn:
            current = await self._required_workload(workload_id, conn=conn)
            if _enum_text(current.state) in {"cancelled", "completed", "failed", "timed_out"}:
                return current
            if current.external_job_id is not None:
                return current
            if _enum_text(current.state) == "running":
                raise RoutePlanningError("asynchronous submission outcome is unknown")
            provider_id_for_attempt = provider.id
            adapter_id_for_attempt = provider.adapter_id.value
            normalized_webhook: str | None = None
            if webhook_url is not None:
                try:
                    normalized_webhook = (await resolve_webhook_target(webhook_url)).url
                except asyncio.CancelledError:
                    completed_at = _utc(self._now())
                    await self._mark_failed(
                        workload_id,
                        completed_at=completed_at,
                        execution_ms=0,
                        failures=[
                            {
                                "provider_id": provider_id_for_attempt,
                                "code": "webhook_resolution_cancelled",
                                "type": "CancelledError",
                            }
                        ],
                        conn=conn,
                    )
                    await self._truth_up_zero_cost(
                        workload_id,
                        completed_at=completed_at,
                        conn=conn,
                    )
                    raise
                except Exception as exc:  # reason: reject any unsafe webhook resolution failure
                    completed_at = _utc(self._now())
                    await self._mark_failed(
                        workload_id,
                        completed_at=completed_at,
                        execution_ms=0,
                        failures=[
                            {
                                "provider_id": provider_id_for_attempt,
                                "code": "webhook_target_rejected",
                                "type": exc.__class__.__name__,
                            }
                        ],
                        conn=conn,
                    )
                    await self._truth_up_zero_cost(
                        workload_id,
                        completed_at=completed_at,
                        conn=conn,
                    )
                    raise RoutePlanningError("webhook target is unavailable") from None
            await self._mark_async_dispatch_started(workload_id, conn=conn)
            attempt_started_at = _utc(self._now())
            try:
                adapter = self._registry.lookup_async_inference(adapter_id_for_attempt)
                submission = await adapter.submit(
                    AsyncInferenceRequest(
                        context=ProviderOperationContext(pool=self._pool, now=observed_at),
                        capability=capability,
                        provider_record=provider,
                        credentials=CredentialReference(provider.credential_ref),
                        payload=safe_payload,
                        webhook_url=normalized_webhook,
                    )
                )
            except asyncio.CancelledError:
                completed_at = _utc(self._now())
                await self._append_route_attempt(
                    workload_id,
                    candidate=candidate,
                    provider_id=provider_id_for_attempt,
                    adapter_id=adapter_id_for_attempt,
                    operation="submit",
                    outcome="cancelled",
                    started_at=attempt_started_at,
                    completed_at=completed_at,
                    conn=conn,
                )
                await self._mark_failed(
                    workload_id,
                    completed_at=completed_at,
                    execution_ms=0,
                    failures=[
                        {
                            "provider_id": provider_id_for_attempt,
                            "code": "provider_call_cancelled",
                            "type": "CancelledError",
                        }
                    ],
                    conn=conn,
                )
                raise
            except Exception as exc:  # reason: persist safe failure truth for any adapter error
                completed_at = _utc(self._now())
                await self._append_route_attempt(
                    workload_id,
                    candidate=candidate,
                    provider_id=provider_id_for_attempt,
                    adapter_id=adapter_id_for_attempt,
                    operation="submit",
                    outcome="failed",
                    started_at=attempt_started_at,
                    completed_at=completed_at,
                    conn=conn,
                )
                await self._mark_failed(
                    workload_id,
                    completed_at=completed_at,
                    execution_ms=0,
                    failures=[_safe_provider_failure(provider_id_for_attempt, exc)],
                    conn=conn,
                )
                raise RouteProviderInvocationError() from exc
            completed_at = _utc(self._now())
            await self._append_route_attempt(
                workload_id,
                candidate=candidate,
                provider_id=provider_id_for_attempt,
                adapter_id=adapter_id_for_attempt,
                operation="submit",
                outcome="accepted",
                started_at=attempt_started_at,
                completed_at=completed_at,
                external_job_id=submission.external_job_id,
                conn=conn,
            )
            await self._mark_job_submitted(
                workload_id,
                provider=provider,
                external_job_id=submission.external_job_id,
                state=submission.state,
                conn=conn,
            )
        return await self._required_workload(workload_id)

    async def job_events(self, workload_id: str, *, limit: int | None = None) -> JobEventPage:
        """Return bounded persisted lifecycle events and explicit stream unavailability."""

        bounded = self._settings.pitwall_job_event_limit if limit is None else limit
        if isinstance(bounded, bool) or not 1 <= bounded <= 100:
            raise ValueError("event limit must be between 1 and 100")
        workload = await self._required_workload(workload_id)
        events = _workload_events(workload)[:bounded]
        return JobEventPage(
            workload_id=workload.id,
            plan_id=workload.route_plan_id,
            events=events,
        )

    async def get_job(self, workload_id: str, *, refresh: bool = True) -> Workload:
        """Return the provider-neutral lifecycle record, refreshing active jobs once."""

        if not refresh:
            return await self._required_workload(workload_id)
        async with self._workload_lock(workload_id) as conn:
            workload = await self._required_workload(workload_id, conn=conn)
            if (
                _enum_text(workload.state) in {"cancelled", "completed", "failed", "timed_out"}
                or workload.external_job_id is None
            ):
                return workload
            provider = await self._providers.get(workload.provider_id)
            if provider is None:
                raise ProviderNotFoundError(workload.provider_id)
            try:
                adapter = self._registry.lookup_async_status(provider.adapter_id.value)
            except UnsupportedProviderCapabilityError:
                return workload
            try:
                async with asyncio.timeout(_JOB_STATUS_TIMEOUT_S):
                    status = await adapter.job_status(
                        AsyncInferenceStatusRequest(
                            context=ProviderOperationContext(pool=self._pool),
                            provider_record=provider,
                            credentials=CredentialReference(provider.credential_ref),
                            external_job_id=workload.external_job_id,
                        )
                    )
            except TimeoutError as exc:
                raise RoutePlanningError("provider status refresh timed out") from exc
            except Exception as exc:  # reason: redact heterogeneous provider status failures
                raise RouteProviderInvocationError() from exc
            await self._apply_job_status(workload.id, status, conn=conn)
            return await self._required_workload(workload.id, conn=conn)

    async def job_result(
        self,
        workload_id: str,
        *,
        max_bytes: int = _MAX_JOB_RESULT_BYTES,
        refresh: bool = True,
    ) -> JobResultPage:
        """Return a bounded result or one stable provider-neutral unavailable reason."""

        if isinstance(max_bytes, bool) or not 1 <= max_bytes <= _MAX_JOB_RESULT_BYTES:
            raise ValueError(f"result max_bytes must be between 1 and {_MAX_JOB_RESULT_BYTES}")
        workload = await self.get_job(workload_id, refresh=refresh)
        state = _enum_text(workload.state)
        if state not in {"cancelled", "completed", "failed", "timed_out"}:
            return JobResultPage(
                workload_id=workload.id,
                plan_id=workload.route_plan_id,
                state=state,
                provider_id=workload.provider_id,
                external_job_id=workload.external_job_id,
                plan=workload.route_plan,
                cost_estimate_usd=workload.cost_estimate_usd,
                cost_actual_usd=workload.cost_actual_usd,
                trace_id=workload.langfuse_trace_id,
                unavailable_reason="job_not_terminal",
            )
        if workload.result is None:
            return JobResultPage(
                workload_id=workload.id,
                plan_id=workload.route_plan_id,
                state=state,
                provider_id=workload.provider_id,
                external_job_id=workload.external_job_id,
                plan=workload.route_plan,
                cost_estimate_usd=workload.cost_estimate_usd,
                cost_actual_usd=workload.cost_actual_usd,
                trace_id=workload.langfuse_trace_id,
                unavailable_reason="provider_result_unavailable",
            )
        result_bytes = len(_canonical_json(workload.result).encode())
        if result_bytes > max_bytes:
            return JobResultPage(
                workload_id=workload.id,
                plan_id=workload.route_plan_id,
                state=state,
                provider_id=workload.provider_id,
                external_job_id=workload.external_job_id,
                plan=workload.route_plan,
                cost_estimate_usd=workload.cost_estimate_usd,
                cost_actual_usd=workload.cost_actual_usd,
                trace_id=workload.langfuse_trace_id,
                unavailable_reason="result_limit_exceeded",
            )
        return JobResultPage(
            workload_id=workload.id,
            plan_id=workload.route_plan_id,
            state=state,
            provider_id=workload.provider_id,
            external_job_id=workload.external_job_id,
            plan=workload.route_plan,
            cost_estimate_usd=workload.cost_estimate_usd,
            cost_actual_usd=workload.cost_actual_usd,
            trace_id=workload.langfuse_trace_id,
            result=workload.result,
            available=True,
        )

    async def cancel_job(self, workload_id: str) -> Workload:
        """Cancel through the selected adapter, then transition local state."""

        async with self._workload_lock(workload_id) as conn:
            workload = await self._required_workload(workload_id, conn=conn)
            if workload.type != _WORKLOAD_TYPE_ASYNC:
                raise JobNotCancellableError("workload is not an asynchronous job")
            state = _enum_text(workload.state)
            if state in {"cancelled", "completed", "failed", "timed_out"}:
                return workload
            if workload.external_job_id is None:
                completed_at = _utc(self._now())
                cancelled = await self._set_cancelled(
                    workload.id,
                    completed_at=completed_at,
                    conn=conn,
                )
                await self._truth_up_zero_cost(
                    workload.id,
                    completed_at=completed_at,
                    conn=conn,
                )
                return cancelled
            provider = await self._providers.get(workload.provider_id)
            if provider is None:
                raise ProviderNotFoundError(workload.provider_id)
            try:
                adapter = self._registry.lookup_async_cancel(provider.adapter_id.value)
            except UnsupportedProviderCapabilityError:
                raise JobNotCancellableError(
                    "selected provider does not support cancellation"
                ) from None
            started_at = _utc(self._now())
            try:
                async with asyncio.timeout(_JOB_STATUS_TIMEOUT_S):
                    outcome = await adapter.cancel_job(
                        AsyncInferenceCancelRequest(
                            context=ProviderOperationContext(pool=self._pool),
                            provider_record=provider,
                            credentials=CredentialReference(provider.credential_ref),
                            external_job_id=workload.external_job_id,
                        )
                    )
            except Exception as exc:  # reason: redact heterogeneous provider cancellation failures
                raise RouteProviderInvocationError() from exc
            completed_at = _utc(self._now())
            await self._append_route_attempt(
                workload.id,
                provider_id=provider.id,
                adapter_id=provider.adapter_id.value,
                operation="cancel",
                outcome="confirmed" if outcome.cancelled else "rejected",
                started_at=started_at,
                completed_at=completed_at,
                external_job_id=workload.external_job_id,
                conn=conn,
            )
            if not outcome.cancelled:
                raise RoutePlanningError("provider did not confirm cancellation")
            return await self._set_cancelled(
                workload.id,
                completed_at=completed_at,
                conn=conn,
            )

    async def _plan_safe_payload(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        operation: RoutingOperation,
        provider_id: str | None,
        now: dt.datetime | None,
        capability: Capability | None = None,
        max_attempts: int | None = None,
        openai_lease_proxy: bool = False,
        openai_attempt_order: Sequence[str] | None = None,
        context: PlanningContext | None = None,
    ) -> ProductionRoutePlan:
        if max_attempts is not None and (
            isinstance(max_attempts, bool) or not 1 <= max_attempts <= 10
        ):
            raise ValueError("max_attempts must be between 1 and 10")
        capability_snapshot = capability or await self._capability(capability_id)
        providers = await self._provider_snapshots(capability_snapshot.id, provider_id)
        weights = routing_weights_for(self._settings, capability_snapshot)
        budget_limit = await self._available_budget_limit()
        if context is None:
            quota_snapshot = await self._load_quota_snapshot()
            context = PlanningContext.replay(
                now=_utc(now or self._now()),
                quota_snapshot=quota_snapshot,
            )
        own_pod_usd_per_hour, gpu_class = await self._resolve_own_pod(capability_snapshot)
        try:
            return build_production_plan(
                capability=capability_snapshot,
                providers=providers,
                payload=payload,
                operation=operation,
                registry=self._registry,
                now=_utc(now or self._now()),
                mode=self._settings.pitwall_routing_mode,
                weights=weights,
                max_attempts=(
                    1
                    if operation == RoutingOperation.ASYNC_INFERENCE
                    else min(
                        self._settings.pitwall_routing_max_attempts,
                        max_attempts or self._settings.pitwall_routing_max_attempts,
                    )
                ),
                provider_id=provider_id,
                budget_limit_usd=budget_limit,
                openai_lease_proxy=openai_lease_proxy,
                openai_attempt_order=openai_attempt_order,
                context=context,
                own_pod_usd_per_hour=own_pod_usd_per_hour,
                gpu_class=gpu_class,
                lockouts=_lockout.get_lockout_table().frozen(),
            )
        except NoExecutableRouteError as exc:
            # A provider that reached the budget stage passed every other gate, so the budget
            # is what blocks this request: report it as the budget rejection it is.
            if exc.cheapest_over_budget_usd is not None:
                raise await self._budget_rejection(exc.cheapest_over_budget_usd) from exc
            raise

    async def _resolve_own_pod(
        self,
        capability: Capability,
    ) -> tuple[Decimal | None, str | None]:
        """Return the cheapest secure-cloud $/hr + GPU class for ``capability``.

        Resolves ``pitwall.models.fit_options`` against the capability's served
        model.  Returns ``(None, None)`` when the capability is missing the
        served-model binding or no fit options are available, so ``build_production_plan``
        never surfaces a prong-3 escape hatch without operator-confirmed evidence.
        """
        return await self._own_pod_resolver(capability)

    async def _default_own_pod_resolver(
        self,
        capability: Capability,
    ) -> tuple[Decimal | None, str | None]:
        """Default resolver that returns ``(None, None)`` so the escape hatch stays silent.

        Callers (autopilot, REST preview) inject a real resolver built on
        ``pitwall.models.fit_options`` once the capability's served model and
        Runpod GPU catalogue are loaded; production default keeps the escape
        hatch dormant unless explicitly armed.
        """
        del capability
        return None, None

    async def _available_budget_limit(self) -> Decimal:
        limits = await self._budget.effective_limits()
        spent = await self._budget.current_mtd_spend()
        monthly_remaining = limits.monthly_budget_usd - spent
        if monthly_remaining <= 0:
            return Decimal("0")
        return min(limits.per_request_max_usd, monthly_remaining)

    async def _load_quota_snapshot(self) -> QuotaSnapshot:
        """Read provider_quotas into an immutable snapshot; degrade to empty on missing pool."""

        try:
            records = await QuotaRepository(self._pool).list_all()
        except Exception:  # reason: missing quota state must not break provider delivery
            log.warning("quota snapshot fetch failed; treating as empty", exc_info=True)
            return QuotaSnapshot.empty()
        return QuotaSnapshot(records=records)

    async def _record_quota_usage(
        self,
        provider: Provider,
        result: Any,
        candidate: ProductionRouteCandidate,
    ) -> None:
        """Record token usage on zero-cost providers; never fails delivered output."""

        if candidate.quote.pricing.kind != "zero":
            return
        total_tokens = getattr(result, "total_tokens", None)
        if not isinstance(total_tokens, int) or total_tokens <= 0:
            return
        catalog = (
            provider.config.get("gateway", {}).get("catalog", {})
            if isinstance(provider.config, Mapping)
            else {}
        )
        pool_key = catalog.get("pool_key") if isinstance(catalog, Mapping) else None
        if not isinstance(pool_key, str) or not pool_key:
            return
        try:
            await QuotaRepository(self._pool).add_usage(
                provider.id, pool_key, Decimal(total_tokens)
            )
        except Exception:  # reason: provider data never fails delivered output
            log.warning("quota usage persistence failed", exc_info=True)

    async def _budget_rejection(self, estimate_usd: Decimal) -> BudgetRejected:
        limits = await self._budget.effective_limits()
        spent = await self._budget.current_mtd_spend()
        reason: BudgetRejectionReason = (
            "per_request_cap" if estimate_usd > limits.per_request_max_usd else "monthly_budget"
        )
        return BudgetRejected(
            reason,
            BudgetSnapshot(
                monthly_budget_usd=limits.monthly_budget_usd,
                per_request_max_usd=limits.per_request_max_usd,
                mtd_spend_usd=spent,
                estimate_usd=estimate_usd,
                budget_remaining_usd=max(limits.monthly_budget_usd - spent, Decimal("0")),
            ),
        )

    async def _capability(self, capability_id: str) -> Capability:
        capability = await self._capabilities.get_by_name(capability_id)
        if capability is None:
            capability = await self._capabilities.get(capability_id)
        if capability is None:
            raise CapabilityNotFoundError(capability_id)
        if not capability.enabled:
            raise CapabilityDisabledError(capability.name)
        return capability

    async def _provider_snapshots(
        self, capability_id: str, provider_id: str | None
    ) -> tuple[Provider, ...]:
        if provider_id is not None:
            provider = await self._providers.get(provider_id)
            if provider is None:
                raise ProviderNotFoundError(provider_id)
            return (provider,)
        return tuple(
            await self._providers.list(
                capability_id=capability_id,
                enabled_only=False,
                limit=100,
                offset=0,
            )
        )

    def _prepare_payload(
        self,
        payload: Mapping[str, Any],
        *,
        record: bool,
    ) -> PreparedRoutingPayload:
        snapshot = _payload_mapping(payload)
        result = (
            self._guardrails.inspect(snapshot) if record else self._guardrails.preview(snapshot)
        )
        return PreparedRoutingPayload.from_inspection(result)

    async def _idempotency_replay(
        self,
        key: str | None,
        payload: Mapping[str, Any],
        *,
        capability_id: str,
        workload_type: str,
        provider_id: str | None,
        webhook_sha256: str | None = None,
    ) -> Workload | None:
        if key is None:
            return None
        existing = await self._workloads.get_by_idempotency_key(key)
        if existing is None:
            return None
        return _validated_replay(
            existing,
            payload,
            capability_id=capability_id,
            workload_type=workload_type,
            provider_id=provider_id,
            webhook_sha256=webhook_sha256,
        )

    async def _required_workload(self, workload_id: str, *, conn: Any | None = None) -> Workload:
        workload = (
            await self._workloads.get(workload_id)
            if conn is None
            else await self._workloads.get_on_connection(conn, workload_id)
        )
        if workload is None:
            raise LookupError(f"workload {workload_id!r} was not found")
        return workload

    async def _persisted_async_provider(self, workload: Workload) -> Provider:
        plan = workload.route_plan
        if plan is None or plan.get("operation") != RoutingOperation.ASYNC_INFERENCE.value:
            raise RoutePlanningError("persisted workload is not an asynchronous route plan")
        if plan.get("selected_provider_id") != workload.provider_id:
            raise RoutePlanningError("persisted route provider identity does not match workload")
        ranked = plan.get("ranked_candidates")
        if not isinstance(ranked, list):
            raise RoutePlanningError("persisted route candidates are unavailable")
        selected = next(
            (
                item
                for item in ranked
                if isinstance(item, Mapping) and item.get("provider_id") == workload.provider_id
            ),
            None,
        )
        adapter_id = selected.get("adapter_id") if selected is not None else None
        if not isinstance(adapter_id, str):
            raise RoutePlanningError("persisted route adapter identity is unavailable")
        provider = await self._providers.get(workload.provider_id)
        if provider is None:
            raise ProviderNotFoundError(workload.provider_id)
        if provider.adapter_id.value != adapter_id:
            raise RoutePlanningError("persisted route adapter identity no longer matches provider")
        if not provider.enabled or provider.health_status.lower() not in {"healthy", "warming"}:
            raise RoutePlanningError("persisted route provider is no longer executable")
        return provider

    async def _persist_new_sync_plan(
        self,
        conn: Any,
        workload_id: str,
        *,
        plan: ProductionRoutePlan,
        payload: Mapping[str, Any],
        started_at: dt.datetime,
    ) -> None:
        updated = await conn.execute(
            """UPDATE pitwall.workloads
               SET state = 'running', started_at = $2, input = $3::jsonb,
                   input_bytes = $4, fallback_chain = $5::text[],
                   route_plan_id = $6, route_plan = $7::jsonb
               WHERE id = $1 AND route_plan_id IS NULL AND route_plan IS NULL""",
            workload_id,
            started_at,
            dict(payload),
            len(_canonical_json(payload).encode()),
            list(plan.fallback_chain),
            plan.plan_id,
            plan.to_dict(),
        )
        if updated != "UPDATE 1":
            raise RoutePlanningError("workload route plan is already persisted")

    async def _persist_new_async_plan(
        self,
        conn: Any,
        workload_id: str,
        *,
        plan: ProductionRoutePlan,
        plan_document: Mapping[str, object],
        payload: Mapping[str, Any],
    ) -> None:
        updated = await conn.execute(
            """UPDATE pitwall.workloads
               SET input = $2::jsonb, input_bytes = $3,
                   fallback_chain = $4::text[], route_plan_id = $5,
                   route_plan = $6::jsonb
               WHERE id = $1 AND route_plan_id IS NULL AND route_plan IS NULL""",
            workload_id,
            dict(payload),
            len(_canonical_json(payload).encode()),
            list(plan.fallback_chain),
            plan.plan_id,
            dict(plan_document),
        )
        if updated != "UPDATE 1":
            raise RoutePlanningError("workload route plan is already persisted")

    @asynccontextmanager
    async def _workload_lock(self, workload_id: str) -> AsyncIterator[Any]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "SELECT pg_advisory_lock(hashtextextended($1, $2))",
                workload_id,
                _WORKLOAD_LOCK_NAMESPACE,
            )
            try:
                yield conn
            finally:
                await conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended($1, $2))",
                    workload_id,
                    _WORKLOAD_LOCK_NAMESPACE,
                )

    async def _append_route_attempt(
        self,
        workload_id: str,
        *,
        operation: str,
        outcome: str,
        started_at: dt.datetime,
        completed_at: dt.datetime,
        candidate: ProductionRouteCandidate | None = None,
        provider_id: str | None = None,
        adapter_id: str | None = None,
        external_job_id: str | None = None,
        conn: Any | None = None,
    ) -> None:
        resolved_provider_id = candidate.provider_id if candidate is not None else provider_id
        resolved_adapter_id = candidate.adapter_id if candidate is not None else adapter_id
        if resolved_provider_id is None or resolved_adapter_id is None:
            raise ValueError("route attempt requires provider and adapter identity")
        entry = {
            "sequence": candidate.rank if candidate is not None else None,
            "operation": operation,
            "provider_id": resolved_provider_id,
            "adapter_id": resolved_adapter_id,
            "outcome": outcome,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "external_job_id": external_job_id,
        }
        await self._execute(
            """UPDATE pitwall.workloads
               SET route_attempts = route_attempts || $2::jsonb
               WHERE id = $1""",
            workload_id,
            [entry],
            conn=conn,
        )

    async def _mark_async_dispatch_started(self, workload_id: str, *, conn: Any) -> None:
        updated = await conn.execute(
            """UPDATE pitwall.workloads
               SET state = 'running', started_at = COALESCE(started_at, $2)
               WHERE id = $1 AND state = 'queued' AND external_job_id IS NULL""",
            workload_id,
            _utc(self._now()),
        )
        if updated != "UPDATE 1":
            raise RoutePlanningError("asynchronous submission could not claim dispatch")

    async def _apply_job_status(
        self,
        workload_id: str,
        status: Any,
        *,
        conn: Any | None = None,
    ) -> None:
        state = _enum_text(status.state)
        observed_at = _utc(self._now())
        terminal = state in {"cancelled", "completed", "failed", "timed_out"}
        result: dict[str, Any] | None = None
        output_bytes: int | None = None
        error: dict[str, str] | None = None
        if status.output is not None:
            normalized_output = _json_value(status.output)
            encoded = _canonical_json(normalized_output).encode()
            if len(encoded) <= _MAX_JOB_RESULT_BYTES:
                result = _json_object(normalized_output)
                output_bytes = len(encoded)
            else:
                error = {"code": "provider_result_too_large"}
        if status.error is not None:
            error = {"code": "provider_job_failed"}
        await self._execute(
            """UPDATE pitwall.workloads
                   SET state = $2,
                       started_at = CASE
                           WHEN $2 = 'running' THEN COALESCE(started_at, $3)
                           ELSE started_at
                       END,
                       completed_at = CASE WHEN $4 THEN COALESCE(completed_at, $3)
                                           ELSE completed_at END,
                       result = COALESCE($5::jsonb, result),
                       output_bytes = COALESCE($6, output_bytes),
                       error = COALESCE($7::jsonb, error)
                   WHERE id = $1
                     AND state IN ('queued', 'running')""",
            workload_id,
            state,
            observed_at,
            terminal,
            result,
            output_bytes,
            error,
            conn=conn,
        )

    async def _truth_up_zero_cost(
        self,
        workload_id: str,
        *,
        completed_at: dt.datetime,
        conn: Any | None = None,
    ) -> None:
        await self._execute(
            """UPDATE pitwall.workloads
                   SET cost_actual_usd = 0,
                       cost_actual_provenance = 'broker:no_provider_invocation',
                       cost_reconciled_at = $2
                   WHERE id = $1 AND NOT EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(route_attempts) AS attempt
                       WHERE attempt ->> 'operation' IN ('inference', 'submit')
                   ) AND started_at IS NULL""",
            workload_id,
            completed_at,
            conn=conn,
        )

    async def _mark_sync_completed(
        self,
        workload_id: str,
        *,
        provider: Provider,
        result: Any,
        external_job_id: str | None,
        completed_at: dt.datetime,
        execution_ms: int,
        usage_actual_usd: Decimal | None,
        usage_provenance: str | None,
    ) -> None:
        runpod_job_id = external_job_id if provider.adapter_id.value == "runpod" else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """UPDATE pitwall.workloads
                   SET state = 'completed', provider_id = $2, completed_at = $3,
                       execution_ms = $4, result = $5::jsonb, output_bytes = $6,
                       external_job_id = $7, runpod_job_id = $8,
                       cost_actual_usd = COALESCE($9, cost_actual_usd),
                       cost_actual_provenance = COALESCE($10, cost_actual_provenance),
                       cost_reconciled_at = CASE WHEN $9 IS NULL
                           THEN cost_reconciled_at ELSE $3 END
                   WHERE id = $1 AND state = 'running'""",
                workload_id,
                provider.id,
                completed_at,
                execution_ms,
                _json_object(result),
                len(_canonical_json(result).encode()),
                external_job_id,
                runpod_job_id,
                usage_actual_usd,
                usage_provenance,
            )

    async def _record_sync_trace(
        self,
        *,
        workload_id: str,
        capability: Capability,
        provider: Provider,
        candidate: ProductionRouteCandidate,
        payload: Mapping[str, Any],
        output: Any,
        execution_ms: int,
    ) -> None:
        """Emit optional trace metadata without making observability part of delivery."""

        try:
            trace_id = emit_inference_trace(
                workload_id=workload_id,
                capability_name=capability.name,
                provider_id=provider.id,
                provider_type=provider.provider_type.value,
                runpod_endpoint_id=provider.runpod_endpoint_id,
                cost_estimate_usd=float(candidate.quote.upper_bound()),
                input_bytes=len(_canonical_json(payload).encode()),
                output_bytes=len(_canonical_json(output).encode()),
                execution_ms=execution_ms,
                status="success",
            )
            if trace_id:
                await self._execute(
                    "UPDATE pitwall.workloads SET langfuse_trace_id = $2 WHERE id = $1",
                    workload_id,
                    trace_id,
                )
        except Exception:  # reason: tracing is optional and cannot fail provider delivery
            log.warning("inference trace persistence failed", exc_info=True)

    async def _mark_job_submitted(
        self,
        workload_id: str,
        *,
        provider: Provider,
        external_job_id: str,
        state: WorkloadState,
        conn: Any | None = None,
    ) -> None:
        runpod_job_id = external_job_id if provider.adapter_id.value == "runpod" else None
        await self._execute(
            """UPDATE pitwall.workloads
                   SET provider_id = $2, state = $3, external_job_id = $4,
                       runpod_job_id = $5
                   WHERE id = $1
                     AND state IN ('queued', 'running')
                     AND external_job_id IS NULL""",
            workload_id,
            provider.id,
            state.value,
            external_job_id,
            runpod_job_id,
            conn=conn,
        )

    async def _mark_failed(
        self,
        workload_id: str,
        *,
        completed_at: dt.datetime,
        execution_ms: int,
        failures: Sequence[Mapping[str, str]],
        conn: Any | None = None,
    ) -> None:
        await self._execute(
            """UPDATE pitwall.workloads
                   SET state = 'failed', completed_at = $2, execution_ms = $3,
                       error = $4::jsonb
                   WHERE id = $1 AND state IN ('queued', 'running')""",
            workload_id,
            completed_at,
            execution_ms,
            {"code": "provider_attempts_failed", "attempts": list(failures)},
            conn=conn,
        )

    async def _set_cancelled(
        self,
        workload_id: str,
        *,
        completed_at: dt.datetime,
        conn: Any | None = None,
    ) -> Workload:
        await self._execute(
            """UPDATE pitwall.workloads
                   SET state = 'cancelled', completed_at = $2
                   WHERE id = $1 AND state IN ('queued', 'running')""",
            workload_id,
            completed_at,
            conn=conn,
        )
        return await self._required_workload(workload_id, conn=conn)

    async def _execute(self, query: str, *args: Any, conn: Any | None = None) -> str:
        if conn is not None:
            return str(await conn.execute(query, *args))
        async with self._pool.acquire() as acquired:
            return str(await acquired.execute(query, *args))


def _candidate(
    provider: Provider,
    quote: CostQuote,
    latency_ms: Decimal,
    latency_signal: str,
    *,
    operation: RoutingOperation,
    openai_lease_proxy: bool,
    mode: str,
    weights: RoutingWeights,
) -> ProductionRouteCandidate:
    incremental_ceiling = (
        Decimal("0")
        if _lease_cost_is_covered(provider, operation, openai_lease_proxy)
        else quote.upper_bound()
    )
    cost_component = incremental_ceiling * weights.cost
    latency_component = latency_ms * weights.latency
    objective = (
        Decimal(provider.priority) if mode == "priority" else cost_component + latency_component
    )
    return ProductionRouteCandidate(
        provider_id=provider.id,
        adapter_id=provider.adapter_id.value,
        provider_name=provider.name,
        priority=provider.priority,
        rank=0,
        quote=quote,
        latency_ms=latency_ms,
        latency_signal=latency_signal,
        cost_component=cost_component,
        latency_component=latency_component,
        objective=objective,
        recent_error_rate=Decimal(str(provider.recent_error_rate)),
        provider=provider,
    )


def _lease_cost_is_covered(
    provider: Provider,
    operation: RoutingOperation,
    openai_lease_proxy: bool,
) -> bool:
    """Return whether sync inference uses a demonstrably active paid lease."""

    if (
        not openai_lease_proxy
        or operation != RoutingOperation.SYNC_INFERENCE
        or provider.provider_type.value != "pod_lease"
    ):
        return False
    lease_id = provider.config.get("active_lease_id")
    return (
        isinstance(lease_id, str)
        and bool(lease_id.strip())
        and pod_lease_base_url(provider) is not None
    )


def _is_pod_lease_sync(
    provider: Provider,
    operation: RoutingOperation,
) -> bool:
    return (
        operation == RoutingOperation.SYNC_INFERENCE and provider.provider_type.value == "pod_lease"
    )


def _candidate_key(mode: str) -> Any:
    if mode == "priority":
        return lambda item: (item.priority, item.provider_name, item.provider_id)
    return lambda item: (item.objective, item.priority, item.provider_name, item.provider_id)


def _budgeted_attempts(
    ranked: Sequence[ProductionRouteCandidate],
    *,
    max_attempts: int,
    budget_limit_usd: Decimal | None,
    zero_incremental_provider_ids: frozenset[str] = frozenset(),
) -> tuple[ProductionRouteCandidate, ...]:
    if budget_limit_usd is None:
        return tuple(ranked[:max_attempts])
    attempts: list[ProductionRouteCandidate] = []
    reserved = Decimal("0")
    for candidate in ranked:
        candidate_ceiling = (
            Decimal("0")
            if candidate.provider_id in zero_incremental_provider_ids
            else candidate.quote.upper_bound()
        )
        if reserved + candidate_ceiling > budget_limit_usd:
            continue
        attempts.append(candidate)
        reserved += candidate_ceiling
        if len(attempts) == max_attempts:
            break
    return tuple(attempts)


def _payload_incompatibility(
    provider: Provider,
    payload: Mapping[str, Any],
    operation: RoutingOperation,
) -> str | None:
    if operation != RoutingOperation.SYNC_INFERENCE and payload.get("stream") is True:
        return "streaming_unsupported"
    if payload.get("stream") is True:
        openai_lease_stream = provider.provider_type.value == "pod_lease" and isinstance(
            provider.config.get("openai_proxy_port"), int
        )
        if provider.config.get("supports_streaming") is not True and not openai_lease_stream:
            return "streaming_unsupported"
    requested_model = payload.get("model")
    priced_model = next(
        (
            provider.config[key]
            for key in ("served_model_name", "model_id", "together_model", "model")
            if isinstance(provider.config.get(key), str) and provider.config[key]
        ),
        None,
    )
    if (
        isinstance(requested_model, str)
        and priced_model is not None
        and requested_model != priced_model
    ):
        return "model_mismatch"
    return None


def _hints(capability: Capability, payload: Mapping[str, Any]) -> Hints:
    return Hints(
        latency_sensitive="latency_sensitive" in capability.hints_supported,
        cost_sensitive="cost_sensitive" in capability.hints_supported,
        region_preference=(
            payload.get("region") if isinstance(payload.get("region"), str) else None
        ),
    )


def _build_escape_hatch(
    *,
    ranked: tuple[ProductionRouteCandidate, ...],
    eliminated: tuple[RouteElimination, ...],
    capability: Capability,
    quota_snapshot: QuotaSnapshot,
    now: dt.datetime,
    own_pod_usd_per_hour: Decimal | None,
    gpu_class: str | None,
) -> EscapeHatch | None:
    """Compute the prong-3 ``pitwall serve`` proposal when every free pool is exhausted."""

    if own_pod_usd_per_hour is None or not gpu_class:
        return None

    class _PlanView:
        def __init__(
            self,
            ranked: tuple[ProductionRouteCandidate, ...],
            eliminated: tuple[RouteElimination, ...],
            capability: Capability,
        ) -> None:
            self._ranked = ranked
            self._capability = capability
            self._eliminated = eliminated

        @property
        def ranked_candidates(self) -> tuple[ProductionRouteCandidate, ...]:
            return self._ranked

        @property
        def capability_snapshot(self) -> Capability:
            return self._capability

        @property
        def dropped_provider_reasons(self) -> dict[str, list[str]]:
            out: dict[str, list[str]] = {}
            for item in self._eliminated:
                out.setdefault(item.provider_id, []).append(item.reason)
            return out

    view = _PlanView(ranked, eliminated, capability)
    message = escape_hatch_message(
        view,
        quota_snapshot=quota_snapshot,
        now=now,
        own_pod_usd_per_hour=own_pod_usd_per_hour,
        gpu_class=gpu_class,
    )
    if message is None:
        return None
    return EscapeHatch(proposed_command=message)


def _latency(provider: Provider) -> tuple[Decimal | None, str]:
    raw = provider.config.get("expected_latency_ms")
    signal = "provider_config"
    if raw is None:
        raw = provider.cold_start_p50_ms
        signal = "cold_start_p50"
    if raw is None:
        return None, "missing"
    try:
        value = _non_negative_decimal(raw, "expected_latency_ms")
    except ValueError:
        return None, "invalid"
    return value, signal


def routing_weights_for(settings: PitwallSettings, capability: Capability) -> RoutingWeights:
    """Resolve the typed per-capability weights with one stable precedence rule."""

    for key in (capability.id, capability.name, capability.class_.value, "*"):
        if key in settings.pitwall_routing_weights:
            return settings.pitwall_routing_weights[key]
    return RoutingWeights()


def stage12_elimination(
    request: RoutingRequest,
    provider: Provider,
    *,
    capability: Capability,
    now: dt.datetime,
) -> RouteElimination | None:
    """Hard-constraint and health gate shared by the planner and the capability resolver."""

    constraint = evaluate_hard_constraints(request, provider, capability=capability)
    if not constraint.passed:
        reason = constraint.reason.value if constraint.reason is not None else "hard_constraint"
        return _elimination(provider, reason, "constraint")
    if not provider.enabled:
        return _elimination(provider, "disabled", "health")
    if provider.health_status.lower() not in {"healthy", "warming"}:
        return _elimination(provider, "health_unavailable", "health")
    if is_in_cooldown(provider, now=now):
        return _elimination(provider, "health_cooldown", "health")
    return None


def _elimination(provider: Provider, reason: str, stage: str) -> RouteElimination:
    return RouteElimination(
        provider_id=provider.id,
        adapter_id=provider.adapter_id.value,
        reason=reason,
        stage=stage,
    )


def _sorted_eliminations(items: Sequence[RouteElimination]) -> tuple[RouteElimination, ...]:
    return tuple(sorted(items, key=lambda item: (item.provider_id, item.stage, item.reason)))


def _workload_events(workload: Workload) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = [
        {"event": "submitted", "state": "queued", "at": workload.submitted_at.isoformat()}
    ]
    if workload.started_at is not None:
        events.append(
            {"event": "started", "state": "running", "at": workload.started_at.isoformat()}
        )
    if workload.completed_at is not None:
        events.append(
            {
                "event": "terminal",
                "state": _enum_text(workload.state),
                "at": workload.completed_at.isoformat(),
            }
        )
    return tuple(events)


def _validated_replay(
    existing: Workload,
    payload: Mapping[str, Any],
    *,
    capability_id: str,
    workload_type: str,
    provider_id: str | None,
    webhook_sha256: str | None,
) -> Workload:
    """Return *existing* only if it is the same request as the one replaying its key."""

    if existing.capability_id != capability_id or existing.type != workload_type:
        raise RoutePlanningError("idempotency key was already used for a different request")
    if (
        existing.route_plan is not None
        and existing.route_plan.get("provider_constraint") != provider_id
    ):
        raise RoutePlanningError("idempotency key was already used for a different request")
    if existing.input is None or existing.route_plan is None or existing.route_plan_id is None:
        raise RoutePlanningError("idempotent workload has no complete production route plan")
    if _canonical_json(existing.input) != _canonical_json(payload):
        raise RoutePlanningError("idempotency key was already used for a different payload")
    if existing.route_plan.get("webhook_sha256") != webhook_sha256:
        raise RoutePlanningError("idempotency key was already used for a different webhook")
    return existing


def _execution_from_replay(workload: Workload) -> RouteExecutionResult:
    if workload.route_plan is None or workload.route_plan_id is None:
        raise RoutePlanningError("idempotent workload predates production route-plan persistence")
    if _enum_text(workload.state) != "completed" or workload.result is None:
        raise RoutePlanningError("idempotent synchronous workload has no completed result")
    return RouteExecutionResult(
        workload=workload,
        plan=PersistedRoutePlan(
            plan_id=workload.route_plan_id,
            document=workload.route_plan,
        ),
        output=workload.result,
    )


def _persisted_plan_document(
    plan: ProductionRoutePlan,
    *,
    webhook_sha256: str | None,
) -> dict[str, object]:
    document = plan.to_dict()
    if webhook_sha256 is not None:
        document["webhook_sha256"] = webhook_sha256
    return document


def _optional_value_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()


def _provider_snapshots_by_id(plan: ProductionRoutePlan) -> dict[str, Provider]:
    snapshots = {provider.id: provider for provider in plan.provider_snapshots}
    missing = [provider_id for provider_id in plan.fallback_chain if provider_id not in snapshots]
    if missing:
        raise RoutePlanningError("production route plan is missing an execution snapshot")
    return snapshots


def _safe_provider_failure(provider_id: str, exc: Exception) -> dict[str, str]:
    return {
        "provider_id": provider_id,
        "code": "provider_call_failed",
        "type": exc.__class__.__name__,
    }


def _usage_derived_actual(
    result: InferenceResult,
    candidate: ProductionRouteCandidate,
) -> Decimal | None:
    """Price only provider-reported token counts under the admitted token rates."""

    if not isinstance(candidate.quote.pricing, PerTokenPricing):
        return None
    raw = result.raw
    if not isinstance(raw, Mapping):
        return None
    try:
        usage = parse_usage_json(dict(raw))
        if usage is None:
            return None
        return candidate.quote.pricing.estimate(
            candidate.quote.capability,
            {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_tokens": usage.cached_tokens,
            },
        )
    except ArithmeticError, TypeError, ValueError:
        # Provider-controlled usage must never turn delivered output into a
        # failed workload. Invalid usage leaves actual cost unavailable.
        return None


def _payload_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError("routing payload must be a JSON object")
    snapshot = _json_value(dict(value))
    if not isinstance(snapshot, dict):
        raise ValueError("routing payload must be a JSON object")
    return snapshot


def _prepared_payload(value: PreparedRoutingPayload) -> dict[str, Any]:
    if not isinstance(value, PreparedRoutingPayload):
        raise TypeError("prepared_payload must be PreparedRoutingPayload")
    snapshot = _payload_mapping(value.payload)
    if hashlib.sha256(_canonical_json(snapshot).encode()).hexdigest() != value.payload_sha256:
        raise RoutePlanningError("prepared routing payload changed after inspection")
    return snapshot


def _json_value(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _json_object(value: Any) -> dict[str, Any]:
    normalized = _json_value(value)
    return normalized if isinstance(normalized, dict) else {"result": normalized}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, dt.datetime):
        return _utc(value).isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"unsupported JSON value {type(value).__name__}")


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("route Decimal values must be finite")
    return format(value, "f")


def _non_negative_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative number")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return parsed


def _utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("routing now must include timezone information")
    return value.astimezone(dt.UTC)


def _elapsed_ms(started_at: dt.datetime, completed_at: dt.datetime) -> int:
    return max(0, int((completed_at - started_at).total_seconds() * 1000))


def _enum_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


__all__ = [
    "JobEventPage",
    "JobResultPage",
    "NoExecutableRouteError",
    "PreparedRoutingPayload",
    "ProductionRouteCandidate",
    "ProductionRoutePlan",
    "ProductionRoutingService",
    "RouteBudgetQuote",
    "RouteElimination",
    "RouteExecutionResult",
    "RouteGuardrailRejected",
    "RoutePlanningError",
    "RouteProviderInvocationError",
    "RoutingOperation",
    "build_production_plan",
    "routing_weights_for",
    "stage12_elimination",
]
