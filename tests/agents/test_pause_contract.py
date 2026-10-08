"""Exit-75 pause classification: paused runs are not failures (Phase A, Task 8)."""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000a1"

_FAKE_HARNESS = """\
#!/usr/bin/env python3
\"\"\"Fake harness for pause tests: optionally asks, then exits 75.\"\"\"
import os
import signal
import sys

sys.path.insert(0, {runtime!r})

mode = os.environ.get("FAKE_PAUSE_MODE", "ask")
if mode == "done":
    print("resumed", flush=True)
    sys.exit(0)
if mode in ("ask", "crash", "answered"):
    from pitwall.agents.mailbox import Mailbox
    from pitwall.agents.run_store import state_root

    env = dict(os.environ)
    dispatch_id = env.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID") or env["PITWALL_AGENTS_DISPATCH_ID"]
    run_dir = state_root(env) / "runs" / dispatch_id
    box = Mailbox(run_dir, dispatch_id)
    ask = box.write_ask(
        blocked_on="choice",
        question="Migrations 0031 and 0032 both touch rate_buckets - apply 0032 first?",
        options=[
            {{"id": "a", "text": "Apply 0032 then 0031"}},
            {{"id": "b", "text": "Rebase 0031 onto 0032"}},
        ],
        default="b",
        deadline_s=600,
    )
    if mode == "crash":
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == "answered":
        # The scheduler answers asks while an attempt runs; here its answer lands
        # before the dispatcher has seen this process exit.
        box.write_answer(ask["ask_id"], choice="a", answered_by="orchestrator")
sys.exit(75)
"""


def _age_ask(run_dir: Path, ask_id: str, *, seconds: int) -> None:
    path = run_dir / "mailbox" / "asks" / f"{ask_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["created_at"] = (
        (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(doc), encoding="utf-8")


class _PauseHelpers(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def _install_ask_then_exit_harness(self, sandbox: ShimSandbox) -> None:
        target = sandbox.bin / "codex"
        target.write_text(_FAKE_HARNESS.format(runtime=str(ROOT / "src")), encoding="utf-8")
        target.chmod(0o755)

    def _dispatch(
        self, sandbox: ShimSandbox, prompt: Path, mode: str, *, ask_support: bool = False
    ) -> subprocess.CompletedProcess[bytes]:
        env = sandbox.environment(
            PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
            FAKE_PAUSE_MODE=mode,
            **({"PITWALL_AGENTS_ASK_SUPPORT": "1"} if ask_support else {}),
        )
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(prompt),
            ],
            capture_output=True,
            env=env,
            check=False,
        )

    def _run_dir(self, sandbox: ShimSandbox) -> Path:
        return sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID


