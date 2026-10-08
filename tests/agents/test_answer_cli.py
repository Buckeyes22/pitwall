"""Operator `answer` verb, D2 context pointers, and live `runs diff` (plan Task 5)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c5"
CLI = [str(PITWALL), "agents"]


class AnswerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {
            "HOME": str(root / "home"),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": "/usr/bin:/bin",
            "PITWALL_AGENTS_LEDGER": str(root / "ledger.jsonl"),
        }
        self.store = RunStore(root / "state" / "pitwall" / "agents", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.store.mailbox().write_ask(
            blocked_on="file-selection",
            question="Which migration first?",
            options=[{"id": "a", "text": "0031"}, {"id": "b", "text": "0032"}],
            default="b",
            deadline_s=600,
            files_touched=["db/migrations/0032_rename.sql"],
            default_rationale="0031 is additive",
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*CLI, *args], capture_output=True, text=True, env=self.env, check=False
        )

    def test_answer_writes_once_and_emits_ask_resolved(self) -> None:
        result = self._cli("answer", DISPATCH_ID[:8], "0001", "a", "--note", "0031 first")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"answered {DISPATCH_ID}/0001: a (by operator)", result.stdout)
        answer = self.store.mailbox().get_answer("0001")
        assert answer is not None
        self.assertEqual(
            ("operator", "a", "0031 first"),
            (answer["answered_by"], answer["choice"], answer["note"]),
        )
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [{"askId": "0001", "resolvedBy": "operator"}],
            [e["data"] for e in events if e["event"] == "ask.resolved"],
        )
        again = self._cli("answer", DISPATCH_ID, "0001", "b")
        self.assertEqual(1, again.returncode)
        self.assertIn("already has an answer", again.stderr)

    def test_refusals(self) -> None:
        self.assertIn("not found", self._cli("answer", "ffffffff", "0001", "a").stderr)
        bad = self._cli("answer", DISPATCH_ID, "0001", "zzz")
        self.assertEqual(1, bad.returncode)
        self.assertIsNone(self.store.mailbox().get_answer("0001"))
        by = self._cli("answer", DISPATCH_ID, "0001", "a", "--by", "orchestrator", "--json")
        self.assertEqual("orchestrator", json.loads(by.stdout)["answered_by"])

    def test_inbox_carries_d2_pointers(self) -> None:
        payload = json.loads(self._cli("inbox", "--json").stdout)
        ask = payload["asks"][0]
        self.assertEqual(["db/migrations/0032_rename.sql"], ask["filesTouched"])
        self.assertEqual(["a", "b"], [o["id"] for o in ask["options"]])
        self.assertEqual("b", ask["default"])
        self.assertEqual("0031 is additive", ask["defaultRationale"])
        self.assertEqual(f"pitwall agents runs diff {DISPATCH_ID}", ask["contextCommand"])
        self.assertIn(f"context: pitwall agents runs diff {DISPATCH_ID}", self._cli("inbox").stdout)

    def test_runs_diff_explains_shared_workspace_runs(self) -> None:
        result = self._cli("runs", "diff", DISPATCH_ID)
        self.assertEqual(1, result.returncode)
        self.assertIn("uses the shared workspace", result.stderr)


if __name__ == "__main__":
    unittest.main()
