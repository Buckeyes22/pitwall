"""Autopilot signals and actions snapshot their inputs deeply and immutably."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from pitwall.autopilot import AutopilotAction, AutopilotActionKind, AutopilotSignal


def _inputs() -> dict[str, Any]:
    return {
        "params": {"priority": 1, "nested": {"labels": ["a", "b"]}, "steps": [{"n": 1}]},
        "policy_provider": {"id": "prov", "config": {"autopilot": {"allowed": True}}},
        "policy_workloads": ({"request": {"tags": ["x"]}},),
        "policy_capability": {"name": "cap", "config": {"tiers": ["gold"]}},
    }


def _signal(inputs: dict[str, Any]) -> AutopilotSignal:
    return AutopilotSignal(
        signal_id="sig",
        source="unit-test",
        action_kind=AutopilotActionKind.ADJUST_PROVIDER_PRIORITY,
        target_kind="provider",
        target_id="prov",
        reason="adjust",
        confidence=Decimal("0.5"),
        **inputs,
    )


def _mutate(inputs: dict[str, Any]) -> None:
    inputs["params"]["priority"] = 999
    inputs["params"]["nested"]["labels"].append("c")
    inputs["params"]["steps"][0]["n"] = 2
    inputs["policy_provider"]["config"]["autopilot"]["allowed"] = False
    inputs["policy_workloads"][0]["request"]["tags"].append("y")
    inputs["policy_capability"]["config"]["tiers"].append("silver")


def _assert_unchanged(owner: AutopilotSignal | AutopilotAction) -> None:
    assert owner.params["priority"] == 1
    assert owner.params["nested"]["labels"] == ("a", "b")
    assert owner.params["steps"][0]["n"] == 1
    assert owner.policy_provider is not None
    assert owner.policy_provider["config"]["autopilot"]["allowed"] is True
    assert owner.policy_workloads[0]["request"]["tags"] == ("x",)
    assert owner.policy_capability is not None
    assert owner.policy_capability["config"]["tiers"] == ("gold",)


def _assert_read_only(owner: AutopilotSignal | AutopilotAction) -> None:
    with pytest.raises(TypeError):
        owner.params["priority"] = 5  # type: ignore[index]  # reason: asserts the frozen snapshot rejects mutation
    with pytest.raises(TypeError):
        owner.params["nested"]["labels"] = ()  # type: ignore[index]  # reason: asserts the frozen snapshot rejects mutation
    with pytest.raises(TypeError):
        owner.params["steps"][0]["n"] = 3  # type: ignore[index]  # reason: asserts the frozen snapshot rejects mutation
    with pytest.raises(AttributeError):
        owner.params["nested"]["labels"].append("c")  # type: ignore[attr-defined]  # reason: asserts the frozen snapshot rejects mutation
    assert owner.policy_provider is not None
    with pytest.raises(TypeError):
        owner.policy_provider["config"]["autopilot"]["allowed"] = False  # type: ignore[index]  # reason: asserts the frozen snapshot rejects mutation
    with pytest.raises(TypeError):
        owner.policy_workloads[0]["request"]["tags"][0] = "z"  # type: ignore[index]  # reason: asserts the frozen snapshot rejects mutation
    assert owner.policy_capability is not None
    with pytest.raises(TypeError):
        owner.policy_capability["config"]["tiers"][0] = "z"  # type: ignore[index]  # reason: asserts the frozen snapshot rejects mutation


def test_a_signal_is_unaffected_by_later_mutation_of_the_callers_inputs() -> None:
    inputs = _inputs()
    signal = _signal(inputs)

    _mutate(inputs)

    _assert_unchanged(signal)
    _assert_read_only(signal)


def test_an_action_is_unaffected_by_later_mutation_of_the_callers_inputs() -> None:
    inputs = _inputs()
    action = AutopilotAction.from_signal(_signal(inputs))
    direct_inputs = _inputs()
    direct = AutopilotAction(
        action_id="ap-direct",
        signal_id="direct",
        source="unit-test",
        action_kind=AutopilotActionKind.ADJUST_PROVIDER_PRIORITY,
        target_kind="provider",
        target_id="prov",
        reason="adjust",
        priority=1,
        confidence=Decimal("1"),
        **direct_inputs,
        simulation_workloads=(),
    )

    _mutate(inputs)
    _mutate(direct_inputs)

    for owner in (action, direct):
        _assert_unchanged(owner)
        _assert_read_only(owner)


def test_serialisation_converts_snapshots_back_to_plain_containers() -> None:
    action = AutopilotAction.from_signal(_signal(_inputs()))

    payload = action.policy_provider_snapshot()

    assert payload["config"]["autopilot"]["allowed"] is True  # type: ignore[index]  # reason: serialised payload is a plain JSON mapping
    assert isinstance(action.to_dict(), dict)
