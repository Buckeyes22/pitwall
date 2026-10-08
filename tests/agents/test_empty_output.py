"""A zero exit with nothing on stdout is a failure for harnesses that always print an answer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from pitwall.agents.dispatch import _LegacyDispatch
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.process import ProcessResult
from tests.agents.shim_test_support import ShimSandbox

EMPTY_REASON = "exited 0 without writing anything to stdout; recording exit 77"
AGY_DENIAL = (
    'jetski: no output produced — a tool required the "command" permission '
    "that headless mode cannot prompt for, so it was auto-denied.\n"
)


class EmptyOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.addCleanup(self.sandbox.cleanup)

    def _dispatch(
        self, harness: str, stdout: str, **extra: str
    ) -> tuple[int, dict[str, Any], bytes]:
        self.sandbox.install_harness(harness)
        prompt = str(self.sandbox.prompt())
        args = ["test-provider/test-model", prompt] if harness == "opencode" else [prompt]
        result = self.sandbox.run(
            harness, args, env=self.sandbox.environment(FAKE_STDOUT=stdout, **extra), timeout=30
        )
        (run,) = self.sandbox.run_directories()
        document = json.loads((run / "result.json").read_text(encoding="utf-8"))
        return result.returncode, document, result.stderr

    def _run_document(self) -> dict[str, Any]:
        (run,) = self.sandbox.run_directories()
        document: dict[str, Any] = json.loads((run / "run.json").read_text(encoding="utf-8"))
        return document

    def _terminal_event(self) -> dict[str, Any]:
        (run,) = self.sandbox.run_directories()
        events = [
            json.loads(line)
            for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        (terminal,) = [event for event in events if event["event"].startswith("dispatch.f")]
        return terminal

    def _assert_reason_everywhere(self, document: dict[str, Any], fragment: str) -> None:
        self.assertNotIn("softDenialReason", document)
        reason = self._run_document()["softDenialReason"]
        self.assertIn(fragment, reason)
        ledger = self.sandbox.ledger_records()[-1]
        self.assertEqual(
            ("finished", 77, reason), (ledger["event"], ledger["exit"], ledger["softDenialReason"])
        )
        event = self._terminal_event()
        self.assertEqual(
            ("dispatch.failed", 77, reason),
            (event["event"], event["data"]["exitCode"], event["data"]["softDenialReason"]),
        )

    def test_opencode_empty_stdout_is_exit_77(self) -> None:
        code, result, stderr = self._dispatch("opencode", "")
        self.assertEqual((77, "failed", 77), (code, result["status"], result["exitCode"]))
        self.assertIn(f"opencode-shim: {EMPTY_REASON}".encode(), stderr)
        self.assertEqual(EMPTY_REASON, self._run_document()["softDenialReason"])
        self._assert_reason_everywhere(result, "without writing anything to stdout")

    def test_opencode_whitespace_only_stdout_is_exit_77(self) -> None:
        code, result, _ = self._dispatch("opencode", " \n\t\r\n  ")
        self.assertEqual((77, "failed"), (code, result["status"]))
        self._assert_reason_everywhere(result, "without writing anything to stdout")

    def test_opencode_with_an_answer_still_succeeds(self) -> None:
        code, result, _ = self._dispatch("opencode", "an answer\n")
        self.assertEqual((0, "succeeded"), (code, result["status"]))
        self.assertNotIn("softDenialReason", result)
        self.assertNotIn("softDenialReason", self._run_document())
        self.assertNotIn("softDenialReason", self.sandbox.ledger_records()[-1])
        self.assertNotIn("softDenialReason", self._success_event_data())

    def _success_event_data(self) -> dict[str, Any]:
        (run,) = self.sandbox.run_directories()
        events = [
            json.loads(line)
            for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        (terminal,) = [event for event in events if event["event"] == "dispatch.succeeded"]
        data: dict[str, Any] = terminal["data"]
        return data

    def test_codex_keeps_exit_0_on_empty_stdout(self) -> None:
        code, result, _ = self._dispatch("codex", "")
        self.assertEqual((0, "succeeded"), (code, result["status"]))
        self.assertNotIn("softDenialReason", result)
        self.assertNotIn("softDenialReason", self._run_document())

    def test_opencode_timed_out_run_with_empty_stdout_stays_timed_out(self) -> None:
        code, result, stderr = self._dispatch(
            "opencode", "", FAKE_SLEEP_SECS="2", PITWALL_AGENTS_TIMEOUT_SECS="1"
        )
        self.assertEqual((124, "timed_out"), (code, result["status"]))
        self.assertNotIn("softDenialReason", result)
        self.assertNotIn("softDenialReason", self._run_document())
        self.assertNotIn(b"without writing anything", stderr)
        self.assertNotIn("softDenialReason", self.sandbox.ledger_records()[-1])

    def test_antigravity_soft_denial_records_its_reason_everywhere(self) -> None:
        code, result, stderr = self._dispatch("agy", "", FAKE_STDERR=AGY_DENIAL)
        self.assertEqual((77, "failed"), (code, result["status"]))
        self.assertIn(b"agy-shim:", stderr)
        self._assert_reason_everywhere(result, "Antigravity auto-denied a tool permission")


class _Store:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def artifact(self, name: str) -> Path:
        return self.directory / name


class SoftDenialPrecedenceTests(unittest.TestCase):
    """apply_soft_denial on a stub run: the adapter's reason beats the empty-output reason."""

    def _run(
        self,
        directory: Path,
        *,
        stdout: bytes,
        stderr: bytes = b"",
        adapter_reason: str | None = None,
        timed_out: bool = False,
    ) -> _LegacyDispatch:
        (directory / "stdout.log").write_bytes(stdout)
        (directory / "stderr.log").write_bytes(stderr)
        run = _LegacyDispatch.__new__(_LegacyDispatch)
        run.harness_id = "opencode"
        run.adapter = get_adapter("opencode")
        run.store = _Store(directory)  # type: ignore[assignment]  # reason: stub with artifact() only
        run.soft_denial_reason = None
        run.process_result = ProcessResult(0, None, timed_out, False, 5, len(stdout), len(stderr))
        if adapter_reason is not None:
            run.adapter.detect_soft_denial = lambda _code, _tail: adapter_reason  # type: ignore[method-assign]  # reason: stub the instance's detector
        return run

    def test_adapter_reason_wins_over_empty_output_and_only_one_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self._run(Path(temporary), stdout=b"", adapter_reason="adapter says no")
            run.apply_soft_denial()
        self.assertEqual("adapter says no", run.soft_denial_reason)
        self.assertEqual(77, run.process_result.exit_code)

    def test_empty_output_reason_is_used_when_the_adapter_finds_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self._run(Path(temporary), stdout=b"\n")
            run.apply_soft_denial()
        self.assertEqual(EMPTY_REASON, run.soft_denial_reason)

    def test_large_blank_stdout_and_unreadable_stdout_are_not_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self._run(Path(temporary), stdout=b" " * 70000)
            run.apply_soft_denial()
            self.assertIsNone(run.soft_denial_reason)
            run = self._run(Path(temporary), stdout=b"x")
            (Path(temporary) / "stdout.log").unlink()
            run.apply_soft_denial()
            self.assertIsNone(run.soft_denial_reason)
            self.assertEqual(0, run.process_result.exit_code)

    def test_a_timed_out_run_is_not_converted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run = self._run(Path(temporary), stdout=b"", timed_out=True)
            run.apply_soft_denial()
        self.assertIsNone(run.soft_denial_reason)
        self.assertEqual(0, run.process_result.exit_code)
