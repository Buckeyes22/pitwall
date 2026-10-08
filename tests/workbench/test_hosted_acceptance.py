"""Hosted acceptance gates, translated from packages/pi-workbench/tests/hosted-acceptance.test.ts."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.hosted import acceptance as hosted_acceptance
from pitwall.workbench.hosted.evaluation import (
    evaluate_compaction_retention,
    evaluate_model_command,
)
from pitwall.workbench.hosted.native_evaluation import (
    ChildSession,
    ExpectedChild,
    evaluate_native_child_evidence,
)
from pitwall.workbench.hosted_profiles import credential_value

ACCOUNTING_SUITE = Path(__file__).resolve().parent / "fixtures" / "accounting-path.test.mjs"
TEST_COMMAND = "node --test add.test.mjs"


def bash_pair(
    call_id: str, command: str, output: str, *, is_error: bool = False
) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_execution_start",
            "toolName": "bash",
            "toolCallId": call_id,
            "args": {"command": command},
        },
        {
            "type": "tool_execution_end",
            "toolName": "bash",
            "toolCallId": call_id,
            "isError": is_error,
            "result": {"content": [{"type": "text", "text": output}]},
        },
    ]


@pytest.mark.parity
def test_hosted_runner_uses_the_exact_account_reference_and_does_not_serialize_a_credential() -> (
    None
):
    """Source: hosted-acceptance.test.ts 'hosted runner uses the exact account reference and does not serialize a credential'."""
    value = credential_value(
        {
            "minimax-coding-plan": {"type": "api", "key": "memory-only-test-secret"}
        },  # pragma: allowlist secret
        "minimax-coding-plan",
    )
    assert value == "memory-only-test-secret"  # pragma: allowlist secret
    source = Path(hosted_acceptance.__file__).read_text()
    assert '"secretValuesPrinted": False' in source
    assert 'credential_value(auth, profile["accountRef"])' in source
    assert '"key": account_key' not in source


@pytest.mark.parity
def test_hosted_runner_requires_bounded_coding_and_forced_compaction_gates() -> None:
    """Source: hosted-acceptance.test.ts 'hosted runner requires bounded coding and forced compaction gates'."""
    source = Path(hosted_acceptance.__file__).read_text()
    assert 'profile.get("apiKeyEnv") != CREDENTIAL_ENV' in source
    assert 'session.request("set_auto_compaction", {"enabled": False})' in source
    assert "configure_provider_profile(compiled, fixture)" in source
    assert "**provider.env" in source
    assert 'session.request("compact"' in source
    assert "firstKeptEntryId" in source
    assert "retainedSuffixFacts" in source
    assert "after_repair" in source
    assert "after_compaction" in source


@pytest.mark.parity
def test_accounting_defaults_to_the_private_agent_directory_and_honors_an_explicit_destination() -> (
    None
):
    """Source: hosted-acceptance.test.ts 'accounting defaults to the private agent directory and honors an explicit destination'."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; the Pi extension suites need Node 22 or newer")
    completed = subprocess.run(  # noqa: S603  # reason: fixed argv, node resolved from PATH, no shell
        [node, "--test", "--test-timeout=60000", str(ACCOUNTING_SUITE)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
        timeout=300,
    )
    tail = "\n".join(completed.stdout.splitlines()[-30:] + completed.stderr.splitlines()[-10:])
    assert completed.returncode == 0, f"node --test failed:\n{tail}"
    if "# skipped 0" not in completed.stdout:
        pytest.skip("pinned Pi packages are not installed; the extension case skipped itself")
    assert "# pass 1" in completed.stdout, tail


@pytest.mark.parity
def test_continuation_command_gate_requires_a_correlated_exact_successful_model_bash_call() -> None:
    """Source: hosted-acceptance.test.ts 'continuation command gate requires a correlated exact successful model bash call'."""
    passing = bash_pair("good", TEST_COMMAND, "ℹ pass 1\nℹ fail 0\n")
    assert evaluate_model_command(passing, TEST_COMMAND).matched
    assert not evaluate_model_command(
        bash_pair("wrong", "echo hello", "hello"), TEST_COMMAND
    ).matched
    echo_exit = f'{TEST_COMMAND}; echo "exit=$?"'
    assert not evaluate_model_command(
        bash_pair("masked", echo_exit, "ℹ pass 1\nℹ fail 1\nexit=0"), [echo_exit]
    ).matched
    assert not evaluate_model_command(
        bash_pair("bad", TEST_COMMAND, "Command exited with code 1", is_error=True), TEST_COMMAND
    ).matched
    assert evaluate_model_command(
        bash_pair("echo", echo_exit, "ℹ pass 1\nℹ fail 0\nexit=0"), [echo_exit]
    ).matched
    upper = f'{TEST_COMMAND}; echo "EXIT: $?"'
    assert evaluate_model_command(
        bash_pair("upper", upper, "# pass 1\n# fail 0\nEXIT: 0"), [upper]
    ).matched
    exit_code = f'{TEST_COMMAND}; echo "exit code: $?"'
    assert evaluate_model_command(
        bash_pair("exit-code", exit_code, "# pass 1\n# fail 0\nexit code: 0"), [exit_code]
    ).matched
    failed = evaluate_model_command(
        bash_pair("bad", TEST_COMMAND, "Command exited with code 1", is_error=True), TEST_COMMAND
    )
    assert (failed.reason, failed.command_seen) == ("failed", True)
    assert evaluate_model_command([], TEST_COMMAND).reason == "missing-call"


@pytest.mark.parity
def test_compaction_acceptance_uses_concrete_facts_retained_in_the_pi_suffix() -> None:
    """Source: hosted-acceptance.test.ts 'compaction acceptance uses concrete facts retained in the Pi suffix'."""
    marker = "PW_ALIBABA_TOKEN_PLAN_7391"
    summary = (
        f"The task marker {marker} and changed file add.mjs were preserved; "
        "validation passed and the next test should be rerun."
    )
    suffix = (
        f"**Report**\n- Task marker: {marker}\n- Criteria: add(2,3)=5 and add(-2,3)=1\n"
        "- Changed file: add.mjs only\n- Validation state: exit code 0\n"
        "- Next action: rerun node --test add.test.mjs"
    )
    result = evaluate_compaction_retention(summary, suffix, marker)
    assert not result.summary_facts.criteria
    assert result.retained_suffix_facts.criteria
    assert result.retained_suffix_complete


@pytest.mark.parity
def test_compaction_acceptance_does_not_treat_labels_as_retained_concrete_criteria() -> None:
    """Source: hosted-acceptance.test.ts 'compaction acceptance does not treat labels as retained concrete criteria'."""
    marker = "PW_ALIBABA_TOKEN_PLAN_7391"
    result = evaluate_compaction_retention(
        f"Task marker {marker}; criteria; changed file add.mjs; validation passed; next action rerun test",
        f"Task marker {marker}; criteria; changed file add.mjs; validation exit code 0; next action rerun node --test add.test.mjs",
        marker,
    )
    assert not result.retained_suffix_facts.criteria
    assert not result.retained_suffix_complete


@pytest.mark.parity
def test_native_child_gate_requires_the_correlated_child_session_read_result() -> None:
    """Source: hosted-acceptance.test.ts 'native child gate requires the correlated child session read result'."""
    expected = ExpectedChild(
        name="zai-coding-plan", provider="zai", model_id="glm-5.2", workspace="/fixture"
    )
    profile = {"name": "zai-coding-plan", "provider": "zai", "modelId": "glm-5.2"}
    record = {
        "executionState": "succeeded",
        "childId": "child-1",
        "backendSessionFile": "/agent/child.jsonl",
        "profile": profile,
    }
    marker = "HOSTED_NATIVE_CHILD_ORACLE_7F31"
    lines: list[dict[str, Any]] = [
        {"type": "model_change", "provider": "zai", "modelId": "glm-5.2"},
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "read-1",
                        "name": "read",
                        "arguments": {"path": "witness.txt"},
                    }
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "read-1",
                "toolName": "read",
                "isError": False,
                "content": [{"type": "text", "text": f"{marker}\n"}],
            },
        },
    ]

    def evaluate(
        sessions: list[ChildSession] | None = None,
        records: list[dict[str, Any]] | None = None,
        task_child_id: str | None = None,
    ) -> str:
        return evaluate_native_child_evidence(
            task_records=records if records is not None else [record],
            child_sessions=sessions
            if sessions is not None
            else [ChildSession("/agent/child.jsonl", lines)],
            expected=expected,
            marker=marker,
            protected_unchanged=True,
            task_started=True,
            task_finished=True,
            task_error=False,
            task_child_id=task_child_id,
        ).status

    assert evaluate() == "passed"
    narrated = [
        {
            "type": "message",
            "message": {"role": "assistant", "content": [{"type": "text", "text": marker}]},
        }
    ]
    assert evaluate([ChildSession("/agent/child.jsonl", narrated)]) == "failed"
    bash_lines = [
        lines[0],
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "bash-1",
                        "name": "bash",
                        "arguments": {"command": "cat witness.txt"},
                    }
                ],
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "bash-1",
                "toolName": "bash",
                "isError": False,
                "content": [{"type": "text", "text": marker}],
            },
        },
    ]
    assert evaluate([ChildSession("/agent/child.jsonl", bash_lines)]) == "failed"
    assert evaluate([ChildSession("/agent/other.jsonl", lines)]) == "failed"
    absolute = copy.deepcopy(lines)
    absolute[1]["message"]["content"][0]["arguments"]["path"] = "/fixture/witness.txt"
    assert evaluate([ChildSession("/agent/child.jsonl", absolute)]) == "passed"
    wrong_then_correct = [
        {"type": "model_change", "provider": "other", "modelId": "wrong"},
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "wrong-read",
                        "name": "read",
                        "arguments": {"path": "witness.txt"},
                    }
                ],
            },
        },
        {"type": "model_change", "provider": "zai", "modelId": "glm-5.2"},
        *lines[1:],
    ]
    assert evaluate([ChildSession("/agent/child.jsonl", wrong_then_correct)]) == "failed"
    sibling = {**record, "childId": "sibling", "backendSessionFile": "/agent/sibling.jsonl"}
    assert evaluate(records=[sibling, record], task_child_id="sibling") == "failed"
    assert evaluate(records=[sibling, record], task_child_id="child-1") == "passed"
    # The result evidence keeps every field the report serializes.
    passed = evaluate_native_child_evidence(
        task_records=[record],
        child_sessions=[ChildSession("/agent/child.jsonl", lines)],
        expected=replace(expected),
        marker=marker,
        protected_unchanged=True,
        task_started=True,
        task_finished=True,
        task_error=False,
    )
    assert passed.actual.tool_names == ["read"]
    assert passed.actual.marker_from_read_result


