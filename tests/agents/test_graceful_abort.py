"""Graceful abort: SIGTERM and ignored stop steers end runs with a receipt (plan Task 22)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path

from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000f2"


class GracefulAbortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_harness("codex")
        self.run_dir = self.sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID
        self.pid_file = self.sandbox.root / "child.pid"

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _start(self, **env: str) -> subprocess.Popen[bytes]:
        process = self.sandbox.popen(
            [str(PITWALL), "agents", "dispatch", "codex", str(self.sandbox.prompt())],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.sandbox.environment(
                PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
                FAKE_SLEEP_SECS="30",
                FAKE_PID_FILE=str(self.pid_file),
                **env,
            ),
        )
        deadline = time.monotonic() + HANG_GUARD_SECS
        while not self.pid_file.exists():
            self.assertLess(time.monotonic(), deadline, "child never started")
            time.sleep(0.05)
        return process

    def _finished(self) -> dict:
        return [row for row in self.sandbox.ledger_records() if row.get("event") == "finished"][-1]

    def test_sigterm_to_the_supervisor_ends_with_a_receipt(self) -> None:
        process = self._start()
        process.send_signal(signal.SIGTERM)
        out, _err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(143, process.returncode)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=143\n"), out[-200:])
        self.assertEqual(
            "cancelled", json.loads((self.run_dir / "result.json").read_text())["status"]
        )
        self.assertEqual(
            ("cancelled", "signal SIGTERM"),
            (self._finished()["outcome"], self._finished()["abortReason"]),
        )
        with self.assertRaises(ProcessLookupError):
            os.kill(int(self.pid_file.read_text()), 0)

    def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(self) -> None:
        process = self._start(FAKE_IGNORE_TERM="1", PITWALL_AGENTS_ABORT_GRACE_SECS="1")
        process.send_signal(signal.SIGTERM)
        out, _err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(137, process.returncode)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=137\n"))
        self.assertEqual("killed", self._finished()["outcome"])
        self.assertTrue(json.loads((self.run_dir / "abort.json").read_text())["killed"])

    def test_an_ignored_stop_steer_aborts_after_its_window(self) -> None:
        process = self._start()
        subprocess.run(
            [str(PITWALL), "agents", "runs", "stop", DISPATCH_ID, "--grace", "1"],
            env=self.sandbox.environment(),
            check=True,
            capture_output=True,
        )
        out, _err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(143, process.returncode)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=143\n"))
        self.assertEqual("stop steer 0001 not honored within 1s", self._finished()["abortReason"])
        self.assertEqual(["0001"], self._finished()["unackedSteerIds"])
        events = [
            json.loads(line) for line in (self.run_dir / "events.jsonl").read_text().splitlines()
        ]
        self.assertIn("steer.unacked", [event["event"] for event in events])
        self.assertEqual(["0001"], events[-1]["data"]["unackedSteerIds"])


if __name__ == "__main__":
    unittest.main()
