"""Subagent-role MCP tools against a real server process (plan Task 14)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents import (
    mcp_tools,
)
from pitwall.agents.channel import (
    ChannelConfig,
    answer_ask,
    write_channel_config,
)
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.mailbox_probe import OpenAskContention
from tests.agents.mcp_test_client import (
    McpTestClient,
)
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000d4"
OPTIONS = [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}]


def _ask_args(**extra: object) -> dict:
    return {
        "question": "Which suffix?",
        "blocked_on": "naming",
        "options": OPTIONS,
        "default": "a",
        "default_rationale": "alpha is conventional",
        **extra,
    }


class SubagentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.state_root = root / "state" / "pitwall" / "agents"
        self.store = RunStore(self.state_root, DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        write_channel_config(
            self.store,
            ChannelConfig(
                DISPATCH_ID, timeout_seconds=600.0, attempt_started_epoch=time.time(), tier="1"
            ),
        )
        self.env = {
            "HOME": str(root),
            "PATH": os.environ.get("PATH", ""),
            "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": DISPATCH_ID,
            "PITWALL_AGENTS_CHANNEL_STATE_ROOT": str(self.state_root),
            "XDG_STATE_HOME": "${XDG_STATE_HOME:-}",
        }  # an unexpanded reference must be ignored
        self.operator_env = {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}
        self.client = McpTestClient(self.env)
        self.client.initialize()

    def tearDown(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def _call_in_background(
        self, arguments: dict, timeout: float = 20.0
    ) -> tuple[threading.Thread, dict]:
        box: dict = {}
        thread = threading.Thread(
            target=lambda: box.update(self.client.call("ask_orchestrator", arguments, timeout))
        )
        thread.start()
        return thread, box

    def _wait_for(self, path: Path, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > deadline:
                raise AssertionError(f"{path} never appeared")
            time.sleep(0.05)

    def test_tools_listed_for_the_subagent_role(self) -> None:
        names = {tool["name"] for tool in self.client.request("tools/list")["result"]["tools"]}
        self.assertEqual({"ask_orchestrator", "read_steering", "ack_steer"}, names)

    def test_ask_blocks_until_the_operator_answers(self) -> None:
        thread, box = self._call_in_background(_ask_args())
        self._wait_for(self.store.path / "mailbox" / "asks" / "0001.json")
        answer_ask(
            self.operator_env,
            DISPATCH_ID,
            "0001",
            choice="b",
            answered_by="operator",
            note="beta",
            harness="operator",
        )
        thread.join(HANG_GUARD_SECS)
        payload = box["result"]["structuredContent"]
        self.assertEqual(
            ("b", "operator", "operator"),
            (payload["choice"], payload["answered_by"], payload["resolved_by"]),
        )

    def test_concurrent_identical_asks_reenter_the_same_ask(self) -> None:
        from pitwall.agents.mailbox import Mailbox

        tools = mcp_tools.SubagentTools(self.env)
        # The open-ask lookup and the write share one lock. Hold the first caller inside that lock
        # (in its lookup) until the second has asked for the same lock: only a lock that really
        # covers lookup and write then lands both callers on one ask. A handshake, not a timer.
        original = Mailbox.pending_asks
        original_write = Mailbox.write_ask
        contention = OpenAskContention()
        written = threading.Semaphore(0)
        seen = threading.local()

        def widened(box: Mailbox) -> list[dict]:
            asks = original(box)
            if threading.current_thread().name.startswith("ask-caller") and not getattr(
                seen, "waited", False
            ):
                seen.waited = True  # only each caller's first lookup is held
                contention.hold()
            return asks

        def counted(box: Mailbox, **kwargs: Any) -> dict:
            try:
                return original_write(box, **kwargs)
            finally:
                written.release()

        outcomes: list[object] = []

        def call() -> None:
            try:
                outcomes.append(tools.ask(_ask_args(), threading.Event(), lambda _m: None))
            except Exception as exc:  # noqa: BLE001  # reason: collected, then asserted
                outcomes.append(exc)

        with (
            mock.patch.object(Mailbox, "pending_asks", widened),
            mock.patch.object(Mailbox, "write_ask", counted),
            contention.installed(),
        ):
            threads = [
                threading.Thread(target=call, name=f"ask-caller-{index}") for index in range(2)
            ]
            for thread in threads:
                thread.start()
            self._wait_for(self.store.path / "mailbox" / "asks" / "0001.json")
            # The answer may land only after both callers have finished their ask lookup.
            for _ in threads:
                self.assertTrue(written.acquire(timeout=HANG_GUARD_SECS), "a caller never wrote")
            answer_ask(
                self.operator_env,
                DISPATCH_ID,
                "0001",
                choice="b",
                answered_by="operator",
                note=None,
                harness="operator",
            )
            for thread in threads:
                thread.join(HANG_GUARD_SECS)
        self.assertTrue(contention.contended.is_set(), "the callers never contended for the lock")
        self.assertEqual(
            ["0001", "0001"],
            [o["ask_id"] if isinstance(o, dict) else repr(o) for o in outcomes],
        )
        self.assertEqual(1, len(list((self.store.path / "mailbox" / "asks").glob("*.json"))))

    def test_deadline_returns_the_default_and_records_it(self) -> None:
        reply = self.client.call(
            "ask_orchestrator", _ask_args(deadline_s=1), timeout=HANG_GUARD_SECS
        )
        payload = reply["result"]["structuredContent"]
        self.assertEqual(("a", "default"), (payload["choice"], payload["resolved_by"]))
        stored = self.store.mailbox().get_answer("0001")
        assert stored is not None
        self.assertEqual("default", stored["answered_by"])
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            ["ask.escalated", "ask.resolved"],
            [e["event"] for e in events if e["event"] in ("ask.escalated", "ask.resolved")],
        )

    def test_d1_cap_clamps_the_requested_deadline(self) -> None:
        thread, _box = self._call_in_background(_ask_args(deadline_s=3600))
        ask_file = self.store.path / "mailbox" / "asks" / "0001.json"
        self._wait_for(ask_file)
        self.assertEqual(90, json.loads(ask_file.read_text())["deadline_s"])  # 15% of 600 s
        answer_ask(
            self.operator_env,
            DISPATCH_ID,
            "0001",
            choice="a",
            answered_by="operator",
            note=None,
            harness="operator",
        )
        thread.join(HANG_GUARD_SECS)

    def test_one_open_ask_and_same_question_rewaits(self) -> None:
        first, first_box = self._call_in_background(_ask_args())
        self._wait_for(self.store.path / "mailbox" / "asks" / "0001.json")
        other = self.client.call("ask_orchestrator", _ask_args(question="Something else?"))
        self.assertTrue(other["result"]["isError"])
        self.assertIn("ask 0001 is still open", other["result"]["content"][0]["text"])
        # The retry runs in this process so the test can see it land on the open ask: the answer
        # must not be written before that, or the retry would open a second ask instead.
        from pitwall.agents.mailbox import Mailbox

        original_write = Mailbox.write_ask
        reentered = threading.Event()
        again_box: dict = {}

        def noting(box: Mailbox, **kwargs: Any) -> dict:
            try:
                return original_write(box, **kwargs)
            finally:
                if threading.current_thread().name == "ask-retry":
                    reentered.set()

        def retry() -> None:
            again_box.update(
                mcp_tools.SubagentTools(self.env).ask(
                    _ask_args(), threading.Event(), lambda _m: None
                )
            )

        again = threading.Thread(target=retry, name="ask-retry")
        with mock.patch.object(Mailbox, "write_ask", noting):
            again.start()
            self.assertTrue(reentered.wait(HANG_GUARD_SECS), "the retry never reached the mailbox")
        answer_ask(
            self.operator_env,
            DISPATCH_ID,
            "0001",
            choice="b",
            answered_by="operator",
            note=None,
            harness="operator",
        )
        first.join(HANG_GUARD_SECS)
        again.join(HANG_GUARD_SECS)
        self.assertEqual("b", first_box["result"]["structuredContent"]["choice"])
        self.assertEqual(("0001", "b"), (again_box["ask_id"], again_box["choice"]))
        self.assertEqual(1, len(self.store.mailbox().asks()))

    def test_cap_and_disabled_channel_are_refusals(self) -> None:
        write_channel_config(
            self.store,
            ChannelConfig(
                DISPATCH_ID, max_asks=1, timeout_seconds=600.0, attempt_started_epoch=time.time()
            ),
        )
        self.client.call("ask_orchestrator", _ask_args(deadline_s=1), timeout=HANG_GUARD_SECS)
        capped = self.client.call("ask_orchestrator", _ask_args(question="Second?"))
        self.assertIn("ask cap exceeded", capped["result"]["content"][0]["text"])
        self.store.artifact("channel.json").unlink()
        disabled = self.client.call("ask_orchestrator", _ask_args(question="Third?"))
        self.assertIn("asks are not enabled", disabled["result"]["content"][0]["text"])

    def test_steering_delivery_and_acknowledgement(self) -> None:
        box = self.store.mailbox()
        note = box.write_steer(kind="note", message="FYI", requires_ack=False)
        stop = box.write_steer(kind="stop", message="wrap up")
        first = self.client.call("read_steering", {})["result"]["structuredContent"]["steers"]
        self.assertEqual([note["steer_id"], stop["steer_id"]], [s["steer_id"] for s in first])
        second = self.client.call("read_steering", {})["result"]["structuredContent"]["steers"]
        self.assertEqual(
            [stop["steer_id"]], [s["steer_id"] for s in second]
        )  # the note was delivered once
        acked = self.client.call("ack_steer", {"steer_id": stop["steer_id"]})["result"][
            "structuredContent"
        ]
        self.assertEqual("stop", acked["kind"])
        self.assertIn("wrap up", acked["instruction"])
        self.assertTrue(
            self.client.call("ack_steer", {"steer_id": stop["steer_id"]})["result"][
                "structuredContent"
            ]["already"]
        )
        self.assertTrue(self.client.call("ack_steer", {"steer_id": "0099"})["result"]["isError"])
        events = [
            json.loads(line)["event"]
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(2, events.count("steer.acked"))

    def test_a_run_without_a_mailbox_has_no_steers_and_stays_channel_less(self) -> None:
        self.assertFalse((self.store.path / "mailbox").exists())
        steers = self.client.call("read_steering", {})["result"]["structuredContent"]["steers"]
        self.assertEqual([], steers)
        unknown = self.client.call("ack_steer", {"steer_id": "0001"})["result"]
        self.assertTrue(unknown["isError"])
        self.assertIn("unknown steer 0001", unknown["content"][0]["text"])
        self.assertFalse((self.store.path / "mailbox").exists())


class ProgressTests(unittest.TestCase):
    def test_long_waits_emit_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "state"
            store = RunStore(root, DISPATCH_ID)
            store.path.mkdir(parents=True)
            write_channel_config(
                store,
                ChannelConfig(
                    DISPATCH_ID, timeout_seconds=600.0, attempt_started_epoch=time.time()
                ),
            )
            tools = mcp_tools.SubagentTools(
                {
                    "HOME": directory,
                    "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": DISPATCH_ID,
                    "PITWALL_AGENTS_CHANNEL_STATE_ROOT": str(root),
                }
            )
            messages: list[str] = []
            with (
                mock.patch.object(mcp_tools, "PROGRESS_INTERVAL_S", 0.1),
                mock.patch.object(mcp_tools, "POLL_INTERVAL_S", 0.05),
            ):
                tools.ask(_ask_args(deadline_s=1), threading.Event(), messages.append)
            self.assertGreaterEqual(len(messages), 3)
            self.assertIn("ask 0001", messages[0])


if __name__ == "__main__":
    unittest.main()
