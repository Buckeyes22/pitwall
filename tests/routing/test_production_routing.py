"""Deterministic planner and guarded provider-neutral executor coverage."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, SecretStr

from pitwall.config import PitwallSettings, RoutingWeights
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
    WorkloadState,
)
from pitwall.core.models import Capability, Provider, Workload
from pitwall.cost.budget_gate import BudgetAdmission, BudgetRejected
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.cost.estimator import TaggedPricingModel, parse_pricing_model
from pitwall.providers.interface import (
    AsyncInferenceCancelRequest,
    AsyncInferenceCancelResult,
    AsyncInferenceRequest,
    AsyncInferenceStatusRequest,
    AsyncInferenceStatusResult,
    AsyncInferenceSubmission,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderDeclaration,
)
from pitwall.providers.registry import ProviderRegistry, get_default_registry
from pitwall.routing.lockout import LockoutKey, LockoutTable
from pitwall.routing.production import (
    NoExecutableRouteError,
    PreparedRoutingPayload,
    ProductionRoutePlan,
    ProductionRoutingService,
    RouteGuardrailRejected,
    RoutePlanningError,
    RouteProviderInvocationError,
    RoutingOperation,
    build_production_plan,
)
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 1, 14, 30, tzinfo=dt.UTC)


def _capability(*, cost_mode: CostMode = CostMode.PER_TOKEN) -> Capability:
    return Capability(
        id="cap_chat",
        name="llm.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=cost_mode,
        source=CapabilitySource.API,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(
    provider_id: str,
    adapter_id: ProviderAdapterId,
    *,
    priority: int,
    latency_ms: int | None,
    config: dict[str, Any] | None = None,
    health: str = "healthy",
    provider_type: ProviderType = ProviderType.SERVERLESS_LB,
) -> Provider:
    provider_config = (
        {
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            }
        }
        if config is None
        else config
    )
    if latency_ms is not None:
        provider_config["expected_latency_ms"] = latency_ms
    return Provider(
        id=provider_id,
        capability_id="cap_chat",
        name=provider_id,
        adapter_id=adapter_id,
        provider_type=provider_type,
        config=provider_config,
        priority=priority,
        enabled=True,
        health_status=health,
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


def _plan(
    providers: list[Provider],
    *,
    mode: str = "weighted",
    operation: RoutingOperation = RoutingOperation.SYNC_INFERENCE,
    payload: Mapping[str, Any] | None = None,
    budget_limit_usd: Decimal | None = None,
    openai_lease_proxy: bool = False,
):
    return build_production_plan(
        capability=_capability(),
        providers=providers,
        payload=payload
        or {
            "messages": [{"role": "user", "content": "hello"}],
            "max_output_tokens": 64,
        },
        operation=operation,
        registry=get_default_registry(),
        now=_NOW,
        mode=mode,
        weights=RoutingWeights(cost="1", latency="0.001"),
        max_attempts=3,
        budget_limit_usd=budget_limit_usd,
        openai_lease_proxy=openai_lease_proxy,
    )


def test_same_snapshots_and_now_produce_byte_stable_plan_without_payload() -> None:
    providers = [
        _provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=2, latency_ms=250),
        _provider("provider_together", ProviderAdapterId.TOGETHER, priority=1, latency_ms=100),
    ]

    first = _plan(providers)
    second = _plan(list(reversed(providers)))

    assert first.plan_id == second.plan_id
    assert first.canonical_bytes() == second.canonical_bytes()
    assert b"hello" not in first.canonical_bytes()
    assert first.payload_sha256

    stable_bytes = first.canonical_bytes()
    providers[0].config["expected_latency_ms"] = 999_999
    assert first.canonical_bytes() == stable_bytes
    assert first.provider_snapshots[1].config["expected_latency_ms"] == 250


def test_explicit_provider_constraint_is_part_of_plan_identity() -> None:
    provider = _provider(
        "provider_runpod",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=10,
    )

    automatic = _plan([provider])
    constrained = build_production_plan(
        capability=_capability(),
        providers=[provider],
        payload={"messages": [], "max_output_tokens": 64},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode="weighted",
        weights=RoutingWeights(cost="1", latency="0.001"),
        max_attempts=3,
        provider_id=provider.id,
    )

    assert automatic.plan_id != constrained.plan_id
    assert constrained.provider_constraint == provider.id


def test_plan_detaches_capability_snapshot_from_caller_mutation() -> None:
    capability = _capability()
    plan = build_production_plan(
        capability=capability,
        providers=[
            _provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)
        ],
        payload={"max_output_tokens": 1},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode="priority",
        weights=RoutingWeights(),
        max_attempts=1,
    )

    capability.name = "changed.after.snapshot"

    assert plan.capability_name == "llm.chat"
    assert plan.capability_snapshot.name == "llm.chat"


def test_weighted_objective_and_priority_compatibility_are_explicit() -> None:
    expensive_fast = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=2,
        latency_ms=10,
        config={
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "100",
                "per_million_output_tokens": "100",
            }
        },
    )
    cheap_slow = _provider(
        "provider_runpod",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=100,
    )

    weighted = _plan([expensive_fast, cheap_slow], mode="weighted")
    compatible = _plan([expensive_fast, cheap_slow], mode="priority")

    assert weighted.selected_provider_id == "provider_together"
    assert compatible.selected_provider_id == "provider_runpod"
    assert compatible.mode == "priority"
    assert compatible.selected.objective == Decimal("1")


def test_openai_explicit_fallback_keeps_weighted_order_for_remaining_attempts() -> None:
    def openai_provider(
        provider_id: str,
        *,
        priority: int,
        latency_ms: int,
        fallback_chain: list[str] | None = None,
    ) -> Provider:
        config: dict[str, object] = {
            "openai_base_url": f"https://{provider_id}.example/v1",
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            },
        }
        if fallback_chain is not None:
            config["fallback_chain"] = fallback_chain
        return _provider(
            provider_id,
            ProviderAdapterId.RUNPOD,
            priority=priority,
            latency_ms=latency_ms,
            config=config,
            provider_type=ProviderType.PUBLIC_ENDPOINT,
        )

    plan = _plan(
        [
            openai_provider("priority_first", priority=1, latency_ms=3),
            openai_provider("weighted_second", priority=2, latency_ms=2),
            openai_provider("explicit", priority=3, latency_ms=100),
            openai_provider(
                "weighted_primary",
                priority=4,
                latency_ms=1,
                fallback_chain=["explicit"],
            ),
        ],
        mode="weighted",
        openai_lease_proxy=True,
    )

    assert plan.fallback_chain == (
        "weighted_primary",
        "explicit",
        "weighted_second",
    )


def test_budget_ineligible_candidate_is_removed_before_soft_scoring() -> None:
    expensive_fast = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=1,
        latency_ms=0,
        config={
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1000000",
                "per_million_output_tokens": "1000000",
            }
        },
    )
    cheap_slow = _provider(
        "provider_runpod",
        ProviderAdapterId.RUNPOD,
        priority=2,
        latency_ms=1000,
    )

    plan = _plan(
        [expensive_fast, cheap_slow],
        payload={"input_tokens": 1, "max_output_tokens": 1},
        budget_limit_usd=Decimal("0.01"),
    )

    assert plan.selected_provider_id == "provider_runpod"
    assert [candidate.provider_id for candidate in plan.ranked_candidates] == ["provider_runpod"]
    assert [(item.provider_id, item.stage, item.reason) for item in plan.eliminated] == [
        ("provider_together", "budget", "budget_unavailable")
    ]


def test_already_paid_pod_lease_remains_eligible_with_no_budget_headroom() -> None:
    lease = _provider(
        "provider_lease",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=10,
        provider_type=ProviderType.POD_LEASE,
        config={
            "active_lease_id": "lease-active",
            "active_pod_id": "pod-active",
            "openai_proxy_port": 8000,
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            },
        },
    )

    plan = _plan(
        [lease],
        budget_limit_usd=Decimal("0"),
        openai_lease_proxy=True,
    )

    assert plan.selected_provider_id == "provider_lease"
    assert plan.selected.quote.upper_bound() > Decimal("0")
    assert plan.selected.cost_component == Decimal("0")


def test_compute_pod_launch_keeps_real_ceiling_with_no_budget_headroom() -> None:
    launch_provider = _provider(
        "provider_launch",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=10,
        provider_type=ProviderType.POD_LEASE,
        config={
            "active_lease_id": "lease-existing",
            "active_pod_id": "pod-existing",
            "cost": {"per_second_active": "0.001"},
        },
    )

    with pytest.raises(NoExecutableRouteError) as exc_info:
        build_production_plan(
            capability=_capability(cost_mode=CostMode.PER_SECOND),
            providers=[launch_provider],
            payload={"expected_seconds": 60},
            operation=RoutingOperation.COMPUTE,
            registry=get_default_registry(),
            now=_NOW,
            mode="priority",
            weights=RoutingWeights(),
            max_attempts=1,
            budget_limit_usd=Decimal("0"),
            openai_lease_proxy=True,
        )

    assert [
        (item.provider_id, item.stage, item.reason) for item in exc_info.value.eliminations
    ] == [("provider_launch", "budget", "budget_unavailable")]


def test_adapter_capability_and_payload_compatibility_fail_closed() -> None:
    together = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=1,
        latency_ms=10,
        config={
            "model": "vendor/priced-model",
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            },
        },
    )

    with pytest.raises(NoExecutableRouteError) as async_exc:
        _plan([together], operation=RoutingOperation.ASYNC_INFERENCE)
    assert async_exc.value.eliminations[0].reason == "capability_unsupported"

    with pytest.raises(NoExecutableRouteError) as model_exc:
        _plan([together], payload={"model": "other/model"})
    assert model_exc.value.eliminations[0].reason == "model_mismatch"

    with pytest.raises(NoExecutableRouteError) as stream_exc:
        _plan([together], payload={"stream": True})
    assert stream_exc.value.eliminations[0].reason == "streaming_unsupported"


def test_served_model_alias_takes_precedence_over_source_model() -> None:
    provider = _provider(
        "provider_runpod",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=10,
        config={
            "model": "vendor/source-model",
            "served_model_name": "public-model",
            "cost": {"kind": "per_request", "per_request": "0.001"},
        },
    )

    plan = _plan([provider], payload={"model": "public-model"})

    assert plan.selected_provider_id == provider.id


def test_missing_cost_and_explicit_capacity_unavailable_are_eliminated() -> None:
    missing_cost = _provider(
        "provider_runpod",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=10,
        config={},
    )
    unavailable = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=2,
        latency_ms=20,
    )
    unavailable.config["capacity_available"] = False

    with pytest.raises(NoExecutableRouteError) as exc_info:
        _plan([missing_cost, unavailable])

    reasons = {item.provider_id: item.reason for item in exc_info.value.eliminations}
    assert reasons == {
        "provider_runpod": "cost_unavailable",
        "provider_together": "capacity_unavailable",
    }


def test_invalid_latency_is_neutral_and_explicit() -> None:
    invalid = _provider(
        "provider_runpod",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=None,
    )
    invalid.config["expected_latency_ms"] = "not-a-number"
    known = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=2,
        latency_ms=25,
    )

    plan = _plan([invalid, known])
    by_id = {candidate.provider_id: candidate for candidate in plan.ranked_candidates}

    assert by_id["provider_runpod"].latency_ms == Decimal("25")
    assert by_id["provider_runpod"].latency_signal == "neutral_invalid"
    policy = plan.to_dict()["signal_policy"]
    assert isinstance(policy, dict)
    assert policy["invalid_latency"] == "worst_known_or_zero"


def test_compute_plan_uses_only_current_compute_adapters() -> None:
    capability = _capability(cost_mode=CostMode.PER_SECOND)
    vast = _provider(
        "provider_vast",
        ProviderAdapterId.VAST,
        priority=2,
        latency_ms=100,
        provider_type=ProviderType.POD_LEASE,
        config={"cost": {"price_per_hour": "0.20"}},
    )
    lambda_cloud = _provider(
        "provider_lambda",
        ProviderAdapterId.LAMBDA_CLOUD,
        priority=1,
        latency_ms=200,
        provider_type=ProviderType.POD_LEASE,
        config={"cost": {"price_per_hour": "0.30"}},
    )
    together = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=0,
        latency_ms=1,
    )

    plan = build_production_plan(
        capability=capability,
        providers=[together, lambda_cloud, vast],
        payload={"expected_seconds": 60},
        operation=RoutingOperation.COMPUTE,
        registry=get_default_registry(),
        now=_NOW,
        mode="priority",
        weights=RoutingWeights(),
        max_attempts=3,
    )

    assert plan.selected_provider_id == "provider_lambda"
    assert plan.fallback_chain == ("provider_lambda", "provider_vast")
    assert plan.eliminated[0].reason == "capability_unsupported"


@pytest.mark.property
@given(st.permutations(["provider_runpod", "provider_together"]))
def test_tie_breaking_is_independent_of_repository_order(order: list[str]) -> None:
    by_id = {
        "provider_runpod": _provider(
            "provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=None
        ),
        "provider_together": _provider(
            "provider_together", ProviderAdapterId.TOGETHER, priority=1, latency_ms=None
        ),
    }

    plan = _plan([by_id[provider_id] for provider_id in order])

    assert plan.selected_provider_id == "provider_runpod"


class _Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    api_key: SecretStr


class _FakeAdapter:
    name = "fake"
    credential_schema = _Credentials
    declaration = ProviderDeclaration()

    def __init__(
        self,
        adapter_id: str,
        *,
        fail_sync: bool = False,
        fail_submit: bool = False,
        async_capable: bool = True,
        status_state: WorkloadState = WorkloadState.RUNNING,
        status_output: Any = None,
        submit_started: asyncio.Event | None = None,
        submit_release: asyncio.Event | None = None,
        sync_raw: Mapping[str, Any] | None = None,
        sync_exception: BaseException | None = None,
    ) -> None:
        self.id = adapter_id
        self.capabilities = frozenset(
            {
                ProviderCapability.SYNC_INFERENCE,
                *(
                    {
                        ProviderCapability.ASYNC_INFERENCE,
                        ProviderCapability.ASYNC_STATUS,
                        ProviderCapability.ASYNC_CANCEL,
                    }
                    if async_capable
                    else set()
                ),
            }
        )
        self.fail_sync = fail_sync
        self.fail_submit = fail_submit
        self.sync_calls = 0
        self.submit_calls = 0
        self.status_calls = 0
        self.cancel_calls = 0
        self.status_state = status_state
        self.status_output = status_output
        self.submit_started = submit_started
        self.submit_release = submit_release
        self.sync_raw = dict(sync_raw or {})
        self.sync_exception = sync_exception

    def pricing_model(
        self, capability: Capability, provider_record: Provider
    ) -> TaggedPricingModel:
        return parse_pricing_model(provider_record, cost_mode=capability.cost_mode)

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        self.sync_calls += 1
        if self.sync_exception is not None:
            raise self.sync_exception
        if self.fail_sync:
            raise RuntimeError("provider canary secret must not escape")
        return InferenceResult(
            provider_id=request.provider_record.id,
            output={"ok": self.id},
            raw=self.sync_raw,
        )

    async def submit(self, request: AsyncInferenceRequest) -> AsyncInferenceSubmission:
        self.submit_calls += 1
        if self.submit_started is not None:
            self.submit_started.set()
        if self.submit_release is not None:
            await self.submit_release.wait()
        if self.fail_submit:
            raise RuntimeError("ambiguous async submission")
        return AsyncInferenceSubmission(
            provider_id=request.provider_record.id,
            external_job_id=f"job-{self.id}",
            state=WorkloadState.QUEUED,
        )

    async def job_status(self, request: AsyncInferenceStatusRequest) -> AsyncInferenceStatusResult:
        self.status_calls += 1
        return AsyncInferenceStatusResult(
            provider_id=request.provider_record.id,
            external_job_id=request.external_job_id,
            state=self.status_state,
            output=self.status_output,
        )

    async def cancel_job(self, request: AsyncInferenceCancelRequest) -> AsyncInferenceCancelResult:
        self.cancel_calls += 1
        return AsyncInferenceCancelResult(
            provider_id=request.provider_record.id,
            external_job_id=request.external_job_id,
            cancelled=True,
        )


class _CapabilityRepo:
    def __init__(self, capability: Capability) -> None:
        self.capability = capability

    async def get(self, capability_id: str) -> Capability | None:
        return self.capability if capability_id == self.capability.id else None

    async def get_by_name(self, name: str) -> Capability | None:
        return self.capability if name == self.capability.name else None


class _ProviderRepo:
    def __init__(self, providers: list[Provider]) -> None:
        self.providers = providers
        self.patches: list[tuple[str, dict[str, Any]]] = []

    async def get(self, provider_id: str) -> Provider | None:
        return next((item for item in self.providers if item.id == provider_id), None)

    async def list(self, **_: object) -> list[Provider]:
        return list(self.providers)

    async def patch(self, provider_id: str, **fields: Any) -> Provider | None:
        self.patches.append((provider_id, fields))
        return await self.get(provider_id)


class _WorkloadRepo:
    def __init__(self) -> None:
        self.items: dict[str, Workload] = {}

    async def get(self, workload_id: str) -> Workload | None:
        return self.items.get(workload_id)

    async def get_on_connection(self, _conn: Any, workload_id: str) -> Workload | None:
        return self.items.get(workload_id)

    async def get_by_idempotency_key(self, key: str) -> Workload | None:
        return next((item for item in self.items.values() if item.idempotency_key == key), None)

    async def update_state(
        self, workload_id: str, state: WorkloadState, **patch: Any
    ) -> Workload | None:
        current = self.items.get(workload_id)
        if current is None:
            return None
        updated = current.model_copy(update={"state": state, **patch})
        self.items[workload_id] = updated
        return updated


class _BudgetGate:
    def __init__(self, workloads: _WorkloadRepo) -> None:
        self.workloads = workloads
        self.admissions = 0
        self.last_ceiling: Decimal | None = None
        self.monthly_budget_usd = Decimal("100")
        self.per_request_max_usd = Decimal("100")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **kwargs: Any) -> BudgetAdmission:
        self.admissions += 1
        quote = kwargs["estimate_usd"]
        self.last_ceiling = quote.upper_bound()
        workload = Workload(
            id=f"wkl_{self.admissions}",
            capability_id=kwargs["capability_id"],
            provider_id=kwargs["provider_id"],
            type=kwargs["workload_type"],
            state=WorkloadState.QUEUED,
            idempotency_key=kwargs.get("idempotency_key"),
            submitted_at=kwargs["submitted_at"],
            cost_estimate_usd=quote.estimate(),
            cost_ceiling_usd=quote.upper_bound(),
            cost_quote=quote.to_serializable_dict(),
        )
        self.workloads.items[workload.id] = workload
        callback = kwargs.get("after_new_admission")
        if callback is not None:
            await callback(_Connection(self.workloads), workload.id)
        return BudgetAdmission(workload_id=workload.id, is_new=True)


class _Connection:
    def __init__(self, workloads: _WorkloadRepo, lock: asyncio.Lock | None = None) -> None:
        self.workloads = workloads
        self.lock = lock

    async def execute(self, query: str, *args: Any) -> str:
        workload = self.workloads.items[args[0]]
        if "pg_advisory_lock" in query:
            if self.lock is not None:
                await self.lock.acquire()
            return "SELECT 1"
        if "pg_advisory_unlock" in query:
            if self.lock is not None and self.lock.locked():
                self.lock.release()
            return "SELECT 1"
        if "started_at = COALESCE(started_at" in query:
            patch = {
                "state": WorkloadState.RUNNING,
                "started_at": workload.started_at or args[1],
            }
        elif "SET state = 'running'" in query:
            patch = {
                "state": WorkloadState.RUNNING,
                "started_at": args[1],
                "input": args[2],
                "input_bytes": args[3],
                "fallback_chain": args[4],
                "route_plan_id": args[5],
                "route_plan": args[6],
            }
        elif "SET state = 'completed'" in query:
            patch = {
                "state": WorkloadState.COMPLETED,
                "provider_id": args[1],
                "completed_at": args[2],
                "execution_ms": args[3],
                "result": args[4],
                "output_bytes": args[5],
                "external_job_id": args[6],
                "runpod_job_id": args[7],
                "cost_actual_usd": (args[8] if args[8] is not None else workload.cost_actual_usd),
                "cost_actual_provenance": (
                    args[9] if args[9] is not None else workload.cost_actual_provenance
                ),
                "cost_reconciled_at": args[2]
                if args[8] is not None
                else workload.cost_reconciled_at,
            }
        elif "SET input = $2::jsonb" in query:
            patch = {
                "input": args[1],
                "input_bytes": args[2],
                "fallback_chain": args[3],
                "route_plan_id": args[4],
                "route_plan": args[5],
            }
        elif "SET provider_id = $2" in query:
            patch = {
                "provider_id": args[1],
                "state": WorkloadState(args[2]),
                "external_job_id": args[3],
                "runpod_job_id": args[4],
            }
        elif "SET state = 'failed'" in query:
            patch = {
                "state": WorkloadState.FAILED,
                "completed_at": args[1],
                "execution_ms": args[2],
                "error": args[3],
            }
        elif "SET route_attempts = route_attempts" in query:
            patch = {"route_attempts": [*workload.route_attempts, *args[1]]}
        elif "SET state = 'cancelled'" in query:
            patch = {
                "state": WorkloadState.CANCELLED,
                "completed_at": args[1],
            }
        elif "SET cost_actual_usd = 0" in query:
            patch = (
                {
                    "cost_actual_usd": Decimal("0"),
                    "cost_actual_provenance": "broker:no_provider_invocation",
                    "cost_reconciled_at": args[1],
                }
                if workload.started_at is None
                else {}
            )
        elif "SET langfuse_trace_id = $2" in query:
            patch = {"langfuse_trace_id": args[1]}
        elif "started_at = CASE" in query:
            state = WorkloadState(args[1])
            output = args[4]
            patch = {
                "state": state,
                "started_at": args[2] if state is WorkloadState.RUNNING else workload.started_at,
                "completed_at": (
                    args[2]
                    if state
                    in {
                        WorkloadState.CANCELLED,
                        WorkloadState.COMPLETED,
                        WorkloadState.FAILED,
                        WorkloadState.TIMED_OUT,
                    }
                    else workload.completed_at
                ),
                "result": output or workload.result,
                "output_bytes": args[5] or workload.output_bytes,
                "error": args[6] or workload.error,
            }
        else:  # pragma: no cover - regression tripwire for new SQL
            raise AssertionError(query)
        self.workloads.items[workload.id] = workload.model_copy(update=patch)
        return "UPDATE 1"


class _Acquire:
    def __init__(self, pool: _Pool, connection: _Connection) -> None:
        self.pool = pool
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        current = asyncio.current_task()
        assert self.pool.connection_owner is not current, (
            "workload lock must not reacquire the pool"
        )
        await self.pool.connection_available.acquire()
        self.pool.connection_owner = current
        self.pool.active_connections += 1
        assert self.pool.active_connections == 1
        return self.connection

    async def __aexit__(self, *_: object) -> None:
        self.pool.active_connections -= 1
        self.pool.connection_owner = None
        self.pool.connection_available.release()


class _Pool:
    def __init__(self, workloads: _WorkloadRepo) -> None:
        self.connection = _Connection(workloads, asyncio.Lock())
        self.active_connections = 0
        self.connection_available = asyncio.Lock()
        self.connection_owner: asyncio.Task[Any] | None = None

    def acquire(self) -> _Acquire:
        return _Acquire(self, self.connection)


def _service(
    providers: list[Provider],
    registry: ProviderRegistry,
    *,
    guardrails: PreSpendInspectionService | None = None,
) -> tuple[ProductionRoutingService, _BudgetGate, _WorkloadRepo]:
    workloads = _WorkloadRepo()
    budget = _BudgetGate(workloads)
    service = ProductionRoutingService(
        _Pool(workloads),
        settings=PitwallSettings(
            pitwall_routing_mode="priority",
            pitwall_routing_max_attempts=3,
        ),
        capability_repository=_CapabilityRepo(_capability()),
        provider_repository=_ProviderRepo(providers),
        workload_repository=workloads,  # type: ignore[arg-type]  # reason: test protocol fake
        registry=registry,
        guardrails=guardrails or PreSpendInspectionService(),
        budget_gate=budget,  # type: ignore[arg-type]  # reason: test protocol fake
        now=lambda: _NOW,
    )
    return service, budget, workloads


async def test_guardrail_blocks_before_budget_or_provider_egress() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    with pytest.raises(RouteGuardrailRejected):
        await service.execute_sync(
            capability_id="llm.chat",
            payload={"api_key": "sk-abcdefghijklmnop12345678"},
        )

    assert budget.admissions == 0
    assert adapter.sync_calls == 0


async def test_explicit_prepared_execution_records_exactly_one_external_decision() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    guardrails = PreSpendInspectionService()
    service, _, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
        guardrails=guardrails,
    )
    inspection = guardrails.inspect({"messages": [], "max_output_tokens": 1})
    prepared = PreparedRoutingPayload.from_inspection(inspection)

    result = await service.execute_sync_prepared(
        capability_id="llm.chat",
        prepared_payload=prepared,
    )

    assert result.workload.state is WorkloadState.COMPLETED
    assert guardrails.status().counters.total == 1
    assert adapter.sync_calls == 1


async def test_prepared_payload_cannot_change_after_inspection() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    guardrails = PreSpendInspectionService()
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
        guardrails=guardrails,
    )
    prepared = PreparedRoutingPayload.from_inspection(
        guardrails.inspect({"messages": [], "max_output_tokens": 1})
    )
    assert isinstance(prepared.payload, dict)
    prepared.payload["api_key"] = "sk-abcdefghijklmnop12345678"

    with pytest.raises(RoutePlanningError, match="changed after inspection"):
        await service.execute_sync_prepared(
            capability_id="llm.chat",
            prepared_payload=prepared,
        )

    assert guardrails.status().counters.total == 1
    assert budget.admissions == 0
    assert adapter.sync_calls == 0


async def test_normal_execution_still_records_its_own_guardrail_decision() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    guardrails = PreSpendInspectionService()
    service, _, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
        guardrails=guardrails,
    )

    await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 1},
    )

    assert guardrails.status().counters.total == 1
    assert adapter.sync_calls == 1


async def test_generic_sync_excludes_active_lease_that_requires_openai_proxy_transport() -> None:
    runpod = _FakeAdapter("runpod")
    together = _FakeAdapter("together")
    registry = ProviderRegistry()
    registry.register(runpod)
    registry.register(together)
    active_lease = _provider(
        "provider_lease",
        ProviderAdapterId.RUNPOD,
        priority=1,
        latency_ms=1,
        provider_type=ProviderType.POD_LEASE,
        config={
            "active_lease_id": "lease-active",
            "active_pod_id": "pod-active",
            "openai_proxy_port": 8000,
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            },
        },
    )
    paid = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=2,
        latency_ms=20,
    )
    service, _, _ = _service([active_lease, paid], registry)

    result = await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 1},
    )

    assert result.workload.provider_id == "provider_together"
    assert runpod.sync_calls == 0
    assert together.sync_calls == 1
    assert [(item.provider_id, item.reason) for item in result.plan.eliminated] == [
        ("provider_lease", "transport_unsupported")
    ]


async def test_sync_execution_persists_optional_trace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    monkeypatch.setattr(
        "pitwall.routing.production.emit_inference_trace",
        lambda **_: "trace-production-routing",
    )

    result = await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 1},
    )

    assert result.workload.langfuse_trace_id == "trace-production-routing"


async def test_sync_execution_survives_optional_trace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    def fail_trace(**_: object) -> None:
        raise RuntimeError("optional tracer unavailable")

    monkeypatch.setattr("pitwall.routing.production.emit_inference_trace", fail_trace)

    result = await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 1},
    )

    assert result.workload.state is WorkloadState.COMPLETED


async def test_sync_fallback_reserves_sum_and_persists_one_plan_trace() -> None:
    first = _FakeAdapter("runpod", fail_sync=True)
    second = _FakeAdapter("together")
    registry = ProviderRegistry()
    registry.register(first)
    registry.register(second)
    service, budget, _ = _service(
        [
            _provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10),
            _provider("provider_together", ProviderAdapterId.TOGETHER, priority=2, latency_ms=20),
        ],
        registry,
    )

    result = await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
    )

    assert first.sync_calls == 1
    assert second.sync_calls == 1
    assert isinstance(result.plan, ProductionRoutePlan)
    assert budget.last_ceiling == sum(
        (candidate.quote.upper_bound() for candidate in result.plan.attempts),
        Decimal("0"),
    )
    assert result.workload.provider_id == "provider_together"
    assert result.workload.route_plan_id == result.plan.plan_id
    assert result.workload.fallback_chain == ["provider_runpod", "provider_together"]
    assert result.workload.cost_actual_usd is None
    assert [
        (attempt["provider_id"], attempt["operation"], attempt["outcome"])
        for attempt in result.workload.route_attempts
    ] == [
        ("provider_runpod", "inference", "failed"),
        ("provider_together", "inference", "completed"),
    ]
    assert "provider canary secret" not in str(result.workload.model_dump())


async def test_failed_sync_attempts_remain_actual_unavailable_instead_of_zero() -> None:
    adapter = _FakeAdapter("runpod", fail_sync=True)
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    with pytest.raises(RoutePlanningError, match="all planned providers failed"):
        await service.execute_sync(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
        )

    workload = next(iter(workloads.items.values()))
    assert workload.state is WorkloadState.FAILED
    assert workload.route_attempts[0]["outcome"] == "failed"
    assert workload.cost_actual_usd is None
    assert workload.cost_actual_provenance is None


async def test_together_sync_truths_up_only_from_provider_token_usage() -> None:
    adapter = _FakeAdapter(
        "together",
        sync_raw={
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            }
        },
    )
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_provider("provider_together", ProviderAdapterId.TOGETHER, priority=1, latency_ms=20)],
        registry,
    )

    result = await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
    )

    assert result.workload.cost_actual_usd == Decimal("0.000150")
    assert result.workload.cost_actual_provenance == "broker:provider_usage:together"
    assert result.workload.cost_reconciled_at == _NOW


async def test_together_invalid_overlong_usage_keeps_actual_unavailable() -> None:
    adapter = _FakeAdapter(
        "together",
        sync_raw={
            "usage": {
                "prompt_tokens": "9" * 5_000,
                "completion_tokens": 50,
                "total_tokens": "9" * 5_000,
            }
        },
    )
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_provider("provider_together", ProviderAdapterId.TOGETHER, priority=1, latency_ms=20)],
        registry,
    )

    result = await service.execute_sync(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
    )

    assert result.workload.state is WorkloadState.COMPLETED
    assert result.workload.result == {"ok": "together"}
    assert result.workload.cost_actual_usd is None
    assert result.workload.cost_actual_provenance is None


async def test_async_submit_persists_plan_and_bounded_events() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    workload = await service.submit_job(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
    )
    events = await service.job_events(workload.id, limit=1)

    assert budget.admissions == 1
    assert adapter.submit_calls == 1
    assert workload.external_job_id == "job-runpod"
    assert workload.route_plan_id is not None
    assert len(events.events) == 1
    assert events.streaming_supported is False
    assert events.unavailable_reason == "provider_event_stream_unavailable"


async def test_idempotent_replay_resumes_only_a_queued_pre_dispatch_job() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    payload = {"messages": [], "max_output_tokens": 64}
    submitted = await service.submit_job(
        capability_id="llm.chat",
        payload=payload,
        idempotency_key="resume-before-dispatch",
    )
    workloads.items[submitted.id] = submitted.model_copy(
        update={
            "state": WorkloadState.QUEUED,
            "external_job_id": None,
            "runpod_job_id": None,
            "started_at": None,
            "route_attempts": [],
        }
    )
    adapter.submit_calls = 0

    resumed = await service.submit_job(
        capability_id="llm.chat",
        payload=payload,
        idempotency_key="resume-before-dispatch",
    )

    assert resumed.external_job_id == "job-runpod"
    assert adapter.submit_calls == 1
    assert budget.admissions == 1


async def test_idempotent_replay_never_repeats_an_ambiguous_async_write() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    payload = {"messages": [], "max_output_tokens": 64}
    submitted = await service.submit_job(
        capability_id="llm.chat",
        payload=payload,
        idempotency_key="ambiguous-dispatch",
    )
    workloads.items[submitted.id] = submitted.model_copy(
        update={
            "state": WorkloadState.RUNNING,
            "external_job_id": None,
            "runpod_job_id": None,
        }
    )
    adapter.submit_calls = 0

    with pytest.raises(RoutePlanningError, match="outcome is unknown"):
        await service.submit_job(
            capability_id="llm.chat",
            payload=payload,
            idempotency_key="ambiguous-dispatch",
        )

    cancelled = await service.cancel_job(submitted.id)
    assert adapter.submit_calls == 0
    assert cancelled.state is WorkloadState.CANCELLED
    assert cancelled.cost_actual_usd is None


async def test_provider_neutral_status_refresh_and_result_bounds() -> None:
    adapter = _FakeAdapter(
        "runpod",
        status_state=WorkloadState.COMPLETED,
        status_output={"answer": "ok"},
    )
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    workload = await service.submit_job(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
    )

    refreshed = await service.get_job(workload.id)
    bounded = await service.job_result(workload.id, max_bytes=1, refresh=False)
    available = await service.job_result(workload.id, max_bytes=100, refresh=False)

    assert adapter.status_calls == 1
    assert refreshed.state is WorkloadState.COMPLETED
    assert refreshed.result == {"answer": "ok"}
    assert not bounded.available
    assert bounded.unavailable_reason == "result_limit_exceeded"
    assert bounded.provider_id == "provider_runpod"
    assert bounded.to_dict().keys().isdisjoint({"input", "route_plan"})
    assert available.available
    assert available.result == {"answer": "ok"}


async def test_webhook_dns_resolution_occurs_only_after_atomic_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    async def resolve_after_admission(url: str) -> Any:
        assert url == "https://example.invalid/callback"
        assert budget.admissions == 1
        admitted = next(iter(workloads.items.values()))
        assert admitted.route_plan_id is not None
        return type("Resolved", (), {"url": url})()

    monkeypatch.setattr(
        "pitwall.routing.production.resolve_webhook_target",
        resolve_after_admission,
    )

    workload = await service.submit_job(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
        webhook_url="https://example.invalid/callback",
    )

    assert workload.external_job_id == "job-runpod"
    assert adapter.submit_calls == 1


async def test_rejected_webhook_truths_up_exact_zero_without_provider_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    async def reject_target(_url: str) -> Any:
        raise ValueError("injected DNS rejection")

    monkeypatch.setattr("pitwall.routing.production.resolve_webhook_target", reject_target)

    with pytest.raises(RoutePlanningError, match="webhook target is unavailable"):
        await service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
            webhook_url="https://example.invalid/callback",
        )

    workload = next(iter(workloads.items.values()))
    assert workload.state is WorkloadState.FAILED
    assert workload.route_attempts == []
    assert workload.cost_actual_usd == Decimal("0")
    assert workload.cost_actual_provenance == "broker:no_provider_invocation"
    assert workload.cost_reconciled_at == _NOW
    assert adapter.submit_calls == 0


async def test_async_idempotency_replays_only_the_same_operation() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    payload = {"messages": [], "max_output_tokens": 64}

    first = await service.submit_job(
        capability_id="llm.chat",
        payload=payload,
        idempotency_key="route-same-request",
    )
    replay = await service.submit_job(
        capability_id="llm.chat",
        payload=payload,
        idempotency_key="route-same-request",
    )

    assert replay.id == first.id
    assert budget.admissions == 1
    assert adapter.submit_calls == 1

    with pytest.raises(RoutePlanningError, match="different request"):
        await service.execute_sync(
            capability_id="llm.chat",
            payload=payload,
            idempotency_key="route-same-request",
        )
    assert adapter.sync_calls == 0

    with pytest.raises(RoutePlanningError, match="different request"):
        await service.submit_job(
            capability_id="llm.chat",
            payload=payload,
            provider_id="provider_runpod",
            idempotency_key="route-same-request",
        )
    assert adapter.submit_calls == 1


async def test_idempotency_rejects_changed_webhook_identity() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    await service.submit_job(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
        idempotency_key="same-key",
    )

    with pytest.raises(RoutePlanningError, match="different webhook"):
        await service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
            idempotency_key="same-key",
            webhook_url="https://example.invalid/callback",
        )
    assert adapter.submit_calls == 1


async def test_concurrent_idempotent_replay_observes_complete_plan_before_dispatch() -> None:
    submit_started = asyncio.Event()
    submit_release = asyncio.Event()
    adapter = _FakeAdapter(
        "runpod",
        submit_started=submit_started,
        submit_release=submit_release,
    )
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    first_task = asyncio.create_task(
        service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
            idempotency_key="concurrent-key",
        )
    )
    await submit_started.wait()
    replay_task = asyncio.create_task(
        service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
            idempotency_key="concurrent-key",
        )
    )
    await asyncio.sleep(0)
    assert not replay_task.done()
    submit_release.set()
    first, replay = await asyncio.gather(first_task, replay_task)

    assert replay.id == first.id
    assert replay.route_plan_id == first.route_plan_id
    assert replay.input == {"messages": [], "max_output_tokens": 64}
    assert budget.admissions == 1
    assert adapter.submit_calls == 1


async def test_submit_and_cancel_are_serialized_and_provider_cancel_runs_once() -> None:
    submit_started = asyncio.Event()
    submit_release = asyncio.Event()
    adapter = _FakeAdapter(
        "runpod",
        submit_started=submit_started,
        submit_release=submit_release,
    )
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    submit_task = asyncio.create_task(
        service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
        )
    )
    await submit_started.wait()
    workload_id = next(iter(workloads.items))
    cancel_tasks = [
        asyncio.create_task(service.cancel_job(workload_id)),
        asyncio.create_task(service.cancel_job(workload_id)),
    ]
    await asyncio.sleep(0)
    assert not any(task.done() for task in cancel_tasks)
    submit_release.set()

    submitted, first_cancel, second_cancel = await asyncio.gather(submit_task, *cancel_tasks)

    assert submitted.external_job_id == "job-runpod"
    assert first_cancel.state is WorkloadState.CANCELLED
    assert second_cancel.state is WorkloadState.CANCELLED
    assert adapter.submit_calls == 1
    assert adapter.cancel_calls == 1
    assert [item["operation"] for item in second_cancel.route_attempts] == ["submit", "cancel"]


async def test_cancelled_async_submit_is_terminal_and_audited_without_fallback() -> None:
    submit_started = asyncio.Event()
    submit_release = asyncio.Event()
    first = _FakeAdapter(
        "runpod",
        submit_started=submit_started,
        submit_release=submit_release,
    )
    second = _FakeAdapter("together")
    registry = ProviderRegistry()
    registry.register(first)
    registry.register(second)
    service, _, workloads = _service(
        [
            _provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10),
            _provider("provider_together", ProviderAdapterId.TOGETHER, priority=2, latency_ms=20),
        ],
        registry,
    )
    task = asyncio.create_task(
        service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
        )
    )
    await submit_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    workload = next(iter(workloads.items.values()))
    assert workload.state is WorkloadState.FAILED
    assert workload.route_attempts[0]["outcome"] == "cancelled"
    assert workload.cost_actual_usd is None
    assert workload.cost_actual_provenance is None
    assert first.submit_calls == 1
    assert second.submit_calls == 0


async def test_ambiguous_async_submit_never_falls_back_to_a_second_write() -> None:
    first = _FakeAdapter("runpod", fail_submit=True)
    second = _FakeAdapter("together")
    registry = ProviderRegistry()
    registry.register(first)
    registry.register(second)
    service, budget, workloads = _service(
        [
            _provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10),
            _provider("provider_together", ProviderAdapterId.TOGETHER, priority=2, latency_ms=20),
        ],
        registry,
    )

    with pytest.raises(RouteProviderInvocationError) as exc_info:
        await service.submit_job(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
        )

    assert budget.admissions == 1
    assert first.submit_calls == 1
    assert second.submit_calls == 0
    assert "ambiguous async submission" not in str(exc_info.value)
    assert next(iter(workloads.items.values())).fallback_chain == ["provider_runpod"]


async def test_concurrent_previews_use_one_immutable_snapshot_per_call() -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )

    plans = await asyncio.gather(
        *(
            service.preview(
                capability_id="llm.chat",
                payload={"messages": [], "max_output_tokens": 64},
                now=_NOW,
            )
            for _ in range(25)
        )
    )

    assert len({plan.plan_id for plan in plans}) == 1
    assert budget.admissions == 0
    assert adapter.sync_calls == 0


def test_route_budget_quote_round_trips_through_persisted_cost_read() -> None:
    from pitwall.cost.read_models import WorkloadCostRead
    from pitwall.routing.production import RouteBudgetQuote

    plan = _plan(
        [
            _provider("prov-a", ProviderAdapterId.RUNPOD, priority=1, latency_ms=100),
            _provider("prov-b", ProviderAdapterId.RUNPOD, priority=2, latency_ms=200),
        ]
    )
    quote = RouteBudgetQuote(plan, fallback_spend=True)

    serialized = quote.to_serializable_dict()

    assert set(serialized) == {
        "model",
        "components",
        "estimate",
        "ceiling",
        "confidence",
        "provenance",
        "currency",
        "assumptions",
    }
    read = WorkloadCostRead.from_persisted(
        cost_estimate_usd=quote.estimate(),
        cost_ceiling_usd=quote.upper_bound(),
        cost_quote=serialized,
        cost_actual_usd=None,
    )
    assert read.estimate == quote.estimate()
    assert read.ceiling == quote.upper_bound()
    assert [component.name for component in read.components] == [
        "provider_attempt_1",
        "provider_attempt_2",
    ]
    assert any("plan_" in item for item in read.assumptions)


def _gateway_provider(
    provider_id: str,
    *,
    priority: int = 1,
    model_id: str = "beta/b1",
    cost_mode: str = "per_token",
) -> Provider:
    cost = (
        {"kind": "per_token", "per_million_input_tokens": "1", "per_million_output_tokens": "1"}
        if cost_mode == "per_token"
        else {"kind": cost_mode}
    )
    return Provider(
        id=provider_id,
        capability_id="cap_chat",
        name=provider_id,
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=priority,
        updated_at=_NOW,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.API,
        config={
            "cost": cost,
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": model_id,
                "catalog": {
                    "pool_key": "alpha-pool",
                    "free_type": "recurring-monthly",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                },
            },
        },
    )


async def test_quota_exhausted_failure_locks_the_gateway_model_for_next_plan() -> None:
    from pitwall.providers.gateway import QuotaExhausted
    from pitwall.routing import lockout

    reset_at = _NOW + dt.timedelta(hours=2)
    failure = QuotaExhausted(
        429,
        "daily quota exhausted",
        reason="quota_exhausted",
        reset_at=reset_at,
    )
    adapter = _FakeAdapter(
        "openai_gateway",
        sync_exception=failure,
    )
    registry = ProviderRegistry()
    registry.register(adapter)
    table = LockoutTable()
    service, _, workloads = _service(
        [_gateway_provider("prov_gw", priority=1)],
        registry,
    )
    monkeypatch_set = pytest.MonkeyPatch()
    try:
        monkeypatch_set.setattr(lockout, "get_lockout_table", lambda: table)
        with pytest.raises(RoutePlanningError):
            await service.execute_sync(
                capability_id="llm.chat",
                payload={"messages": [], "max_output_tokens": 64},
            )
    finally:
        monkeypatch_set.undo()

    workload = next(iter(workloads.items.values()))
    assert workload.state is WorkloadState.FAILED
    attempt = workload.route_attempts[0]
    assert attempt["outcome"] == "failed"
    assert attempt["provider_id"] == "prov_gw"

    state = table.snapshot()["prov_gw/beta/b1"]
    assert state["locked_until"] is not None
    assert dt.datetime.fromisoformat(state["locked_until"]) == reset_at
    assert state["reason"] == "quota_exhausted"


async def test_lockout_state_eliminates_gateway_provider_in_next_plan() -> None:
    from pitwall.routing import lockout

    table = LockoutTable()
    table.record_failure(
        LockoutKey("prov_gw", "beta/b1"),
        now=_NOW - dt.timedelta(minutes=1),
        reason="rate_limit_exceeded",
    )
    adapter = _FakeAdapter("openai_gateway")
    together_adapter = _FakeAdapter("together")
    registry = ProviderRegistry()
    registry.register(adapter)
    registry.register(together_adapter)
    metered = _provider(
        "provider_together",
        ProviderAdapterId.TOGETHER,
        priority=2,
        latency_ms=20,
    )
    service, _, _ = _service(
        [_gateway_provider("prov_gw", priority=1), metered],
        registry,
    )
    monkeypatch_set = pytest.MonkeyPatch()
    try:
        monkeypatch_set.setattr(lockout, "get_lockout_table", lambda: table)
        result = await service.execute_sync(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
        )
    finally:
        monkeypatch_set.undo()

    assert result.workload.provider_id == "provider_together"
    assert adapter.sync_calls == 0
    eliminated_reasons = {item.provider_id: item.reason for item in result.plan.eliminated}
    assert eliminated_reasons.get("prov_gw") == "model_locked_out"


async def test_successful_call_clears_backoff_via_record_success() -> None:
    from pitwall.routing import lockout

    table = LockoutTable()
    past_failure_at = _NOW - dt.timedelta(hours=2)
    table.record_failure(
        LockoutKey("prov_gw", "beta/b1"),
        now=past_failure_at,
        reason="rate_limit_exceeded",
    )
    assert not table.is_locked(LockoutKey("prov_gw", "beta/b1"), now=_NOW)

    adapter = _FakeAdapter("openai_gateway")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, _ = _service(
        [_gateway_provider("prov_gw", priority=1)],
        registry,
    )
    monkeypatch_set = pytest.MonkeyPatch()
    try:
        monkeypatch_set.setattr(lockout, "get_lockout_table", lambda: table)
        result = await service.execute_sync(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 64},
        )
    finally:
        monkeypatch_set.undo()

    assert result.workload.state is WorkloadState.COMPLETED
    state = table.snapshot()["prov_gw/beta/b1"]
    assert state["failures"] == 0
    assert state["locked_until"] is None


def test_keyless_floor_survives_when_paid_rungs_are_budget_blocked() -> None:
    """§9.6 emergency descent: zero-priced keyless pools stay attemptable when metered rungs are over budget."""
    metered = _gateway_provider("gw_metered", priority=1, cost_mode="per_token")
    keyless = _gateway_provider("gw_keyless", priority=2, cost_mode="zero", model_id="beta/free")
    keyless.config["gateway"]["catalog"].update(
        {"free_type": "keyless", "hard_stop_guaranteed": True}
    )

    from pitwall.routing.context import PlanningContext
    from pitwall.routing.quota import QuotaRecord, QuotaSnapshot

    evidence = QuotaSnapshot(
        records=(
            QuotaRecord(
                provider_id="gw_keyless",
                pool_key="alpha-pool",
                free_type="keyless",
                window_start=None,
                reset_at=None,
                budget_units=None,
                used_units=Decimal("0"),
                tos_verdict="ok",
                evidence={},
            ),
        )
    )
    plan = build_production_plan(
        capability=_capability(),
        providers=[metered, keyless],
        payload={"messages": [{"role": "user", "content": "hello"}], "max_output_tokens": 64},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode="weighted",
        weights=RoutingWeights(cost="1", latency="0.001"),
        max_attempts=3,
        budget_limit_usd=Decimal("0.0000001"),
        context=PlanningContext.replay(
            now=_NOW,
            providers=[metered, keyless],
            capability=_capability(),
            quota_snapshot=evidence,
        ),
    )

    assert [(item.provider_id, item.stage, item.reason) for item in plan.eliminated] == [
        ("gw_metered", "budget", "budget_unavailable")
    ]
    assert [candidate.provider_id for candidate in plan.attempts] == ["gw_keyless"]
    assert plan.selected_provider_id == "gw_keyless"


@pytest.mark.parametrize(
    ("monthly_budget_usd", "per_request_max_usd", "reason"),
    [
        (Decimal("0.000001"), Decimal("100"), "monthly_budget"),
        (Decimal("100"), Decimal("0.000001"), "per_request_cap"),
    ],
)
async def test_budget_eliminated_route_raises_budget_rejected_before_egress(
    monthly_budget_usd: Decimal,
    per_request_max_usd: Decimal,
    reason: str,
) -> None:
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    budget.monthly_budget_usd = monthly_budget_usd
    budget.per_request_max_usd = per_request_max_usd

    with pytest.raises(BudgetRejected) as exc_info:
        await service.execute_sync(
            capability_id="llm.chat",
            payload={"input_tokens": 1000, "max_output_tokens": 1000},
        )

    assert exc_info.value.reason == reason
    assert exc_info.value.snapshot.estimate_usd > min(monthly_budget_usd, per_request_max_usd)
    assert budget.admissions == 0
    assert adapter.sync_calls == 0


def test_all_free_pools_exhausted_raises_with_the_escape_hatch_attached() -> None:
    """R5: when every zero-priced pool is exhausted and nothing metered remains, the
    NoExecutableRouteError carries the prong-3 ``pitwall serve`` proposal."""
    from pitwall.routing.context import PlanningContext
    from pitwall.routing.quota import QuotaRecord, QuotaSnapshot

    keyless = _gateway_provider("gw_keyless", priority=1, cost_mode="zero", model_id="beta/free")
    keyless.config["gateway"]["catalog"].update(
        {"free_type": "keyless", "hard_stop_guaranteed": True}
    )
    reset_at = _NOW + dt.timedelta(hours=3)
    exhausted = QuotaSnapshot(
        records=(
            QuotaRecord(
                provider_id="gw_keyless",
                pool_key="alpha-pool",
                free_type="keyless",
                window_start=_NOW - dt.timedelta(hours=1),
                reset_at=reset_at,
                budget_units=Decimal("100"),
                used_units=Decimal("100"),
                tos_verdict="ok",
                evidence={},
            ),
        )
    )

    with pytest.raises(NoExecutableRouteError) as exc_info:
        build_production_plan(
            capability=_capability(),
            providers=[keyless],
            payload={"messages": [{"role": "user", "content": "hello"}], "max_output_tokens": 64},
            operation=RoutingOperation.SYNC_INFERENCE,
            registry=get_default_registry(),
            now=_NOW,
            mode="weighted",
            weights=RoutingWeights(cost="1", latency="0.001"),
            max_attempts=3,
            context=PlanningContext.replay(
                now=_NOW, providers=[keyless], capability=_capability(), quota_snapshot=exhausted
            ),
            own_pod_usd_per_hour=Decimal("0.50"),
            gpu_class="RTX 4090",
        )

    assert [(e.provider_id, e.reason) for e in exc_info.value.eliminations] == [
        ("gw_keyless", "quota_ineligible")
    ]
    hatch = exc_info.value.escape_hatch
    assert hatch is not None
    assert "pitwall serve" in hatch.proposed_command
    assert "RTX 4090" in hatch.proposed_command


def test_compute_routes_only_leasable_runpod_providers() -> None:
    """A RunPod serverless provider cannot be leased; compute must skip it for a pod lease."""
    serverless = _provider(
        "provider_serverless",
        ProviderAdapterId.RUNPOD,
        priority=0,
        latency_ms=1,
        config={"cost": {"per_second_active": "0.001"}},
    )
    pod = _provider(
        "provider_pod",
        ProviderAdapterId.RUNPOD,
        priority=5,
        latency_ms=100,
        provider_type=ProviderType.POD_LEASE,
        config={"cost": {"per_second_active": "0.001"}},
    )

    plan = build_production_plan(
        capability=_capability(cost_mode=CostMode.PER_SECOND),
        providers=[serverless, pod],
        payload={"expected_seconds": 60},
        operation=RoutingOperation.COMPUTE,
        registry=get_default_registry(),
        now=_NOW,
        mode="priority",
        weights=RoutingWeights(),
        max_attempts=3,
    )

    assert plan.selected_provider_id == "provider_pod"
    assert [(item.provider_id, item.reason) for item in plan.eliminated] == [
        ("provider_serverless", "provider_type_unsupported")
    ]


async def test_cancelling_a_workload_that_is_not_an_async_job_is_not_cancellable() -> None:
    from pitwall.routing.production import JobNotCancellableError

    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, _, workloads = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    submitted = await service.submit_job(
        capability_id="llm.chat",
        payload={"messages": [], "max_output_tokens": 64},
        idempotency_key="not-an-async-job",
    )
    workloads.items[submitted.id] = submitted.model_copy(
        update={"type": "sync", "state": WorkloadState.RUNNING}
    )

    with pytest.raises(JobNotCancellableError, match="not an asynchronous job"):
        await service.cancel_job(submitted.id)


@pytest.mark.parametrize(
    ("operation", "adapter_id"),
    [("execute_sync", ProviderAdapterId.TOGETHER), ("submit_job", ProviderAdapterId.RUNPOD)],
)
async def test_a_request_that_loses_the_idempotency_race_is_validated_like_a_replay(
    operation: str, adapter_id: ProviderAdapterId
) -> None:
    """Finding #14: pre-flight checks the key's workload, but a request whose pre-flight ran
    before a concurrent winner committed found the key at admission and returned the winner's
    workload unchecked, even for a different payload."""
    registry = ProviderRegistry()
    registry.register(_FakeAdapter(adapter_id.value))
    service, budget, workloads = _service(
        [_provider("provider_x", adapter_id, priority=1, latency_ms=10)], registry
    )
    run = getattr(service, operation)
    payload = {"messages": [], "max_output_tokens": 64}

    def workload_id(result: Any) -> str:
        return str(result.workload.id if operation == "execute_sync" else result.id)

    winner_id = workload_id(
        await run(capability_id="llm.chat", payload=payload, idempotency_key="raced-key")
    )

    async def not_yet_committed(key: str) -> None:
        return None

    async def lost_the_race(**kwargs: Any) -> BudgetAdmission:
        return BudgetAdmission(workload_id=winner_id, is_new=False)

    workloads.get_by_idempotency_key = not_yet_committed  # type: ignore[method-assign]  # reason: test double replaces the gate method
    budget.try_launch_admission = lost_the_race  # type: ignore[method-assign]  # reason: test double replaces the gate method

    with pytest.raises(RoutePlanningError, match="different payload"):
        await run(
            capability_id="llm.chat",
            payload={"messages": [], "max_output_tokens": 65},
            idempotency_key="raced-key",
        )
    same = await run(capability_id="llm.chat", payload=payload, idempotency_key="raced-key")
    assert workload_id(same) == winner_id
