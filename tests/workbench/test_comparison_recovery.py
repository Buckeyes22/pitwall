"""Turn recovery, translated from packages/pi-workbench/tests/comparison-recovery.test.ts."""

from __future__ import annotations

import re
from typing import Any

import pytest

from pitwall.workbench.comparison.recovery import (
    RecoveryDependencies,
    RecoveryDisposition,
    confirms_idle_state,
    recover_turn,
)


@pytest.mark.parity
def test_allows_the_next_turn_only_after_a_fresh_settled_event() -> None:
    """Source: comparison-recovery.test.ts 'allows the next turn only after a fresh settled event'."""
    calls: list[str] = []
    settled = False

    def request(kind: str, fields: dict[str, Any] | None = None, timeout_ms: int = 0) -> Any:
        calls.append(kind)
        return {}

    def wait_for_settled() -> None:
        nonlocal settled
        calls.append("wait:agent_settled")
        settled = True

    def invalidate(reason: str) -> None:
        raise AssertionError("must not invalidate a settled session")

    disposition = recover_turn(RecoveryDependencies(request, wait_for_settled, invalidate))

    assert disposition == RecoveryDisposition(settled=True, source="agent_settled")
    assert settled
    assert calls == ["clear_queue", "abort", "wait:agent_settled"]


@pytest.mark.parity
def test_uses_only_the_pinned_idle_state_as_a_fallback_and_invalidates_otherwise() -> None:
    """Source: comparison-recovery.test.ts 'uses only the pinned idle state as a fallback and invalidates otherwise'."""
    calls: list[str] = []
    invalid: list[str] = []

    def idle_request(kind: str, fields: dict[str, Any] | None = None, timeout_ms: int = 0) -> Any:
        calls.append(kind)
        if kind == "get_state":
            return {"data": {"isStreaming": False, "isCompacting": False, "pendingMessageCount": 0}}
        return {}

    def never_settles() -> None:
        calls.append("wait:agent_settled")
        raise TimeoutError("settled event timed out")

    disposition = recover_turn(
        RecoveryDependencies(idle_request, never_settles, lambda reason: invalid.append(reason))
    )

    assert disposition == RecoveryDisposition(settled=True, source="state")
    assert invalid == []
    assert calls == ["clear_queue", "abort", "wait:agent_settled", "get_state"]

    blocked_reasons: list[str] = []

    def busy_request(kind: str, fields: dict[str, Any] | None = None, timeout_ms: int = 0) -> Any:
        if kind == "get_state":
            return {"data": {"isStreaming": True, "isCompacting": False, "pendingMessageCount": 0}}
        return {}

    def no_settled() -> None:
        raise TimeoutError("no settled event")

    blocked = recover_turn(
        RecoveryDependencies(
            busy_request, no_settled, lambda reason: blocked_reasons.append(reason)
        )
    )
    assert not blocked.settled
    assert re.search(r"could not confirm settlement", blocked_reasons[0])


@pytest.mark.parity
def test_rejects_incomplete_or_compacting_state_as_an_idle_confirmation() -> None:
    """Source: comparison-recovery.test.ts 'rejects incomplete or compacting state as an idle confirmation'."""
    assert confirms_idle_state(
        {"isStreaming": False, "isCompacting": False, "pendingMessageCount": 0}
    )
    assert not confirms_idle_state(
        {"isStreaming": False, "isCompacting": True, "pendingMessageCount": 0}
    )
    assert not confirms_idle_state(
        {"isStreaming": False, "isCompacting": False, "pendingMessageCount": 1}
    )
    assert not confirms_idle_state({"isStreaming": False})
