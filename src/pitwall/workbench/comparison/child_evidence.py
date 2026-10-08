"""Evidence that a candidate backend ran a child (port of ``comparison-child-evidence.ts``).

A tool name or model text is launch evidence only; completion needs a terminal backend result and a
persisted child identity that the caller correlates separately.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from pitwall.workbench.comparison._json import (
    JsonObject,
    as_list,
    as_number,
    as_object,
    as_str,
    first_non_negative,
    get_list,
    get_object,
    get_str,
    jsonl_objects,
    parse_json,
    parse_json_object,
)
from pitwall.workbench.comparison.metrics import UsageTotals, add_usage, empty_usage

ChildReceiptEvent = Mapping[str, object]
CHILD_MARKER = "CHILD_ORACLE_7F31"
_THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class ChildReceipt:
    tool_call_id: str | None = None
    child_id: str | None = None
    session_file: str | None = None
    completed: bool = False
    result_marker: bool | None = None
    model_name: str | None = None

    def to_json(self) -> JsonObject:
        return {
            "toolCallId": self.tool_call_id,
            "childId": self.child_id,
            "completed": self.completed,
            "modelName": self.model_name,
        }


@dataclass(frozen=True)
class ChildSessionTiming:
    started_at_ms: float
    finished_at_ms: float
    duration_ms: float

    def to_json(self) -> JsonObject:
        return {
            "startedAtMs": self.started_at_ms,
            "finishedAtMs": self.finished_at_ms,
            "durationMs": self.duration_ms,
        }


def timestamp_ms(value: object) -> float | None:
    number = as_number(value)
    if number is not None:
        return number
    text = as_str(value)
    if text is None:
        return None
    try:
        return round(datetime.fromisoformat(text).timestamp() * 1000, 3)
    except ValueError:
        # An unparseable timestamp is not a measurement.
        return None


def child_session_timing(content: str) -> ChildSessionTiming | None:
    """Measure only the exact persisted child session selected by its receipt."""
    stamps = [
        stamp
        for record in jsonl_objects(content)
        if (stamp := timestamp_ms(record.get("timestamp"))) is not None
    ]
    if len(stamps) < 2:
        return None
    started, finished = min(stamps), max(stamps)
    return ChildSessionTiming(started, finished, max(0, finished - started))


def child_session_header_matches(
    content: str, child_id: str, expected_parent_session: str | None = None
) -> bool:
    """Match a persisted child session only by its public header identity and parent link.

    Tintin's public session name is ``Explore#<first-eight-id>``; that documented display form is
    accepted only with the full child id supplied separately.
    """
    lines = [line for line in content.split("\n") if line.strip()][:8]
    records = [record for line in lines if (record := parse_json_object(line)) is not None]
    session = next((record for record in records if record.get("type") == "session"), None)
    if (
        session is None
        or not isinstance(expected_parent_session, str)
        or session.get("parentSession") != expected_parent_session
    ):
        return False
    identities = [
        value
        for header in records
        if header.get("type") in ("session", "session_info")
        for field in ("childId", "agentId", "runId", "childRunId", "name", "agent")
        if isinstance(value := header.get(field), str)
    ]
    if child_id in identities or f"Explore#{child_id}" in identities:
        return True
    # A shortened Tintin display name is usable only when the exact parent session has already
    # bound this candidate. A bare child id/name cannot distinguish siblings.
    return identities.count(f"Explore#{child_id[:8]}") == 1


def select_single_child_evidence_path(paths: Sequence[str]) -> str | None:
    """A fallback scan must identify exactly one candidate, never merge siblings."""
    unique = list(dict.fromkeys(paths))
    return unique[0] if len(unique) == 1 else None


def child_session_usage(content: str) -> UsageTotals:
    """Sum the child's assistant usage once per message id; missing usage stays unknown."""
    seen: set[str] = set()
    total = empty_usage()
    for record in jsonl_objects(content):
        message = (
            get_object(record, "message")
            if record.get("type") == "message" or record.get("recordType") == "message"
            else None
        )
        if message is None or message.get("role") != "assistant":
            continue
        identity = get_str(record, "id") or get_str(message, "id")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        usage = get_object(message, "usage")
        if usage is None:
            total = add_usage(total, UsageTotals(None, None, None, None, None, 0, 1))
            continue
        total = add_usage(
            total,
            UsageTotals(
                first_non_negative(usage, "input", "inputTokens", "prompt_tokens"),
                first_non_negative(usage, "output", "outputTokens", "completion_tokens"),
                first_non_negative(usage, "cacheRead", "cache_read_input_tokens"),
                first_non_negative(usage, "cacheWrite", "cache_creation_input_tokens"),
                first_non_negative(usage, "reasoning", "reasoningTokens"),
                1,
                1,
            ),
        )
    return total


