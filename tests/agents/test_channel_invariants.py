"""§10 protocol invariants (plan Task 6)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents.broker import ChannelError, apply_channel_ask
from pitwall.agents.channel import (
    read_channel_inbox,
)
from pitwall.agents.mailbox import (
    Mailbox,
    MailboxOpenAskError,
)
from pitwall.agents.run_store import (
    RunStore,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c6"
OPTIONS = [{"id": "a", "text": "x"}]


class InvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}
        self.store = RunStore(root / "state" / "pitwall" / "agents", DISPATCH_ID)
        self.store.path.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _ask(self, **extra: object) -> dict:
        return self.store.mailbox().write_ask(
            blocked_on="choice", question="q", options=OPTIONS, default="a", deadline_s=600, **extra
        )

    def test_programmatic_writers_get_one_open_ask(self) -> None:
        self._ask(max_open=1)
        with self.assertRaises(MailboxOpenAskError):
            self._ask(max_open=1)
        self.store.mailbox().write_answer("0001", choice="a", answered_by="operator")
        self.assertEqual("0002", self._ask(max_open=1)["ask_id"])
        self.assertEqual("0003", self._ask()["ask_id"])  # direct callers stay unlimited

    def test_broker_refuses_a_second_open_ask_with_409(self) -> None:
        payload = {
            "delivery_id": "d1",
            "dispatch_id": DISPATCH_ID,
            "blocked_on": "choice",
            "question": "q",
            "options": OPTIONS,
            "default": "a",
            "deadline_s": 60,
        }
        apply_channel_ask(payload, json.dumps(payload).encode(), self.env)
        second = {**payload, "delivery_id": "d2"}
        with self.assertRaises(ChannelError) as caught:
            apply_channel_ask(second, json.dumps(second).encode(), self.env)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("ask_already_open", str(caught.exception))

    def test_unacked_steer_past_deadline_is_flagged_ignored(self) -> None:
        box = self.store.mailbox()
        steer = box.write_steer(kind="scope", message="narrow", deadline_s=60)
        path = box.root / "steer" / f"{steer['steer_id']}.json"
        doc = json.loads(path.read_text())
        doc["created_at"] = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        path.write_text(json.dumps(doc))
        entry = read_channel_inbox(self.env, DISPATCH_ID)["steers"][0]
        self.assertTrue(entry["ignored"])
        self.assertLess(entry["deadlineRemainingS"], 0)

    def test_sequence_gaps_are_logged_not_fatal(self) -> None:
        box = self.store.mailbox()
        box.write_steer(kind="note", message="one")
        (box.root / "steer" / "0003.json").write_text(
            json.dumps({**box.steers()[0], "steer_id": "0003"}), encoding="utf-8"
        )
        self.assertEqual({"steer": ["0002"]}, Mailbox(self.store.path, DISPATCH_ID).sequence_gaps())
        gaps = read_channel_inbox(self.env, None)["sequenceGaps"]
        self.assertEqual([{"dispatchId": DISPATCH_ID, "box": "steer", "missing": ["0002"]}], gaps)
        self.store.refresh_mailbox_summary()
        summary = json.loads(self.store.artifact("mailbox.json").read_text())
        self.assertEqual({"steer": ["0002"]}, summary["sequenceGaps"])


if __name__ == "__main__":
    unittest.main()
