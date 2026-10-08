"""Autopilot shadow proposals: PROPOSE_* actions are advisory and never applied."""

from __future__ import annotations

import datetime as dt
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pitwall.autopilot import (
    ActionApplyResult,
    AutopilotActionKind,
    AutopilotController,
    AutopilotHardLimits,
    AutopilotMode,
    AutopilotSignal,
)
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Provider
from pitwall.cost.simulator import ProngOption, WhatIfSimulator, WhatIfWorkload
from pitwall.policy import PolicySet
from pitwall.routing import PlanningContext, RoutingRequest
from pitwall.routing.cascade_seed import escape_hatch_message
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot

_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
_RESET_AT = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
_CAP_ID = "cap_shadow"
_CAP_NAME = "coding.chat"


class RecordingExecutor:
    def __init__(self) -> None:
        self.applied: list[str] = []

    def apply(self, action: Any) -> ActionApplyResult:
        self.applied.append(action.action_id)
        return ActionApplyResult(action_id=action.action_id, applied=True, message="recorded")


def _capability() -> Capability:
    return Capability(
        id=_CAP_ID,
        name=_CAP_NAME,
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        served_model_id="coding.chat",
        source=CapabilitySource.YAML,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _keyless() -> Provider:
    return Provider(
        id="prov_keyless",
        capability_id=_CAP_ID,
        name="keyless",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"kind": "zero"},
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "keyless/m1",
                "catalog": {
                    "free_type": "keyless",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                    "pool_key": "keyless-pool",
                },
            },
        },
        priority=10,
        updated_at=_NOW,
    )


def _budgeted_free() -> Provider:
    return Provider(
        id="prov_budgeted",
        capability_id=_CAP_ID,
        name="budgeted",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"kind": "zero"},
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "budgeted/b1",
                "catalog": {
                    "free_type": "recurring-monthly",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                    "pool_key": "budgeted-pool",
                    "monthly_tokens": 5_000_000,
                },
            },
        },
        priority=20,
        updated_at=_NOW,
    )


def _exhausted_snapshot() -> QuotaSnapshot:
    return QuotaSnapshot(
        records=(
            QuotaRecord(
                provider_id="prov_keyless",
                pool_key="keyless-pool",
                free_type="keyless",
                window_start=None,
                reset_at=_RESET_AT,
                budget_units=None,
                used_units=Decimal("0"),
                tos_verdict="ok",
                evidence={},
            ),
            QuotaRecord(
                provider_id="prov_budgeted",
                pool_key="budgeted-pool",
                free_type="recurring-monthly",
                window_start=_RESET_AT - dt.timedelta(days=10),
                reset_at=_RESET_AT,
                budget_units=Decimal(5_000_000),
                used_units=Decimal(5_000_000),
                tos_verdict="ok",
                evidence={},
            ),
        )
    )


class _CandidateView:
    def __init__(self, *, provider_id: str, provider: Provider) -> None:
        self.provider_id = provider_id
        self.provider = provider


