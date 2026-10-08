"""Child lifecycle evidence for the comparison runner.

The comparison-lifecycle observer itself is a packaged Pi extension
(``pitwall.workbench.pi_extensions`` ``comparison-lifecycle-observer``): it subscribes to the public
event bus and appends identity and timing fields to ``COMPARISON_LIFECYCLE_PATH``. This module reads
those records back and summarizes the backend's own lifecycle events.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from pitwall.workbench.comparison._json import JsonObject, as_number, parse_json_object

_AGENT_TOOL = re.compile(r"agent|subagent", re.IGNORECASE)


def read_lifecycle_observer(path: Path | str, expected_pid: int | None = None) -> list[JsonObject]:
    """Observed lifecycle records as comparison events, optionally only those of one Pi process."""
    try:
        text = Path(path).read_text()
    except OSError:
        # No lifecycle file means the candidate emitted no observable lifecycle events.
        return []
    events: list[JsonObject] = []
    for line in text.split("\n"):
        value = parse_json_object(line) if line else None
        if value is None:
            continue
        if expected_pid is not None and value.get("pid") != expected_pid:
            continue
        observed = as_number(value.get("observedAt"))
        if not isinstance(value.get("event"), str) or observed is None:
            continue
        event: JsonObject = {"type": value["event"], "observedAt": observed}
        if isinstance(value.get("id"), str):
            event["id"] = value["id"]
        if isinstance(value.get("pid"), int | float):
            event["pid"] = value["pid"]
        events.append(event)
    return events


def count_child_events(events: Sequence[JsonObject]) -> JsonObject:
    """How many child ``started``, ``completed``, and ``failed`` lifecycle events were recorded."""
    counts = {"started": 0, "completed": 0, "failed": 0}
    for event in events:
        kind = str(event.get("type"))
        for name in counts:
            if kind == f"subagents:{name}":
                counts[name] += 1
    return dict(counts)


def correlated_tool_interval(
    events: Sequence[JsonObject], tool_call_id: str | None
) -> float | None:
    """The observed start-to-end interval of one tool call, or ``None`` when it is not paired."""
    if not tool_call_id:
        return None
    start = next(
        (
            as_number(event.get("observedAt"))
            for event in events
            if event.get("type") == "tool_execution_start"
            and event.get("toolCallId") == tool_call_id
            and as_number(event.get("observedAt")) is not None
        ),
        None,
    )
    end = next(
        (
            as_number(event.get("observedAt"))
            for event in events
            if event.get("type") == "tool_execution_end"
            and event.get("toolCallId") == tool_call_id
            and as_number(event.get("observedAt")) is not None
        ),
        None,
    )
    return end - start if start is not None and end is not None and end >= start else None


def called_child_tool(tool_names: Sequence[str]) -> bool:
    """Whether the model invoked a candidate child surface."""
    return any(_AGENT_TOOL.search(name) for name in tool_names)
