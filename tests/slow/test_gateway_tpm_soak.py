"""Task 17 TPM soak (research §14 Phase 2 exit): zero paid leakage, bounded 429s.

Drives the real ``ProductionRoutingService`` over the real ``GatewayProvider``
adapter against a fake OpenAI server (``httpx.MockTransport``) that rate limits
each pool after ``ok_each`` requests, across a simulated multi-pool day on a
fixed clock. Hermetic; excluded from the fast suite via the ``slow`` marker.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from pitwall.config import PitwallSettings
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
    WorkloadState,
)
from pitwall.core.models import Capability, Provider, Workload
from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL, BudgetAdmission
from pitwall.cost.budget_limits import BudgetLimits
from pitwall.providers.gateway import GatewayProvider
from pitwall.providers.registry import ProviderRegistry
from pitwall.routing.lockout import get_lockout_table
from pitwall.routing.production import ProductionRoutingService
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = pytest.mark.slow

_START = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
_MINUTE = dt.timedelta(minutes=1)
_SPEND_COALESCE = "COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)"
assert _SPEND_COALESCE in MONTH_TO_DATE_SPEND_SQL  # the spend oracle below mirrors this SQL


class _Clock:
    """Fixed clock: one simulated minute per ``tick`` call."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> dt.datetime:
        return self.now

    def tick(self) -> None:
        self.now += _MINUTE


class _FakeOpenAI:
    """OpenAI-compatible upstream: 200 for the first ``ok_each`` calls per model, then 429."""

    def __init__(self, *, ok_each: int) -> None:
        self.ok_each = ok_each
        self.oks: dict[str, int] = {}
        self.rate_limited: dict[str, int] = {}

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        model = str(json.loads(request.content)["model"])
        served = self.oks.get(model, 0)
        if served >= self.ok_each:
            self.rate_limited[model] = self.rate_limited.get(model, 0) + 1
            return httpx.Response(
                429,
                headers={"retry-after": "60"},
                json={"error": {"message": "rate limited"}},
            )
        self.oks[model] = served + 1
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "pong"}}],
                "model": model,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )


def _capability() -> Capability:
    return Capability(
        id="cap_coding",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        source=CapabilitySource.YAML,
        enabled=True,
        created_at=_START,
        updated_at=_START,
    )


def _provider(pool_name: str, model: str, *, priority: int) -> Provider:
    return Provider(
        id=f"prov_{pool_name}",
        capability_id="cap_coding",
        name=f"gw-{pool_name}",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=priority,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.YAML,
        updated_at=_START,
        config={
            "cost": {"mode": "zero"},
            "expected_latency_ms": 5,
            "gateway": {
                "base_url": "https://soak.test/v1",
                "model_id": model,
                "catalog": {
                    "free_type": "keyless",
                    "tos": "ok",
                    "trains_on_prompts": False,
                    "hard_stop_guaranteed": True,
                    "pool_key": pool_name,
                    "monthly_tokens": 0,
                    "credit_tokens": 0,
                    "eligibility_gate": None,
                },
            },
        },
    )


class _State:
    def __init__(self, providers: list[Provider]) -> None:
        self.quota_rows: list[dict[str, Any]] = [
            {
                "provider_id": provider.id,
                "pool_key": provider.config["gateway"]["catalog"]["pool_key"],
                "free_type": "keyless",
                "window_start": _START,
                "reset_at": None,
                "budget_units": None,
                "used_units": "0",
                "tos_verdict": "ok",
                "evidence": {},
                "updated_at": _START,
            }
            for provider in providers
        ]
        self.workloads = _WorkloadRepo()


class _FakeConnection:
    def __init__(self, state: _State) -> None:
        self.state = state

    async def fetch(self, query: str, *_args: Any) -> list[dict[str, Any]]:
        if "provider_quotas" in query:
            return [dict(row) for row in self.state.quota_rows]
        raise AssertionError(f"unexpected fetch: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "pg_advisory_lock" in query or "pg_advisory_unlock" in query:
            return "SELECT 1"
        if "provider_quotas" in query or "provider_quota_samples" in query:
            return "UPDATE 1"
        workload = self.state.workloads.items.get(args[0]) if args else None
        if workload is None:
            return "UPDATE 0"
        if "SET state = 'running', started_at = $2" in query:
            patch: dict[str, Any] = {
                "state": WorkloadState.RUNNING,
                "started_at": args[1],
                "input": args[2],
                "input_bytes": args[3],
                "fallback_chain": args[4],
                "route_plan_id": args[5],
                "route_plan": args[6],
            }
        elif "SET route_attempts = route_attempts" in query:
            patch = {"route_attempts": [*workload.route_attempts, *args[1]]}
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
                "cost_actual_usd": args[8] if args[8] is not None else workload.cost_actual_usd,
                "cost_actual_provenance": (
                    args[9] if args[9] is not None else workload.cost_actual_provenance
                ),
                "cost_reconciled_at": (
                    args[2] if args[8] is not None else workload.cost_reconciled_at
                ),
            }
        elif "SET state = 'failed'" in query:
            patch = {
                "state": WorkloadState.FAILED,
                "completed_at": args[1],
                "execution_ms": args[2],
                "error": args[3],
            }
        else:
            return "UPDATE 0"
        self.state.workloads.items[workload.id] = workload.model_copy(update=patch)
        return "UPDATE 1"