def test_a_later_passing_run_of_the_command_is_the_final_state_and_keeps_the_earlier_failure() -> (
    None
):
    events = [
        *bash_pair("first", TEST_COMMAND, "ℹ pass 0\nℹ fail 1\n"),
        *bash_pair("second", TEST_COMMAND, "ℹ pass 1\nℹ fail 0\n"),
    ]
    result = evaluate_model_command(events, TEST_COMMAND)
    assert result.matched
    assert result.tool_call_id == "second"
    assert [(item.tool_call_id, item.reason) for item in result.earlier] == [
        ("first", "bad-output")
    ]


def test_an_error_run_followed_by_a_passing_run_passes_and_a_later_failure_overrides() -> None:
    passing = bash_pair("good", TEST_COMMAND, "ℹ pass 1\nℹ fail 0\n")
    failing = bash_pair("bad", TEST_COMMAND, "Command exited with code 1", is_error=True)
    recovered = evaluate_model_command([*failing, *passing], TEST_COMMAND)
    assert recovered.matched
    assert [item.reason for item in recovered.earlier] == ["failed"]
    regressed = evaluate_model_command([*passing, *failing], TEST_COMMAND)
    assert not regressed.matched
    assert regressed.reason == "failed"
    assert [item.reason for item in regressed.earlier] == ["matched"]


def test_the_gate_report_persists_the_earlier_failure_of_a_fail_then_pass_run() -> None:
    events = [
        *bash_pair("first", TEST_COMMAND, "Command exited with code 1", is_error=True),
        *bash_pair("second", TEST_COMMAND, "ℹ pass 1\nℹ fail 0\n"),
    ]
    report = hosted_acceptance._command_evaluation_report(
        evaluate_model_command(events, TEST_COMMAND)
    )
    assert report["matched"] is True
    assert report["toolCallId"] == "second"
    assert report["earlier"] == [
        {
            "matched": False,
            "commandSeen": True,
            "toolCallId": "first",
            "isError": True,
            "reason": "failed",
        }
    ]
    clean = hosted_acceptance._command_evaluation_report(
        evaluate_model_command(
            bash_pair("only", TEST_COMMAND, "ℹ pass 1\nℹ fail 0\n"), TEST_COMMAND
        )
    )
    assert clean["earlier"] == []
