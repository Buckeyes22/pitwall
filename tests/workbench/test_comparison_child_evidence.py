"""Comparison child evidence, translated from packages/pi-workbench/tests/comparison-child-evidence.test.ts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.comparison.child_evidence import (
    ChildReceipt,
    ChildSessionTiming,
    child_execution_evidence_accepted,
    child_model_matches,
    child_receipt,
    child_session_header_matches,
    child_session_proves_read,
    child_session_thinking_levels,
    child_session_timing,
    child_session_usage,
    durable_child_execution_evidence_accepted,
    durable_child_record_matches,
    select_single_child_evidence_path,
)
from pitwall.workbench.comparison.evidence import persisted_child_evidence

MARKER = "CHILD_ORACLE_7F31"


def line(value: dict[str, Any]) -> str:
    return json.dumps(value)


def epoch_ms(text: str) -> int:
    return int(
        datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC).timestamp() * 1000
    )


@pytest.mark.parity
def test_requires_a_complete_child_identity_and_does_not_use_a_same_parent_sibling() -> None:
    """Source: comparison-child-evidence.test.ts 'requires a complete child identity and does not use a same-parent sibling'."""
    parent = "/agent/parent.jsonl"
    exact = line({"type": "session", "name": "Explore#child-1", "parentSession": parent})
    sibling = line({"type": "session", "name": "Explore#child-2", "parentSession": parent})
    prefix_only = line({"type": "session", "name": "Explore#child", "parentSession": parent})
    assert child_session_header_matches(exact, "child-1", parent)
    assert not child_session_header_matches(exact, "child-1")
    assert not child_session_header_matches(sibling, "child-1")
    assert not child_session_header_matches(prefix_only, "child-1")
    tintin = "\n".join(
        [
            line({"type": "session", "parentSession": parent}),
            line({"type": "session_info", "name": "Explore#ba205c9f"}),
            line({"type": "thinking_level_change", "thinkingLevel": "low"}),
        ]
    )
    assert child_session_header_matches(tintin, "ba205c9f-a8bc-42f", parent)
    assert not child_session_header_matches(tintin, "ba205c9f-a8bc-42f")
    duplicate_short_name = "\n".join(
        [tintin, line({"type": "session_info", "name": "Explore#ba205c9f"})]
    )
    assert not child_session_header_matches(duplicate_short_name, "ba205c9f-a8bc-42f", parent)
    assert not child_session_header_matches(tintin, "ba205c9f-a8bc-42f", "/agent/sibling.jsonl")
    assert not child_session_header_matches(tintin, "deadbeef-dead-beef", parent)
    assert select_single_child_evidence_path(["/agent/child-1.jsonl"]) == "/agent/child-1.jsonl"
    assert (
        select_single_child_evidence_path(["/agent/child-1.jsonl", "/agent/copy-child-1.jsonl"])
        is None
    )


@pytest.mark.parity
def test_accepts_only_a_successful_tintin_terminal_agent_record() -> None:
    """Source: comparison-child-evidence.test.ts 'accepts only a successful Tintin terminal agent record'."""
    start = {"type": "tool_execution_start", "toolName": "Agent", "toolCallId": "call-1"}
    base = {"type": "tool_execution_end", "toolName": "Agent", "toolCallId": "call-1"}
    completed = {
        **base,
        "result": {
            "details": {"agentId": "t-1", "status": "completed", "outputFile": "/tmp/t-1.jsonl"}
        },
    }
    assert child_receipt([start, completed]) == ChildReceipt(
        tool_call_id="call-1", child_id="t-1", session_file="/tmp/t-1.jsonl", completed=True
    )
    errored = {
        **base,
        "result": {
            "details": {"agentId": "t-1", "status": "error", "outputFile": "/tmp/t-1.jsonl"}
        },
    }
    assert not child_receipt([start, errored]).completed
    background = {**base, "result": {"details": {"agentId": "t-1", "status": "background"}}}
    assert not child_receipt([start, background]).completed
    with_model = {
        **base,
        "result": {"details": {"agentId": "t-2", "status": "completed", "modelName": "qwen"}},
    }
    durable = {
        "type": "custom",
        "customType": "subagents:record",
        "data": {"id": "t-2", "status": "completed", "result": MARKER},
    }
    receipt = child_receipt([start, with_model, durable])
    assert receipt.child_id == "t-2"
    assert receipt.completed
    assert receipt.result_marker is True
    assert receipt.model_name == "qwen"
    other = {
        "type": "custom",
        "customType": "subagents:record",
        "data": {"id": "other", "status": "completed", "result": MARKER},
    }
    unmatched = {**base, "result": {"details": {"agentId": "t-3", "status": "completed"}}}
    assert child_receipt([start, unmatched, other]).result_marker is None


@pytest.mark.parity
def test_requires_a_successful_nicobailon_result_row_not_a_launch_id_or_failed_row() -> None:
    """Source: comparison-child-evidence.test.ts 'requires a successful Nicobailon result row, not a launch id or failed row'."""
    start = {"type": "tool_execution_start", "toolName": "subagent", "toolCallId": "call-2"}
    base = {"type": "tool_execution_end", "toolName": "subagent", "toolCallId": "call-2"}

    def result(details: dict[str, Any]) -> dict[str, Any]:
        return {**base, "result": {"details": details}}

    ok_row = {"exitCode": 0, "sessionFile": "/tmp/run-1.jsonl"}
    assert child_receipt([start, result({"runId": "run-1", "results": [ok_row]})]).completed
    assert not child_receipt([start, result({"asyncId": "run-2"})]).completed
    failed_row = {"exitCode": 1, "sessionFile": "/tmp/run-3.jsonl"}
    assert not child_receipt([start, result({"runId": "run-3", "results": [failed_row]})]).completed
    detached_row = {"exitCode": 0, "detached": True, "sessionFile": "/tmp/run-4.jsonl"}
    assert not child_receipt(
        [start, result({"runId": "run-4", "results": [detached_row]})]
    ).completed
    orphan = {**base, "result": {"details": {"agentId": "orphan", "status": "completed"}}}
    assert not child_receipt([orphan]).completed


@pytest.mark.parity
def test_requires_a_correlated_successful_read_result_not_a_narrated_marker_or_mismatched_call() -> (
    None
):
    """Source: comparison-child-evidence.test.ts 'requires a correlated successful read result, not a narrated marker or mismatched call'."""

    def assistant(call_id: str, path: str = "child-probe.txt") -> str:
        return line(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": call_id,
                            "name": "read",
                            "arguments": {"path": path},
                        }
                    ],
                },
            }
        )

    def result(call_id: str, is_error: bool = False) -> str:
        return line(
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": call_id,
                    "isError": is_error,
                    "content": [{"type": "text", "text": MARKER}],
                },
            }
        )

    assert child_session_proves_read(f"{assistant('call')}\n{result('call')}", MARKER)
    assert not child_session_proves_read(f"{assistant('call')}\n{result('other')}", MARKER)
    assert not child_session_proves_read(f"{assistant('call')}\n{result('call', True)}", MARKER)
    narrated = line(
        {
            "type": "message",
            "message": {"role": "assistant", "content": [{"type": "text", "text": MARKER}]},
        }
    )
    assert not child_session_proves_read(narrated, MARKER)
    wrong_path = f"{assistant('wrong', 'other-child-probe.txt')}\n{result('wrong')}"
    assert not child_session_proves_read(wrong_path, MARKER)
    decoy = line(
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "decoy",
                        "name": "read",
                        "arguments": {"path": "notes.txt", "note": "child-probe.txt"},
                    }
                ],
            },
        }
    )
    assert not child_session_proves_read(f"{decoy}\n{result('decoy')}", MARKER)
    echo_session = "\n".join(
        [
            line(
                {
                    "type": "tool_start",
                    "toolName": "bash",
                    "toolCallId": "echo",
                    "argsPreview": "echo child-probe.txt",
                }
            ),
            line({"type": "tool_end", "toolName": "bash", "toolCallId": "echo", "isError": False}),
            line(
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "echo",
                        "content": [{"type": "text", "text": MARKER}],
                    },
                }
            ),
        ]
    )
    assert not child_session_proves_read(echo_session, MARKER)
    absolute = "/fixture/child-probe.txt"
    assert child_session_proves_read(
        f"{assistant('absolute', absolute)}\n{result('absolute')}", MARKER, absolute
    )
    cwd = "/fixture/project"
    relative_session = "\n".join(
        [line({"type": "session", "cwd": cwd}), assistant("cwd"), result("cwd")]
    )
    assert child_session_proves_read(relative_session, MARKER, f"{cwd}/child-probe.txt")
    assert not child_session_proves_read(relative_session, MARKER, "/other/child-probe.txt")


@pytest.mark.parity
def test_deduplicates_canonical_assistant_records_and_preserves_missing_usage_as_unknown() -> None:
    """Source: comparison-child-evidence.test.ts 'deduplicates canonical assistant records and preserves missing usage as unknown'."""
    record = line(
        {
            "type": "message",
            "id": "assistant-1",
            "message": {"role": "assistant", "usage": {"input": 3, "output": 2}},
        }
    )
    missing = line(
        {"type": "message", "id": "assistant-2", "message": {"role": "assistant", "content": []}}
    )
    deduplicated = child_session_usage(f"{record}\n{record}")
    assert (deduplicated.input, deduplicated.output) == (3, 2)
    assert (deduplicated.assistant_messages, deduplicated.reported_messages) == (1, 1)
    partial = child_session_usage(f"{record}\n{missing}")
    assert (partial.input, partial.output) == (None, None)
    assert (partial.assistant_messages, partial.reported_messages) == (2, 1)


@pytest.mark.parity
def test_reads_reasoning_only_from_typed_child_session_events_not_nested_parent_or_result_text() -> (
    None
):
    """Source: comparison-child-evidence.test.ts 'reads reasoning only from typed child session events, not nested parent or result text'."""
    content = "\n".join(
        [
            line(
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "parent requested thinkingLevel high"}
                        ],
                    },
                }
            ),
            line(
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "details": {"thinkingLevel": "high"},
                        "content": [{"type": "text", "text": "nested thinkingLevel high"}],
                    },
                }
            ),
            line({"type": "thinking_level_change", "thinkingLevel": "low"}),
        ]
    )
    assert child_session_thinking_levels(content) == ["low"]
    prose = line(
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "thinkingLevel: low"}],
            },
        }
    )
    assert child_session_thinking_levels(prose) == []


@pytest.mark.parity
def test_measures_only_the_correlated_persisted_child_session_interval() -> None:
    """Source: comparison-child-evidence.test.ts 'measures only the correlated persisted child session interval'."""
    content = "\n".join(
        [
            line({"type": "session", "timestamp": "2026-09-20T03:00:00.000Z"}),
            line({"type": "message", "timestamp": "2026-09-20T03:00:02.500Z"}),
            line({"type": "message", "timestamp": "2026-09-20T03:00:07.000Z"}),
        ]
    )
    assert child_session_timing(content) == ChildSessionTiming(
        started_at_ms=epoch_ms("2026-09-20T03:00:00.000Z"),
        finished_at_ms=epoch_ms("2026-09-20T03:00:07.000Z"),
        duration_ms=7000,
    )
    single = line({"type": "session", "timestamp": "2026-09-20T03:00:00.000Z"})
    assert child_session_timing(single) is None


@pytest.mark.parity
def test_requires_the_durable_record_to_match_the_child_identity_and_selected_model() -> None:
    """Source: comparison-child-evidence.test.ts 'requires the durable record to match the child identity and selected model'."""
    content = line(
        {
            "type": "custom",
            "customType": "subagents:record",
            "data": {"id": "child-1", "status": "completed", "result": MARKER},
        }
    )
    assert durable_child_record_matches(content, "child-1", MARKER)
    assert not durable_child_record_matches(content, "child-2", MARKER)
    assert not durable_child_record_matches(
        content.replace("completed", "error"), "child-1", MARKER
    )
    expected = "qwen3.8-27b-huihui-int8-mtp"
    assert child_model_matches([expected], expected)
    assert child_model_matches([f"workbench-local-qwen/{expected}"], expected)
    assert child_model_matches(
        [f"workbench-local-qwen/{expected}:high"], expected, "workbench-local-qwen"
    )
    assert not child_model_matches(
        [f"other-provider/{expected}:high"], expected, "workbench-local-qwen"
    )
    assert not child_model_matches(
        [f"workbench-local-qwen/{expected}:high-extra"], expected, "workbench-local-qwen"
    )
    assert not child_model_matches(["other-model"], expected)
    assert not child_model_matches(["provider:qwen-wrong"], "qwen")
    assert not child_model_matches(["provider/qwen:wrong-model"], "qwen")
    assert not child_model_matches([f"provider/{expected}-sibling"], expected)
    assert not child_model_matches([f"provider/{expected}:wrong-model"], expected)
    assert not child_model_matches([f"provider/{expected}:high-extra"], expected)


@pytest.mark.parity
def test_does_not_accept_a_parent_marker_without_persisted_child_read_proof() -> None:
    """Source: comparison-child-evidence.test.ts 'does not accept a parent marker without persisted child read proof'."""
    accepted = {
        "invoked": True,
        "completed": True,
        "model_matched": True,
        "persisted_read": True,
        "result_marker": True,
    }
    assert child_execution_evidence_accepted(**accepted)
    assert not child_execution_evidence_accepted(**{**accepted, "persisted_read": False})
    assert not child_execution_evidence_accepted(**{**accepted, "result_marker": False})
    assert durable_child_execution_evidence_accepted(
        invoked=True, completed=True, model_matched=True, result_marker=True
    )
    assert not durable_child_execution_evidence_accepted(
        invoked=True, completed=True, model_matched=True, result_marker=False
    )


def _child_session_text(parent: str, *, child_id: str, session_parent: str | None = None) -> str:
    return "\n".join(
        line(record)
        for record in (
            {"type": "session", "parentSession": session_parent or parent, "childId": child_id},
            {
                "type": "message",
                "id": "m1",
                "message": {
                    "role": "assistant",
                    "usage": {"input": 4, "output": 2},
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "r1",
                            "name": "read",
                            "arguments": {"path": "child-probe.txt"},
                        }
                    ],
                },
            },
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "r1",
                    "isError": False,
                    "content": [{"type": "text", "text": MARKER}],
                },
            },
        )
    )


@pytest.mark.parametrize(
    ("header_child", "header_parent"),
    [("other-child", None), ("child-1", "/work/some-other-parent.jsonl")],
)
def test_a_receipt_session_file_must_name_the_expected_child_and_parent(
    tmp_path: Path, header_child: str, header_parent: str | None
) -> None:
    parent = tmp_path / "parent.jsonl"
    parent.write_text(line({"type": "session", "cwd": str(tmp_path)}))
    session = tmp_path / "other.jsonl"
    session.write_text(
        _child_session_text(str(parent), child_id=header_child, session_parent=header_parent)
    )
    receipt = ChildReceipt(child_id="child-1", session_file=str(session), completed=True)
    evidence = persisted_child_evidence(tmp_path, tmp_path, MARKER, receipt, str(parent))
    assert evidence.correlated is False
    assert evidence.files == []
    assert evidence.child_usage.reported_messages == 0


def test_a_receipt_session_file_that_names_the_expected_child_and_parent_is_correlated(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent.jsonl"
    parent.write_text(line({"type": "session", "cwd": str(tmp_path)}))
    session = tmp_path / "child.jsonl"
    session.write_text(_child_session_text(str(parent), child_id="child-1"))
    receipt = ChildReceipt(child_id="child-1", session_file=str(session), completed=True)
    evidence = persisted_child_evidence(tmp_path, tmp_path, MARKER, receipt, str(parent))
    assert evidence.correlated is True
    assert evidence.files == [str(session)]


def test_a_missing_parent_path_gets_its_own_reason(tmp_path: Path) -> None:
    session = tmp_path / "child.jsonl"
    session.write_text(_child_session_text("/p.jsonl", child_id="child-1"))
    receipt = ChildReceipt(child_id="child-1", session_file=str(session), completed=True)
    no_parent = persisted_child_evidence(tmp_path, tmp_path, MARKER, receipt, None)
    assert no_parent.correlated is False
    assert no_parent.reason == "no parent session path is known to validate the child session"
    wrong = persisted_child_evidence(tmp_path, tmp_path, MARKER, receipt, "/other.jsonl")
    assert wrong.reason == (
        "authoritative child session header does not name the expected child and parent"
    )
