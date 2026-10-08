"""Comparison acceptance gates, translated from packages/pi-workbench/tests/comparison-acceptance.test.ts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from pitwall.workbench.comparison.acceptance import (
    CaptureDisposition,
    CommandEvidence,
    CompactionSummaryFacts,
    RepairInput,
    assertion_failure_observed,
    capture_disposition,
    command_evidence,
    compaction_protocol_facts,
    compaction_summary_facts,
    generated_tests_have_oracle,
    medium_search_accepted,
    preserve_read_evidence,
    protected_tree_hash,
    repair_accepted,
    suite_passed,
)

Event = dict[str, Any]

GOOD_END: Event = {
    "type": "tool_execution_end",
    "toolName": "bash",
    "toolCallId": "run",
    "isError": False,
    "result": {"content": [{"type": "text", "text": "# pass 1\n# fail 0"}]},
}
GOOD_START: Event = {
    "type": "tool_execution_start",
    "toolName": "bash",
    "toolCallId": "run",
    "args": {"command": "node --test add.test.mjs"},
}
TEST_COMMAND = "node --test add.test.mjs"
TARGET = "medium/sector-7/node/target.mjs"
REPORT = f"{TARGET} exports add and return left + right"
ADD_SOURCE = "export function add(left, right) { return left + right; }"


def text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def with_(base: Event, **changes: Any) -> Event:
    return {**base, **changes}


@pytest.mark.parity
def test_requires_paired_command_evidence_and_does_not_treat_fail_0_as_failure() -> None:
    """Source: comparison-acceptance.test.ts 'requires paired command evidence and does not treat fail 0 as failure'."""
    assert command_evidence([GOOD_START, GOOD_END], TEST_COMMAND) == CommandEvidence(
        started=True, completed=True, successful=True, failed=False
    )
    echoed = with_(GOOD_START, args={"command": "echo node --test add.test.mjs"})
    assert not command_evidence([echoed, GOOD_END], TEST_COMMAND).started
    quoted = with_(GOOD_START, args={"command": 'echo "before; node --test add.test.mjs"'})
    assert not command_evidence([quoted, GOOD_END], TEST_COMMAND).started
    piped = with_(
        GOOD_START, args={"command": "cd fixture && node --test add.test.mjs 2>&1 | grep pass"}
    )
    assert command_evidence([piped, GOOD_END], TEST_COMMAND).started
    masked = with_(GOOD_START, args={"command": "false && node --test add.test.mjs || true"})
    assert not command_evidence(
        [masked, with_(GOOD_END, result={"content": []})], TEST_COMMAND
    ).successful
    for output in ("# pass 0\n# fail 10", "# pass 0\n# fail 1"):
        failed_end = with_(GOOD_END, result=text_result(output))
        assert not command_evidence([GOOD_START, failed_end], TEST_COMMAND).successful
    exited = with_(GOOD_END, result=text_result("# pass 1\n# fail 0\nProcess exited with code 12"))
    assert not command_evidence([GOOD_START, exited], TEST_COMMAND).successful
    orphan_end = with_(GOOD_END, toolCallId="other")
    assert not command_evidence([orphan_end, GOOD_START], TEST_COMMAND).completed
    repair_turn = [
        with_(GOOD_START, toolCallId="first"),
        with_(GOOD_END, toolCallId="first", result=text_result("# pass 0\n# fail 2")),
        with_(GOOD_START, toolCallId="last"),
        with_(GOOD_END, toolCallId="last", result=text_result("# pass 2\n# fail 0")),
    ]
    assert command_evidence(repair_turn, TEST_COMMAND).successful


@pytest.mark.parity
def test_rejects_arbitrary_failures_comment_only_tests_and_collateral_changes() -> None:
    """Source: comparison-acceptance.test.ts 'rejects arbitrary failures, comment-only tests, and collateral changes'."""
    assert not generated_tests_have_oracle("// test(add(2,3)) assert 5")
    assert generated_tests_have_oracle(
        "import {test} from 'node:test'; import assert from 'node:assert'; "
        "test('add',()=>assert.equal(add(2,3),5));"
    )
    base = RepairInput(
        forced_failure_observed=True,
        first_failure_result=True,
        final_command=command_evidence([GOOD_START, GOOD_END], TEST_COMMAND),
        tests_have_oracle=True,
        test_suite_passed=True,
        host_oracle_exit_code=0,
        tests_unchanged=True,
        protected_unchanged=True,
    )
    assert repair_accepted(base)
    assert not repair_accepted(replace(base, first_failure_result=False))
    assert not repair_accepted(replace(base, host_oracle_exit_code=1))
    assert not repair_accepted(replace(base, protected_unchanged=False))
    assert suite_passed(0, "# pass 1\n# fail 0")
    assert suite_passed(0, "# pass 10\n# fail 0")
    assert not suite_passed(0, "# pass 0\n# fail 0")
    assert not suite_passed(0, "# pass 1\n# fail 1")
    pending = (
        "- Pending action: read `preserve.txt` after compaction and report "
        "COMPARISON_PROTECTED_CONTENT\n"
    )
    assert compaction_summary_facts(
        f"# Checkpoint\n{pending}- Criteria: add(2,3)=5 and add(-2,3)=1"
    ) == CompactionSummaryFacts(marker=False, criteria=True, pending_action=True)
    assert compaction_summary_facts(
        f"# Checkpoint COMPARISON_COMPACT_CHECKPOINT_7F31\n{pending}"
        "- Criteria: add(2,3) === 5 and add(-2,3) === 1"
    ) == CompactionSummaryFacts(marker=True, criteria=True, pending_action=True)

    preserve_start: Event = {
        "type": "tool_execution_start",
        "toolName": "read",
        "toolCallId": "preserve",
        "args": {"path": "/fixture/preserve.txt"},
    }
    preserve_end: Event = {
        "type": "tool_execution_end",
        "toolName": "read",
        "toolCallId": "preserve",
        "isError": False,
        "result": text_result("COMPARISON_PROTECTED_CONTENT"),
    }
    assert preserve_read_evidence([preserve_start, preserve_end], "/fixture/preserve.txt")
    relative = with_(preserve_start, args={"path": "preserve.txt"})
    assert preserve_read_evidence([relative, preserve_end], "/fixture/preserve.txt", "/fixture")
    dotted = with_(preserve_start, args={"path": "./preserve.txt"})
    assert preserve_read_evidence([dotted, preserve_end], "preserve.txt", "/fixture")
    assert not preserve_read_evidence(
        [relative, preserve_end], "/fixture/preserve.txt", "/other-fixture"
    )
    escaping = with_(preserve_start, args={"path": "../other-fixture/preserve.txt"})
    assert not preserve_read_evidence([escaping, preserve_end], "preserve.txt", "/fixture")
    similar = with_(preserve_start, args={"path": "/fixture/not-preserve.txt"})
    assert not preserve_read_evidence([similar, preserve_end], "/fixture/preserve.txt")
    narrated = with_(preserve_start, toolName="bash", args={"command": "echo preserve.txt"})
    assert not preserve_read_evidence(
        [narrated, with_(preserve_end, toolName="bash")], "/fixture/preserve.txt"
    )
    assert not preserve_read_evidence([preserve_start], "/fixture/preserve.txt")

    assert assertion_failure_observed("not ok 1 - add\nExpected values to be strictly equal")
    assert not assertion_failure_observed("not ok 1 - syntax\nSyntaxError: unexpected token")
    assert capture_disposition(False).complete
    disposition = capture_disposition(True)
    assert isinstance(disposition, CaptureDisposition)
    assert disposition.truncated and not disposition.complete

    assert medium_search_accepted(REPORT, TARGET)
    assert medium_search_accepted(
        f"{TARGET} exports add and return expression: left + right", TARGET
    )
    assert not medium_search_accepted(
        "medium/sector-7/other/target.mjs exports add and return left + right", TARGET
    )
    search_start: Event = {
        "type": "tool_execution_start",
        "toolName": "grep",
        "toolCallId": "search-read",
    }
    search_end: Event = {
        "type": "tool_execution_end",
        "toolName": "grep",
        "toolCallId": "search-read",
        "isError": False,
        "result": text_result(f"{TARGET}\n{ADD_SOURCE}"),
    }
    assert medium_search_accepted(REPORT, TARGET, [search_start, search_end])
    sed_command = "cd fixture && sed -n '1,6p' medium/sector-7/node/target.mjs"
    assert medium_search_accepted(
        REPORT,
        TARGET,
        [
            with_(search_start, toolName="bash", args={"command": sed_command}),
            with_(search_end, toolName="bash", result=text_result(ADD_SOURCE)),
        ],
    )
    # A read after `cd` names the file relative to the new directory (2026-09-26 rerun, C-tintin rep 1).
    cd_cases = [
        ("cd medium; echo '=== sector-7/node/target.mjs ==='; cat sector-7/node/target.mjs", True),
        ("cd medium/sector-7 && cat node/target.mjs", True),
        ("cd medium && cd sector-7/node && cat ./target.mjs", True),
        ("cd medium/apps && cat ../sector-7/node/target.mjs", True),
        ("cd other; cat sector-7/node/target.mjs", False),
        ("cd /tmp/elsewhere; cat sector-7/node/target.mjs", False),
        ("cd -; cat sector-7/node/target.mjs", False),
    ]
    for command, accepted in cd_cases:
        cd_events = [
            with_(search_start, toolName="bash", args={"command": command}),
            with_(
                search_end,
                toolName="bash",
                result=text_result("export function add(left, right) {\n  return left + right;\n}"),
            ),
        ]
        assert medium_search_accepted(REPORT, TARGET, cd_events) is accepted, command
    rejected_commands = [
        ("echo 'cat medium/sector-7/node/target.mjs left + right'", "left + right"),
        ("false && cat medium/sector-7/node/target.mjs", "return left + right"),
        (
            "echo 'before; cat medium/sector-7/node/target.mjs'; echo 'left + right'",
            f"{TARGET}\nleft + right",
        ),
        (
            "grep -n 'left + right' <(printf 'medium/sector-7/node/target.mjs\\n"
            "export function add(left, right) { return left + right; }')",
            f"{TARGET}\n{ADD_SOURCE}",
        ),
    ]
    for command, output in rejected_commands:
        rejected_events = [
            with_(search_start, toolName="bash", args={"command": command}),
            with_(search_end, toolName="bash", result=text_result(output)),
        ]
        assert not medium_search_accepted(REPORT, TARGET, rejected_events), command
    assert not medium_search_accepted(
        REPORT,
        TARGET,
        [with_(search_start, toolCallId="unrelated"), with_(search_end, toolCallId="other")],
    )
    read_events = [
        with_(search_start, toolName="read", args={"path": TARGET}),
        with_(search_end, toolName="read", result=text_result(ADD_SOURCE)),
    ]
    assert medium_search_accepted(REPORT, TARGET, read_events)
    neighbor_events = [
        with_(search_start, toolName="read", args={"path": "medium/sector-7/node/neighbor.mjs"}),
        with_(search_end, toolName="read", result=text_result(ADD_SOURCE)),
    ]
    assert not medium_search_accepted(REPORT, TARGET, neighbor_events)
    assert command_evidence(
        [{"seq": 1, "event": GOOD_START}, {"seq": 2, "event": GOOD_END}], TEST_COMMAND
    ) == CommandEvidence(started=True, completed=True, successful=True, failed=False)
    wrapped = [
        {
            "seq": 1,
            "event": with_(search_start, toolName="read", args={"path": f"/tmp/fixture/{TARGET}"}),
        },
        {"seq": 2, "event": with_(search_end, toolName="read", result=text_result(ADD_SOURCE))},
    ]
    assert medium_search_accepted(REPORT, TARGET, wrapped)

    before = {"preserve.txt": "hash-a", "medium/target.mjs": "hash-b", "add.mjs": "mutable-before"}
    after = {"medium/target.mjs": "hash-b", "preserve.txt": "hash-a", "add.mjs": "mutable-after"}
    assert protected_tree_hash(before, ["add.mjs"]) == protected_tree_hash(after, ["add.mjs"])
    assert protected_tree_hash(before) != protected_tree_hash(after)


@pytest.mark.parity
def test_accepts_the_persisted_medium_search_event_shape_used_by_comparison_reevaluation() -> None:
    """Source: comparison-acceptance.test.ts 'accepts the persisted medium-search event shape used by comparison reevaluation'."""
    events: list[Event] = [
        {
            "seq": 1,
            "event": {
                "type": "tool_execution_start",
                "toolName": "bash",
                "toolCallId": "find-target",
                "args": {
                    "command": "find medium -path '*sector-7*' -type f; echo '---'; "
                    "cat medium/sector-7/node/target.mjs"
                },
            },
        },
        {
            "seq": 2,
            "event": {
                "type": "tool_execution_end",
                "toolName": "bash",
                "toolCallId": "find-target",
                "isError": False,
                "result": text_result(
                    f"{TARGET}\nexport function add(left, right) {{\n  return left + right;\n}}"
                ),
            },
        },
    ]
    report = f"Found {TARGET}; exported add returns left + right."
    assert medium_search_accepted(report, TARGET, events)
    unrelated = with_(events[1], event={**events[1]["event"], "toolCallId": "unrelated"})
    assert not medium_search_accepted(report, TARGET, [events[0], unrelated])


@pytest.mark.parity
def test_requires_retained_tool_call_result_pairs_and_validates_provider_reasoning_fields() -> None:
    """Source: comparison-acceptance.test.ts 'requires retained tool-call/result pairs and validates provider reasoning fields'."""
    retained: list[dict[str, Any]] = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Recover the pending validation action.",
                        "thinkingSignature": "sig-1",
                    },
                    {
                        "type": "toolCall",
                        "id": "call-1",
                        "name": "bash",
                        "arguments": {"command": TEST_COMMAND},
                    },
                ],
            }
        },
        {
            "message": {
                "role": "toolResult",
                "toolCallId": "call-1",
                "isError": False,
                "content": [{"type": "text", "text": "# pass 1\n# fail 0"}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "The retained validation passed.",
                        "thinkingSignature": "sig-2",
                    },
                    {"type": "text", "text": "Recovered."},
                ],
            }
        },
    ]
    facts = compaction_protocol_facts(retained)
    assert (
        facts.tool_calls,
        facts.tool_results,
        facts.paired_tool_results,
        facts.orphan_tool_calls,
        facts.orphan_tool_results,
        facts.reasoning_fields,
        facts.malformed_reasoning_fields,
        facts.reasoning_observed,
        facts.valid,
    ) == (1, 1, 1, 0, 0, 2, 0, True, True)
    wrong = {"message": {"role": "toolResult", "toolCallId": "wrong", "content": []}}
    facts = compaction_protocol_facts([retained[0], wrong])
    assert facts.orphan_tool_results == 1
    assert not facts.valid
    missing = {
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinkingSignature": "missing-body"},
                {"type": "toolCall", "name": "bash"},
            ],
        }
    }
    facts = compaction_protocol_facts([missing])
    assert facts.orphan_tool_calls == 1
    assert facts.malformed_reasoning_fields == 1
    assert not facts.valid