class _FakeAcquire:
    def __init__(self, state: _State) -> None:
        self._connection = _FakeConnection(state)

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakePool:
    def __init__(self, state: _State) -> None:
        self._state = state

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._state)


class _WorkloadRepo:
    def __init__(self) -> None:
        self.items: dict[str, Workload] = {}

    async def get(self, workload_id: str) -> Workload | None:
        return self.items.get(workload_id)

    async def get_on_connection(self, _conn: Any, workload_id: str) -> Workload | None:
        return self.items.get(workload_id)

    async def get_by_idempotency_key(self, key: str | None) -> Workload | None:
        if key is None:
            return None
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
        self.monthly_budget_usd = Decimal("100")
        self.per_request_max_usd = Decimal("100")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("0")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return BudgetLimits(self.monthly_budget_usd, self.per_request_max_usd, "environment")

    async def try_launch_admission(self, **kwargs: Any) -> BudgetAdmission:
        quote = kwargs["estimate_usd"]
        workload = Workload(
            id=f"wkl_{len(self.workloads.items) + 1}",
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
            await callback(_FakeConnection(self._state_for_callback), workload.id)
        return BudgetAdmission(workload_id=workload.id, is_new=True)

    def bind(self, state: _State) -> None:
        self._state_for_callback = state


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

    async def get(self, provider_id: str) -> Provider | None:
        return next((item for item in self.providers if item.id == provider_id), None)

    async def list(self, **_: object) -> list[Provider]:
        return list(self.providers)


def _service(providers: list[Provider], state: _State, clock: _Clock) -> ProductionRoutingService:
    registry = ProviderRegistry()
    registry.register(GatewayProvider(transport=httpx.MockTransport(_SERVER), environ={}))
    budget = _BudgetGate(state.workloads)
    budget.bind(state)
    return ProductionRoutingService(
        _FakePool(state),
        settings=PitwallSettings(
            pitwall_routing_mode="priority",
            pitwall_routing_max_attempts=3,
        ),
        capability_repository=_CapabilityRepo(_capability()),
        provider_repository=_ProviderRepo(providers),
        workload_repository=state.workloads,
        registry=registry,
        guardrails=PreSpendInspectionService(),
        budget_gate=budget,
        now=clock,
    )


def _coalesce_cost(workload: Workload) -> Decimal:
    """MONTH_TO_DATE_SPEND_SQL's COALESCE evaluated for one workload row."""

    for value in (workload.cost_actual_usd, workload.cost_ceiling_usd, workload.cost_estimate_usd):
        if value is not None:
            return value
    return Decimal("0")


async def test_tpm_soak_zero_paid_spend_and_bounded_429_rate() -> None:
    get_lockout_table().clear()
    providers = [
        _provider("pool-a", "a/m1", priority=1),
        _provider("pool-b", "b/m1", priority=2),
        _provider("pool-c", "c/m1", priority=3),
    ]
    state = _State(providers)
    clock = _Clock()
    service = _service(providers, state, clock)
    minutes = 6
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": "soak"}],
        "max_output_tokens": 4,
    }

    try:
        completed = 0
        for _ in range(minutes):
            result = await service.execute_sync(capability_id="coding.chat", payload=payload)
            assert result.workload.state is WorkloadState.COMPLETED
            completed += 1
            clock.tick()

        assert completed == minutes

        # Zero paid leakage: MONTH_TO_DATE_SPEND_SQL stays 0 over the soak's workloads.
        assert sum(
            (_coalesce_cost(item) for item in state.workloads.items.values()), Decimal("0")
        ) == Decimal("0")
        for item in state.workloads.items.values():
            assert item.cost_estimate_usd == Decimal("0")
            assert item.cost_ceiling_usd == Decimal("0")
            assert item.cost_actual_usd in (None, Decimal("0"))

        # Bounded 429 rate: at most one probe per lockout window per pool.
        for provider in providers:
            model = str(provider.config["gateway"]["model_id"])
            assert _SERVER.rate_limited.get(model, 0) <= 1 + minutes

        # The 429s actually engaged the lockout ladder instead of hammering upstream.
        snapshot = get_lockout_table().snapshot()
        assert any(entry["reason"] == "rate_limit_exceeded" for entry in snapshot.values()), (
            snapshot
        )
    finally:
        get_lockout_table().clear()


_SERVER = _FakeOpenAI(ok_each=2)
