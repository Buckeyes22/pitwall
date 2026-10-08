"""Closed-loop Autopilot controller with policy, simulation, and budget rails."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from pitwall.autopilot.schema import (
    ActionApplyResult,
    AutopilotAction,
    AutopilotActionKind,
    AutopilotDecision,
    AutopilotGateResult,
    AutopilotHardLimits,
    AutopilotMode,
    AutopilotRunResult,
    AutopilotSignal,
    is_proposal_action_kind,
)
from pitwall.cost.circuit_breaker import CircuitBreakerDecision
from pitwall.policy import PolicyEvaluationResult, PolicySet, evaluate_policies

if TYPE_CHECKING:
    from pitwall.cost.simulator import ProngOption, WhatIfBatchProjection, WhatIfWorkload


class AutopilotExecutor(Protocol):
    """Applies an action after all controller gates pass."""

    def apply(self, action: AutopilotAction) -> ActionApplyResult:
        """Apply *action* and return an audited result."""


class _WhatIfSimulator(Protocol):
    def simulate_workloads(
        self,
        workloads: Iterable[WhatIfWorkload],
    ) -> WhatIfBatchProjection:
        """Return aggregate cost projection for the supplied workloads."""


class AutopilotSignalSource(Protocol):
    """Collects recommendation, scorecard, or drift signals."""

    def collect(self, now: dt.datetime) -> Iterable[AutopilotSignal]:
        """Return signals for the supplied deterministic control-loop timestamp."""


@dataclass(frozen=True, slots=True)
class _PolicySnapshot:
    providers: tuple[Mapping[str, object], ...]
    workloads: tuple[Mapping[str, object], ...]
    capability: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _HardLimitEvaluation:
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class _SimulationEvaluation:
    passed: bool
    reason: str
    projection: WhatIfBatchProjection | None = None


class AutopilotController:
    """Gather signals, propose actions, gate them, and optionally apply them."""

    def __init__(
        self,
        *,
        policy_set: PolicySet,
        simulator: _WhatIfSimulator,
        executor: AutopilotExecutor | None = None,
        mode: AutopilotMode | str = AutopilotMode.SHADOW,
        limits: AutopilotHardLimits | None = None,
    ) -> None:
        self._policy_set = policy_set
        self._simulator = simulator
        self._executor = executor
        self._mode = AutopilotMode(mode)
        self._limits = limits or AutopilotHardLimits()

    def run(
        self,
        *,
        now: dt.datetime,
        signals: Iterable[AutopilotSignal] = (),
        sources: Iterable[AutopilotSignalSource] = (),
        breaker_decision: CircuitBreakerDecision | None = None,
        escape_hatch_message: str | None = None,
        prong_comparison: Sequence[ProngOption] = (),
    ) -> AutopilotRunResult:
        """Run one deterministic Autopilot control-loop iteration."""

        now_utc = _normalize_utc(now)
        collected = self._collect_signals(now=now_utc, signals=signals, sources=sources)
        actions = tuple(AutopilotAction.from_signal(signal) for signal in collected)

        decisions: list[AutopilotDecision] = []
        applied_count = 0
        accepted_reserved_usd = Decimal("0.000000")
        for action in actions:
            decision = self._decide(
                action=action,
                applied_count=applied_count,
                accepted_reserved_usd=accepted_reserved_usd,
                breaker_decision=breaker_decision,
            )
            decisions.append(decision)
            # Shadow mode counts what apply mode would have applied, so both reach the same
            # hard-limit decisions.
            if decision.outcome in {"applied", "shadowed"} and decision.simulation is not None:
                applied_count += 1
                accepted_reserved_usd += decision.simulation.total_reserved_usd

        proposals = self._shadow_proposals(
            escape_hatch_message=escape_hatch_message,
            prong_comparison=prong_comparison,
        )
        for action in proposals:
            decisions.append(self._proposal_decision(action))

        return AutopilotRunResult(
            now=now_utc,
            mode=self._mode,
            limits=self._limits,
            decisions=tuple(decisions),
        )

    def _shadow_proposals(
        self,
        *,
        escape_hatch_message: str | None,
        prong_comparison: Sequence[ProngOption],
    ) -> tuple[AutopilotAction, ...]:
        """Derive advisory PROPOSE_* actions from the prong comparison (plan Task 20)."""

        proposals: list[AutopilotAction] = []
        if escape_hatch_message is not None:
            proposals.append(
                AutopilotAction.proposal(
                    action_kind=AutopilotActionKind.PROPOSE_SERVE,
                    signal_id="propose-serve",
                    source="escape_hatch",
                    target_kind="capability",
                    target_id="own-serve",
                    reason=escape_hatch_message,
                )
            )
        own = next((row for row in prong_comparison if row.prong == "own_serve"), None)
        metered = next((row for row in prong_comparison if row.prong == "metered"), None)
        if (
            own is not None
            and metered is not None
            and metered.usd_per_million_tokens < own.usd_per_million_tokens
        ):
            proposals.append(
                AutopilotAction.proposal(
                    action_kind=AutopilotActionKind.PROPOSE_SEAT_SWITCH,
                    signal_id="propose-seat-switch",
                    source="prong_comparison",
                    target_kind="provider",
                    target_id="metered",
                    reason=f"metered {metered.usd_per_million_tokens}/M beats own-pod",
                )
            )
        return tuple(proposals)

    def _proposal_decision(self, action: AutopilotAction) -> AutopilotDecision:
        """Audit a PROPOSE_* action; proposals are never executed."""

        return AutopilotDecision(
            action=action,
            outcome="shadowed",
            gates=(
                AutopilotGateResult(
                    name="proposal",
                    passed=False,
                    reason="PROPOSE_* actions are advisory and are never applied",
                    details={"action_kind": action.action_kind.value},
                ),
            ),
        )

    def _collect_signals(
        self,
        *,
        now: dt.datetime,
        signals: Iterable[AutopilotSignal],
        sources: Iterable[AutopilotSignalSource],
    ) -> tuple[AutopilotSignal, ...]:
        collected = list(signals)
        for source in sources:
            collected.extend(source.collect(now))
        return tuple(
            sorted(
                collected,
                key=lambda signal: (
                    signal.priority,
                    signal.source,
                    signal.signal_id,
                    signal.target_kind,
                    signal.target_id,
                ),
            )
        )

    def _decide(
        self,
        *,
        action: AutopilotAction,
        applied_count: int,
        accepted_reserved_usd: Decimal,
        breaker_decision: CircuitBreakerDecision | None,
    ) -> AutopilotDecision:
        gates: list[AutopilotGateResult] = []

        if is_proposal_action_kind(action.action_kind):
            return self._proposal_decision(action)

        policy_result = self._evaluate_policy(action)
        policy_gate = _policy_gate(policy_result)
        gates.append(policy_gate)
        if not policy_gate.passed:
            return AutopilotDecision(action=action, outcome="denied", gates=tuple(gates))

        simulation = self._simulate(action)
        gates.append(_simulation_gate(simulation))
        if not simulation.passed or simulation.projection is None:
            return AutopilotDecision(action=action, outcome="denied", gates=tuple(gates))

        limit = self._evaluate_hard_limits(
            simulation=simulation.projection,
            applied_count=applied_count,
            accepted_reserved_usd=accepted_reserved_usd,
        )
        gates.append(
            AutopilotGateResult(
                name="hard_limits",
                passed=limit.passed,
                reason=limit.reason,
                details={
                    "reserved_usd": simulation.projection.total_reserved_usd,
                    "accepted_reserved_usd": accepted_reserved_usd,
                    "applied_count": applied_count,
                },
            )
        )
        if not limit.passed:
            return AutopilotDecision(
                action=action,
                outcome="denied",
                gates=tuple(gates),
                simulation=simulation.projection,
            )

        breaker_gate = _breaker_gate(breaker_decision)
        gates.append(breaker_gate)
        if not breaker_gate.passed:
            return AutopilotDecision(
                action=action,
                outcome="denied",
                gates=tuple(gates),
                simulation=simulation.projection,
            )

        if self._mode == AutopilotMode.SHADOW:
            gates.append(
                AutopilotGateResult(
                    name="mode",
                    passed=False,
                    reason="shadow mode; apply skipped",
                )
            )
            return AutopilotDecision(
                action=action,
                outcome="shadowed",
                gates=tuple(gates),
                simulation=simulation.projection,
            )

        if self._mode != AutopilotMode.APPLY:
            gates.append(
                AutopilotGateResult(
                    name="mode",
                    passed=False,
                    reason=f"unsupported mode {self._mode.value}; apply skipped",
                )
            )
            return AutopilotDecision(
                action=action,
                outcome="denied",
                gates=tuple(gates),
                simulation=simulation.projection,
            )

        if self._executor is None:
            gates.append(
                AutopilotGateResult(
                    name="executor",
                    passed=False,
                    reason="apply mode requires an executor",
                )
            )
            return AutopilotDecision(
                action=action,
                outcome="denied",
                gates=tuple(gates),
                simulation=simulation.projection,
            )

        apply_result = self._executor.apply(action)
        gates.append(
            AutopilotGateResult(
                name="executor",
                passed=apply_result.applied,
                reason=apply_result.message,
                details=apply_result.details,
            )
        )
        return AutopilotDecision(
            action=replace(action, executed=True) if apply_result.applied else action,
            outcome="applied" if apply_result.applied else "denied",
            gates=tuple(gates),
            simulation=simulation.projection,
            apply_result=apply_result,
        )

    def _evaluate_policy(self, action: AutopilotAction) -> PolicyEvaluationResult:
        snapshot = _PolicySnapshot(
            providers=(action.policy_provider_snapshot(),),
            workloads=action.policy_workloads,
            capability=action.policy_capability,
        )
        return evaluate_policies(self._policy_set, snapshot)

    def _simulate(self, action: AutopilotAction) -> _SimulationEvaluation:
        if not action.simulation_workloads:
            return _SimulationEvaluation(False, "action has no simulation workload")
        try:
            projection = self._simulator.simulate_workloads(action.simulation_workloads)
        except ValueError as exc:
            return _SimulationEvaluation(False, f"simulation failed: {exc}")
        if projection.would_exceed_budget is True:
            return _SimulationEvaluation(False, "simulation would exceed budget", projection)
        return _SimulationEvaluation(True, "simulation passed", projection)

    def _evaluate_hard_limits(
        self,
        *,
        simulation: WhatIfBatchProjection,
        applied_count: int,
        accepted_reserved_usd: Decimal,
    ) -> _HardLimitEvaluation:
        if applied_count >= self._limits.max_actions_per_run:
            return _HardLimitEvaluation(False, "max actions per run reached")
        if (
            self._limits.max_reserved_usd_per_action is not None
            and simulation.total_reserved_usd > self._limits.max_reserved_usd_per_action
        ):
            return _HardLimitEvaluation(False, "action reserved cost exceeds hard limit")
        if (
            self._limits.max_reserved_usd_per_run is not None
            and accepted_reserved_usd + simulation.total_reserved_usd
            > self._limits.max_reserved_usd_per_run
        ):
            return _HardLimitEvaluation(False, "run reserved cost exceeds hard limit")
        if (
            self._limits.max_projected_spend_usd is not None
            and simulation.projected_spend_usd > self._limits.max_projected_spend_usd
        ):
            return _HardLimitEvaluation(False, "projected spend exceeds hard limit")
        return _HardLimitEvaluation(True, "hard limits passed")


def _policy_gate(result: PolicyEvaluationResult) -> AutopilotGateResult:
    return AutopilotGateResult(
        name="policy",
        passed=result.allowed,
        reason="policy allowed action" if result.allowed else "policy denied action",
        details={
            "decision": result.decision,
            "violations": [violation.model_dump(mode="json") for violation in result.violations],
        },
    )


def _simulation_gate(evaluation: _SimulationEvaluation) -> AutopilotGateResult:
    if not evaluation.passed:
        return AutopilotGateResult(
            name="simulation",
            passed=False,
            reason=evaluation.reason,
            details=(
                {}
                if evaluation.projection is None
                else {
                    "total_reserved_usd": evaluation.projection.total_reserved_usd,
                    "projected_spend_usd": evaluation.projection.projected_spend_usd,
                    "would_exceed_budget": evaluation.projection.would_exceed_budget,
                }
            ),
        )
    projection = evaluation.projection
    if projection is None:
        return AutopilotGateResult(
            name="simulation",
            passed=False,
            reason="simulation result missing",
        )
    return AutopilotGateResult(
        name="simulation",
        passed=True,
        reason=evaluation.reason,
        details={
            "total_reserved_usd": projection.total_reserved_usd,
            "projected_spend_usd": projection.projected_spend_usd,
            "would_exceed_budget": projection.would_exceed_budget,
        },
    )


def _breaker_gate(
    decision: CircuitBreakerDecision | None,
) -> AutopilotGateResult:
    if decision is None:
        return AutopilotGateResult(
            name="circuit_breaker",
            passed=True,
            reason="no breaker decision supplied",
        )
    if decision.action == "allow":
        return AutopilotGateResult(
            name="circuit_breaker",
            passed=True,
            reason=decision.reason,
            details={
                "action": decision.action,
                "state": decision.state,
                "headroom_usd": decision.headroom_usd,
                "headroom_pct": decision.headroom_pct,
                "runway_hours": decision.runway_hours,
            },
        )
    return AutopilotGateResult(
        name="circuit_breaker",
        passed=False,
        reason=f"breaker requested {decision.action}: {decision.reason}",
        details={
            "action": decision.action,
            "state": decision.state,
            "headroom_usd": decision.headroom_usd,
            "headroom_pct": decision.headroom_pct,
            "runway_hours": decision.runway_hours,
        },
    )


def _normalize_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must include timezone information")
    return value.astimezone(dt.UTC)


__all__ = [
    "AutopilotController",
    "AutopilotExecutor",
    "AutopilotSignalSource",
]
