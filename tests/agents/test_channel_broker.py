"""Broker channel endpoints: asks/answers/steer/inbox discipline (Phase A, Task 11)."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib import error, request

from pitwall.agents import broker
from pitwall.agents.broker import apply_channel_steer
from pitwall.agents.channel import ChannelConfig, write_channel_config
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

SECRET = "channel-test-secret"
BODY_CAP = 1024 * 1024


def _signature(body: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    moment = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{moment}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


def _ask_payload(delivery_id: str, dispatch_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "delivery_id": delivery_id,
        "dispatch_id": dispatch_id,
        "blocked_on": "choice",
        "question": "Proceed with the plan?",
        "options": [{"id": "a", "text": "yes"}, {"id": "b", "text": "no"}],
        "default": "a",
        "deadline_s": 600,
        "severity": "normal",
    }
    payload.update(overrides)
    return payload


@contextlib.contextmanager
def _serving(env: dict[str, str]):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    process = subprocess.Popen(
        [
            str(PITWALL),
            "agents",
            "broker",
            "receiver",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        opener = request.build_opener(request.ProxyHandler({}))
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = process.communicate(timeout=HANG_GUARD_SECS)[1]
                raise AssertionError(f"receiver exited early: {stderr.strip()}")
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5):
                    break
            except error.URLError, TimeoutError:
                time.sleep(0.05)
        else:
            raise AssertionError("receiver did not become healthy")
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=HANG_GUARD_SECS)
        if process.stderr is not None:
            process.stderr.close()


class ChannelBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.env = {
            "HOME": str(root / "home"),
            "PATH": "/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
            "PITWALL_AGENTS_WEBHOOK_SECRET": SECRET,
        }
        self.dispatch_id = "dispatch-chan-1"
        RunStore.create(self.env, self.dispatch_id)
        self.opener = request.build_opener(request.ProxyHandler({}))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _post(
        self, base: str, path: str, payload: object, *, sign: bool = True
    ) -> tuple[int, dict[str, object]]:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        if sign:
            headers["X-Pitwall-Signature"] = _signature(body)
        try:
            with self.opener.open(
                request.Request(base + path, data=body, headers=headers, method="POST"),
                timeout=HANG_GUARD_SECS,
            ) as response:
                return response.status, json.loads(response.read().decode())
        except error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            finally:
                exc.close()

    def _get(self, base: str, path: str, *, sign: bool = True) -> tuple[int, dict[str, object]]:
        headers = {}
        if sign:
            headers["X-Pitwall-Signature"] = _signature(b"")
        try:
            with self.opener.open(
                request.Request(base + path, headers=headers, method="GET"), timeout=HANG_GUARD_SECS
            ) as response:
                return response.status, json.loads(response.read().decode())
        except error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode() or "{}")
            finally:
                exc.close()

    def _run_dir(self) -> Path:
        return Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents" / "runs" / self.dispatch_id

    def test_hmac_rejection(self) -> None:
        with _serving(self.env) as base:
            status, _ = self._post(
                base, "/asks", _ask_payload("d-hmac-1", self.dispatch_id), sign=False
            )
            self.assertEqual(401, status)
            body = json.dumps(_ask_payload("d-hmac-2", self.dispatch_id)).encode()
            tampered = request.Request(
                base + "/asks",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Pitwall-Signature": _signature(b"other"),
                },
                method="POST",
            )
            with self.assertRaises(error.HTTPError) as ctx:
                self.opener.open(tampered, timeout=HANG_GUARD_SECS)
            self.assertEqual(401, ctx.exception.code)
            ctx.exception.close()

    def test_replay_returns_409(self) -> None:
        with _serving(self.env) as base:
            status, _ = self._post(base, "/asks", _ask_payload("d-replay-1", self.dispatch_id))
            self.assertEqual(202, status)
            status, body = self._post(base, "/asks", _ask_payload("d-replay-1", self.dispatch_id))
            self.assertEqual(409, status)
            self.assertEqual("replayed_delivery_id", body.get("error"))

    def test_oversized_body_is_rejected(self) -> None:
        with _serving(self.env) as base:
            status, _ = self._post(base, "/asks", b"x" * (BODY_CAP + 1))
            self.assertEqual(413, status)

    def test_invalid_schema_dead_letters_with_400(self) -> None:
        with _serving(self.env) as base:
            bad = _ask_payload("d-bad-1", self.dispatch_id)
            del bad["default"]  # default is mandatory
            status, _ = self._post(base, "/asks", bad)
            self.assertEqual(400, status)
            dead = list((self._run_dir() / "mailbox" / "dead-letter").glob("*.json"))
            self.assertEqual(1, len(dead))
            status, inbox = self._get(base, f"/inbox?dispatch_id={self.dispatch_id}")
            self.assertEqual(200, status)
            self.assertEqual([], inbox["asks"])

    def test_happy_path_writes_mailbox_files_and_inbox_reflects_state(self) -> None:
        with _serving(self.env) as base:
            status, created = self._post(base, "/asks", _ask_payload("d-happy-1", self.dispatch_id))
            self.assertEqual(202, status)
            self.assertEqual("0001", created.get("askId"))
            self.assertTrue((self._run_dir() / "mailbox" / "asks" / "0001.json").is_file())

            status, inbox = self._get(base, f"/inbox?dispatch_id={self.dispatch_id}")
            self.assertEqual(200, status)
            self.assertEqual(["0001"], [a["askId"] for a in inbox["asks"]])

            answer = {
                "delivery_id": "d-happy-2",
                "dispatch_id": self.dispatch_id,
                "choice": "a",
                "answered_by": "operator",
                "note": "go ahead",
            }
            status, _ = self._post(base, "/answers/0001", answer)
            self.assertEqual(202, status)
            self.assertTrue((self._run_dir() / "mailbox" / "answers" / "0001.json").is_file())

            status, inbox = self._get(base, f"/inbox?dispatch_id={self.dispatch_id}")
            self.assertEqual([], inbox["asks"])

            self._set_state("running")  # a real run always has run.json; steering needs it
            write_channel_config(
                RunStore(Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id),
                ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time()),
            )
            steer = {
                "delivery_id": "d-happy-3",
                "dispatch_id": self.dispatch_id,
                "kind": "scope",
                "message": "SQLite only",
            }
            status, posted = self._post(base, "/steer", steer)
            self.assertEqual(202, status)
            status, inbox = self._get(base, f"/inbox?dispatch_id={self.dispatch_id}")
            self.assertEqual(["0001"], [s["steerId"] for s in inbox["steers"]])
            self.assertEqual("0001", posted.get("steerId"))

            events = (self._run_dir() / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("ask.resolved", events)

    def _steer(self, delivery_id: str, kind: str = "scope") -> dict[str, object]:
        return {
            "delivery_id": delivery_id,
            "dispatch_id": self.dispatch_id,
            "kind": kind,
            "message": "SQLite only",
        }

    def _set_state(self, state: str) -> None:
        RunStore(
            Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id
        ).write_json("run.json", {"schemaVersion": 1, "state": state})

    def test_steer_to_a_run_without_the_channel_is_refused_with_409(self) -> None:
        self._set_state("running")
        with _serving(self.env) as base:
            status, body = self._post(base, "/steer", self._steer("d-nochan-1"))
            self.assertEqual(409, status)
            self.assertIn("without the orchestrator channel", str(body))
            self.assertFalse((self._run_dir() / "mailbox").exists())
            status, _ = self._post(base, "/steer", self._steer("d-nochan-2", "stop"))
            self.assertEqual(202, status)

    def test_steer_to_a_terminal_run_is_refused_with_409_and_records_nothing(self) -> None:
        for state in ("succeeded", "failed", "cancelled", "timed_out"):
            with self.subTest(state):
                self._set_state(state)
                write_channel_config(
                    RunStore(
                        Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id
                    ),
                    ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time()),
                )
                for kind in ("scope", "stop"):
                    with self.assertRaises(broker.ChannelError) as caught:
                        self._apply_steer(self._steer(f"d-term-{state}-{kind}", kind))
                    self.assertEqual(409, caught.exception.status)
                    self.assertIn(f"is {state}", str(caught.exception))
                steers = self._run_dir() / "mailbox" / "steer"
                self.assertEqual([], list(steers.glob("*.json")) if steers.is_dir() else [])

    def test_a_matching_replay_to_a_run_that_has_since_finished_keeps_its_receipt(self) -> None:
        self.addCleanup(self._forget_deliveries)
        self._set_state("running")
        write_channel_config(
            RunStore(Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id),
            ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time()),
        )
        first = self._apply_steer(self._steer("d-term-replay"))
        self._forget_deliveries()
        self._set_state("succeeded")
        again = self._apply_steer(self._steer("d-term-replay"))
        self.assertEqual(first["steerId"], again["steerId"])
        self.assertIs(True, again["idempotent"])

    def test_invalid_steer_kind_keeps_its_400_without_a_channel(self) -> None:
        self._set_state("running")
        with _serving(self.env) as base:
            status, _ = self._post(base, "/steer", self._steer("d-nochan-3", "bogus"))
            self.assertEqual(400, status)

    def test_steer_to_a_pre_launch_run_without_its_config_says_still_preparing(self) -> None:
        self._set_state("workspace_preparing")
        with _serving(self.env) as base:
            status, body = self._post(base, "/steer", self._steer("d-prelaunch-1"))
            self.assertEqual(409, status)
            self.assertIn("is still preparing and has not recorded", str(body))
            self.assertNotIn("without the orchestrator channel", str(body))
            self.assertFalse((self._run_dir() / "mailbox").exists())

    def test_malformed_steer_to_a_channel_less_run_is_a_400_without_a_trace(self) -> None:
        self._set_state("running")
        bad = self._steer("d-malformed-1")
        bad["message"] = ""
        with _serving(self.env) as base:
            status, body = self._post(base, "/steer", bad)
            self.assertEqual(400, status)
            self.assertEqual("invalid steer schema", body.get("error"))
            self.assertFalse((self._run_dir() / "mailbox").exists())

    def test_malformed_steer_to_a_channel_run_is_quarantined(self) -> None:
        self._set_state("running")
        write_channel_config(
            RunStore(Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id),
            ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time()),
        )
        bad = self._steer("d-malformed-2")
        bad["message"] = ""
        with _serving(self.env) as base:
            status, _ = self._post(base, "/steer", bad)
            self.assertEqual(400, status)
            dead = list((self._run_dir() / "mailbox" / "dead-letter").glob("*.json"))
            self.assertEqual(1, len(dead))

    def _apply_steer(self, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload).encode()
        return apply_channel_steer(payload, body, self.env)

    def _forget_deliveries(self) -> None:
        """Simulate a broker restart that lost its delivery-id memory."""
        broker._RECENT_DELIVERIES.clear()
        broker._RECENT_DELIVERY_SET.clear()
        sync = broker._sync_path(self.env)
        if sync.exists():
            sync.unlink()

    def test_malformed_stop_steer_to_a_channel_less_run_is_a_400_without_a_trace(self) -> None:
        self._set_state("running")
        bad = self._steer("d-malformed-stop", "stop")
        bad["message"] = ""
        with self.assertRaises(broker.ChannelError) as caught:
            self._apply_steer(bad)
        self.assertEqual(400, caught.exception.status)
        self.assertFalse((self._run_dir() / "mailbox").exists())
        self.assertFalse((self._run_dir() / "mailbox.json").exists())

    def test_replayed_steer_after_a_restart_gets_its_original_reply(self) -> None:
        self.addCleanup(self._forget_deliveries)
        self._set_state("running")
        write_channel_config(
            RunStore(Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id),
            ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time()),
        )
        first = self._apply_steer(self._steer("d-restart-1"))
        self._forget_deliveries()
        again = self._apply_steer(self._steer("d-restart-1"))
        self.assertEqual({**first, "idempotent": True}, again)
        malformed = self._steer("d-restart-1")
        malformed["message"] = ""
        self._forget_deliveries()
        self.assertEqual({**first, "idempotent": True}, self._apply_steer(malformed))

    def test_replayed_stop_to_a_channel_less_run_gets_its_original_reply(self) -> None:
        self.addCleanup(self._forget_deliveries)
        self._set_state("running")
        first = self._apply_steer(self._steer("d-restart-2", "stop"))
        self._forget_deliveries()
        again = self._apply_steer(self._steer("d-restart-2", "stop"))
        self.assertEqual({**first, "idempotent": True}, again)

    def test_unknown_dispatch_is_404(self) -> None:
        with _serving(self.env) as base:
            status, _ = self._post(base, "/asks", _ask_payload("d-404-1", "dispatch-nope"))
            self.assertEqual(404, status)
            status, _ = self._get(base, "/inbox?dispatch_id=dispatch-nope")
            self.assertEqual(404, status)

    def test_unsigned_inbox_is_rejected_by_default(self) -> None:
        with _serving(self.env) as base:
            status, _ = self._get(base, "/inbox", sign=False)
            self.assertEqual(401, status)

    def test_ask_cap_returns_429(self) -> None:
        with _serving(self.env) as base:
            for index in range(5):
                status, _ = self._post(
                    base, "/asks", _ask_payload(f"d-cap-{index}", self.dispatch_id)
                )
                self.assertEqual(202, status)
                # §10: the broker allows one open ask at a time, so settle each
                # ask before posting the next; the cap test needs five distinct asks.
                ask_id = f"{index + 1:04d}"
                status, _ = self._post(
                    base,
                    f"/answers/{ask_id}",
                    {
                        "delivery_id": f"d-cap-answer-{index}",
                        "dispatch_id": self.dispatch_id,
                        "choice": "a",
                        "answered_by": "operator",
                    },
                )
                self.assertEqual(202, status)
            status, body = self._post(base, "/asks", _ask_payload("d-cap-5", self.dispatch_id))
            self.assertEqual(429, status)
            self.assertEqual("ask_cap_exceeded", body.get("error"))

    def test_ask_deadline_is_clamped_by_the_channel_record(self) -> None:
        import time as time_module

        from pitwall.agents.channel import ChannelConfig, write_channel_config

        store = RunStore(Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents", self.dispatch_id)
        write_channel_config(
            store,
            ChannelConfig(
                self.dispatch_id, timeout_seconds=600.0, attempt_started_epoch=time_module.time()
            ),
        )
        with _serving(self.env) as base:
            status, _ = self._post(
                base, "/asks", _ask_payload("d-clamp-1", self.dispatch_id, deadline_s=3600)
            )
            self.assertEqual(202, status)
        ask = json.loads((self._run_dir() / "mailbox" / "asks" / "0001.json").read_text())
        # 15% of the 600 s remaining is 90 s; a slow runner burns some of the window between
        # writing the record and the POST, so allow that without accepting the 3600 s asked for.
        self.assertLessEqual(ask["deadline_s"], 90)
        self.assertGreaterEqual(ask["deadline_s"], 60)

    def test_concurrent_duplicate_asks_apply_exactly_once(self) -> None:
        with _serving(self.env) as base:
            delivery_id = "d-race-1"
            body = json.dumps(_ask_payload(delivery_id, self.dispatch_id)).encode()
            headers = {
                "Content-Type": "application/json",
                "X-Pitwall-Signature": _signature(body),
            }
            results: list[tuple[int, dict[str, object]]] = []
            results_lock = threading.Lock()

            def post() -> None:
                try:
                    with self.opener.open(
                        request.Request(base + "/asks", data=body, headers=headers, method="POST"),
                        timeout=HANG_GUARD_SECS,
                    ) as response:
                        outcome = (response.status, json.loads(response.read().decode()))
                except error.HTTPError as exc:
                    try:
                        outcome = (exc.code, json.loads(exc.read().decode() or "{}"))
                    finally:
                        exc.close()
                with results_lock:
                    results.append(outcome)

            threads = [threading.Thread(target=post) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=HANG_GUARD_SECS)

            self.assertEqual(8, len(results))
            self.assertEqual(1, sum(1 for status, _ in results if status == 202), results)
            self.assertEqual(7, sum(1 for status, _ in results if status == 409), results)
            for status, body in results:
                if status == 409:
                    self.assertEqual("replayed_delivery_id", body.get("error"))
            asks = list((self._run_dir() / "mailbox" / "asks").glob("*.json"))
            self.assertEqual(1, len(asks))


class ChannelBrokerSecretTests(unittest.TestCase):
    def test_missing_secret_is_503(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "HOME": str(root / "home"),
                "PATH": "/usr/bin:/bin",
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
            }
            opener = request.build_opener(request.ProxyHandler({}))
            with _serving(env) as base:
                with self.assertRaises(error.HTTPError) as ctx:
                    opener.open(
                        request.Request(
                            base + "/asks",
                            data=b"{}",
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        ),
                        timeout=HANG_GUARD_SECS,
                    )
                self.assertEqual(503, ctx.exception.code)
                ctx.exception.close()


class ChannelInboxPublicTests(unittest.TestCase):
    def test_explicit_opt_in_allows_unsigned_inbox_reads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "HOME": str(root / "home"),
                "PATH": "/usr/bin:/bin",
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "PITWALL_AGENTS_WEBHOOK_SECRET": SECRET,
                "PITWALL_CHANNEL_PUBLIC_INBOX": "1",
            }
            opener = request.build_opener(request.ProxyHandler({}))
            with (
                _serving(env) as base,
                opener.open(base + "/inbox", timeout=HANG_GUARD_SECS) as response,
            ):
                self.assertEqual(200, response.status)
                self.assertEqual(
                    {"asks": [], "steers": [], "sequenceGaps": []},
                    json.loads(response.read().decode()),
                )


if __name__ == "__main__":
    unittest.main()