def child_session_thinking_levels(content: str) -> list[str]:
    """Reasoning controls from typed ``thinking_level_change`` records only.

    Parent prompts, assistant prose, and result metadata can mention a thinking level without
    controlling the child, so no arbitrary JSON text is searched.
    """
    levels: list[str] = []
    for record in jsonl_objects(content):
        level = record.get("thinkingLevel")
        if (
            record.get("type") == "thinking_level_change"
            and isinstance(level, str)
            and level not in levels
        ):
            levels.append(level)
    return levels


def _session_working_directory(content: str) -> str | None:
    first = next((line for line in content.split("\n") if line.strip()), None)
    return get_str(parse_json_object(first), "cwd") if first is not None else None


def _normalized_read_path(value: str, cwd: str | None = None) -> str:
    path = value.replace("\\", "/")
    base = cwd.replace("\\", "/") if cwd is not None else None
    return posixpath.normpath(
        posixpath.join(base, path) if base and not path.startswith("/") else path
    )


def _read_path_from_arguments(value: object) -> str | None:
    record = as_object(value)
    if record is not None:
        for key in ("path", "file_path", "filePath"):
            found = as_str(record.get(key))
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        return _read_path_from_arguments(parse_json(value))
    return None


def child_session_proves_read(
    content: str, marker: str, expected_path: str = "child-probe.txt"
) -> bool:
    """Correlate a successful native read result to its exact requested path."""
    records = jsonl_objects(content)
    cwd = _session_working_directory(content)
    expected = _normalized_read_path(expected_path, cwd)
    requested: set[str] = set()
    successful: set[str] = set()
    for record in records:
        message = (
            get_object(record, "message")
            if record.get("type") == "message" or record.get("recordType") == "message"
            else None
        )
        for part in get_list(message, "content") or []:
            item = as_object(part) or {}
            path = _read_path_from_arguments(
                item.get("arguments") if item.get("arguments") is not None else item.get("input")
            )
            call_id = item.get("id")
            if (
                item.get("type") == "toolCall"
                and item.get("name") == "read"
                and isinstance(call_id, str)
                and path is not None
                and _normalized_read_path(path, cwd) == expected
            ):
                requested.add(call_id)
        call_id = record.get("toolCallId")
        if (
            record.get("type") == "tool_execution_start" or record.get("recordType") == "tool_start"
        ) and (record.get("toolName") == "read" and isinstance(call_id, str)):
            for key in ("argsPayload", "args", "input"):
                if record.get(key) is not None:
                    path = _read_path_from_arguments(record[key])
                    break
            else:
                path = None
            if path is not None and _normalized_read_path(path, cwd) == expected:
                requested.add(call_id)
        if (
            (record.get("type") == "tool_execution_end" or record.get("recordType") == "tool_end")
            and record.get("isError") is not True
            and isinstance(call_id, str)
            and call_id in requested
        ):
            successful.add(call_id)
        message_id = get_str(message, "toolCallId")
        if (
            get_str(message, "role") == "toolResult"
            and message_id is not None
            and message_id in requested
            and (message or {}).get("isError") is not True
        ):
            successful.add(message_id)
        if (
            get_str(message, "role") == "toolResult"
            and message_id is not None
            and message_id in successful
            and marker in json.dumps((message or {}).get("content") or [])
        ):
            return True
        is_message = record.get("recordType") == "message" or record.get("type") == "message"
        result_id = record.get("toolCallId") if record.get("toolCallId") is not None else message_id
        if (
            is_message
            and (record.get("role") == "toolResult" or get_str(message, "role") == "toolResult")
            and isinstance(result_id, str)
            and result_id in successful
            and marker
            in json.dumps(
                record.get("text")
                if record.get("text") is not None
                else (message or {}).get("content") or []
            )
        ):
            return True
    return False


def durable_child_record_matches(content: str, child_id: str, marker: str) -> bool:
    """Read the backend's durable parent-session record when RPC omits custom events."""
    for record in jsonl_objects(content):
        data = (
            get_object(record, "data")
            if record.get("type") == "custom" and record.get("customType") == "subagents:record"
            else None
        )
        result = (data or {}).get("result")
        if (
            data is not None
            and data.get("id") == child_id
            and data.get("status") == "completed"
            and isinstance(result, str)
            and marker in result
        ):
            return True
    return False


