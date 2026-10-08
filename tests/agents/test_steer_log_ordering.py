"""The run event log never shows ``steer.acked`` before its ``steer.sent`` (ledger G-39)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents import channel, events
from pitwall.agents.channel import ChannelConfig, write_channel_config
from pitwall.agents.mailbox import Mailbox, MailboxError
from pitwall.agents.run_store import RunStore

DISPATCH_ID = "00000000-0000-4000-8000-0000000000a9"


def _names(store: RunStore) -> list[str]:
    path = store.artifact("events.jsonl")
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


class SteerLogOrderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        state = Path(self._temp.name) / "state"
        self.env = {"PITWALL_AGENTS_STATE_HOME": str(state)}
        self.store = RunStore(state, DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        (self.store.path / "run.json").write_text(json.dumps({"state": "running"}))
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))

    def _send(self) -> dict[str, Any]:
        return channel.send_steer(
            self.env,
            DISPATCH_ID,
            kind="scope",
            message="SQLite only",
            requires_ack=True,
            deadline_s=60,
            harness="cli",
        )

    def test_a_consumer_that_acks_instantly_cannot_beat_the_send_record(self) -> None:
        box = Mailbox(self.store.path, DISPATCH_ID)
        stop = threading.Event()

        def consumer() -> None:
            emitter = events.EventEmitter(self.store, harness="codex", model="m")
            while not stop.is_set():
                pending = box.unacked_steers()
                if pending:
                    box.write_ack(pending[0]["steer_id"])
                    emitter.emit_steer_acked(pending[0]["steer_id"])
                    return

        real_emit = events.EventEmitter.emit

        def slow_emit(
            self: events.EventEmitter, event: str, data: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            if event == "steer.sent":
                time.sleep(0.3)  # the window a fast consumer used to win
            return real_emit(self, event, data)

        thread = threading.Thread(target=consumer)
        thread.start()
        try:
            with mock.patch.object(events.EventEmitter, "emit", slow_emit):
                self._send()
            thread.join(timeout=10)
        finally:
            stop.set()
            thread.join(timeout=10)
        self.assertEqual(["steer.sent", "steer.acked"], _names(self.store))

    def test_a_failed_publish_is_recorded_not_left_dangling(self) -> None:
        with (
            mock.patch.object(Mailbox, "_write_doc", side_effect=MailboxError("disk full")),
            self.assertRaises(MailboxError),
        ):
            self._send()
        self.assertEqual(["steer.sent", "steer.failed"], _names(self.store))

    def test_a_lost_sequence_race_fails_that_id_and_retries(self) -> None:
        real = Mailbox._write_doc
        calls = {"n": 0}

        def racy(self: Mailbox, box: str, name: str, doc: dict[str, Any]) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise FileExistsError(name)
            return real(self, box, name, doc)

        with mock.patch.object(Mailbox, "_write_doc", racy):
            self._send()
        self.assertEqual(["steer.sent", "steer.failed", "steer.sent"], _names(self.store))


if __name__ == "__main__":
    unittest.main()
