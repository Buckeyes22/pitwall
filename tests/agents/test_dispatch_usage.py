"""Dispatch retains machine output while exposing answers and normalized usage."""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from collections.abc import Iterator, Mapping
from typing import Any

import jsonschema
import pytest

from pitwall.agents.harnesses.base import ParsedRequest, PreparedCommand
from pitwall.agents.harnesses.codex import CodexAdapter
from pitwall.agents.resources import read_resource_json
from pitwall.agents.result import ResultError, validate_result
from pitwall.agents.run_store import MANAGED_LAUNCH_ENV
from pitwall.agents.token_usage import ParsedOutput, TokenUsage
from tests.agents.shim_test_support import ShimSandbox
from tests.agents.test_result import valid_result

RAW = '{"type":"turn.completed"}\n'


class FakeJsonCodex(CodexAdapter):
    output_mode = "json"
    empty_stdout_is_failure = True

    def parse_output(self, raw: bytes) -> ParsedOutput:
        if b"turn.completed" in raw:
            return ParsedOutput(
                b"answer\n", TokenUsage(3, 1, complete=not raw.endswith(b'"turn.started"}\n'))
            )
        if b"empty answer" in raw:
            return ParsedOutput(b"", TokenUsage(0, 0), "no answer")
        return ParsedOutput(b"", None, "unparseable")


class FakeUsageFileCodex(CodexAdapter):
    output_mode = "usage-file"

    def prepare(
        self,
        request: ParsedRequest,
        binary: str,
        prompt: bytes,
        env: Mapping[str, str],
        preflight_data: Mapping[str, str],
    ) -> PreparedCommand:
        prepared = super().prepare(request, binary, prompt, env, preflight_data)
        prepared.argv.extend(["--usage-file", preflight_data["usageFile"]])
        return prepared

    def parse_usage_file(self, data: Any) -> TokenUsage | None:
        return TokenUsage(data["input"], data["output"], source="harness-report")


@pytest.fixture
def sandbox() -> Iterator[ShimSandbox]:
    value = ShimSandbox()
    harness = value.install_harness("codex")
    # Only the sandbox's fake executable writes the report requested by the adapter.
    harness.write_text(
        harness.read_text().replace(
            '    default_stdout = "fake-harness\\n"',
            '    if "--usage-file" in args and "FAKE_USAGE_REPORT" in os.environ:\n'
            '        Path(args[args.index("--usage-file") + 1]).write_text(\n'
            '            os.environ["FAKE_USAGE_REPORT"])\n'
            '    default_stdout = "fake-harness\\n"',
        )
    )
    # ShimSandbox.run starts a fresh interpreter: register the test adapter there.
    bootstrap = (
        "import os, sys; from unittest.mock import patch; "
        "from tests.agents.test_dispatch_usage import FakeJsonCodex, FakeUsageFileCodex; "
        "from pitwall.agents.harnesses.codex import CodexAdapter; "
        "from pitwall.agents.cli import main; "
        "adapter = {'json': FakeJsonCodex, 'usage-file': FakeUsageFileCodex, "
        "'text': CodexAdapter}[os.environ.get('FAKE_OUTPUT_MODE', 'json')]; "
        "sys.argv.remove('agents'); "
        "patcher = patch.dict('pitwall.agents.harnesses._ADAPTERS', {'codex': adapter}); "
        "patcher.start(); raise SystemExit(main())"
    )
    (value.bin / "pitwall").write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -c {shlex.quote(bootstrap)} "$@"\n'
    )
    try:
        yield value
    finally:
        value.cleanup()


