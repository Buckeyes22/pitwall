"""Pre-merge review fixes R3, R4, R10, R15 (plan continuation, 2026-09-17)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.mailbox import (
    Mailbox,
    MailboxError,
)
from pitwall.agents.run_store import (
    RunStore,
    atomic_write_json,
    state_root,
)
from pitwall.agents.scheduler import (
    AttemptOutcome,
    _ProductionRunner,
)
from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)
from tests.agents.test_pause_contract import (
    DISPATCH_ID,
    _PauseHelpers,
)
from tests.agents.test_scheduler import (
    SchedulerFixture,
    make_registry,
    task,
)
from tests.agents.test_scheduler_wait import (
    PausingRunner,
)

ROOT = Path(__file__).resolve().parents[2]


def _ask_doc(dispatch_id: str, ask_id: str) -> dict:
    return {
        "version": 1,
        "dispatch_id": dispatch_id,
        "ask_id": ask_id,
        "created_at": "2026-09-17T00:00:00Z",
        "blocked_on": "choice",
        "question": "q",
        "context": {"files_touched": [], "options": [{"id": "a", "text": "x"}]},
        "default": "a",
        "deadline_s": 600,
        "severity": "normal",
    }


class MailboxAskIdTests(unittest.TestCase):
    """R10: ask_id is NNNN and matches the file; answers never escape the mailbox."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"
        self.box = Mailbox(self.run_dir, "d-1")
        self.box.ensure()

    def _drop(self, name: str, doc: dict) -> Path:
        path = self.run_dir / "mailbox" / "asks" / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_non_numeric_ask_id_is_quarantined_not_a_crash(self) -> None:
        self._drop("0001.json", _ask_doc("d-1", "first"))
        self.assertEqual([], self.box.asks())
        self.assertEqual([], self.box.enforce_cap())
        self.assertTrue(list((self.run_dir / "mailbox" / "dead-letter").glob("asks_0001*.json")))

    def test_ask_id_must_match_the_file_stem(self) -> None:
        self._drop("0002.json", _ask_doc("d-1", "0001"))
        self.assertEqual([], self.box.asks())
        self.assertTrue(list((self.run_dir / "mailbox" / "dead-letter").glob("asks_0002*.json")))

    def test_answer_for_a_traversal_id_never_writes_outside_the_mailbox(self) -> None:
        escape = "../../../../escaped"
        with self.assertRaises(MailboxError):
            self.box.write_answer(escape, choice="a", answered_by="operator")
        self.assertFalse(list(Path(self.tmp.name).glob("**/escaped.json")))
        self.assertFalse((self.run_dir / "mailbox" / "answers").joinpath("escaped.json").exists())


class MailboxSummaryRewriteTests(unittest.TestCase):
    """R15: reads do not rewrite mailbox.json unless the mailbox changed."""

    def test_pending_asks_read_does_not_rewrite_the_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp), "d-2")
            store.path.mkdir(parents=True)
            box = store.mailbox()
            box.write_ask(
                blocked_on="choice",
                question="q",
                options=[{"id": "a", "text": "x"}],
                default="a",
                deadline_s=60,
            )
            summary = store.artifact("mailbox.json")
            before = summary.stat().st_mtime_ns
            os.utime(summary, ns=(before - 10**9, before - 10**9))
            stamped = summary.stat().st_mtime_ns
            for _ in range(3):
                self.assertEqual(1, len(store.mailbox().pending_asks()))
            self.assertEqual(
                stamped, summary.stat().st_mtime_ns, "read-only polls must not rewrite"
            )
            # a quarantine that changes nothing visible in the summary still does not rewrite it
            (store.path / "mailbox" / "asks" / "0002.json").write_text(
                "{not json", encoding="utf-8"
            )
            store.mailbox().pending_asks()
            self.assertEqual(stamped, summary.stat().st_mtime_ns)
            # a real change (a second ask) does
            store.mailbox().write_ask(
                blocked_on="choice",
                question="q2",
                options=[{"id": "a", "text": "x"}],
                default="a",
                deadline_s=60,
            )
            self.assertNotEqual(stamped, summary.stat().st_mtime_ns)
            self.assertEqual(["0001", "0003"], json.loads(summary.read_text())["unresolvedAskIds"])


