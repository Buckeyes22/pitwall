"""Acceptance gates over recorded tool events (port of ``comparison-acceptance.ts``).

The comparison runner has no shell interpreter, so command matching fails closed on constructs it
cannot reason about (process substitution, ``false &&``, ``|| true``).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pitwall.workbench.comparison._json import (
    JsonObject,
    as_object,
    as_str,
    get_list,
    get_object,
    parse_json,
    result_text,
)

EventLike = Mapping[str, object]

_QUOTES = ("'", '"')
_FAILURE_OUTPUT = re.compile(
    r"not ok|failureType|fail(?:ed|ure)?\s*[=:]?\s*[1-9][0-9]*\b"
    r"|\bexit(?:ed)?(?:\s+with)?(?:\s+code)?\s*[:=]?\s*[1-9][0-9]*\b",
    re.IGNORECASE,
)
_TEST_RESULT_OUTPUT = re.compile(r"not ok\b|(?:#|ℹ)\s*fail\s+[1-9][0-9]*\b", re.IGNORECASE)
_UNSUPPORTED_SHELL = re.compile(r"\bfalse\s*&&|\|\|\s*true\b|<\s*\(|>\s*\(|\$\s*\(")
_READ_COMMAND = re.compile(r"^(?:cat|sed|head|tail|grep|rg|find|ls|node)\b")
_IMPLEMENTATION = re.compile(r"(?:return\s+)?left\s*\+\s*right")


@dataclass(frozen=True)
class CommandEvidence:
    started: bool
    completed: bool
    successful: bool
    failed: bool

    def to_json(self) -> JsonObject:
        return {
            "started": self.started,
            "completed": self.completed,
            "successful": self.successful,
            "failed": self.failed,
        }


def _unwrap_events(events: Iterable[EventLike]) -> list[EventLike]:
    unwrapped: list[EventLike] = []
    for entry in events:
        inner = get_object(entry, "event")
        unwrapped.append(inner if inner is not None else entry)
    return unwrapped


def protected_tree_hash(entries: Mapping[str, str], excluded: Sequence[str] = ()) -> str:
    """Hash a path-to-hash map, ignoring the excluded paths and the map's insertion order."""
    lines = [f"{path}:{digest}" for path, digest in sorted(entries.items()) if path not in excluded]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _candidate_object(candidate: object) -> JsonObject | None:
    """A tool argument shape: an object, or a JSON string holding one."""
    if isinstance(candidate, str):
        return as_object(parse_json(candidate))
    return as_object(candidate)


def _command_arg(event: EventLike) -> str | None:
    for key in ("args", "arguments", "input"):
        command = as_str((_candidate_object(event.get(key)) or {}).get("command"))
        if command is not None:
            return command
    return None


def _tool_path(event: EventLike) -> str | None:
    for key in ("args", "arguments", "input"):
        arguments = _candidate_object(event.get(key)) or {}
        for name in ("path", "file_path", "filePath"):
            value = as_str(arguments.get(name))
            if value is not None:
                return value
    return None


def _invokes(command: str | None, target: str) -> bool:
    if not command:
        return False
    quote: str | None = None
    escaped = False
    for index in range(len(command) - len(target) + 1):
        character = command[index]
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in _QUOTES:
            quote = character
            continue
        if command[index : index + len(target)] != target:
            continue
        before = command[:index]
        after = command[index + len(target) :]
        if re.search(r"(?:^|[;&|])\s*\Z", before) and re.match(r"(?:\s|\Z|[;&|])", after):
            return True
    return False


def _split_segments(command: str) -> list[str]:
    segments: list[str] = []
    segment = ""
    quote: str | None = None
    escaped = False
    for character in command:
        if escaped:
            segment += character
            escaped = False
            continue
        if character == "\\" and quote != "'":
            segment += character
            escaped = True
            continue
        if quote:
            segment += character
            if character == quote:
                quote = None
            continue
        if character in _QUOTES:
            segment += character
            quote = character
            continue
        if character in ";|&":
            segments.append(segment)
            segment = ""
            continue
        segment += character
    segments.append(segment)
    return segments


def _names(path: str) -> re.Pattern[str]:
    return re.compile(rf"(?:^|[\s'\"(])(?:\./)?{re.escape(path)}(?:[\s'\"),}}]|\Z)")


