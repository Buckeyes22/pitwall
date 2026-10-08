"""D5: raw Q&A expires with the run; aggregates reach the ledger first (plan Task 28)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.run_store import (
    RunStore,
    cleanup_runs,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000a9"


class DistillBeforeCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.ledger = root / "ledger.jsonl"
        self.env = {
            "HOME": str(root),
            "XDG_STATE_HOME": str(root / "state"),
            "PITWALL_AGENTS_LEDGER": str(self.ledger),
        }
        self.store = RunStore(root / "state" / "pitwall" / "agents", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": DISPATCH_ID,
                "provider": "opencode",
                "model": "zai-coding-plan/glm-5.3",
                "state": "succeeded",
            },
        )

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_aggregate_row_precedes_removal_and_holds_no_content(self) -> None:
        box = self.store.mailbox()
        box.write_ask(
            blocked_on="naming",
            question="SECRET QUESTION TEXT",
            options=[{"id": "a", "text": "x"}],
            default="a",
            deadline_s=60,
            files_touched=["secret/path.py"],
        )
        box.write_answer("0001", choice="a", answered_by="operator")
        box.write_steer(kind="scope", message="narrow")
        self.assertEqual(
            [self.store.path], cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        )
        rows = [json.loads(line) for line in self.ledger.read_text().splitlines()]
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(
            ("channel", "opencode", "zai-coding-plan/glm-5.3"),
            (row["event"], row["shim"], row["model"]),
        )
        self.assertEqual(
            (1, {"naming": 1}, {"operator": 1}, 1),
            (row["asks"], row["blockedOn"], row["resolvedBy"], row["steers"]),
        )
        self.assertNotIn("SECRET", json.dumps(row))
        self.assertNotIn("secret/path.py", json.dumps(row))

    def test_runs_without_channel_activity_add_no_row(self) -> None:
        cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        self.assertFalse(self.ledger.exists())

    def test_an_unwritable_ledger_keeps_the_run(self) -> None:
        self.ledger.mkdir()  # appending to a directory raises OSError
        self.store.mailbox().write_steer(kind="note", message="m")
        self.assertEqual([], cleanup_runs(self.env, older_than_seconds=None, remove_all=True))
        self.assertTrue(self.store.path.is_dir())


if __name__ == "__main__":
    unittest.main()
