"""One model turn over RPC, shared by the baseline and hosted acceptance runners."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pitwall.workbench.accounting import safe_usage
from pitwall.workbench.comparison._json import JsonObject, as_object, get_list
from pitwall.workbench.comparison.session import RpcSession, assistant_messages
from pitwall.workbench.hosted.fixture import FIXTURE_CHECK_TIMEOUT_S, FixtureCheck, fixture_check

# The ceiling for one model turn, and for any single event a gate waits on. A wait returns the moment
# its event arrives; only a turn or event that never comes reaches the ceiling.
TURN_TIMEOUT_MS = 120_000

_BAD_STOP = frozenset({"error", "aborted", "length"})


class GateFailure(RuntimeError):
    """An acceptance gate observed behavior that differs from its criterion."""


def gate_fixture_check(cwd: Path, check: str) -> FixtureCheck:
    """``fixture_check`` that fails the gate, naming ``check`` and the ceiling, when the run hangs."""
    result = fixture_check(cwd)
    if result.timed_out:
        raise GateFailure(f"{check} did not finish within {FIXTURE_CHECK_TIMEOUT_S:g} s")
    return result


@dataclass(frozen=True)
class TurnResult:
    text: str
    tools: list[str]
    usage: list[dict[str, float] | None]


def model_turn(session: RpcSession, message: str, extra: JsonObject | None = None) -> TurnResult:
    """Send one prompt, wait for ``agent_settled``, and require a completed assistant answer.

    A turn whose assistant stopped on error, abort, or length is a failed turn even though Pi
    accepted the prompt.
    """
    start = session.last_seq()
    session.request("prompt", {"message": message, **(extra or {})})
    session.wait_event("agent_settled", start)
    events = [entry.event for entry in session.events_after(start)]
    answers = assistant_messages(events)
    if not answers or any(answer.get("stopReason") in _BAD_STOP for answer in answers):
        raise GateFailure("model turn did not complete successfully")
    text = "\n".join(
        str(part.get("text"))
        for answer in answers
        for part in (as_object(item) for item in get_list(answer, "content") or [])
        if part is not None and part.get("type") == "text"
    )
    tools = [
        str(event.get("toolName"))
        for event in events
        if event.get("type") == "tool_execution_start"
    ]
    return TurnResult(text, tools, [safe_usage(answer.get("usage")) for answer in answers])