class PauseContractTests(_PauseHelpers, unittest.TestCase):
    def test_exit_75_with_unresolved_ask_is_paused_not_failed(self) -> None:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        self._install_ask_then_exit_harness(sandbox)
        result = self._dispatch(sandbox, sandbox.prompt(), "ask")

        self.assertEqual(75, result.returncode)
        self.assertTrue(result.stdout.endswith(b"SHIM-DONE exit=75\n"), result.stdout)

        run_dir = self._run_dir(sandbox)
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("paused", run["state"])
        self.assertFalse((run_dir / "result.json").exists(), "paused runs emit no result document")
        pause = json.loads((run_dir / "pause.json").read_text(encoding="utf-8"))
        self.assertEqual(["0001"], pause["pendingAskIds"])

        events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("dispatch.paused", events)
        self.assertNotIn("dispatch.failed", events)

        ledger_rows = [
            json.loads(line)
            for line in sandbox.ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        paused_rows = [row for row in ledger_rows if row.get("event") == "paused"]
        self.assertEqual(1, len(paused_rows))
        self.assertEqual(75, paused_rows[0]["exit"])
        self.assertEqual("paused", paused_rows[0].get("outcome"))

        mailbox_summary = json.loads((run_dir / "mailbox.json").read_text(encoding="utf-8"))
        self.assertEqual(["0001"], mailbox_summary["unresolvedAskIds"])

    def test_exit_75_without_an_ask_is_contract_abuse_failure(self) -> None:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        self._install_ask_then_exit_harness(sandbox)
        result = self._dispatch(sandbox, sandbox.prompt(), "silent")

        self.assertEqual(75, result.returncode)
        self.assertTrue(result.stdout.endswith(b"SHIM-DONE exit=75\n"), result.stdout)
        self.assertIn(b"exit 75", result.stderr)

        run_dir = self._run_dir(sandbox)
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", run["state"])
        outcome = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", outcome["status"])
        events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("dispatch.paused", events)
        self.assertIn("dispatch.failed", events)


class AnsweredBeforeExitTests(_PauseHelpers, unittest.TestCase):
    """An ask answered before the dispatcher sees the exit is still a pause."""

    def _resume(self, sandbox: ShimSandbox, mode: str) -> subprocess.CompletedProcess[bytes]:
        env = sandbox.environment(
            PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
            FAKE_PAUSE_MODE=mode,
            PITWALL_AGENTS_ASK_SUPPORT="1",
        )
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "resume",
                DISPATCH_ID,
            ],
            capture_output=True,
            env=env,
            check=False,
        )

    def test_exit_75_with_an_ask_answered_before_the_exit_is_paused_and_resumes(self) -> None:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        self._install_ask_then_exit_harness(sandbox)
        result = self._dispatch(sandbox, sandbox.prompt(), "answered", ask_support=True)

        self.assertEqual(75, result.returncode, result.stderr)
        self.assertTrue(result.stdout.endswith(b"SHIM-DONE exit=75\n"), result.stdout)
        run_dir = self._run_dir(sandbox)
        self.assertEqual(
            "paused", json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["state"]
        )
        self.assertFalse((run_dir / "result.json").exists(), "paused runs emit no result document")
        pause = json.loads((run_dir / "pause.json").read_text(encoding="utf-8"))
        self.assertEqual(([], "question"), (pause["pendingAskIds"], pause["reason"]))
        events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
        self.assertIn("dispatch.paused", events)
        self.assertNotIn("dispatch.failed", events)

        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        finished = [r for r in sandbox.ledger_records() if r.get("event") == "finished"]
        self.assertEqual(["0001:orchestrator"], finished[-1]["askResolutions"])

    def test_an_ask_answered_in_an_earlier_attempt_does_not_excuse_a_later_exit_75(self) -> None:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        self._install_ask_then_exit_harness(sandbox)
        paused = self._dispatch(sandbox, sandbox.prompt(), "answered", ask_support=True)
        self.assertEqual(75, paused.returncode, paused.stderr)

        again = self._resume(sandbox, "silent")
        self.assertEqual(75, again.returncode, again.stderr)
        self.assertIn(b"exit 75", again.stderr)
        run_dir = self._run_dir(sandbox)
        self.assertEqual(
            "failed", json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["state"]
        )
        self.assertEqual(
            "failed", json.loads((run_dir / "result.json").read_text(encoding="utf-8"))["status"]
        )


class OrphanedAskTests(_PauseHelpers, unittest.TestCase):
    def _resume(self, sandbox: ShimSandbox) -> subprocess.CompletedProcess[bytes]:
        env = sandbox.environment(
            PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
            FAKE_PAUSE_MODE="done",
            PITWALL_AGENTS_ASK_SUPPORT="1",
        )
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "resume",
                DISPATCH_ID,
            ],
            capture_output=True,
            env=env,
            check=False,
        )

    def _crashing_sandbox(self) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        self._install_ask_then_exit_harness(sandbox)
        return sandbox

    def test_crash_with_a_pending_ask_pauses_and_resume_defaults_it(self) -> None:
        sandbox = self._crashing_sandbox()
        crashed = self._dispatch(sandbox, sandbox.prompt(), "crash", ask_support=True)
        self.assertEqual(137, crashed.returncode)
        self.assertTrue(crashed.stdout.endswith(b"SHIM-DONE exit=137\n"))
        run_dir = self._run_dir(sandbox)
        self.assertEqual("paused", json.loads((run_dir / "run.json").read_text())["state"])
        self.assertEqual("orphaned-ask", json.loads((run_dir / "pause.json").read_text())["reason"])
        _age_ask(run_dir, "0001", seconds=4000)
        resumed = self._resume(sandbox)
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        finished = [r for r in sandbox.ledger_records() if r.get("event") == "finished"]
        self.assertEqual(["0001:default"], finished[-1]["askResolutions"])

    def test_crash_without_ask_support_stays_a_failure(self) -> None:
        sandbox = self._crashing_sandbox()
        crashed = self._dispatch(sandbox, sandbox.prompt(), "crash", ask_support=False)
        self.assertEqual(137, crashed.returncode)
        self.assertEqual(
            "failed", json.loads((self._run_dir(sandbox) / "run.json").read_text())["state"]
        )


if __name__ == "__main__":
    unittest.main()
