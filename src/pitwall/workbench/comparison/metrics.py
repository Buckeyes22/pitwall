"""Turn, usage, and child-lifecycle measurements (port of ``comparison-metrics.ts``).

Events are the parsed JSON objects Pi emits over RPC, with ``observedAt`` (epoch milliseconds)
added by the capturing session. Every measurement that cannot be established from the events is
``None``: unknown is never reported as zero.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pitwall.workbench.comparison._json import (
    JsonObject,
    as_list,
    as_number,
    as_object,
    as_str,
    get_object,
    get_str,
    non_negative,
    parse_json,
)

Number = int | float
ComparisonEvent = Mapping[str, object]
_USAGE_KEYS = ("input", "output", "cacheRead", "cacheWrite", "reasoning")


@dataclass(frozen=True)
class UsageTotals:
    input: Number | None
    output: Number | None
    cache_read: Number | None
    cache_write: Number | None
    reasoning: Number | None
    reported_messages: int
    assistant_messages: int

    def to_json(self) -> JsonObject:
        return {
            "input": self.input,
            "output": self.output,
            "cacheRead": self.cache_read,
            "cacheWrite": self.cache_write,
            "reasoning": self.reasoning,
            "reportedMessages": self.reported_messages,
            "assistantMessages": self.assistant_messages,
        }


def empty_usage() -> UsageTotals:
    return UsageTotals(None, None, None, None, None, 0, 0)


def _add_known(left: Number | None, right: Number | None) -> Number | None:
    return None if left is None or right is None else left + right


def _js_truthy(value: object) -> bool:
    """A usage field is missing when it is null, false, zero, or an empty string (not an empty object)."""
    return not (value is None or value is False or value == 0 or value == "")


def usage_for_messages(messages: Sequence[ComparisonEvent]) -> UsageTotals:
    """Total provider-reported usage over the assistant ``message_end`` events."""
    sums: dict[str, Number | None] = dict.fromkeys(_USAGE_KEYS)
    missing = dict.fromkeys(_USAGE_KEYS, False)
    reported = assistant = 0
    for event in messages:
        message = get_object(event, "message")
        if event.get("type") != "message_end" or get_str(message, "role") != "assistant":
            continue
        assistant += 1
        raw_usage = message.get("usage") if message else None
        if not _js_truthy(raw_usage):
            for key in _USAGE_KEYS:
                missing[key] = True
            continue
        reported += 1
        usage = as_object(raw_usage) or {}
        for key in _USAGE_KEYS:
            value = non_negative(usage.get(key))
            if value is None:
                missing[key] = True
                continue
            current = sums[key]
            sums[key] = value if current is None else current + value
    for key in _USAGE_KEYS:
        if missing[key]:
            sums[key] = None
    return UsageTotals(
        sums["input"],
        sums["output"],
        sums["cacheRead"],
        sums["cacheWrite"],
        sums["reasoning"],
        reported,
        assistant,
    )


def add_usage(left: UsageTotals, right: UsageTotals) -> UsageTotals:
    """Add two run totals; an unknown field on either side stays unknown."""
    if left.assistant_messages == 0:
        return right
    if right.assistant_messages == 0:
        return left
    return UsageTotals(
        _add_known(left.input, right.input),
        _add_known(left.output, right.output),
        _add_known(left.cache_read, right.cache_read),
        _add_known(left.cache_write, right.cache_write),
        _add_known(left.reasoning, right.reasoning),
        left.reported_messages + right.reported_messages,
        left.assistant_messages + right.assistant_messages,
    )


@dataclass(frozen=True)
class TurnMeasurement:
    wall_ms: Number
    latency_to_useful_action_ms: Number | None
    tools: list[str]
    tool_errors: int
    retries: int
    queue_ms: Number | None
    tool_execution_ms: Number | None
    non_tool_elapsed_ms: Number | None
    model_elapsed_ms: Number | None
    first_request_usage: UsageTotals
    usage: UsageTotals

    def to_json(self) -> JsonObject:
        return {
            "wallMs": self.wall_ms,
            "latencyToUsefulActionMs": self.latency_to_useful_action_ms,
            "tools": list(self.tools),
            "toolErrors": self.tool_errors,
            "retries": self.retries,
            "queueMs": self.queue_ms,
            "toolExecutionMs": self.tool_execution_ms,
            "nonToolElapsedMs": self.non_tool_elapsed_ms,
            "modelElapsedMs": self.model_elapsed_ms,
            "firstRequestUsage": self.first_request_usage.to_json(),
            "usage": self.usage.to_json(),
        }


@dataclass(frozen=True)
class ChildLifecycleMeasurement:
    launch_wait_ms: Number | None
    run_ms: Number | None
    terminal_status: Literal["completed", "failed"] | None

    def to_json(self) -> JsonObject:
        return {
            "launchWaitMs": self.launch_wait_ms,
            "runMs": self.run_ms,
            "terminalStatus": self.terminal_status,
        }


_AGENT_TOOL = re.compile(r"agent|subagent", re.IGNORECASE)
_NON_LAUNCH_ACTIONS = frozenset({"list", "status", "stop", "steer", "cancel", "clear", "help"})


def _child_identity(event: ComparisonEvent) -> str | None:
    pid = as_number(event.get("pid"))
    for key in ("id", "childId", "agentId", "runId"):
        value = as_str(event.get(key))
        if value:
            return f"{pid if pid is not None else 'unknown'}:{value}"
    return None


def _is_launch(event: ComparisonEvent) -> bool:
    tool = event.get("toolName")
    if (
        event.get("type") != "tool_execution_start"
        or not _AGENT_TOOL.search(str(tool if tool is not None else ""))
        or as_number(event.get("observedAt")) is None
    ):
        return False
    if tool == "agent_task":
        return True
    raw = event.get("args")
    parsed = parse_json(raw) if isinstance(raw, str) else raw
    args = as_object(parsed)
    if args is None:
        return False
    if tool == "Agent":
        return isinstance(args.get("prompt"), str) and isinstance(args.get("subagent_type"), str)
    if tool != "subagent":
        return False
    action = args.get("action")
    if isinstance(action, str) and action in _NON_LAUNCH_ACTIONS:
        return False
    return isinstance(args.get("agent"), str) or isinstance(args.get("task"), str)


def measure_child_lifecycle(events: Sequence[ComparisonEvent]) -> ChildLifecycleMeasurement:
    """Separate a child's launch/admission wait from its runtime.

    The parent tool-call start to the backend's ``subagents:started`` event is launch wait; started
    to the first terminal lifecycle event is child runtime. A lifecycle event without an identity
    cannot be safely joined to a sibling, so exactly one valid same-identity pair in event and
    timestamp order is required.
    """
    launches = [
        (index, as_number(event.get("observedAt")))
        for index, event in enumerate(events)
        if _is_launch(event)
    ]
    starts: list[tuple[int, ComparisonEvent, str, Number]] = []
    terminals: list[tuple[int, ComparisonEvent, str, Number]] = []
    for index, event in enumerate(events):
        identity = _child_identity(event)
        observed = as_number(event.get("observedAt"))
        if not identity or observed is None:
            continue
        if event.get("type") == "subagents:started":
            starts.append((index, event, identity, observed))
        elif event.get("type") in ("subagents:completed", "subagents:failed"):
            terminals.append((index, event, identity, observed))
    pairs = [
        (started, terminal)
        for started in starts
        for terminal in terminals
        if terminal[2] == started[2] and terminal[0] > started[0] and terminal[3] >= started[3]
    ]
    pair = pairs[0] if len(pairs) == 1 else None
    launch = [item for item in launches if pair is not None and item[0] < pair[0][0]]
    if pair is None or len(launch) != 1:
        return ChildLifecycleMeasurement(None, None, None)
    started, terminal = pair
    launched_at = launch[0][1]
    wait = (
        max(0, started[3] - launched_at)
        if launched_at is not None and started[3] >= launched_at
        else None
    )
    status: Literal["completed", "failed"] = (
        "completed" if terminal[1].get("type") == "subagents:completed" else "failed"
    )
    return ChildLifecycleMeasurement(wait, max(0, terminal[3] - started[3]), status)


def _union_duration(ranges: list[tuple[Number, Number]]) -> Number:
    total: Number = 0
    current: list[Number] | None = None
    for start, end in sorted(ranges):
        if current is None or start > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = [start, end]
        else:
            current[1] = max(current[1], end)
    if current is not None:
        total += current[1] - current[0]
    return total


def _is_useful(event: ComparisonEvent) -> bool:
    kind = event.get("type")
    if kind == "tool_execution_start":
        return True
    if kind == "message_update":
        update = get_str(event.get("assistantMessageEvent"), "type")
        return bool(re.search(r"text|content", update or "", re.IGNORECASE))
    message = get_object(event, "message")
    if kind == "message_end" and get_str(message, "role") == "assistant":
        parts = as_list(message.get("content")) if message else None
        return bool(parts) and any(get_str(part, "type") == "text" for part in parts or [])
    return False


def measure_turn(
    events: Sequence[ComparisonEvent], started_at: Number, finished_at: Number
) -> TurnMeasurement:
    """Measure one prompt turn from its events and the wall-clock bounds."""
    tools = [
        str(event["toolName"])
        for event in events
        if event.get("type") == "tool_execution_start" and isinstance(event.get("toolName"), str)
    ]
    tool_errors = sum(
        1
        for event in events
        if event.get("type") == "tool_execution_end" and event.get("isError") is True
    )
    starts: dict[str, Number] = {}
    intervals: list[tuple[Number, Number]] = []
    unpaired_tool = False
    for event in events:
        call_id = as_str(event.get("toolCallId"))
        observed = as_number(event.get("observedAt"))
        if event.get("type") == "tool_execution_start":
            if call_id is None or observed is None:
                unpaired_tool = True
                continue
            if call_id in starts:
                unpaired_tool = True
            starts[call_id] = observed
        if event.get("type") == "tool_execution_end":
            if call_id is None:
                unpaired_tool = True
                continue
            began = starts.get(call_id)
            if began is None or observed is None:
                unpaired_tool = True
                continue
            intervals.append((began, max(began, observed)))
            del starts[call_id]
    if starts:
        unpaired_tool = True
    wall_ms = max(0, finished_at - started_at)
    tool_execution_ms = None if unpaired_tool else _union_duration(intervals)

    # The provider/model interval is only measured when the runtime emits an explicit assistant
    # message_start/message_end pair. Wall minus tool time also contains queue/setup time, so it
    # must not be mislabeled as model generation.
    model_starts: list[Number] = []
    model_intervals: list[tuple[Number, Number]] = []
    saw_model_lifecycle = False
    unpaired_model = False
    for event in events:
        role = get_str(event.get("message"), "role")
        observed = as_number(event.get("observedAt"))
        if event.get("type") == "message_start" and role == "assistant":
            saw_model_lifecycle = True
            if observed is None:
                unpaired_model = True
                continue
            model_starts.append(observed)
        if event.get("type") == "message_end" and role == "assistant":
            saw_model_lifecycle = True
            began = model_starts.pop(0) if model_starts else None
            if began is None or observed is None:
                unpaired_model = True
                continue
            model_intervals.append((began, max(began, observed)))
    if model_starts:
        unpaired_model = True
    model_elapsed_ms = (
        None
        if not saw_model_lifecycle or unpaired_model or not model_intervals
        else _union_duration(model_intervals)
    )
    non_tool_elapsed_ms = None if tool_execution_ms is None else max(0, wall_ms - tool_execution_ms)
    first_assistant = next(
        (
            event
            for event in events
            if event.get("type") == "message_end"
            and get_str(event.get("message"), "role") == "assistant"
        ),
        None,
    )
    first_request_usage = (
        usage_for_messages([first_assistant]) if first_assistant is not None else empty_usage()
    )
    # Pi emits paired retry lifecycle events; one retry is one start/request, not two counted edges.
    retries = sum(
        1
        for event in events
        if re.search("retry", str(event.get("type")), re.IGNORECASE)
        and re.search("start|begin|request", str(event.get("type")), re.IGNORECASE)
    )
    useful = next((event for event in events if _is_useful(event)), None)
    useful_at = as_number(useful.get("observedAt")) if useful is not None else None
    return TurnMeasurement(
        wall_ms=wall_ms,
        latency_to_useful_action_ms=None if useful_at is None else max(0, useful_at - started_at),
        tools=tools,
        tool_errors=tool_errors,
        retries=retries,
        queue_ms=None,
        tool_execution_ms=tool_execution_ms,
        non_tool_elapsed_ms=non_tool_elapsed_ms,
        model_elapsed_ms=model_elapsed_ms,
        first_request_usage=first_request_usage,
        usage=usage_for_messages(events),
    )


def task_accepted(effect: Mapping[str, object], errors: Sequence[str] = ()) -> bool:
    """A task is accepted only when its effect says so and no collateral check failed."""
    if errors or effect.get("accepted") is not True:
        return False
    if (
        effect.get("protectedUnchanged") is False
        or effect.get("answerMatched") is False
        or effect.get("testsUnchanged") is False
    ):
        return False
    final_exit = as_number(effect.get("finalExitCode"))
    return not (final_exit is not None and final_exit != 0)


@dataclass(frozen=True)
class AdmissionAccounting:
    instrumented: bool
    queue_ms: Number | None
    max_queue_ms: Number | None
    request_count: int
    settled_count: int
    unavailable: int

    def to_json(self) -> JsonObject:
        return {
            "instrumented": self.instrumented,
            "queueMs": self.queue_ms,
            "maxQueueMs": self.max_queue_ms,
            "requestCount": self.request_count,
            "settledCount": self.settled_count,
            "unavailable": self.unavailable,
        }


def summarize_admission_accounting(records: Sequence[object]) -> AdmissionAccounting:
    """Summarize the Workbench provider-admission lock waits written to native-accounting.jsonl."""
    request_count = settled_count = unavailable = 0
    queue_ms: Number = 0
    max_queue_ms: Number = 0
    for record in records:
        entry = as_object(record)
        if entry is None:
            continue
        if entry.get("type") == "native_settled":
            settled_count += 1
            continue
        if entry.get("type") != "native_request":
            continue
        request_count += 1
        queued = non_negative(entry.get("queuedAt"))
        acquired = non_negative(entry.get("acquiredAt"))
        if queued is None or acquired is None or acquired < queued:
            unavailable += 1
            continue
        queue_ms += acquired - queued
        max_queue_ms = max(max_queue_ms, acquired - queued)
    measured = request_count - unavailable > 0
    return AdmissionAccounting(
        instrumented=request_count > 0,
        queue_ms=queue_ms if measured else None,
        max_queue_ms=max_queue_ms if measured else None,
        request_count=request_count,
        settled_count=settled_count,
        unavailable=unavailable,
    )
