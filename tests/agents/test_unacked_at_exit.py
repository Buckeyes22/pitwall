"""Blocking steers still unacknowledged when a run ends are reported, and the inbox forgets finished runs."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents.channel import (
    ChannelConfig,
    SteerWatcher,
    read_channel_inbox,
    write_channel_config,
)
from pitwall.agents.dispatch import Lifecycle, _finish_early
from pitwall.agents.events import EventEmitter
from pitwall.agents.harnesses.base import ParsedRequest
from pitwall.agents.run_store import TERMINAL_STATES, RunStore

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c1"


class UnackedAtExitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.env = {"PITWALL_AGENTS_STATE_HOME": str(Path(self._temp.name))}
        self.store = RunStore.create(self.env, DISPATCH_ID)
        self.store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))

    def _events(self, name: str) -> list[dict[str, Any]]:
        events_file = self.store.artifact("events.jsonl")
        if not events_file.exists():
            return []
        lines = events_file.read_text(encoding="utf-8").splitlines()
        return [event for event in map(json.loads, lines) if event["event"] == name]

    def test_pending_blocking_steer_is_reported_once_at_exit(self) -> None:
        steer = self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        watcher = SteerWatcher(
            self.store, EventEmitter(self.store, harness="codex", model="m"), threading.Event()
        )
        self.assertEqual([steer["steer_id"]], watcher.report_unacked_at_exit())
        watcher.report_unacked_at_exit()
        (event,) = self._events("steer.unacked")
        self.assertEqual(
            (steer["steer_id"], True), (event["data"]["steerId"], event["data"]["atExit"])
        )

    def _watcher(self) -> SteerWatcher:
        return SteerWatcher(
            self.store, EventEmitter(self.store, harness="codex", model="m"), threading.Event()
        )

    def test_advisory_steer_is_excluded_from_the_exit_list(self) -> None:
        self.store.mailbox().write_steer(kind="note", message="fyi", requires_ack=False)
        self.assertEqual([], self._watcher().report_unacked_at_exit())
        self.assertEqual([], self._events("steer.unacked"))

    def test_steer_acked_after_its_deadline_is_absent_at_exit(self) -> None:
        steer = self.store.mailbox().write_steer(kind="scope", message="SQLite only", deadline_s=1)
        watcher = self._watcher()
        with mock.patch("pitwall.agents.channel.time.time", return_value=time.time() + 60):
            watcher()
        self.assertEqual(1, len(self._events("steer.unacked")))
        self.store.mailbox().write_ack(steer["steer_id"])
        self.assertEqual([], watcher.report_unacked_at_exit())
        self.assertEqual(1, len(self._events("steer.unacked")))

    def test_early_finish_terminal_event_carries_the_unacked_steer_ids(self) -> None:
        steer = self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        emitter = EventEmitter(self.store, harness="codex", model="m")
        lifecycle = Lifecycle(self.store, "codex", "m", emitter)
        for state in ("preflighting", "ready"):
            lifecycle.transition(state)
        with contextlib.redirect_stdout(io.TextIOWrapper(io.BytesIO())):
            _finish_early(
                store=self.store,
                lifecycle=lifecycle,
                emitter=emitter,
                request=ParsedRequest(source="-", model="m", extra_args=[]),
                state="failed",
                event="dispatch.failed",
                exit_code=3,
                outcome="error",
                leading_newline=False,
            )
        (terminal,) = self._events("dispatch.failed")
        self.assertEqual([steer["steer_id"]], terminal["data"]["unackedSteerIds"])
        (unacked,) = self._events("steer.unacked")
        self.assertTrue(unacked["data"]["atExit"])

    def test_inbox_lists_paused_runs_and_skips_every_terminal_state(self) -> None:
        self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        self.store.write_json("run.json", {"schemaVersion": 1, "state": "paused"})
        self.assertEqual(1, len(read_channel_inbox(self.env, None)["steers"]))
        self.assertEqual(5, len(TERMINAL_STATES))
        for state in sorted(TERMINAL_STATES):
            with self.subTest(state=state):
                self.store.write_json("run.json", {"schemaVersion": 1, "state": state})
                self.assertEqual([], read_channel_inbox(self.env, None)["steers"])

    def test_all_runs_inbox_skips_finished_runs(self) -> None:
        self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        self.store.write_json("run.json", {"schemaVersion": 1, "state": "succeeded"})
        self.assertEqual([], read_channel_inbox(self.env, None)["steers"])
        self.assertEqual(1, len(read_channel_inbox(self.env, DISPATCH_ID)["steers"]))
