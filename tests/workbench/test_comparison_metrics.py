"""Comparison measurements, translated from packages/pi-workbench/tests/comparison-metrics.test.ts."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.workbench.comparison.metrics import (
    ChildLifecycleMeasurement,
    UsageTotals,
    add_usage,
    empty_usage,
    measure_child_lifecycle,
    measure_turn,
    summarize_admission_accounting,
    task_accepted,
    usage_for_messages,
)

NONE = ChildLifecycleMeasurement(launch_wait_ms=None, run_ms=None, terminal_status=None)


def launch(at: float, tool: str = "agent_task", **extra: Any) -> dict[str, Any]:
    return {"type": "tool_execution_start", "toolName": tool, "observedAt": at, **extra}


def lifecycle(kind: str, child: str | None, at: float, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"type": f"subagents:{kind}", "observedAt": at, **extra}
    if child is not None:
        event["id"] = child
    return event


@pytest.mark.parity
def test_separates_observable_child_launch_wait_from_child_runtime() -> None:
    """Source: comparison-metrics.test.ts 'separates observable child launch wait from child runtime'."""
    assert measure_child_lifecycle(
        [launch(100), lifecycle("started", "child-a", 125), lifecycle("completed", "child-a", 190)]
    ) == ChildLifecycleMeasurement(launch_wait_ms=25, run_ms=65, terminal_status="completed")
    assert (
        measure_child_lifecycle(
            [launch(100), lifecycle("started", None, 125), lifecycle("completed", None, 190)]
        )
        == NONE
    )
    failed = measure_child_lifecycle(
        [launch(100), lifecycle("started", "child-a", 125), lifecycle("failed", "child-a", 140)]
    )
    assert (failed.launch_wait_ms, failed.run_ms, failed.terminal_status) == (25, 15, "failed")
    assert (
        measure_child_lifecycle(
            [
                launch(100),
                lifecycle("started", "child-a", 110),
                lifecycle("started", "child-b", 120),
                lifecycle("completed", "child-b", 130),
                lifecycle("completed", "child-a", 150),
            ]
        )
        == NONE
    )
    assert measure_child_lifecycle(
        [
            launch(100, "subagent", args={"action": "list", "capabilities": True}),
            launch(200, "subagent", args={"agent": "reviewer", "task": "read the fixture"}),
            lifecycle("started", "run-1", 225),
            lifecycle("completed", "run-1", 290),
        ]
    ) == ChildLifecycleMeasurement(launch_wait_ms=25, run_ms=65, terminal_status="completed")
    assert (
        measure_child_lifecycle(
            [
                launch(100, "subagent", args={"agent": "reviewer", "task": "first"}),
                launch(110, "subagent", args={"agent": "reviewer", "task": "second"}),
                lifecycle("started", "run-1", 125),
                lifecycle("completed", "run-1", 190),
            ]
        )
        == NONE
    )
    assert measure_child_lifecycle(
        [
            launch(
                300,
                "Agent",
                args={
                    "prompt": "read the fixture",
                    "subagent_type": "Explore",
                    "run_in_background": False,
                },
            ),
            lifecycle("started", "agent-a", 315),
            lifecycle("completed", "agent-a", 350),
        ]
    ) == ChildLifecycleMeasurement(launch_wait_ms=15, run_ms=35, terminal_status="completed")
    assert (
        measure_child_lifecycle(
            [
                launch(100),
                lifecycle("started", "child-a", 125, pid=101),
                lifecycle("completed", "child-a", 190, pid=202),
            ]
        )
        == NONE
    )
    assert (
        measure_child_lifecycle(
            [
                launch(100),
                lifecycle("completed", "child-a", 90),
                lifecycle("started", "child-a", 100),
            ]
        )
        == NONE
    )


@pytest.mark.parity
def test_keeps_provider_reported_usage_separate_from_unavailable_queue_timing() -> None:
    """Source: comparison-metrics.test.ts 'keeps provider reported usage separate from unavailable queue timing'."""
    usage = usage_for_messages(
        [
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {
                        "input": 12,
                        "output": 3,
                        "cacheRead": 4,
                        "cacheWrite": 1,
                        "reasoning": 2,
                    },
                },
            },
            {
                "type": "message_end",
                "message": {"role": "assistant", "usage": {"input": 5, "output": 2}},
            },
        ]
    )
    assert usage == UsageTotals(
        input=17,
        output=5,
        cache_read=None,
        cache_write=None,
        reasoning=None,
        reported_messages=2,
        assistant_messages=2,
    )
    measured = measure_turn(
        [
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "toolCallId": "read-1",
                "observedAt": 110,
            },
            {
                "type": "tool_execution_end",
                "toolName": "read",
                "toolCallId": "read-1",
                "observedAt": 130,
                "isError": True,
            },
        ],
        100,
        150,
    )
    assert measured.wall_ms == 50
    assert measured.latency_to_useful_action_ms == 10
    assert measured.tools == ["read"]
    assert measured.tool_errors == 1
    assert measured.queue_ms is None
    assert measured.tool_execution_ms == 20
    assert measured.non_tool_elapsed_ms == 30
    assert measured.model_elapsed_ms is None
    assert measured.usage == empty_usage()
    parallel = measure_turn(
        [
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "toolCallId": "parallel-a",
                "observedAt": 110,
            },
            {
                "type": "tool_execution_start",
                "toolName": "grep",
                "toolCallId": "parallel-b",
                "observedAt": 115,
            },
            {
                "type": "tool_execution_end",
                "toolName": "read",
                "toolCallId": "parallel-a",
                "observedAt": 130,
            },
            {
                "type": "tool_execution_end",
                "toolName": "grep",
                "toolCallId": "parallel-b",
                "observedAt": 145,
            },
        ],
        100,
        150,
    )
    assert (
        parallel.tool_execution_ms,
        parallel.non_tool_elapsed_ms,
        parallel.model_elapsed_ms,
    ) == (
        35,
        15,
        None,
    )
    model_only = measure_turn(
        [
            {"type": "message_start", "observedAt": 105, "message": {"role": "assistant"}},
            {
                "type": "message_end",
                "observedAt": 140,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
            },
        ],
        100,
        150,
    )
    assert (
        model_only.tool_execution_ms,
        model_only.non_tool_elapsed_ms,
        model_only.model_elapsed_ms,
    ) == (
        0,
        50,
        35,
    )
    multi_request = measure_turn(
        [
            {
                "type": "message_end",
                "observedAt": 120,
                "message": {"role": "assistant", "usage": {"input": 12, "output": 2}},
            },
            {
                "type": "message_end",
                "observedAt": 180,
                "message": {"role": "assistant", "usage": {"input": 99, "output": 3}},
            },
        ],
        100,
        200,
    )
    assert (
        multi_request.first_request_usage.input,
        multi_request.first_request_usage.output,
        multi_request.first_request_usage.assistant_messages,
    ) == (12, 2, 1)
    assert (
        multi_request.usage.input,
        multi_request.usage.output,
        multi_request.usage.assistant_messages,
    ) == (111, 5, 2)
    unfinished = measure_turn(
        [
            {
                "type": "tool_execution_start",
                "toolName": "read",
                "toolCallId": "read-2",
                "observedAt": 110,
            }
        ],
        100,
        150,
    )
    assert unfinished.tool_execution_ms is None
    unstarted = measure_turn(
        [{"type": "tool_execution_end", "toolName": "read", "observedAt": 130}], 100, 150
    )
    assert unstarted.tool_execution_ms is None
    assert (
        measure_turn([{"type": "auto_retry_start"}, {"type": "auto_retry_end"}], 0, 10).retries == 1
    )
    untimed = measure_turn([{"type": "message_end", "message": {"role": "assistant"}}], 100, 150)
    assert untimed.latency_to_useful_action_ms is None
    thinking = measure_turn(
        [
            {
                "type": "message_update",
                "observedAt": 105,
                "assistantMessageEvent": {"type": "thinking_delta"},
            }
        ],
        100,
        150,
    )
    assert thinking.latency_to_useful_action_ms is None


@pytest.mark.parity
def test_adds_independent_run_totals_without_treating_missing_fields_as_zero_token_claims() -> None:
    """Source: comparison-metrics.test.ts 'adds independent run totals without treating missing fields as zero token claims'."""
    assert add_usage(
        UsageTotals(1, 2, None, 4, 5, 1, 1), UsageTotals(2, 3, 4, 5, 6, 1, 1)
    ) == UsageTotals(3, 5, None, 9, 11, 2, 2)
    partial = usage_for_messages(
        [
            {
                "type": "message_end",
                "message": {"role": "assistant", "usage": {"input": 1, "output": 1}},
            },
            {"type": "message_end", "message": {"role": "assistant"}},
        ]
    )
    assert (
        partial.input,
        partial.output,
        partial.reported_messages,
        partial.assistant_messages,
    ) == (
        None,
        None,
        1,
        2,
    )
    assert add_usage(empty_usage(), UsageTotals(1, 1, 0, 0, 0, 1, 1)).input == 1
    assert task_accepted({"accepted": True})
    assert not task_accepted({"accepted": False})
    assert not task_accepted({"accepted": True}, ["tool failed"])
    assert not task_accepted({"answerMatched": True})
    assert not task_accepted({"accepted": True, "protectedUnchanged": False})
    assert not task_accepted({"accepted": True, "answerMatched": False})
    assert not task_accepted({"accepted": True, "finalExitCode": 1})
    assert not task_accepted({"accepted": True, "testsUnchanged": False})


@pytest.mark.parity
def test_sums_lock_waits_from_native_request_records_and_counts_settlements() -> None:
    """Source: comparison-metrics.test.ts 'admission accounting summary: sums lock waits from native request records and counts settlements'."""
    summary = summarize_admission_accounting(
        [
            {"type": "native_request", "requestId": "a", "queuedAt": 1_000, "acquiredAt": 1_000},
            {"type": "native_settled", "requestId": "a", "queuedAt": 1_000, "acquiredAt": 1_000},
            {"type": "native_request", "requestId": "b", "queuedAt": 2_000, "acquiredAt": 2_250},
            {"type": "native_settled", "requestId": "b", "queuedAt": 2_000, "acquiredAt": 2_250},
            {"type": "native_released", "requestId": "b"},
            {"type": "provider_request"},
        ]
    )
    assert summary.instrumented
    assert (summary.request_count, summary.settled_count) == (2, 2)
    assert (summary.queue_ms, summary.max_queue_ms, summary.unavailable) == (250, 250, 0)


@pytest.mark.parity
def test_reports_a_request_without_both_timestamps_as_unavailable_rather_than_zero() -> None:
    """Source: comparison-metrics.test.ts 'admission accounting summary: reports a request without both timestamps as unavailable rather than zero'."""
    summary = summarize_admission_accounting(
        [
            {"type": "native_request", "requestId": "a", "queuedAt": 1_000},
            {"type": "native_request", "requestId": "b", "queuedAt": 2_000, "acquiredAt": 2_010},
        ]
    )
    assert (summary.request_count, summary.unavailable) == (2, 1)
    assert (summary.queue_ms, summary.max_queue_ms) == (10, 10)


@pytest.mark.parity
def test_is_uninstrumented_when_no_native_request_was_recorded() -> None:
    """Source: comparison-metrics.test.ts 'admission accounting summary: is uninstrumented when no native request was recorded'."""
    summary = summarize_admission_accounting([])
    assert not summary.instrumented
    assert summary.queue_ms is None
    assert summary.request_count == 0