class OverCapPauseTests(_PauseHelpers, unittest.TestCase):
    """R4 (dispatch): the cap is enforced before pending asks are counted."""

    _OVERCAP_HARNESS = """\
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, {runtime!r})
from pitwall.agents.mailbox import Mailbox
from pitwall.agents.run_store import state_root
env = dict(os.environ)
dispatch_id = env.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID") or env["PITWALL_AGENTS_DISPATCH_ID"]
box = Mailbox(state_root(env) / "runs" / dispatch_id, dispatch_id, max_asks=99)
opts = [{{"id": "a", "text": "x"}}]
box.write_ask(blocked_on="choice", question="one", options=opts, default="a", deadline_s=600)
box.write_answer("0001", choice="a", answered_by="operator")
box.write_ask(blocked_on="choice", question="two", options=opts, default="a", deadline_s=600)
sys.exit(75)
"""

    def test_exit_75_with_only_an_over_cap_ask_is_a_failure_not_an_empty_pause(
        self,
    ) -> None:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        target = sandbox.bin / "codex"
        target.write_text(
            self._OVERCAP_HARNESS.format(runtime=str(ROOT / "src")),
            encoding="utf-8",
        )
        target.chmod(0o755)
        env = sandbox.environment(PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID)
        result = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(sandbox.prompt()),
                "--routing-max-asks=1",
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        run_dir = self._run_dir(sandbox)
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", run["state"], result.stderr)
        self.assertFalse((run_dir / "pause.json").exists())
        self.assertIn(b"ask cap", result.stderr)
        self.assertTrue(list((run_dir / "mailbox" / "dead-letter").glob("asks_0002*.json")))


class PauseClassificationTests(SchedulerFixture):
    """R3: a run whose run.json says paused is paused, whatever the exit code."""

    def test_launch_reads_the_paused_state_for_a_non_75_exit(self) -> None:
        runner = _ProductionRunner(make_registry(), self.env)
        dispatch_id = "00000000-0000-4000-8000-0000000000b3"
        run_dir = state_root(self.env) / "runs" / dispatch_id
        run_dir.mkdir(parents=True)
        atomic_write_json(
            run_dir / "run.json",
            {"schemaVersion": 1, "dispatchId": dispatch_id, "state": "paused"},
        )
        outcome = runner._launch(
            [sys.executable, "-c", "import sys; sys.exit(137)"],
            dict(self.env),
            self.env,
            self.repo,
            dispatch_id,
            self.root / "out.log",
            self.root / "err.log",
        )
        self.assertEqual("paused", outcome.status)
        self.assertFalse(outcome.transport_error)
        self.assertEqual(137, outcome.exit_code)


class ResumeLoopBoundTests(SchedulerFixture):
    """R4 (scheduler): a dispatch that pauses again and again stops at maxAsks resumes."""

    def test_endless_pausing_stops_at_max_asks(self) -> None:
        class AlwaysPausing(PausingRunner):
            def resume(
                self,
                task_id,
                task_value,
                attempt,
                dispatch_id,
                workflow_id,
                workflow_dir,
                env,
                repo_root,
            ):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
                self.resumes.append(dispatch_id)
                store = RunStore(state_root(env), dispatch_id)
                atomic_write_json(
                    store.artifact("run.json"),
                    {
                        "schemaVersion": 1,
                        "dispatchId": dispatch_id,
                        "state": "paused",
                        "provider": "opencode",
                        "model": "m",
                        "attempt": attempt,
                    },
                )
                return AttemptOutcome(dispatch_id, "paused", 75)

        runner = AlwaysPausing(self.env, self.repo, {"a": {"blocked_on": "naming", "age": 4000}})
        asking = task()
        asking["askSupport"] = True
        asking["maxAsks"] = 2
        state = self.execute(self.workflow({"a": asking}), runner)
        self.assertEqual("failed", state["status"], state)
        self.assertLessEqual(len(runner.resumes), 2)
        self.assertIn("maxAsks", state["tasks"]["a"].get("error") or "")


if __name__ == "__main__":
    unittest.main()