def child_model_matches(
    actual: Sequence[str], expected: str, expected_provider: str | None = None
) -> bool:
    """Whether a reported model label is exactly the selected model.

    Pi's public model label is ``provider/modelId`` optionally followed by its fixed thinking-level
    suffix. The complete model segment is compared; a substring or an unknown suffix is not
    evidence for the selected model.
    """
    expected_id = expected.strip()
    provider = expected_provider.strip() if expected_provider is not None else None
    if not expected_id:
        return False
    for candidate in actual:
        value = candidate.strip()
        if value == expected_id:
            return True
        slash = value.find("/")
        if slash <= 0 or slash != value.rfind("/"):
            continue
        if provider and value[:slash] != provider:
            continue
        model = value[slash + 1 :]
        colon = model.rfind(":")
        model_id = model if colon == -1 else model[:colon]
        suffix = None if colon == -1 else model[colon + 1 :]
        if model_id == expected_id and (suffix is None or suffix in _THINKING_LEVELS):
            return True
    return False


def child_execution_evidence_accepted(
    *,
    invoked: bool,
    completed: bool,
    model_matched: bool,
    persisted_read: bool,
    result_marker: bool,
) -> bool:
    """The child gate requires the persisted child read proof, not only a parent result marker."""
    return invoked and completed and model_matched and persisted_read and result_marker


def durable_child_execution_evidence_accepted(
    *, invoked: bool, completed: bool, model_matched: bool, result_marker: bool
) -> bool:
    """General comparison evidence may use a durable parent record when a backend has no child session."""
    return invoked and completed and model_matched and result_marker


def _is_agent_tool(name: object) -> bool:
    return bool(re.search(r"agent|subagent", str(name if name is not None else ""), re.IGNORECASE))


def child_receipt(events: Sequence[ChildReceiptEvent]) -> ChildReceipt:
    """Parse only the pinned candidate result contracts (Tintin agent record, Nicobailon result row)."""
    starts = {
        str(event["toolCallId"])
        for event in events
        if event.get("type") == "tool_execution_start"
        and _is_agent_tool(event.get("toolName"))
        and event.get("toolCallId")
    }
    ends = [
        event
        for event in events
        if event.get("type") == "tool_execution_end"
        and _is_agent_tool(event.get("toolName"))
        and isinstance(event.get("toolCallId"), str)
        and str(event["toolCallId"]) in starts
    ]
    durable = [
        data
        for event in events
        if event.get("type") == "custom"
        and event.get("customType") == "subagents:record"
        and (data := as_object(event.get("data"))) is not None
    ]
    for event in reversed(ends):
        result = as_object(event.get("result"))
        raw_details = (
            result["details"]
            if result is not None and "details" in result
            else event.get("details")
        )
        details = as_object(raw_details)
        if details is None:
            continue
        tool_call_id = as_str(event.get("toolCallId"))
        # Tintin's foreground result identifies the record as agentId and exposes the authoritative
        # terminal status. Background/running/error records are deliberately not completion evidence.
        agent_id = as_str(details.get("agentId"))
        if agent_id is not None:
            output_file = as_str(details.get("outputFile"))
            session_file = (
                output_file if output_file is not None else as_str(details.get("sessionFile"))
            )
            record = next((item for item in durable if item.get("id") == agent_id), None)
            recorded = (record or {}).get("result")
            marker = isinstance(recorded, str) and CHILD_MARKER in recorded
            return ChildReceipt(
                tool_call_id=tool_call_id,
                child_id=agent_id,
                session_file=session_file,
                completed=event.get("isError") is not True and details.get("status") == "completed",
                result_marker=True if marker else None,
                model_name=as_str(details.get("modelName")) or None,
            )
        # Nicobailon's foreground result identifies the parent run and its `results` rows carry the
        # child session file and exit code. A run id alone (especially asyncId/background) is only
        # launch evidence.
        run_id = as_str(details.get("runId"))
        rows = as_list(details.get("results"))
        if run_id is not None and rows is not None:
            row = as_object(rows[0]) if len(rows) == 1 else None
            objects = [as_object(candidate) or {} for candidate in rows]
            completed = (
                event.get("isError") is not True
                and len(objects) > 0
                and all(
                    candidate.get("exitCode") == 0
                    and candidate.get("stopped") is not True
                    and candidate.get("detached") is not True
                    and candidate.get("interrupted") is not True
                    and candidate.get("timedOut") is not True
                    and "error" not in candidate
                    for candidate in objects
                )
            )
            return ChildReceipt(
                tool_call_id=tool_call_id,
                child_id=run_id,
                session_file=get_str(row, "sessionFile"),
                completed=completed,
                model_name=get_str(row, "model") or None,
            )
    return ChildReceipt()
