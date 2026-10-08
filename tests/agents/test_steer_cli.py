"""steer and runs stop verbs; steering delivered on resume (plan Task 21)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents.channel import ChannelConfig, write_channel_config
from pitwall.agents.cli import _runs_stop
from pitwall.agents.dispatch import (
    build_resume_prompt,
)
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000f1"
CLI = [str(PITWALL), "agents"]


class SteerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {
            "HOME": str(root),
            "XDG_STATE_HOME": str(root / "state"),
            "PATH": "/usr/bin:/bin",
        }
        self.store = RunStore(root / "state" / "pitwall" / "agents", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self._state("running")
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _state(self, state: str) -> None:
        self.store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": DISPATCH_ID,
                "state": state,
                "provider": "codex",
                "model": "m",
                "attempt": 1,
            },
        )

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*CLI, *args], capture_output=True, text=True, env=self.env, check=False
        )

    def test_steer_is_refused_for_a_run_without_the_channel(self) -> None:
        self.store.artifact("channel.json").unlink()
        result = self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        self.assertEqual(1, result.returncode)
        self.assertIn("without the orchestrator channel", result.stderr)
        self.assertFalse((self.store.path / "mailbox").exists())
        stop = self._cli("runs", "stop", DISPATCH_ID)
        self.assertEqual(0, stop.returncode)
        self.assertIn("no orchestrator channel", stop.stdout)

    def test_steer_to_a_pre_launch_run_without_its_config_says_still_preparing(self) -> None:
        self.store.artifact("channel.json").unlink()
        self._state("workspace_preparing")
        result = self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        self.assertEqual(1, result.returncode)
        self.assertIn(
            f"run {DISPATCH_ID} is still preparing and has not recorded its orchestrator channel yet; retry the steer once it is running, or send a stop steer",
            result.stderr,
        )
        self.assertNotIn("without the orchestrator channel", result.stderr)
        self.assertFalse((self.store.path / "mailbox").exists())
        self.assertFalse(self.store.artifact("events.jsonl").exists())

    def test_steer_to_a_pre_launch_run_with_its_config_is_accepted(self) -> None:
        self._state("workspace_preparing")
        result = self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_steer_to_a_run_with_an_unreadable_channel_json_is_refused(self) -> None:
        self.store.artifact("channel.json").write_text("{not json", encoding="utf-8")
        result = self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        self.assertEqual(1, result.returncode)
        self.assertIn(
            f"run {DISPATCH_ID} has an unreadable channel.json; only a stop steer applies",
            result.stderr,
        )
        self.assertNotIn("without the orchestrator channel", result.stderr)
        self.assertFalse((self.store.path / "mailbox").exists())

    def test_stop_steer_to_a_run_without_the_channel_is_accepted(self) -> None:
        self.store.artifact("channel.json").unlink()
        result = self._cli("steer", DISPATCH_ID, "--kind", "stop", "--message", "wrap up", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("stop", json.loads(result.stdout)["kind"])

    def test_malformed_steer_to_a_channel_less_run_is_a_validation_error(self) -> None:
        from pitwall.agents.channel import send_steer
        from pitwall.agents.mailbox import MailboxError

        self.store.artifact("channel.json").unlink()
        with self.assertRaises(MailboxError) as caught:
            send_steer(
                self.env,
                DISPATCH_ID,
                kind="scope",
                message="",
                requires_ack=True,
                deadline_s=300,
                harness="operator",
            )
        self.assertNotIn("orchestrator channel", str(caught.exception))
        self.assertFalse((self.store.path / "mailbox").exists())

    def test_steer_writes_and_emits_sent(self) -> None:
        result = self._cli(
            "steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only", "--json"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        steer = json.loads(result.stdout)
        self.assertEqual(
            ("0001", "scope", True, 300),
            (steer["steer_id"], steer["kind"], steer["requires_ack"], steer["deadline_s"]),
        )
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [{"steerId": "0001", "kind": "scope"}],
            [e["data"] for e in events if e["event"] == "steer.sent"],
        )
        note = json.loads(
            self._cli(
                "steer", DISPATCH_ID, "--kind", "note", "--message", "fyi", "--no-ack", "--json"
            ).stdout
        )
        self.assertFalse(note["requires_ack"])

    def test_terminal_and_paused_runs_are_refused(self) -> None:
        self._state("succeeded")
        refused = self._cli("steer", DISPATCH_ID, "--kind", "note", "--message", "late")
        self.assertEqual(1, refused.returncode)
        self.assertIn("is succeeded", refused.stderr)
        self._state("paused")
        self.assertEqual(
            0,
            self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "on resume").returncode,
        )
        stop = self._cli("runs", "stop", DISPATCH_ID)
        self.assertEqual(1, stop.returncode)
        self.assertIn("is paused", stop.stderr)

    def test_runs_stop_writes_a_stop_steer_with_the_grace_window(self) -> None:
        result = self._cli("runs", "stop", DISPATCH_ID, "--grace", "5")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("stop requested: steer 0001", result.stdout)
        steer = self.store.mailbox().steers()[0]
        self.assertEqual(
            ("stop", True, 5), (steer["kind"], steer["requires_ack"], steer["deadline_s"])
        )

    def test_runs_stop_on_an_abandoned_run_records_the_failure(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", ""])
        child.wait()
        document = json.loads(self.store.artifact("run.json").read_text(encoding="utf-8"))
        document["supervisor"] = {"pid": child.pid, "pidStartIdentity": "exited"}
        self.store.write_json("run.json", document)
        result = self._cli("runs", "stop", DISPATCH_ID)
        self.assertEqual(1, result.returncode)
        self.assertIn("was abandoned", result.stderr)
        state = json.loads(self.store.artifact("run.json").read_text(encoding="utf-8"))["state"]
        self.assertEqual("failed", state)
        self.assertFalse((self.store.path / "mailbox" / "steer").exists())

    def test_runs_stop_ends_the_surviving_harness_of_a_terminal_run(self) -> None:
        from pitwall.agents.pids import process_identity

        harness = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], process_group=0
        )
        try:
            self._state("failed")
            document = json.loads(self.store.artifact("run.json").read_text(encoding="utf-8"))
            document["harnessProcess"] = {
                "pgid": harness.pid,
                "pidStartIdentity": process_identity(harness.pid),
            }
            self.store.write_json("run.json", document)
            result = self._cli("runs", "stop", DISPATCH_ID, "--grace", "5")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("still running", result.stdout)
            self.assertEqual(-signal.SIGTERM, harness.wait(timeout=HANG_GUARD_SECS))
            self.assertFalse((self.store.path / "mailbox" / "steer").exists())
            again = self._cli("runs", "stop", DISPATCH_ID)
            self.assertEqual(1, again.returncode)
            self.assertIn("is failed", again.stderr)
        finally:
            if harness.poll() is None:
                harness.kill()
                harness.wait()

    def test_runs_stop_reports_a_reconcile_error_and_writes_no_steer(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            mock.patch("pitwall.agents.cli.reconcile_run", side_effect=OSError("disk full")),
            contextlib.redirect_stderr(stderr),
        ):
            code = _runs_stop(DISPATCH_ID, None, 5)
        self.assertEqual(1, code)
        self.assertIn(f"cannot reconcile run {DISPATCH_ID}", stderr.getvalue())
        self.assertFalse((self.store.path / "mailbox" / "steer").exists())

    def test_resume_prompt_carries_undelivered_steering(self) -> None:
        steer = {"steer_id": "0002", "kind": "scope", "message": "SQLite only"}
        rebuilt = build_resume_prompt(b"original\n", [], 2, steers=[steer])
        self.assertIn(b"# Orchestrator steering (delivered on resume)", rebuilt)
        self.assertIn(b"[scope 0002] SQLite only", rebuilt)


if __name__ == "__main__":
    unittest.main()