def _command_reads_path(command: str | None, expected_path: str) -> bool:
    # Shell process/command substitutions can manufacture a target-looking output without reading
    # the fixture, so fail closed on these unsupported constructs.
    if not command or _UNSUPPORTED_SHELL.search(command):
        return False
    # Track `cd` so a read relative to a changed directory still names the fixture file. Only
    # relative targets inside the fixture are followed; anything else stops matching.
    cwd: list[str] | None = []
    for raw in _split_segments(command):
        executable = raw.strip()
        cd = re.match(r"cd(?:\s+(.*))?\Z", executable)
        if cd:
            target = (cd.group(1) or "").strip()
            target = re.sub(r"^(['\"])(.*)\1\Z", r"\2", target)
            if cwd is None or not target or target.startswith(("/", "~")) or target == "-":
                cwd = None
                continue
            for part in target.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if not cwd:
                        cwd = None
                        break
                    cwd.pop()
                else:
                    cwd.append(part)
            continue
        if not _READ_COMMAND.match(executable):
            continue
        if _names(expected_path).search(executable):
            return True
        if not cwd:
            continue
        prefix = "/".join(cwd) + "/"
        if expected_path.startswith(prefix) and _names(expected_path[len(prefix) :]).search(
            executable
        ):
            return True
        # `..` steps out of the directory and back into the target's.
        for depth in range(1, len(cwd) + 1):
            base = "/".join(cwd[: len(cwd) - depth])
            relative = (
                "/".join([".."] * depth)
                + "/"
                + (expected_path[len(base) + 1 :] if base else expected_path)
            )
            if (not base or expected_path.startswith(f"{base}/")) and _names(relative).search(
                executable
            ):
                return True
    return False


def command_evidence(events: Iterable[EventLike], command: str) -> CommandEvidence:
    """Paired start/end evidence that the model ran ``command`` through bash."""
    normalized = _unwrap_events(events)
    starts = [
        event
        for event in normalized
        if event.get("type") == "tool_execution_start"
        and event.get("toolName") == "bash"
        and isinstance(event.get("toolCallId"), str)
        and _invokes(_command_arg(event), command)
    ]
    # A repair turn may run the requested suite once before correcting the implementation and once
    # after. Acceptance follows the terminal paired invocation, while retaining evidence that a
    # command was actually run.
    start = starts[-1] if starts else None
    ids = {str(start["toolCallId"])} if start is not None else set()
    ends = [
        event
        for event in normalized
        if event.get("type") == "tool_execution_end"
        and event.get("toolName") == "bash"
        and isinstance(event.get("toolCallId"), str)
        and str(event["toolCallId"]) in ids
    ]
    output = "\n".join(
        result_text(
            event.get("result") if event.get("result") is not None else event.get("output", "")
        )
        for event in ends
    )
    failed = any(event.get("isError") is True for event in ends) or bool(
        _FAILURE_OUTPUT.search(output)
    )
    result_observed = suite_passed(0, output) or bool(_TEST_RESULT_OUTPUT.search(output))
    return CommandEvidence(
        started=bool(starts),
        completed=bool(ends),
        successful=bool(ends) and result_observed and not failed,
        failed=failed,
    )


def generated_tests_have_oracle(source: str) -> bool:
    """Generated tests must contain an executable ``test(...)``, an assertion, and an ``add(...)`` call."""
    executable = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", "", source)
    return bool(
        re.search(r"\btest\s*\(", executable)
        and re.search(r"\bassert\b|\bstrictEqual\b|\bdeepEqual\b", executable)
        and re.search(r"\badd\s*\(", executable)
    )


def medium_search_accepted(
    text: str, expected_path: str, events: Iterable[EventLike] | None = None
) -> bool:
    """The reported path and implementation line, correlated to a successful search/read result."""
    reported = (
        expected_path in text
        and bool(re.search(r"\badd\b", text))
        and bool(_IMPLEMENTATION.search(text))
    )
    if not reported:
        return False
    # When runtime events are available, require the path and implementation line to come from a
    # correlated successful search/read tool result. Assistant prose alone cannot establish that
    # the model actually searched the fixture.
    if events is None:
        return True
    normalized = _unwrap_events(events)

    def path_matches(value: str | None) -> bool:
        stripped = re.sub(r"^\./", "", value) if value is not None else None
        return stripped is not None and (
            stripped == expected_path or stripped.endswith(f"/{expected_path}")
        )

    by_id = {
        str(event["toolCallId"]): event
        for event in normalized
        if event.get("type") == "tool_execution_start"
        and isinstance(event.get("toolCallId"), str)
        and event.get("toolName") in ("read", "grep", "find", "ls", "bash")
    }
    for event in normalized:
        if (
            event.get("type") != "tool_execution_end"
            or event.get("isError") is True
            or not isinstance(event.get("toolCallId"), str)
        ):
            continue
        start = by_id.get(str(event["toolCallId"]))
        if start is None:
            continue
        output = result_text(
            event.get("result") if event.get("result") is not None else event.get("output", "")
        )
        if not _IMPLEMENTATION.search(output):
            continue
        tool = start.get("toolName")
        if tool == "read" and path_matches(_tool_path(start)):
            return True
        if tool != "read" and (
            expected_path in output
            if tool != "bash"
            else _command_reads_path(_command_arg(start), expected_path)
        ):
            return True
    return False


