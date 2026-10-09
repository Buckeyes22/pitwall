"""Pure evidence checks for hosted acceptance sessions (port of ``hosted-evaluation.ts``)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import Literal

from pitwall.workbench.comparison._json import as_list, as_object, as_str, get_object

HostedToolEvent = Mapping[str, object]

CommandReason = Literal[
    "matched", "unrelated-command", "failed", "missing-end", "missing-call", "bad-output"
]


@dataclass(frozen=True)
class ModelCommandEvaluation:
    matched: bool
    command_seen: bool
    reason: CommandReason
    tool_call_id: str | None = None
    command: str | None = None
    is_error: bool | None = None
    # Earlier completed runs of an allowed command, kept as diagnostic evidence. The final state
    # above is always the last completed run.
    earlier: tuple[ModelCommandEvaluation, ...] = ()


@dataclass(frozen=True)
class CompactionFacts:
    task_marker: bool
    criteria: bool
    changed_file: bool
    validation_state: bool
    next_action: bool

    def all(self) -> bool:
        return all(getattr(self, item.name) for item in fields(self))

    def to_json(self) -> dict[str, bool]:
        return {
            "taskMarker": self.task_marker,
            "criteria": self.criteria,
            "changedFile": self.changed_file,
            "validationState": self.validation_state,
            "nextAction": self.next_action,
        }


@dataclass(frozen=True)
class CompactionRetentionEvaluation:
    summary_facts: CompactionFacts
    retained_suffix_facts: CompactionFacts
    effective_facts: CompactionFacts
    retained_suffix_complete: bool


def checkpoint_facts(text: str, task_marker: str) -> CompactionFacts:
    return CompactionFacts(
        task_marker=task_marker in text,
        criteria=bool(
            re.search(r"add\s*\(\s*2\s*,\s*3\s*\)\s*=\s*5", text, re.IGNORECASE)
            and re.search(r"add\s*\(\s*-2\s*,\s*3\s*\)\s*=\s*1", text, re.IGNORECASE)
        ),
        changed_file="add.mjs" in text,
        validation_state=bool(
            re.search(r"passed|pass|success|green|exit.?code.?0|acceptance", text, re.IGNORECASE)
        ),
        next_action=bool(
            re.search(r"next|continue|rerun|follow", text, re.IGNORECASE)
            and re.search(r"test|validat|accept", text, re.IGNORECASE)
        ),
    )


def evaluate_compaction_retention(
    summary_text: str, retained_suffix_text: str, task_marker: str
) -> CompactionRetentionEvaluation:
    """Evaluate facts from both sides of a native compaction boundary.

    Pi keeps a retained suffix in the session transcript in addition to the generated summary. A
    summary that labels a fact without repeating its concrete value must not make the gate fail
    when that concrete value is still in the suffix.
    """
    summary = checkpoint_facts(summary_text, task_marker)
    retained = checkpoint_facts(retained_suffix_text, task_marker)
    effective = CompactionFacts(
        **{
            item.name: getattr(summary, item.name) or getattr(retained, item.name)
            for item in fields(summary)
        }
    )
    return CompactionRetentionEvaluation(summary, retained, effective, effective.all())


def _tool_result_text(result: object) -> str:
    content = as_list((as_object(result) or {}).get("content"))
    if content is None:
        return ""
    return "\n".join(
        text
        for part in content
        if (text := as_str((as_object(part) or {}).get("text"))) is not None
    )


def _command_of(event: HostedToolEvent) -> str | None:
    return as_str((get_object(event, "args") or {}).get("command"))


def evaluate_model_command(
    events: Sequence[HostedToolEvent], expected: str | Sequence[str]
) -> ModelCommandEvaluation:
    """Verify that the model itself executed the exact acceptance command.

    Pi emits the arguments on ``tool_execution_start`` and the outcome on the correlated
    ``tool_execution_end``. The pinned bash tool returns an error result on a non-zero exit, so a
    paired end with ``isError`` false is the native success signal. Every completed invocation of an
    allowed command is evaluated and the last one is the final state; earlier ones are kept in
    ``earlier``.
    Host-side fixture checks remain a separate effect check.
    """
    commands = [expected] if isinstance(expected, str) else list(expected)
    starts: dict[str, HostedToolEvent] = {}
    command_seen = False
    for event in events:
        call_id = as_str(event.get("toolCallId"))
        if (
            event.get("type") != "tool_execution_start"
            or event.get("toolName") != "bash"
            or not call_id
        ):
            continue
        starts[call_id] = event
        command = _command_of(event)
        if command is not None and command in commands:
            command_seen = True

    completed: list[ModelCommandEvaluation] = []
    for event in events:
        call_id = as_str(event.get("toolCallId"))
        if (
            event.get("type") != "tool_execution_end"
            or event.get("toolName") != "bash"
            or not call_id
        ):
            continue
        start = starts.get(call_id)
        if start is None:
            continue
        command = _command_of(start) or ""
        if command not in commands:
            continue
        if event.get("isError") is not False:
            completed.append(
                ModelCommandEvaluation(
                    False, True, "failed", tool_call_id=call_id, command=command, is_error=True
                )
            )
            continue
        output = _tool_result_text(event.get("result"))
        tap_pass = re.search(r"(?:^|[\n\r])\s*(?:[#ℹ]\s*)?pass\s+1\b", output, re.IGNORECASE)
        tap_fail_zero = re.search(r"(?:^|[\n\r])\s*(?:[#ℹ]\s*)?fail\s+0\b", output, re.IGNORECASE)
        requires_explicit_exit = re.search(r"\becho\b", command, re.IGNORECASE)
        explicit_exit = re.search(r"\bexit(?:\s+code)?\s*[:=]\s*0\b", output, re.IGNORECASE)
        matched = bool(tap_pass and tap_fail_zero and (not requires_explicit_exit or explicit_exit))
        completed.append(
            ModelCommandEvaluation(
                matched=matched,
                command_seen=True,
                tool_call_id=call_id,
                command=command,
                is_error=False,
                reason="matched" if matched else "bad-output",
            )
        )
    if completed:
        # A model may rerun the command after fixing a failure, so the last completed run is the
        # final state; earlier runs stay as diagnostics.
        return replace(completed[-1], earlier=tuple(completed[:-1]))
    missing_end = False
    for call_id, start in starts.items():
        command = _command_of(start)
        if command is None or command not in commands:
            continue
        if not any(
            event.get("type") == "tool_execution_end"
            and event.get("toolName") == "bash"
            and event.get("toolCallId") == call_id
            for event in events
        ):
            missing_end = True
    if missing_end or command_seen:
        return ModelCommandEvaluation(False, command_seen, "missing-end")
    if starts:
        return ModelCommandEvaluation(False, False, "unrelated-command")
    return ModelCommandEvaluation(False, False, "missing-call")