def test_json_mode_answer_reaches_stdout_and_usage_is_recorded(sandbox: ShimSandbox) -> None:
    completed = sandbox.run(
        "codex", [str(sandbox.prompt())], env=sandbox.environment(FAKE_STDOUT=RAW, SHIM_RESULT="1")
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(b"answer\n")
    assert completed.stdout.count(b"answer\n") == 1
    assert RAW.encode() not in completed.stdout
    assert completed.stdout.endswith(b"SHIM-DONE exit=0\n")
    (run,) = sandbox.run_directories()
    assert (run / "stdout.log").read_bytes() == b"answer\n"
    assert (run / "harness-output.jsonl").read_bytes() == RAW.encode()
    document = json.loads((run / "result.json").read_text())
    assert document["usage"] == TokenUsage(3, 1).to_public_dict()
    assert "usageError" not in document
    assert document["output"]["stdout"] == {
        "bytes": 7,
        "sha256": hashlib.sha256(b"answer\n").hexdigest(),
    }
    assert str(run / "harness-output.jsonl") in document["artifacts"].values()
    finished = sandbox.ledger_records()[-1]
    assert finished["event"] == "finished" and finished["schema_version"] == 4
    assert finished["usage"] == document["usage"]
    assert "usageError" not in finished
    assert "usage" not in sandbox.ledger_records()[0]
    receipt = next(
        line for line in completed.stdout.splitlines() if line.startswith(b"SHIM-RESULT ")
    )
    assert json.loads(receipt.removeprefix(b"SHIM-RESULT "))["usage"] == document["usage"]
    validate_result(document)


def test_unparseable_output_reaches_the_caller_unchanged(sandbox: ShimSandbox) -> None:
    raw = "Traceback: boom\n"
    completed = sandbox.run(
        "codex", [str(sandbox.prompt())], env=sandbox.environment(FAKE_STDOUT=raw)
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == raw.encode() + b"\nSHIM-DONE exit=0\n"
    (run,) = sandbox.run_directories()
    assert (run / "stdout.log").read_bytes() == raw.encode()
    assert (run / "harness-output.jsonl").read_bytes() == raw.encode()
    document = json.loads((run / "result.json").read_text())
    assert document["usage"] is None
    assert document["usageError"] == "unparseable"
    assert sandbox.ledger_records()[-1]["usageError"] == "unparseable"
    assert (run / "stderr.log").read_bytes() == b"unparseable\n"
    assert b"codex-shim: unparseable" in completed.stderr


def test_text_mode_harness_records_usage_unavailable(sandbox: ShimSandbox) -> None:
    completed = sandbox.run(
        "codex",
        [str(sandbox.prompt())],
        env=sandbox.environment(FAKE_OUTPUT_MODE="text", FAKE_STDOUT="plain answer\n"),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == b"plain answer\n\nSHIM-DONE exit=0\n"
    (run,) = sandbox.run_directories()
    document = json.loads((run / "result.json").read_text())
    assert document["usage"] is None
    assert document["usageError"] == "codex reports no token usage"
    assert sandbox.ledger_records()[-1]["usage"] is None
    assert sandbox.ledger_records()[-1]["usageError"] == document["usageError"]
    assert (run / "stdout.log").read_bytes() == b"plain answer\n"
    assert not (run / "harness-output.jsonl").exists()


@pytest.mark.parametrize("exit_code", [124, 130])
def test_partial_stream_counts_completed_steps(sandbox: ShimSandbox, exit_code: int) -> None:
    completed = sandbox.run(
        "codex",
        [str(sandbox.prompt())],
        env=sandbox.environment(
            FAKE_STDOUT=RAW + '{"type":"turn.started"}\n', FAKE_EXIT=str(exit_code)
        ),
    )
    assert completed.returncode == exit_code, completed.stderr
    (run,) = sandbox.run_directories()
    document = json.loads((run / "result.json").read_text())
    assert document["usage"] == TokenUsage(3, 1, complete=False).to_public_dict()
    assert document["exitCode"] == exit_code
    assert sandbox.ledger_records()[-1]["usage"] == document["usage"]


def test_managed_launch_does_not_echo_the_answer_to_fd1(sandbox: ShimSandbox) -> None:
    completed = sandbox.run(
        "codex",
        [str(sandbox.prompt())],
        env=sandbox.environment(**{MANAGED_LAUNCH_ENV: "1", "FAKE_STDOUT": RAW}),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == b"\nSHIM-DONE exit=0\n"
    (run,) = sandbox.run_directories()
    assert (run / "stdout.log").read_bytes() == b"answer\n"
    assert (run / "harness-output.jsonl").read_bytes() == RAW.encode()


def test_rendered_empty_answer_triggers_empty_stdout_failure(sandbox: ShimSandbox) -> None:
    completed = sandbox.run(
        "codex", [str(sandbox.prompt())], env=sandbox.environment(FAKE_STDOUT="empty answer")
    )
    assert completed.returncode == 77, completed.stderr
    (run,) = sandbox.run_directories()
    document = json.loads((run / "result.json").read_text())
    assert document["output"]["stdout"]["bytes"] == 0
    assert document["usage"] == TokenUsage(0, 0).to_public_dict()
    assert (run / "stdout.log").read_bytes() == b""
    assert (run / "stderr.log").read_bytes() == b"no answer\n"


@pytest.mark.parametrize(
    "report,error",
    [('{"input":3,"output":1}', None), ("invalid", "JSONDecodeError"), (None, "FileNotFoundError")],
)
def test_usage_file_mode(sandbox: ShimSandbox, report: str | None, error: str | None) -> None:
    overrides = {"FAKE_OUTPUT_MODE": "usage-file", "FAKE_STDOUT": "plain answer\n"}
    if report is not None:
        overrides["FAKE_USAGE_REPORT"] = report
    completed = sandbox.run("codex", [str(sandbox.prompt())], env=sandbox.environment(**overrides))
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == b"plain answer\n\nSHIM-DONE exit=0\n"
    (run,) = sandbox.run_directories()
    document = json.loads((run / "result.json").read_text())
    if error is None:
        assert document["usage"] == TokenUsage(3, 1, source="harness-report").to_public_dict()
        assert "usageError" not in document
    else:
        assert document["usage"] is None
        assert document["usageError"] == f"no usage report: {error}"
        assert sandbox.ledger_records()[-1]["usageError"] == document["usageError"]
    assert sandbox.ledger_records()[-1]["usage"] == document["usage"]
    if report is not None:
        assert str(run / "harness-usage.json") in document["artifacts"].values()


@pytest.mark.parametrize(
    "usage",
    [
        None,
        TokenUsage(3, 1).to_public_dict(),
        TokenUsage(
            3,
            1,
            cache_read_tokens=2,
            cache_write_tokens=1,
            reasoning_tokens=1,
            total_tokens=7,
            cost_usd=0.1,
            complete=False,
            source="harness-report",
        ).to_public_dict(),
    ],
)
def test_valid_usage_matches_validator_and_schema(usage: Any) -> None:
    document = {**valid_result(), "usage": usage, "usageError": "reason"}
    validate_result(document)
    jsonschema.Draft202012Validator(
        read_resource_json("schemas/dispatch-result.schema.json")
    ).validate(document)


@pytest.mark.parametrize(
    "usage",
    [
        [],
        "usage",
        {},
        {"inputTokens": 3, "outputTokens": 1},
        {**TokenUsage(3, 1).to_public_dict(), "extra": 0},
    ],
)
def test_usage_requires_exact_public_keys(usage: Any) -> None:
    document = {**valid_result(), "usage": usage}
    with pytest.raises(ResultError, match="usage"):
        validate_result(document)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(
            read_resource_json("schemas/dispatch-result.schema.json")
        ).validate(document)


@pytest.mark.parametrize(
    "field,invalid",
    [
        (key, invalid)
        for key in (
            "inputTokens",
            "outputTokens",
            "cacheReadTokens",
            "cacheWriteTokens",
            "reasoningTokens",
            "totalTokens",
        )
        for invalid in (-1, True, "3", 1.5)
    ]
    + [
        ("inputTokens", None),
        ("outputTokens", None),
        ("costUsd", -1),
        ("costUsd", True),
        ("costUsd", "0.1"),
        ("complete", 1),
        ("complete", None),
        ("source", None),
        ("source", 1),
    ],
)
def test_usage_field_types_match_validator_and_schema(field: str, invalid: Any) -> None:
    document = {**valid_result(), "usage": {**TokenUsage(3, 1).to_public_dict(), field: invalid}}
    with pytest.raises(ResultError, match="usage"):
        validate_result(document)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(
            read_resource_json("schemas/dispatch-result.schema.json")
        ).validate(document)


@pytest.mark.parametrize("invalid", [None, 1, {}, []])
def test_usage_error_must_be_a_string(invalid: Any) -> None:
    document = {**valid_result(), "usageError": invalid}
    with pytest.raises(ResultError, match="usageError"):
        validate_result(document)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(
            read_resource_json("schemas/dispatch-result.schema.json")
        ).validate(document)
