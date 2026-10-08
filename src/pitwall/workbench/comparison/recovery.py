"""Drain a failed comparison turn before another prompt (port of ``comparison-recovery.ts``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pitwall.workbench.comparison._json import JsonObject

# ``request(type, fields, timeout_ms)`` returns the parsed RPC response.
RecoveryRequest = Callable[[str, JsonObject | None, int], object]


@dataclass(frozen=True)
class RecoveryDisposition:
    settled: bool
    source: Literal["agent_settled", "state", "unconfirmed"]
    reason: str | None = None


@dataclass(frozen=True)
class RecoveryDependencies:
    request: RecoveryRequest
    wait_for_settled: Callable[[], None]
    invalidate: Callable[[str], None]


def confirms_idle_state(value: object) -> bool:
    """The pinned RPC contract reports an idle, non-compacting session with no queued messages."""
    if not isinstance(value, dict):
        return False
    return (
        value.get("isStreaming") is False
        and value.get("isCompacting") is False
        and value.get("pendingMessageCount") == 0
    )


def recover_turn(deps: RecoveryDependencies) -> RecoveryDisposition:
    """Clear the queue, abort, and require a settled event or an explicit idle state.

    A state response is accepted only when the pinned RPC contract reports an idle, non-compacting
    session with no queued messages. Otherwise the session is invalidated.
    """
    errors: list[str] = []
    for name, timeout_ms in (("clear_queue", 3_000), ("abort", 5_000)):
        try:
            deps.request(name, {}, timeout_ms)
        except (RuntimeError, OSError, TimeoutError) as error:
            # Recovery is best effort; a failed clear or abort is recorded and settlement is still checked.
            errors.append(f"{name}: {error}")
    try:
        deps.wait_for_settled()
        return RecoveryDisposition(True, "agent_settled")
    except (RuntimeError, OSError, TimeoutError) as error:
        # No settled event: fall back to the explicit idle state below.
        errors.append(f"agent_settled: {error}")
    try:
        response = deps.request("get_state", {}, 3_000)
        data = response.get("data") if isinstance(response, dict) else None
        if confirms_idle_state(data):
            return RecoveryDisposition(True, "state")
        errors.append("get_state: state did not confirm idle")
    except (RuntimeError, OSError, TimeoutError) as error:
        # An unreadable state cannot confirm idle.
        errors.append(f"get_state: {error}")
    reason = f"turn recovery could not confirm settlement ({'; '.join(errors)})"
    deps.invalidate(reason)
    return RecoveryDisposition(False, "unconfirmed", reason)
