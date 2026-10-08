"""Run-store mailbox laziness plus channel event envelopes (Phase A, Task 7)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.events import (
    EventEmitter,
)
from pitwall.agents.run_store import (
    RunStore,
)

ROOT = Path(__file__).resolve().parents[2]


def _env(directory: str) -> dict[str, str]:
    return {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}


def _ask(box: object, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "blocked_on": "choice",
        "question": "Proceed?",
        "options": [{"id": "a", "text": "yes"}, {"id": "b", "text": "no"}],
        "default": "a",
        "deadline_s": 600,
    }
    kwargs.update(overrides)
    return box.write_ask(**kwargs)  # type: ignore[operator]  # reason: test helper calls a loosely typed callable


class RunStoreMailboxTests(unittest.TestCase):
    def test_mailbox_dir_is_created_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore.create(_env(directory), "dispatch-one")
            self.assertFalse((store.path / "mailbox").exists())
            self.assertFalse(store.artifact("mailbox.json").exists())
            store.mailbox()
            self.assertTrue((store.path / "mailbox").is_dir())
            self.assertTrue(store.artifact("mailbox.json").is_file())

    def test_mailbox_json_updates_on_write_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore.create(_env(directory), "dispatch-one")
            box = store.mailbox()
            _ask(box)
            _ask(box)
            summary = json.loads(store.artifact("mailbox.json").read_text(encoding="utf-8"))
            self.assertEqual(2, summary["asks"])
            self.assertEqual(["0001", "0002"], summary["unresolvedAskIds"])
            box.write_answer("0001", choice="a", answered_by="operator")
            summary = json.loads(store.artifact("mailbox.json").read_text(encoding="utf-8"))
            self.assertEqual(["0002"], summary["unresolvedAskIds"])
            steer = box.write_steer(kind="scope", message="narrow it")
            summary = json.loads(store.artifact("mailbox.json").read_text(encoding="utf-8"))
            self.assertEqual([steer["steer_id"]], summary["unackedSteerIds"])
            self.assertIsNone(summary["lastAckedSteerId"])
            box.write_ack(steer["steer_id"])
            summary = json.loads(store.artifact("mailbox.json").read_text(encoding="utf-8"))
            self.assertEqual([], summary["unackedSteerIds"])
            self.assertEqual(steer["steer_id"], summary["lastAckedSteerId"])


class ChannelEventTests(unittest.TestCase):
    def test_dispatch_paused_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore.create(_env(directory), "dispatch-one")
            emitter = EventEmitter(store, harness="codex", model="gpt-test")
            event = emitter.emit_dispatch_paused({"askId": "0001", "reason": "awaiting answer"})
            self.assertEqual("dispatch.paused", event["event"])
            self.assertEqual("0001", event["data"]["askId"])
            tail = store.artifact("events.jsonl").read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(event, json.loads(tail[-1]))

    def test_ask_resolved_carries_resolved_by(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore.create(_env(directory), "dispatch-one")
            emitter = EventEmitter(store, harness="codex", model="gpt-test")
            event = emitter.emit_ask_resolved("0001", "operator")
            self.assertEqual("ask.resolved", event["event"])
            self.assertEqual("operator", event["data"]["resolvedBy"])
            self.assertEqual("0001", event["data"]["askId"])
            with self.assertRaises(ValueError):
                emitter.emit_ask_resolved("0001", "")

    def test_steer_acked_is_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore.create(_env(directory), "dispatch-one")
            emitter = EventEmitter(store, harness="codex", model="gpt-test")
            event = emitter.emit_steer_acked("0003")
            self.assertEqual("steer.acked", event["event"])
            self.assertEqual("0003", event["data"]["steerId"])
            worldwide = (store.state_root / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("steer.acked", worldwide)


if __name__ == "__main__":
    unittest.main()
