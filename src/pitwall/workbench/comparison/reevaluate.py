"""Offline reevaluation of preserved comparison batches (ports of ``comparison-reevaluate.ts`` and ``comparison-child-reevaluate.ts``).

Neither function launches Pi or contacts a provider. They reconstruct the canonical tool events from
the persisted parent session and apply the current acceptance helpers to the original report.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pitwall.workbench.comparison._json import (
    JsonObject,
    all_text,
    as_list,
    as_number,
    as_object,
    as_str,
    dig,
    get_list,
    get_object,
    get_str,
    jsonl_objects,
    parse_json_object,
)
from pitwall.workbench.comparison.acceptance import (
    CommandEvidence,
    assertion_failure_observed,
    command_evidence,
    compaction_protocol_facts,
    compaction_summary_facts,
    medium_search_accepted,
    preserve_read_evidence,
)
from pitwall.workbench.comparison.child_evidence import (
    CHILD_MARKER,
    child_model_matches,
    child_session_header_matches,
    child_session_proves_read,
    child_session_thinking_levels,
    child_session_timing,
    child_session_usage,
    select_single_child_evidence_path,
)
from pitwall.workbench.comparison.evidence import jsonl_files
from pitwall.workbench.comparison.metrics import empty_usage

# Manual review labels for the preserved 2026-09-19 batch; any other report is "reevaluated".
HISTORICAL_CLASSIFICATIONS = {
    "2026-09-20T03:11:30.011Z|A-stock|1": "historical-timeout-abort",
    "2026-09-20T03:11:30.011Z|C-tintin|2": "historical-medium-search-timeout",
}
EXPECTED_MEDIUM_PATH = "medium/sector-7/node/target.mjs"
_UNSEEN = CommandEvidence(started=False, completed=False, successful=False, failed=False)


class ReevaluationError(ValueError):
    """A preserved report or its run directory cannot be reevaluated."""


@dataclass(frozen=True)
class PromptTurn:
    prompt: str
    timestamp: float | None
    events: list[JsonObject]
    text: str
    records: list[JsonObject]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_ms(value: object) -> float | None:
    text = as_str(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp() * 1000
    except ValueError:
        # An unparseable timestamp is not a measurement.
        return None


def _text_content(message: JsonObject) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in (as_object(item) for item in as_list(message.get("content")) or [])
        if part is not None and part.get("type") == "text"
    )


def _canonical_events(records: list[JsonObject]) -> list[JsonObject]:
    events: list[JsonObject] = []
    for record in records:
        message = as_object(record.get("message"))
        if message is None:
            continue
        observed = _iso_ms(record.get("timestamp"))
        if message.get("role") == "assistant":
            for part in as_list(message.get("content")) or []:
                item = as_object(part)
                if item is not None and item.get("type") in ("toolCall", "tool_use"):
                    events.append(
                        {
                            "type": "tool_execution_start",
                            "toolName": item.get("name"),
                            "toolCallId": item.get("id"),
                            "args": item["arguments"]
                            if item.get("arguments") is not None
                            else item.get("input"),
                            "observedAt": observed,
                        }
                    )
        if message.get("role") == "toolResult":
            events.append(
                {
                    "type": "tool_execution_end",
                    "toolName": message.get("toolName"),
                    "toolCallId": message.get("toolCallId"),
                    "result": {"content": message.get("content")},
                    "details": message.get("details"),
                    "isError": message.get("isError") is True,
                    "observedAt": observed,
                }
            )
    return events


def _turns(records: list[JsonObject]) -> list[PromptTurn]:
    starts = [
        index
        for index, record in enumerate(records)
        if get_str(record.get("message"), "role") == "user"
    ]
    turns: list[PromptTurn] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(records)
        piece = records[start:end]
        prompt_record = records[start]
        timestamp = prompt_record.get("timestamp")
        text = "\n".join(
            _text_content(message)
            for record in piece
            if (message := get_object(record, "message")) is not None
            and message.get("role") == "assistant"
        )
        turns.append(
            PromptTurn(
                _text_content(as_object(prompt_record.get("message")) or {}),
                _iso_ms(timestamp) if isinstance(timestamp, str) else None,
                _canonical_events(piece),
                text,
                piece,
            )
        )
    return turns


def _load_session(path: Path) -> list[JsonObject] | None:
    try:
        lines = [line for line in path.read_text().split("\n") if line]
    except OSError:
        return None
    records: list[JsonObject] = []
    for line in lines:
        record = parse_json_object(line)
        if record is None:
            # Preserve malformed evidence as unavailable.
            return None
        records.append(record)
    return records


def _parent_session(run_dir: Path, fixture_path_part: str) -> tuple[Path, list[JsonObject]]:
    candidates: list[tuple[Path, list[JsonObject]]] = []
    for path in jsonl_files(run_dir):
        if "subagent-artifacts" in str(path) or "run-history" in str(path):
            continue
        records = _load_session(path)
        first = records[0] if records else None
        if (
            first is not None
            and first.get("type") == "session"
            and (get_str(first, "cwd") or "").endswith(fixture_path_part)
            and not first.get("parentSession")
        ):
            candidates.append((path, records or []))
    candidates.sort(
        key=lambda item: -sum(1 for r in item[1] if get_str(r.get("message"), "role") == "user")
    )
    if candidates:
        return candidates[0]
    raise ReevaluationError(f"parent session not found for {fixture_path_part}")


def _fixture_path(row: JsonObject) -> str | None:
    for task in as_list(row.get("tasks")) or []:
        for error in as_list((as_object(task) or {}).get("errors")) or []:
            if isinstance(error, str) and "/fixture-" in error:
                match = re.search(r"(/[^' ]+/fixture-[^/' ]+)", error)
                return match.group(1) if match else None
    return None


def _run_directory(report_path: Path, row: JsonObject) -> Path:
    base = report_path.parent
    run_root = next(
        (p for p in sorted(base.iterdir()) if p.is_dir() and p.name.startswith("comparison-run-")),
        None,
    )
    root = base / (run_root.name if run_root else "")
    prefix = f"{row.get('candidate')}-rep-{row.get('repetition')}-"
    match = next(
        (p for p in sorted(root.iterdir()) if p.is_dir() and p.name.startswith(prefix)), None
    )
    return root / (match.name if match else "")


def _assistant_tool_count(turn: PromptTurn) -> int:
    return sum(1 for event in turn.events if event.get("type") == "tool_execution_start")


def _raw_usage(records: list[JsonObject]) -> JsonObject:
    messages = [r for r in records if get_str(r.get("message"), "role") == "assistant"]
    usages = [u for r in messages if (u := get_object(r.get("message"), "usage")) is not None]

    def total(key: str) -> float | None:
        known = len(usages) == len(messages) and all(
            isinstance(u.get(key), int | float) and not isinstance(u.get(key), bool) for u in usages
        )
        return sum(as_number(u.get(key)) or 0 for u in usages) if known else None

    return {
        "assistantMessages": len(messages),
        "reportedMessages": len(usages),
        "input": total("input"),
        "output": total("output"),
    }


def _reevaluate_row(row: JsonObject, report_path: Path, original: JsonObject) -> JsonObject:
    run_dir = _run_directory(report_path, row)
    fixture = _fixture_path(row)
    if not fixture:
        entry = next(
            (p for p in sorted(run_dir.iterdir()) if p.is_dir() and p.name.startswith("fixture-")),
            None,
        )
        if entry is not None:
            fixture = str(entry)
    if not fixture:
        return {
            "candidate": row.get("candidate"),
            "repetition": row.get("repetition"),
            "status": "unavailable",
            "reason": "no fixture path in preserved report",
        }
    parent_path, parent_records = _parent_session(run_dir, fixture)
    turns = _turns(parent_records)
    notes = dig(row, "tasks", 0, "effect", "notes")
    create = (
        len(turns) >= 3
        and all(_assistant_tool_count(turns[i]) > 0 for i in range(3))
        and isinstance(notes, str)
        and notes.startswith("FIRST_LINE\nSECOND_LINE")
    )
    first_repair = turns[3] if len(turns) > 3 else None
    second_repair = turns[4] if len(turns) > 4 else None
    command = "node --test add.test.mjs"
    first_command = command_evidence(first_repair.events, command) if first_repair else _UNSEEN
    final_command = command_evidence(second_repair.events, command) if second_repair else _UNSEEN
    first_output = (
        "\n".join(
            all_text(event.get("result"))
            for event in first_repair.events
            if event.get("type") == "tool_execution_end"
        )
        if first_repair
        else ""
    )
    forced_failure = (
        first_command.started
        and first_command.completed
        and first_command.failed
        and assertion_failure_observed(first_output)
    )
    forced = bool(
        forced_failure
        and final_command.started
        and final_command.completed
        and final_command.successful
        and dig(row, "tasks", 1, "effect", "testsHaveOracle") is True
        and dig(row, "tasks", 1, "effect", "hostOracleExitCode") == 0
        and dig(row, "tasks", 1, "effect", "testsUnchanged") is True
        and dig(row, "tasks", 1, "effect", "protectedUnchanged") is True
    )
    medium_turn = turns[5] if len(turns) > 5 else None
    medium = bool(
        medium_turn is not None
        and medium_search_accepted(medium_turn.text, EXPECTED_MEDIUM_PATH, medium_turn.events)
        and dig(row, "tasks", 2, "effect", "oracleExitCode") == 0
    )
    task_status = {
        "create-read-edit-read": "passed" if create else "failed",
        "forced-failure-repair": "passed"
        if forced
        else "failed"
        if len(turns) > 4
        else "unavailable",
        "medium-search": "passed" if medium else "failed" if len(turns) > 5 else "unavailable",
    }
    compaction_index = next(
        (i for i, r in enumerate(parent_records) if r.get("type") == "compaction"), -1
    )
    compact_record = parent_records[compaction_index] if compaction_index >= 0 else None
    compact_protocol = (
        compaction_protocol_facts(parent_records[compaction_index + 1 :])
        if compact_record
        else None
    )
    continuation = turns[-1] if turns else None
    summary = str((compact_record or {}).get("summary") or "")
    summary_facts = (
        compaction_summary_facts(summary) if compact_record else compaction_summary_facts("")
    )
    continuation_facts = bool(
        continuation
        and "COMPARISON_COMPACT_CHECKPOINT_7F31" in continuation.text
        and re.search(r"add\s*\(\s*2\s*,\s*3\s*\)\s*={1,3}\s*5", continuation.text)
        and re.search(r"add\s*\(\s*-2\s*,\s*3\s*\)\s*={1,3}\s*1", continuation.text)
        and "COMPARISON_PROTECTED_CONTENT" in continuation.text
    )
    continuation_read = bool(
        continuation and preserve_read_evidence(continuation.events, "preserve.txt", fixture)
    )
    if compact_record is None:
        compact = "unavailable"
    elif (
        summary_facts.all()
        and continuation_facts
        and continuation_read
        and compact_protocol is not None
        and compact_protocol.valid
    ):
        compact = "passed"
    else:
        compact = "failed"
    custom_records = [
        r
        for r in parent_records
        if r.get("type") == "custom" and r.get("customType") == "subagents:record"
    ]
    expected_model = str(dig(row, "profile", "modelId") or "")
    expected_provider = str(dig(row, "profile", "provider") or "")
    child_id = str(dig(row, "child", "toolResult", "childId") or "")
    child_record = next(
        (
            r
            for r in custom_records
            if dig(r, "data", "id") == child_id
            and dig(r, "data", "status") == "completed"
            and CHILD_MARKER in str(dig(r, "data", "result") or "")
        ),
        None,
    )
    child_model = (
        next(
            (
                dig(event, "details", "modelName")
                for event in turns[6].events
                if event.get("toolName") == "Agent" and event.get("type") == "tool_execution_end"
            ),
            None,
        )
        if len(turns) > 6
        else None
    )
    child = "unavailable"
    levels_out = list(as_list(dig(row, "child", "childThinkingLevels")) or [])
    reasoning_match = dig(row, "child", "reasoningControlMatch")
    parent_level = dig(row, "child", "parentReasoningLevel")
    completed = dig(row, "child", "toolResult", "completed") is True
    if row.get("candidate") == "B-nicobailon":
        persisted = dig(row, "child", "persistedChildEvidence", "files", 0)
        if isinstance(persisted, str):
            try:
                content = Path(persisted).read_text()
            except OSError:
                # Preserved child artifact unavailable.
                content = None
            if content is not None:
                models = [
                    m
                    for record in jsonl_objects(content)
                    for m in (
                        [record["model"]]
                        if isinstance(record.get("model"), str)
                        else [dig(record, "message", "model")]
                        if isinstance(dig(record, "message", "model"), str)
                        else []
                    )
                    if isinstance(m, str)
                ]
                if (
                    child_session_header_matches(content, child_id, str(parent_path))
                    and child_session_proves_read(content, CHILD_MARKER)
                    and child_model_matches(models, expected_model, expected_provider)
                    and completed
                ):
                    child = "passed"
    elif row.get("candidate") == "C-tintin":
        candidates: list[str] = []
        for session_path in jsonl_files(run_dir):
            try:
                content = session_path.read_text()
            except OSError:
                # Malformed evidence remains unavailable.
                continue
            if session_path != parent_path and child_session_header_matches(
                content, child_id, str(parent_path)
            ):
                candidates.append(str(session_path))
        selected = select_single_child_evidence_path(candidates)
        child_content = ""
        if selected:
            try:
                child_content = Path(selected).read_text()
            except OSError:
                child_content = ""
        levels = child_session_thinking_levels(child_content)
        child_read = len(child_content) > 0 and child_session_proves_read(
            child_content, CHILD_MARKER
        )
        levels_out = list(levels)
        reasoning_match = all(level == parent_level for level in levels) if levels else None
        matches = bool(levels) and all(level == parent_level for level in levels)
        if (
            child_record is not None
            and child_model
            and child_model_matches([str(child_model)], expected_model, expected_provider)
            and completed
            and child_read
            and matches
        ):
            child = "passed"
        else:
            child = "failed" if row.get("repetition") == 2 else "unavailable"
    passed = sum(1 for status in task_status.values() if status == "passed")
    classification = HISTORICAL_CLASSIFICATIONS.get(
        f"{original.get('generatedAt')}|{row.get('candidate')}|{row.get('repetition')}",
        "reevaluated",
    )
    task_times = []
    for index, turn in enumerate(turns[:6]):
        first_end = next(
            (
                e
                for e in turn.events
                if e.get("type") == "tool_execution_end"
                and isinstance(e.get("observedAt"), int | float)
            ),
            None,
        )
        observed = as_number((first_end or {}).get("observedAt"))
        task_times.append(
            {
                "index": index,
                "prompt": turn.prompt[:48],
                "firstUsefulActionMs": observed - turn.timestamp
                if turn.timestamp is not None and observed is not None
                else None,
                "toolCount": _assistant_tool_count(turn),
            }
        )
    return {
        "candidate": row.get("candidate"),
        "repetition": row.get("repetition"),
        "originalStatus": row.get("overallStatus"),
        "classification": classification,
        "parentSession": str(parent_path),
        "fixture": fixture,
        "taskStatus": task_status,
        "acceptedTasks": passed,
        "taskCount": 3,
        "acceptedTaskRate": f"{passed}/3",
        "compact": compact,
        "compactSummaryFacts": summary_facts.to_json(),
        "compactProtocol": compact_protocol.to_json() if compact_protocol else None,
        "continuationRead": continuation_read,
        "child": child,
        "childReasoning": {"parent": parent_level, "child": levels_out, "match": reasoning_match},
        "rawSessionUsage": _raw_usage(parent_records),
        "phaseTiming": {
            "prompts": task_times,
            "modelElapsedMs": None,
            "queueMs": None,
            "queueDisposition": "not observable from persisted candidate session",
        },
        "originalRowPreserved": True,
    }


def _refuse_source_alias(source: Path, output: Path) -> None:
    """Refuse an output that is the source itself, a symlink to it, or a hardlink to it."""
    aliased = source.resolve() == output.resolve() or (
        output.exists() and source.exists() and os.path.samefile(source, output)
    )
    if aliased:
        raise ReevaluationError(
            "source and output reports must differ; the preserved report is never overwritten"
        )


def _write_report(output: Path, text: str) -> None:
    """Write ``text`` to ``output`` through a private temp file and an atomic rename."""
    descriptor, temporary = tempfile.mkstemp(dir=output.parent, prefix=f".{output.name}.")
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def reevaluate_comparison(source: Path | str, output: Path | str) -> JsonObject:
    """Reevaluate every run of a preserved comparison report under the current acceptance helpers."""
    source_path = Path(os.path.abspath(source))
    output_path = Path(os.path.abspath(output))
    _refuse_source_alias(source_path, output_path)
    original = as_object(json.loads(source_path.read_text())) or {}
    reevaluated = [
        _reevaluate_row(row, source_path, original)
        for row in (as_object(item) or {} for item in get_list(original, "results") or [])
    ]
    accepted = sum(int(as_number(r.get("acceptedTasks")) or 0) for r in reevaluated)
    total = sum(int(as_number(r.get("taskCount")) or 0) for r in reevaluated)
    report: JsonObject = {
        "schemaVersion": 1,
        "generatedAt": _now(),
        "kind": "offline-preserved-comparison-reevaluation",
        "sourceOriginalReport": str(source_path),
        "originalReportUnmodified": True,
        "evaluator": {
            "command": "pitwall workbench compare reevaluate ORIGINAL_REPORT OUTPUT_REPORT",
            "currentHelpers": [
                "pitwall.workbench.comparison.acceptance",
                "pitwall.workbench.comparison.child_evidence",
            ],
            "liveExecution": False,
        },
        "aggregate": {
            "acceptedTasks": accepted,
            "taskCount": total,
            "failureInclusiveAcceptedTaskRate": f"{accepted}/{total}",
            "percentage": round(accepted / total * 1000) / 10 if total else None,
            "compactStatuses": [
                f"{r.get('candidate')}-rep{r.get('repetition')}:{r.get('compact')}"
                for r in reevaluated
            ],
        },
        "results": list(reevaluated),
    }
    _write_report(output_path, json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "acceptedTasks": accepted,
                "taskCount": total,
                "rows": len(reevaluated),
            }
        )
    )
    return report


def _parent_session_for(run_dir: Path, fixture: Path) -> Path | None:
    for path in jsonl_files(run_dir):
        if re.search(r"(^|/)run-[^/]+/", str(path)):
            continue
        records = _load_session(path)
        header = records[0] if records else None
        if (
            header is not None
            and header.get("type") == "session"
            and header.get("cwd") == str(fixture)
            and not isinstance(header.get("parentSession"), str)
        ):
            return path
    return None


def _child_session_for(run_dir: Path, parent: Path, child_id: str) -> Path | None:
    matches: list[Path] = []
    for path in jsonl_files(run_dir):
        if path == parent:
            continue
        try:
            content = path.read_text()
        except OSError:
            # Malformed or unrelated evidence remains unavailable.
            continue
        if child_session_header_matches(content, child_id, str(parent)):
            matches.append(path)
    return matches[0] if len(matches) == 1 else None


def _fixture_for(row: JsonObject) -> Path:
    settings = str(dig(row, "childReasoningFixture", "settingsPath") or "")
    suffix = "/.pi/settings.json"
    if not settings.endswith(suffix):
        raise ReevaluationError(
            f"row lacks reasoning fixture path: {row.get('candidate')}#{row.get('repetition')}"
        )
    return Path(settings[: -len(suffix)])


def reevaluate_child_only(source: Path | str, output: Path | str) -> JsonObject:
    """Reevaluate a preserved ``--child-only`` report; the preserved report is never overwritten."""
    source_path = Path(os.path.abspath(source))
    output_path = Path(os.path.abspath(output))
    _refuse_source_alias(source_path, output_path)
    source_bytes = source_path.read_bytes()
    original = as_object(json.loads(source_bytes)) or {}
    if original.get("childOnly") is not True:
        raise ReevaluationError("source report is not a child-only comparison report")
    results: list[JsonObject] = []
    for item in get_list(original, "results") or []:
        row = as_object(item) or {}
        fixture = _fixture_for(row)
        run_dir = fixture.parent
        parent = _parent_session_for(run_dir, fixture)
        child_id = str(dig(row, "child", "toolResult", "childId") or "")
        session_path: str | None = None
        if row.get("candidate") == "B-nicobailon":
            recorded = dig(row, "child", "persistedChildEvidence", "files", 0)
            session_path = recorded if isinstance(recorded, str) else None
        elif row.get("candidate") == "C-tintin" and parent is not None and child_id:
            found = _child_session_for(run_dir, parent, child_id)
            session_path = str(found) if found else None
        content = ""
        if session_path:
            try:
                content = Path(session_path).read_text()
            except OSError:
                session_path = None
        levels = child_session_thinking_levels(content) if content else []
        paired_read_proof = child_session_proves_read(content, CHILD_MARKER) if content else False
        parent_level = dig(row, "child", "parentReasoningLevel")
        reasoning_match = all(level == parent_level for level in levels) if levels else None
        correlated = bool(
            session_path and parent and child_session_header_matches(content, child_id, str(parent))
        )
        timing = child_session_timing(content) if content else None
        old_child = get_object(row, "child") or {}
        child: JsonObject = {
            **old_child,
            "childThinkingLevels": levels,
            "reasoningControlMatch": reasoning_match,
            "modelObserved": old_child.get("modelObserved") is True,
            "persistedChildEvidence": {
                "correlated": correlated,
                "files": [session_path] if session_path else [],
                "sessionTiming": timing.to_json() if timing else None,
            },
            "persistedChildIntervalMs": timing.duration_ms if timing else None,
            "childUsage": (child_session_usage(content) if content else empty_usage()).to_json(),
            "pairedReadProof": paired_read_proof,
            "reason": "correlated authoritative child session; exact parent link where required"
            if correlated
            else "authoritative child session unavailable or ambiguous",
        }
        accepted = (
            child.get("status") == "passed"
            and child["modelObserved"]
            and reasoning_match is True
            and paired_read_proof
            and correlated
            and dig(row, "identity", "exact") is True
            and row.get("protectedUnchanged") is True
            and row.get("protectedTreeUnchanged") is True
            and dig(row, "capture", "complete") is True
        )
        phases = {**(get_object(row, "phases") or {}), "child": child}
        results.append(
            {
                **row,
                "overallStatus": "passed" if accepted else "partial",
                "child": child,
                "phases": phases,
                "childReevaluation": {
                    "parentSession": str(parent) if parent else None,
                    "childSession": session_path,
                    "thinkingLevels": levels,
                    "reasoningControlMatch": reasoning_match,
                    "pairedReadProof": paired_read_proof,
                    "childUsage": child["childUsage"],
                    "childSessionTiming": timing.to_json() if timing else None,
                    "childSessionSha256": hashlib.sha256(content.encode()).hexdigest()
                    if session_path
                    else None,
                },
            }
        )
    passed_runs = sum(1 for row in results if row["overallStatus"] == "passed")
    report: JsonObject = {
        "schemaVersion": 1,
        "generatedAt": _now(),
        "kind": "offline-child-only-preserved-comparison-reevaluation",
        "sourceOriginalReport": str(source_path),
        "sourceOriginalReportSha256": hashlib.sha256(source_bytes).hexdigest(),
        "originalReportUnmodified": True,
        "liveExecution": False,
        "corrections": [
            "select Tintin child sessions by exact parentSession plus documented Explore#<first8> identity",
            "derive typed thinking_level_change, paired child-probe read proof, usage, and persisted session timing from the selected child session",
            "retain the original child-only report and write corrected status in this separate artifact",
        ],
        "aggregate": {
            "passedRuns": passed_runs,
            "totalRuns": len(results),
            "rate": f"{passed_runs}/{len(results)}",
        },
        "results": list(results),
    }
    _write_report(output_path, json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"output": str(output_path), "passedRuns": passed_runs, "totalRuns": len(results)}
        )
    )
    return report
