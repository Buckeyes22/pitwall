"""Broker crash windows stay idempotent (spec §11 chaos; plan Task 7)."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from urllib import error, request

from pitwall.agents.broker import ChannelError, apply_channel_answer, apply_channel_ask
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import PITWALL
from tests.agents.test_channel_broker import (
    SECRET,
    _ask_payload,
    _signature,
)
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c7"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_receiver(env: dict[str, str]) -> tuple[str, subprocess.Popen[str]]:
    """Start a real receiver and return its base URL and process (so a test can SIGKILL it).

    A free port is only free until something else takes it: under parallel load another test's
    server (or a just-killed receiver's lingering socket) can own it by the time the receiver
    binds. A receiver that failed to bind exits, so health only counts while *our* process is
    still alive, and a port that was taken is retried with a fresh one.
    """
    opener = request.build_opener(request.ProxyHandler({}))
    deadline = time.monotonic() + HANG_GUARD_SECS
    while time.monotonic() < deadline:
        port = _free_port()
        process = subprocess.Popen(
            [str(PITWALL), "agents", "broker", "receiver", "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5):
                    pass
            except OSError, http.client.HTTPException:  # not listening yet, or not ours
                time.sleep(0.05)
                continue
            if process.poll() is None:
                return f"http://127.0.0.1:{port}", process
        if process.poll() is None:
            process.kill()
        process.wait(timeout=HANG_GUARD_SECS)
        if process.stderr is not None:
            process.stderr.close()
    raise AssertionError("receiver did not become healthy")


class BrokerCrashTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.env = {
            "HOME": str(root),
            "XDG_STATE_HOME": str(root / "state"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "PATH": os.environ.get("PATH", ""),
            "PITWALL_AGENTS_WEBHOOK_SECRET": SECRET,
        }
        self.store = RunStore(root / "state" / "pitwall" / "agents", DISPATCH_ID)
        self.store.path.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_answer_written_before_the_crash_is_acknowledged_on_retry(self) -> None:
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="q",
            options=[{"id": "a", "text": "x"}],
            default="a",
            deadline_s=60,
        )
        self.store.mailbox().write_answer(
            "0001", choice="a", answered_by="operator"
        )  # broker died here
        payload = {
            "delivery_id": "ans-1",
            "dispatch_id": DISPATCH_ID,
            "choice": "a",
            "answered_by": "operator",
        }
        result = apply_channel_answer("0001", payload, json.dumps(payload).encode(), self.env)
        self.assertTrue(result.get("idempotent"))
        self.assertFalse(any((self.store.path / "mailbox" / "dead-letter").iterdir()))
        with self.assertRaises(ChannelError) as replay:
            apply_channel_answer("0001", payload, json.dumps(payload).encode(), self.env)
        self.assertEqual(409, replay.exception.status)

    def test_conflicting_retry_is_409_and_keeps_the_stored_answer(self) -> None:
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="q",
            options=[{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
            default="a",
            deadline_s=60,
        )
        self.store.mailbox().write_answer("0001", choice="a", answered_by="operator")
        payload = {
            "delivery_id": "ans-2",
            "dispatch_id": DISPATCH_ID,
            "choice": "b",
            "answered_by": "operator",
        }
        with self.assertRaises(ChannelError) as caught:
            apply_channel_answer("0001", payload, json.dumps(payload).encode(), self.env)
        self.assertEqual(
            (409, "ask_already_answered"), (caught.exception.status, str(caught.exception))
        )
        stored = self.store.mailbox().get_answer("0001")
        assert stored is not None
        self.assertEqual("a", stored["choice"])

    def test_ask_written_before_the_crash_is_not_duplicated(self) -> None:
        payload = _ask_payload("ask-1", DISPATCH_ID)
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="Proceed with the plan?",
            options=payload["options"],
            default="a",
            deadline_s=600,  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            delivery_id="ask-1",
        )
        result = apply_channel_ask(payload, json.dumps(payload).encode(), self.env)
        self.assertEqual(("0001", True), (result["askId"], result.get("idempotent")))
        self.assertEqual(1, len(self.store.mailbox().asks()))

    def test_mailbox_survives_a_killed_receiver_and_the_retry_is_a_replay(self) -> None:
        body = json.dumps(_ask_payload("ask-kill", DISPATCH_ID)).encode()
        opener = request.build_opener(request.ProxyHandler({}))

        def post(base: str) -> int:
            headers = {"Content-Type": "application/json", "X-Pitwall-Signature": _signature(body)}
            try:
                with opener.open(
                    request.Request(f"{base}/asks", data=body, headers=headers),
                    timeout=HANG_GUARD_SECS,
                ) as reply:
                    return int(reply.status)
            except error.HTTPError as exc:
                exc.close()
                return int(exc.code)

        base, process = _start_receiver(self.env)
        try:
            self.assertEqual(202, post(base))
        finally:
            process.kill()  # SIGKILL: no cleanup runs
            process.wait(timeout=HANG_GUARD_SECS)
            if process.stderr is not None:
                process.stderr.close()
        base, process = _start_receiver(self.env)
        try:
            self.assertEqual(409, post(base))  # the delivery id survived the crash
        finally:
            process.terminate()
            process.wait(timeout=HANG_GUARD_SECS)
            if process.stderr is not None:
                process.stderr.close()
        self.assertEqual(["0001"], [a["ask_id"] for a in self.store.mailbox().asks()])


if __name__ == "__main__":
    unittest.main()