class _EliminatedView:
    def __init__(self, *, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason


class _PlanView:
    """Plan shape required by escape_hatch_message (mirrors test_cascade_seed)."""

    def __init__(
        self,
        *,
        ranked: tuple[_CandidateView, ...],
        eliminated: tuple[_EliminatedView, ...],
    ) -> None:
        self._ranked = ranked
        self._eliminated = eliminated
        self.capability_snapshot = _capability()

    @property
    def ranked_candidates(self) -> tuple[_CandidateView, ...]:
        return self._ranked

    @property
    def dropped_provider_reasons(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self._eliminated:
            out.setdefault(item.provider_id, []).append(item.reason)
        return out


def _plan_all_quota_ineligible() -> _PlanView:
    keyless = _keyless()
    budgeted = _budgeted_free()
    return _PlanView(
        ranked=(
            _CandidateView(provider_id=keyless.id, provider=keyless),
            _CandidateView(provider_id=budgeted.id, provider=budgeted),
        ),
        eliminated=(
            _EliminatedView(provider_id=keyless.id, reason="quota_ineligible"),
            _EliminatedView(provider_id=budgeted.id, reason="quota_ineligible"),
        ),
    )


def _escape_hatch_message() -> str:
    message = escape_hatch_message(
        _plan_all_quota_ineligible(),
        quota_snapshot=_exhausted_snapshot(),
        now=_NOW,
        own_pod_usd_per_hour=Decimal("0.50"),
        gpu_class="RTX 4090",
    )
    assert message is not None
    return message


def _controller(
    *,
    mode: AutopilotMode = AutopilotMode.SHADOW,
    executor: RecordingExecutor | None = None,
) -> AutopilotController:
    context = PlanningContext.replay(
        now=_NOW,
        providers=[
            {
                "id": "prov_autopilot",
                "capability_id": _CAP_ID,
                "name": "prov_autopilot",
                "provider_type": ProviderType.SERVERLESS_QUEUE.value,
                "priority": 1,
                "enabled": True,
                "health_status": "healthy",
                "cold_start_p50_ms": 0,
                "recent_error_rate": 0.0,
                "config": {"cost": {"per_second_active": "0.001"}},
            }
        ],
        capability=_capability(),
    )
    simulator = WhatIfSimulator(context)
    return AutopilotController(
        policy_set=PolicySet(policies=[]),
        simulator=simulator,
        executor=executor,
        mode=mode,
        limits=AutopilotHardLimits(max_actions_per_run=5),
    )


def _own_serve_row(usd_per_million: str = "4.800000") -> ProngOption:
    return ProngOption(
        prong="own_serve",
        usd_per_million_tokens=Decimal(usd_per_million),
        coverage_pct=100.0,
        note="own pod RTX 4090 x1 at $2.00/hr up 24h",
    )


def _metered_row(usd_per_million: str = "1.000000") -> ProngOption:
    return ProngOption(
        prong="metered",
        usd_per_million_tokens=Decimal(usd_per_million),
        coverage_pct=100.0,
        note="deepseek metered blended 50/50 prompt/completion",
    )


def _workload() -> WhatIfWorkload:
    return WhatIfWorkload(request=RoutingRequest(capability_name=_CAP_NAME, capability_id=_CAP_ID))


def test_shadow_mode_yields_one_propose_serve_when_all_free_pools_exhausted() -> None:
    executor = RecordingExecutor()
    controller = _controller(mode=AutopilotMode.SHADOW, executor=executor)

    result = controller.run(now=_NOW, escape_hatch_message=_escape_hatch_message())

    assert executor.applied == []
    assert result.applied_count == 0
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.action.action_kind is AutopilotActionKind.PROPOSE_SERVE
    assert decision.action.executed is False
    assert decision.outcome == "shadowed"
    assert decision.action.reason == _escape_hatch_message()
    assert decision.to_dict()["action"]["executed"] is False


def test_shadow_mode_proposes_seat_switch_when_metered_beats_own_pod() -> None:
    controller = _controller(mode=AutopilotMode.SHADOW)
    comparison = (
        _own_serve_row("4.800000"),
        ProngOption(
            prong="free",
            usd_per_million_tokens=Decimal("0.000000"),
            coverage_pct=40.0,
            note="free pools cover 40.0% of 10000000 tokens/day",
        ),
        _metered_row("1.000000"),
    )

    result = controller.run(now=_NOW, prong_comparison=comparison)

    assert result.applied_count == 0
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.action.action_kind is AutopilotActionKind.PROPOSE_SEAT_SWITCH
    assert decision.action.executed is False
    assert decision.outcome == "shadowed"
    assert decision.action.reason == "metered 1.000000/M beats own-pod"


def test_no_seat_switch_proposal_when_own_pod_is_cheaper() -> None:
    controller = _controller(mode=AutopilotMode.SHADOW)
    comparison = (_own_serve_row("0.960000"), _metered_row("1.000000"))

    result = controller.run(now=_NOW, prong_comparison=comparison)

    assert result.decisions == ()


def test_apply_mode_never_applies_proposal_actions() -> None:
    executor = RecordingExecutor()
    controller = _controller(mode=AutopilotMode.APPLY, executor=executor)

    result = controller.run(
        now=_NOW,
        escape_hatch_message=_escape_hatch_message(),
        prong_comparison=(_own_serve_row(), _metered_row()),
    )

    assert executor.applied == []
    assert result.applied_count == 0
    kinds = [decision.action.action_kind for decision in result.decisions]
    assert kinds == [
        AutopilotActionKind.PROPOSE_SERVE,
        AutopilotActionKind.PROPOSE_SEAT_SWITCH,
    ]
    assert all(decision.action.executed is False for decision in result.decisions)
    assert all(decision.outcome == "shadowed" for decision in result.decisions)


def test_propose_signals_passing_through_the_gate_path_are_never_applied() -> None:
    executor = RecordingExecutor()
    controller = _controller(mode=AutopilotMode.APPLY, executor=executor)
    signal = AutopilotSignal(
        signal_id="sig-propose",
        source="unit-test",
        action_kind=AutopilotActionKind.PROPOSE_SERVE,
        target_kind="capability",
        target_id="own-serve",
        reason="all free pools exhausted",
        simulation_workloads=(_workload(),),
    )

    result = controller.run(now=_NOW, signals=[signal])

    assert executor.applied == []
    assert result.applied_count == 0
    assert len(result.decisions) == 1
    assert result.decisions[0].outcome == "shadowed"
    assert result.decisions[0].action.executed is False