def suite_passed(exit_code: int | None, output: str) -> bool:
    """Node's TAP summary shows at least one pass and zero failures, with exit code 0."""
    return (
        exit_code == 0
        and bool(re.search(r"(?:#|ℹ)\s*pass\s+[1-9][0-9]*\b", output, re.IGNORECASE))
        and bool(re.search(r"(?:#|ℹ)\s*fail\s+0\b", output, re.IGNORECASE))
    )


@dataclass(frozen=True)
class CompactionSummaryFacts:
    marker: bool
    criteria: bool
    pending_action: bool

    def all(self) -> bool:
        return self.marker and self.criteria and self.pending_action

    def to_json(self) -> JsonObject:
        return {
            "marker": self.marker,
            "criteria": self.criteria,
            "pendingAction": self.pending_action,
        }


def compaction_summary_facts(summary: str) -> CompactionSummaryFacts:
    normalized = re.sub(r"\s+", " ", summary).strip()
    return CompactionSummaryFacts(
        marker="COMPARISON_COMPACT_CHECKPOINT_7F31" in normalized,
        criteria=bool(
            re.search(r"add\s*\(\s*2\s*,\s*3\s*\)\s*={1,3}\s*5", normalized)
            and re.search(r"add\s*\(\s*-2\s*,\s*3\s*\)\s*={1,3}\s*1", normalized)
        ),
        pending_action=bool(
            re.search(r"pending action|next action", normalized, re.IGNORECASE)
            and re.search(r"read\s*[`\"']?\s*preserve\.txt", normalized, re.IGNORECASE)
            and "COMPARISON_PROTECTED_CONTENT" in normalized
        ),
    )


@dataclass(frozen=True)
class CompactionProtocolFacts:
    tool_calls: int
    tool_results: int
    paired_tool_results: int
    orphan_tool_calls: int
    orphan_tool_results: int
    duplicate_tool_call_ids: int
    reasoning_fields: int
    malformed_reasoning_fields: int
    reasoning_observed: bool
    valid: bool

    def to_json(self) -> JsonObject:
        return {
            "toolCalls": self.tool_calls,
            "toolResults": self.tool_results,
            "pairedToolResults": self.paired_tool_results,
            "orphanToolCalls": self.orphan_tool_calls,
            "orphanToolResults": self.orphan_tool_results,
            "duplicateToolCallIds": self.duplicate_tool_call_ids,
            "reasoningFields": self.reasoning_fields,
            "malformedReasoningFields": self.malformed_reasoning_fields,
            "reasoningObserved": self.reasoning_observed,
            "valid": self.valid,
        }


def _valid_string(value: object) -> bool:
    return isinstance(value, str) and len(value) > 0


def compaction_protocol_facts(records: Sequence[Mapping[str, object]]) -> CompactionProtocolFacts:
    """Validate the protocol-bearing part of a retained post-compaction suffix.

    A summary may list a tool action without retaining the assistant tool call, its matching
    result, or provider-specific reasoning fields, so this accepts the raw Pi session record shape
    and checks the persisted suffix directly.
    """
    calls: dict[str, int] = {}
    result_ids: list[tuple[str, int]] = []
    tool_calls = orphan_calls = duplicates = reasoning = malformed = 0
    for index, record in enumerate(records):
        message = get_object(record, "message")
        if message is None:
            continue
        role = message.get("role")
        if role == "assistant":
            for part in get_list(message, "content") or []:
                item = as_object(part)
                if item is None:
                    continue
                if item.get("type") in ("toolCall", "tool_use"):
                    tool_calls += 1
                    call_id = item.get("id")
                    if not _valid_string(call_id):
                        orphan_calls += 1
                    elif isinstance(call_id, str) and call_id in calls:
                        duplicates += 1
                    elif isinstance(call_id, str):
                        calls[call_id] = index
                if item.get("type") in ("thinking", "reasoning"):
                    reasoning += 1
                    if item.get("type") == "thinking":
                        body = item.get("thinking")
                    else:
                        body = (
                            item.get("text")
                            if item.get("text") is not None
                            else item.get("reasoning")
                        )
                    if not _valid_string(body):
                        malformed += 1
                    if "thinkingSignature" in item and not _valid_string(item["thinkingSignature"]):
                        malformed += 1
            for key in ("reasoning_content", "thinking"):
                if key not in message:
                    continue
                reasoning += 1
                if not _valid_string(message.get(key)):
                    malformed += 1
        if role in ("toolResult", "tool_result"):
            call_id = message.get("toolCallId")
            if call_id is None:
                call_id = message.get("tool_call_id")
            result_ids.append((call_id if isinstance(call_id, str) and call_id else "", index))
    paired = orphan_results = 0
    for call_id, index in result_ids:
        call_index = calls.get(call_id)
        if call_index is None or call_index >= index:
            orphan_results += 1
        else:
            paired += 1
    return CompactionProtocolFacts(
        tool_calls=tool_calls,
        tool_results=len(result_ids),
        paired_tool_results=paired,
        orphan_tool_calls=orphan_calls,
        orphan_tool_results=orphan_results,
        duplicate_tool_call_ids=duplicates,
        reasoning_fields=reasoning,
        malformed_reasoning_fields=malformed,
        reasoning_observed=reasoning > 0,
        valid=orphan_calls == 0
        and orphan_results == 0
        and duplicates == 0
        and malformed == 0
        and paired == tool_calls,
    )


