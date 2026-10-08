"""Lifecycle evidence readers for the comparison runner.

The observer extension itself (two cases in comparison-lifecycle-observer.test.ts, seventeen in
dynamic-contract-acceptance.test.ts) is covered by the node suites under ``pi_extensions/`` and
``fixtures/``; these cases cover how the runner reads its records back.
"""

from __future__ import annotations

import json
from pathlib import Path

from pitwall.workbench.comparison.lifecycle import (
    called_child_tool,
    correlated_tool_interval,
    count_child_events,
    read_lifecycle_observer,
)
from pitwall.workbench.comparison.metrics import measure_child_lifecycle


def write_records(path: Path, records: list[object]) -> None:
    path.write_text(
        "\n".join(record if isinstance(record, str) else json.dumps(record) for record in records)
    )


def test_reads_observer_records_as_comparison_events_for_one_process(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle.jsonl"
    write_records(
        path,
        [
            {
                "event": "subagents:started",
                "observedAt": 125,
                "pid": 7,
                "id": "child-a",
                "sourceEvent": "x",
            },
            {
                "event": "subagents:completed",
                "observedAt": 190,
                "pid": 7,
                "id": "child-a",
                "status": "completed",
            },
            {"event": "subagents:started", "observedAt": 130, "pid": 8, "id": "other"},
            {"event": "subagents:failed", "pid": 7, "id": "no-timestamp"},
            "not json",
            "[]",
        ],
    )
    events = read_lifecycle_observer(path, 7)
    assert events == [
        {"type": "subagents:started", "observedAt": 125, "id": "child-a", "pid": 7},
        {"type": "subagents:completed", "observedAt": 190, "id": "child-a", "pid": 7},
    ]
    assert len(read_lifecycle_observer(path)) == 3
    assert read_lifecycle_observer(tmp_path / "missing.jsonl") == []


def test_observer_records_join_the_parent_tool_call_into_a_lifecycle_measurement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecycle.jsonl"
    write_records(
        path,
        [
            {"event": "subagents:started", "observedAt": 125, "pid": 7, "id": "child-a"},
            {"event": "subagents:completed", "observedAt": 190, "pid": 7, "id": "child-a"},
        ],
    )
    parent = [
        {"type": "tool_execution_start", "toolName": "agent_task", "observedAt": 100, "pid": 7}
    ]
    measurement = measure_child_lifecycle([*parent, *read_lifecycle_observer(path, 7)])
    assert (measurement.launch_wait_ms, measurement.run_ms, measurement.terminal_status) == (
        25,
        65,
        "completed",
    )


def test_counts_child_lifecycle_events_by_kind() -> None:
    events = [
        {"type": "subagents:started"},
        {"type": "subagents:started"},
        {"type": "subagents:completed"},
        {"type": "subagents:failed"},
        {"type": "message_end"},
    ]
    assert count_child_events(events) == {"started": 2, "completed": 1, "failed": 1}


def test_correlates_only_paired_tool_intervals() -> None:
    events = [
        {"type": "tool_execution_start", "toolCallId": "a", "observedAt": 10},
        {"type": "tool_execution_end", "toolCallId": "a", "observedAt": 35},
        {"type": "tool_execution_start", "toolCallId": "b", "observedAt": 50},
        {"type": "tool_execution_end", "toolCallId": "c", "observedAt": 60},
    ]
    assert correlated_tool_interval(events, "a") == 25
    assert correlated_tool_interval(events, "b") is None
    assert correlated_tool_interval(events, None) is None


def test_recognizes_agent_and_subagent_tool_names() -> None:
    assert called_child_tool(["read", "Agent"])
    assert called_child_tool(["subagent"])
    assert not called_child_tool(["read", "bash"])
