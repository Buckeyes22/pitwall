"""prompt.deliver.md lives while a run can still resume, and no longer unless retained."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents import dispatch
from tests.agents.shim_test_support import PITWALL, SHIMS, ShimSandbox
from tests.hang_guard import HANG_GUARD_SECS

DISPATCH_ID = "00000000-0000-4000-8000-0000000000f7"
RETAIN = "--routing-retain-prompt"


def _delivery_prompt(run: Path) -> Path:
    return run / "prompt.deliver.md"


class DeliveryPromptRetentionTests(unittest.TestCase):
    def _sandbox(self, harness: str = "codex") -> ShimSandbox:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness(harness)
        return sandbox

    def _run(self, *extra: str) -> bool:
        sandbox = self._sandbox()
        env = sandbox.environment(PITWALL_AGENTS_ASK_SUPPORT="1")
        result = sandbox.run("codex", [str(sandbox.prompt()), *extra], env=env)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        (run,) = sandbox.run_directories()
        return _delivery_prompt(run).exists()

    def test_removed_after_a_finished_run(self) -> None:
        self.assertFalse(self._run())

    def test_kept_when_retention_was_requested(self) -> None:
        self.assertTrue(self._run(RETAIN))

    def _timed_out(self, *extra: str) -> bool:
        sandbox = self._sandbox()
        env = sandbox.environment(
            PITWALL_AGENTS_ASK_SUPPORT="1", PITWALL_AGENTS_TIMEOUT_SECS="1", FAKE_SLEEP_SECS="30"
        )
        result = sandbox.run("codex", [str(sandbox.prompt()), *extra], env=env)
        self.assertEqual(124, result.returncode, result.stderr.decode(errors="replace"))
        (run,) = sandbox.run_directories()
        status = json.loads((run / "result.json").read_text(encoding="utf-8"))["status"]
        self.assertEqual("timed_out", status)
        return _delivery_prompt(run).exists()

    def test_a_timed_out_run_removes_the_prompt_unless_retained(self) -> None:
        self.assertFalse(self._timed_out())
        self.assertTrue(self._timed_out(RETAIN))

    def _aborted(self, *extra: str) -> bool:
        sandbox = self._sandbox()
        pid_file = sandbox.root / "child.pid"
        process = sandbox.popen(
            [str(PITWALL), "agents", "dispatch", "codex", str(sandbox.prompt()), *extra],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=sandbox.environment(
                PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
                PITWALL_AGENTS_ASK_SUPPORT="1",
                FAKE_SLEEP_SECS="30",
                FAKE_PID_FILE=str(pid_file),
            ),
        )
        deadline = time.monotonic() + HANG_GUARD_SECS
        while not pid_file.exists():
            self.assertLess(time.monotonic(), deadline, "child never started")
            time.sleep(0.05)
        subprocess.run(
            [str(PITWALL), "agents", "runs", "stop", DISPATCH_ID, "--grace", "1"],
            env=sandbox.environment(),
            check=True,
            capture_output=True,
        )
        process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(143, process.returncode)
        (run,) = sandbox.run_directories()
        status = json.loads((run / "result.json").read_text(encoding="utf-8"))["status"]
        self.assertEqual("cancelled", status)
        return _delivery_prompt(run).exists()

    def test_an_aborted_run_removes_the_prompt_unless_retained(self) -> None:
        self.assertFalse(self._aborted())
        self.assertTrue(self._aborted(RETAIN))

    def _file_delivery(self, harness: str, *extra: str) -> bool:
        sandbox = self._sandbox(harness)
        result = sandbox.run(harness, ["-", *extra], input_bytes=b"stdin prompt\n")
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        (run,) = sandbox.run_directories()
        return _delivery_prompt(run).exists()

    def test_file_delivery_without_ask_support_removes_the_prompt_unless_retained(self) -> None:
        for harness in ("grok", "muse", "pi"):
            self.assertIn(harness, SHIMS)
            with self.subTest(harness=harness):
                self.assertFalse(self._file_delivery(harness))
                self.assertTrue(self._file_delivery(harness, RETAIN))

    def _workspace_failure(self, *extra: str) -> bool:
        sandbox = self._sandbox()
        repository = sandbox.root / "repository"
        repository.mkdir()
        for command in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "tests@example.com"],
            ["git", "config", "user.name", "Dispatch Tests"],
        ):
            subprocess.run(command, cwd=repository, check=True)
        (repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repository, check=True)
        result = subprocess.run(
            [
                "/bin/bash",
                str(SHIMS["codex"]),
                str(sandbox.prompt()),
                "--routing-workspace=isolated",
                "--routing-base=nonexistent-ref",
                *extra,
            ],
            cwd=repository,
            capture_output=True,
            env=sandbox.environment(PITWALL_AGENTS_ASK_SUPPORT="1"),
            check=False,
        )
        self.assertEqual(1, result.returncode, result.stderr.decode(errors="replace"))
        (run,) = sandbox.run_directories()
        state = json.loads((run / "run.json").read_text(encoding="utf-8"))["state"]
        self.assertEqual("failed", state)
        return _delivery_prompt(run).exists()

    def test_a_workspace_failure_removes_the_prompt_unless_retained(self) -> None:
        self.assertFalse(self._workspace_failure())
        self.assertTrue(self._workspace_failure(RETAIN))

    def _failing_terminal_step(self, *extra: str) -> tuple[bool, list[BaseException]]:
        sandbox = self._sandbox()
        raised: list[BaseException] = []
        original = dispatch._LegacyDispatch._write_terminal

        def recording(self_: Any, *args: Any, **kwargs: Any) -> None:
            try:
                original(self_, *args, **kwargs)
            except Exception as exc:  # reason: record what _write_terminal let propagate
                raised.append(exc)
                raise

        env = sandbox.environment(PITWALL_AGENTS_ASK_SUPPORT="1")
        with (
            mock.patch.object(dispatch._LegacyDispatch, "_write_terminal", recording),
            mock.patch.object(dispatch, "validate_result", side_effect=ValueError("boom")),
            contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO())),
            contextlib.redirect_stderr(io.TextIOWrapper(io.BytesIO())),
        ):
            dispatch.dispatch_legacy("codex", [str(sandbox.prompt()), *extra], environ=env)
        (run,) = sandbox.run_directories()
        self.assertFalse((run / "result.json").exists())
        return _delivery_prompt(run).exists(), raised

    def test_a_failing_terminal_step_still_removes_the_prompt_and_propagates(self) -> None:
        kept, raised = self._failing_terminal_step()
        self.assertFalse(kept)
        self.assertEqual(["boom"], [str(exc) for exc in raised])

    def test_a_failing_terminal_step_keeps_a_retained_prompt(self) -> None:
        kept, raised = self._failing_terminal_step(RETAIN)
        self.assertTrue(kept)
        self.assertEqual(["boom"], [str(exc) for exc in raised])