def preserve_read_evidence(
    events: Iterable[EventLike], expected_path: str = "preserve.txt", cwd: str | None = None
) -> bool:
    """Prove the post-compaction protected-file read from a paired native read invocation.

    The path is matched exactly so a narrated path, an echoed path, or a read of a similarly named
    file cannot satisfy the gate.
    """
    normalized = _unwrap_events(events)

    def normalize(value: str) -> str:
        return re.sub(r"^\./", "", value.replace("\\", "/"))

    def resolve(value: str) -> str:
        cleaned = normalize(value)
        return normalize(os.path.abspath(os.path.join(cwd, cleaned))) if cwd else cleaned

    expected = resolve(expected_path)
    for start in normalized:
        if (
            start.get("type") != "tool_execution_start"
            or start.get("toolName") != "read"
            or not isinstance(start.get("toolCallId"), str)
        ):
            continue
        path = _tool_path(start)
        if not path or resolve(path) != expected:
            continue
        for end in normalized:
            if (
                end.get("type") == "tool_execution_end"
                and end.get("toolName") == "read"
                and end.get("toolCallId") == start.get("toolCallId")
                and end.get("isError") is not True
                and "COMPARISON_PROTECTED_CONTENT"
                in result_text(
                    end.get("result") if end.get("result") is not None else end.get("output", "")
                )
            ):
                return True
    return False


def assertion_failure_observed(output: str) -> bool:
    return bool(re.search(r"not ok\b", output, re.IGNORECASE)) and bool(
        re.search(
            r"expected values|expected\s*[:=]|actual\s*[:=]|assertionerror", output, re.IGNORECASE
        )
    )


@dataclass(frozen=True)
class RepairInput:
    forced_failure_observed: bool
    first_failure_result: bool
    final_command: CommandEvidence
    tests_have_oracle: bool
    test_suite_passed: bool
    host_oracle_exit_code: int | None
    tests_unchanged: bool
    protected_unchanged: bool


def repair_accepted(repair: RepairInput) -> bool:
    return (
        repair.forced_failure_observed
        and repair.first_failure_result
        and repair.test_suite_passed
        and repair.host_oracle_exit_code == 0
        and repair.tests_have_oracle
        and repair.final_command.started
        and repair.final_command.completed
        and repair.final_command.successful
        and repair.tests_unchanged
        and repair.protected_unchanged
    )


@dataclass(frozen=True)
class CaptureDisposition:
    truncated: bool
    complete: bool
    reason: str

    def to_json(self) -> JsonObject:
        return {"truncated": self.truncated, "complete": self.complete, "reason": self.reason}


def capture_disposition(truncated: bool) -> CaptureDisposition:
    if truncated:
        return CaptureDisposition(True, False, "usage and event-derived completion are incomplete")
    return CaptureDisposition(False, True, "complete within bounded capture")


__all__ = [
    "CaptureDisposition",
    "CommandEvidence",
    "CompactionProtocolFacts",
    "CompactionSummaryFacts",
    "RepairInput",
    "assertion_failure_observed",
    "capture_disposition",
    "command_evidence",
    "compaction_protocol_facts",
    "compaction_summary_facts",
    "generated_tests_have_oracle",
    "medium_search_accepted",
    "preserve_read_evidence",
    "protected_tree_hash",
    "repair_accepted",
    "suite_passed",
]
