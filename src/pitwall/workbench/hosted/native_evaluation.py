"""Pure evidence checks for a native child acceptance session (port of ``hosted-native-evaluation.ts``).

The proof must come from the child session itself. Parent prompts, task summaries, and a host-side
file read are deliberately insufficient evidence here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from pitwall.workbench.comparison._json import (
    JsonObject,
    as_list,
    as_object,
    as_str,
    get_object,
    get_str,
)

READ_ONLY_TOOLS = frozenset({"read", "grep", "find", "ls"})


@dataclass(frozen=True)
class ExpectedChild:
    name: str
    provider: str
    model_id: str
    workspace: str


@dataclass(frozen=True)
class ChildSession:
    backend_session_file: str
    lines: Sequence[object]


@dataclass(frozen=True)
class NativeChildActual:
    task_record_found: bool
    execution_state: str | None
    child_id: str | None
    profile_exact: bool
    model_exact: bool
    backend_session_file: bool
    model_change_exact: bool
    wrong_model_tool_call: bool
    read_call: bool
    read_result: bool
    marker_from_read_result: bool
    tool_names: list[str] = field(default_factory=list)
    protected_unchanged: bool = False

    def to_json(self) -> JsonObject:
        return {
            "taskRecordFound": self.task_record_found,
            "executionState": self.execution_state,
            "childId": self.child_id,
            "profileExact": self.profile_exact,
            "modelExact": self.model_exact,
            "backendSessionFile": self.backend_session_file,
            "modelChangeExact": self.model_change_exact,
            "wrongModelToolCall": self.wrong_model_tool_call,
            "readCall": self.read_call,
            "readResult": self.read_result,
            "markerFromReadResult": self.marker_from_read_result,
            "toolNames": list(self.tool_names),
            "protectedUnchanged": self.protected_unchanged,
        }


@dataclass(frozen=True)
class NativeChildEvidence:
    status: Literal["passed", "failed"]
    actual: NativeChildActual


def _text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    parts = as_list(content)
    if parts is None:
        return ""
    return "\n".join(
        part if isinstance(part, str) else (as_str((as_object(part) or {}).get("text")) or "")
        for part in parts
    )


def _matching_record(
    task_records: Sequence[object], expected: ExpectedChild, task_child_id: str | None
) -> JsonObject | None:
    for value in task_records:
        item = as_object(value)
        if item is None:
            continue
        profile = as_object(item.get("profile"))
        child_id = item.get("childId")
        session_file = item.get("backendSessionFile")
        if (
            item.get("executionState") == "succeeded"
            and isinstance(child_id, str)
            and child_id
            and isinstance(session_file, str)
            and session_file
            and (task_child_id is None or child_id == task_child_id)
            and profile is not None
            and profile.get("name") == expected.name
        ):
            return item
    return None


def evaluate_native_child_evidence(
    *,
    task_records: Sequence[object],
    child_sessions: Sequence[ChildSession],
    expected: ExpectedChild,
    marker: str,
    protected_unchanged: bool,
    task_started: bool,
    task_finished: bool,
    task_error: bool,
    task_child_id: str | None = None,
) -> NativeChildEvidence:
    """Require the exact-profile child session to contain a correlated ``read(witness.txt)`` result."""
    record = _matching_record(task_records, expected, task_child_id)
    profile = get_object(record, "profile")
    profile_exact = (
        profile is not None
        and profile.get("name") == expected.name
        and profile.get("provider") == expected.provider
    )
    model_exact = profile is not None and profile.get("modelId") == expected.model_id
    session_file = get_str(record, "backendSessionFile")
    child_lines: Sequence[object] = next(
        (s.lines for s in child_sessions if s.backend_session_file == session_file), []
    )
    model_change_exact = any(
        (item := as_object(line)) is not None
        and item.get("type") == "model_change"
        and item.get("provider") == expected.provider
        and item.get("modelId") == expected.model_id
        for line in child_lines
    )
    messages: list[JsonObject] = []
    for line in child_lines:
        item = as_object(line)
        message = as_object(item.get("message")) if item and item.get("type") == "message" else None
        if message is not None:
            messages.append(message)
    tool_calls: list[JsonObject] = []
    eligible: list[JsonObject] = []
    wrong_model_tool_call = False
    effective_model_exact = False
    for line in child_lines:
        item = as_object(line)
        if item is None:
            continue
        if item.get("type") == "model_change":
            effective_model_exact = (
                item.get("provider") == expected.provider
                and item.get("modelId") == expected.model_id
            )
            continue
        message = as_object(item.get("message")) if item.get("type") == "message" else None
        content = as_list(message.get("content")) if message is not None else None
        if message is None or message.get("role") != "assistant" or content is None:
            continue
        for part in content:
            call = as_object(part)
            if call is None or call.get("type") != "toolCall":
                continue
            tool_calls.append(call)
            if effective_model_exact:
                eligible.append(call)
            else:
                wrong_model_tool_call = True
    tool_names = [str(call.get("name")) for call in tool_calls]
    witness_paths = ("witness.txt", f"{expected.workspace}/witness.txt")
    read_call = next(
        (
            call
            for call in eligible
            if call.get("name") == "read"
            and (arguments := as_object(call.get("arguments"))) is not None
            and arguments.get("path") in witness_paths
        ),
        None,
    )

    def result_of(call: JsonObject) -> list[JsonObject]:
        return [
            m
            for m in messages
            if m.get("role") == "toolResult" and m.get("toolCallId") == call.get("id")
        ]

    read_result = read_call is not None and any(
        m.get("toolName") == "read" and m.get("isError") is False for m in result_of(read_call)
    )
    marker_from_read = read_call is not None and any(
        m.get("isError") is False and marker in _text_from_content(m.get("content"))
        for m in result_of(read_call)
    )
    actual = NativeChildActual(
        task_record_found=record is not None,
        execution_state=get_str(record, "executionState"),
        child_id=get_str(record, "childId"),
        profile_exact=profile_exact,
        model_exact=model_exact,
        backend_session_file=bool(session_file),
        model_change_exact=model_change_exact and effective_model_exact,
        wrong_model_tool_call=wrong_model_tool_call,
        read_call=read_call is not None,
        read_result=read_result,
        marker_from_read_result=marker_from_read,
        tool_names=tool_names,
        protected_unchanged=protected_unchanged,
    )
    passed = (
        task_started
        and task_finished
        and not task_error
        and actual.task_record_found
        and profile_exact
        and model_exact
        and actual.backend_session_file
        and actual.model_change_exact
        and not wrong_model_tool_call
        and actual.read_call
        and actual.read_result
        and actual.marker_from_read_result
        and all(name in READ_ONLY_TOOLS for name in tool_names)
        and protected_unchanged
    )
    return NativeChildEvidence("passed" if passed else "failed", actual)
