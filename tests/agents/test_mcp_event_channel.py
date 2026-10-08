"""Parent-side event channel acceptance cases with a fake MCP-capable child."""

from __future__ import annotations

import json
import os
import subprocess
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from pitwall.agents.managed_channel import (
    LaunchRequest,
    ManagedChannelError,
    _process_state,
    start_dispatch,
)
from pitwall.agents.mcp_registration import plan_registration
from pitwall.agents.profiles_sync import apply_plan
from pitwall.agents.run_store import atomic_write_json, cleanup_runs, state_root
from tests.agents.mcp_test_client import McpTestClient
from tests.agents.shim_test_support import PITWALL, ShimSandbox
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
# Every wait that expects an event returns as soon as the event exists, so this bound only stops a
# wedged child; it stays below the client's hang guard so the server answers first.
EVENT_WAIT_SECS = HANG_GUARD_SECS / 2


_ASKING_HARNESS = """\
#!/usr/bin/env python3
import json, os, subprocess, sys, time
sys.stdin.buffer.read()
time.sleep(float(os.environ.get("FAKE_ASK_DELAY", "0")))
server = subprocess.Popen([{launcher!r}, "mcp", "serve", "channel"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, env=dict(os.environ))
def request(request_id, name, arguments):
    server.stdin.write((json.dumps({{"jsonrpc": "2.0", "id": request_id,
        "method": "tools/call", "params": {{"name": name, "arguments": arguments}}}}) + "\\n").encode())
    server.stdin.flush()
    for line in server.stdout:
        reply = json.loads(line)
        if reply.get("id") == request_id:
            return reply
server.stdin.write((json.dumps({{"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {{"protocolVersion": "2025-06-18", "capabilities": {{}},
    "clientInfo": {{"name": "fake-child", "version": "0"}}}}}}) + "\\n").encode())
server.stdin.flush()
for line in server.stdout:
    if json.loads(line).get("id") == 1:
        break
answer = request(2, "ask_orchestrator", {{
    "question": "Which suffix?", "blocked_on": "choice", "severity": "blocking",
    "options": [{{"id": "a", "text": "alpha"}}, {{"id": "b", "text": "beta"}}],
    "default": "a", "deadline_s": int(os.environ.get("FAKE_ASK_DEADLINE", "60"))
}})
print(json.dumps(answer["result"]["structuredContent"], sort_keys=True), flush=True)
release = os.environ.get("FAKE_RELEASE_FILE")
release_deadline = time.monotonic() + __HANG_GUARD__
while release and not os.path.exists(release) and time.monotonic() < release_deadline:
    time.sleep(0.05)
server.stdin.close()
server.wait()
""".replace("__HANG_GUARD__", repr(HANG_GUARD_SECS))


_STEERING_HARNESS = """\
#!/usr/bin/env python3
import json, os, subprocess, sys, time
sys.stdin.buffer.read()
server = subprocess.Popen(["__LAUNCHER__", "mcp", "serve", "channel"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, env=dict(os.environ))
def request(request_id, name, arguments):
    server.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
        "method": "tools/call", "params": {"name": name, "arguments": arguments}}) + "\\n").encode())
    server.stdin.flush()
    for line in server.stdout:
        reply = json.loads(line)
        if reply.get("id") == request_id:
            return reply
server.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "fake-steering-child", "version": "0"}}}) + "\\n").encode())
server.stdin.flush()
for line in server.stdout:
    if json.loads(line).get("id") == 1:
        break
deadline = time.monotonic() + __HANG_GUARD__
while time.monotonic() < deadline:
    reply = request(2, "read_steering", {})
    steers = reply.get("result", {}).get("structuredContent", {}).get("steers", [])
    if steers:
        ack = request(3, "ack_steer", {"steer_id": steers[0]["steer_id"]})
        print(json.dumps(ack["result"]["structuredContent"], sort_keys=True), flush=True)
        break
    time.sleep(0.05)
server.stdin.close()
server.wait()
"""


_ORDERED_HARNESS = _ASKING_HARNESS.replace(
    'sys.stdin.buffer.read()\ntime.sleep(float(os.environ.get("FAKE_ASK_DELAY", "0")))',
    'prompt = sys.stdin.buffer.read().decode()\ntime.sleep(0.8 if "slow" in prompt else 0)\n'
    'question = "slow suffix?" if "slow" in prompt else "fast suffix?"',
).replace('"question": "Which suffix?"', '"question": question')


_TWO_ASK_HARNESS = _ASKING_HARNESS.replace(
    'print(json.dumps(answer["result"]["structuredContent"], sort_keys=True), flush=True)',
    'second = request(3, "ask_orchestrator", {{"question": "Again?", "blocked_on": "choice", '
    '"severity": "normal", "options": [{{"id": "a", "text": "alpha"}}, {{"id": "b", "text": "beta"}}], '
    '"default": "a", "deadline_s": 60}})\n'
    "print(json.dumps(second, sort_keys=True), flush=True)",
)
assert _TWO_ASK_HARNESS != _ASKING_HARNESS


class ManagedEventChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_ASKING_HARNESS.format(launcher=str(PITWALL)), encoding="utf-8")
        target.chmod(0o755)
        self.env = self.sandbox.environment()
        apply_plan(plan_registration("codex", self.env, self.sandbox.home, command=str(PITWALL)))
        self.client = McpTestClient(self.env)
        self.client.initialize()

    def tearDown(self) -> None:
        self.client.close()
        self.sandbox.cleanup()

    def _wait_for_ask(self) -> tuple[str, Path]:
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            runs_root = self.sandbox.state / "pitwall" / "agents" / "runs"
            run_dirs = list(runs_root.iterdir()) if runs_root.is_dir() else []
            if run_dirs:
                asks = run_dirs[0] / "mailbox" / "asks"
                files = list(asks.glob("*.json")) if asks.is_dir() else []
                if files:
                    return run_dirs[0].name, files[0]
            time.sleep(0.05)
        self.fail("managed child never published its ask")

    def _wait_for_state(self, dispatch_id: str, expected: str) -> Path:
        run_dir = self.sandbox.state / "pitwall" / "agents" / "runs" / dispatch_id
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            try:
                run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                time.sleep(0.05)
                continue
            if run.get("state") == expected:
                return run_dir
            time.sleep(0.05)
        self.fail(f"managed dispatch {dispatch_id} never reached {expected}")

    def _wait_for_launch(self) -> str:
        launches_root = self.sandbox.state / "pitwall" / "agents" / "launches"
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            launch_dirs = list(launches_root.iterdir()) if launches_root.is_dir() else []
            for launch_dir in launch_dirs:
                if launch_dir.is_dir() and (launch_dir / "launcher.json").is_file():
                    return launch_dir.name
            time.sleep(0.02)
        self.fail("managed launcher was never published")

    def _reaper_diagnostics(self, dispatch_id: str, launcher_pid: int) -> str:
        """Who holds the zombie: its parent, the MCP server, and the reaper's records."""
        launch_dir = self.sandbox.state / "pitwall" / "agents" / "launches" / dispatch_id
        listing = sorted(p.name for p in launch_dir.iterdir()) if launch_dir.is_dir() else None
        server_pid = self.client.process.pid
        ps = subprocess.run(
            ["ps", "-o", "pid=,ppid=,stat=,etime=,command=", "-p", f"{launcher_pid},{server_pid}"],
            capture_output=True,
            text=True,
            timeout=HANG_GUARD_SECS,
            check=False,
        ).stdout
        return (
            f"launcher {launcher_pid} not reaped or no exit.json; server pid {server_pid} "
            f"(returncode {self.client.process.poll()}); launch dir {listing}; ps:\n{ps}"
        )

    def test_dispatch_returns_ask_without_inbox_and_answer_returns_terminal(self) -> None:
        request_id = self.client.send(
            "tools/call",
            {
                "name": "dispatch_and_wait",
                "arguments": {
                    "provider": "codex",
                    "prompt": "ask",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        dispatch_id, ask_file = self._wait_for_ask()
        ask = json.loads(ask_file.read_text(encoding="utf-8"))
        first = self.client.wait(request_id, timeout=HANG_GUARD_SECS)
        event = first["result"]["structuredContent"]
        self.assertEqual("ask", event["event"])
        self.assertEqual(dispatch_id, event["dispatch_id"])
        self.assertEqual(ask["context"]["options"], event["options"])
        self.assertEqual("blocking", event["severity"])

        answer_request = self.client.send(
            "tools/call",
            {
                "name": "answer_and_wait",
                "arguments": {
                    "dispatch_id": dispatch_id,
                    "ask_id": "0001",
                    "choice": "b",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        answered = self.client.wait(answer_request, timeout=HANG_GUARD_SECS)["result"][
            "structuredContent"
        ]
        self.assertEqual("terminal", answered["event"])
        self.assertEqual("succeeded", answered["status"])
        self.assertEqual("b", answered["answer"]["choice"])
        launcher_path = (
            self.sandbox.state / "pitwall" / "agents" / "runs" / dispatch_id / "launcher.json"
        )
        self.assertTrue(launcher_path.is_file())
        launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
        launcher_pid = int(launcher["pid"])
        # The managed request has already received its terminal receipt, but the independent
        # launcher may still be exiting. Its monitor writes launches/<id>/exit.json only after it
        # has reaped the child, so wait for that record (a hang guard, not a latency bound), then
        # check once that nothing is left behind. Sampling the process state while it exits would
        # race the reaper: on macOS every sample spawns `ps`, long enough for the launcher to exit
        # between two samples and look like a zombie for the moment before it is reaped.
        exit_record = (
            self.sandbox.state / "pitwall" / "agents" / "launches" / dispatch_id / "exit.json"
        )
        deadline = time.monotonic() + HANG_GUARD_SECS
        while not exit_record.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(exit_record.is_file(), self._reaper_diagnostics(dispatch_id, launcher_pid))
        self.assertNotEqual(
            "Z", _process_state(launcher_pid), self._reaper_diagnostics(dispatch_id, launcher_pid)
        )

        duplicate = self.client.call(
            "answer_and_wait",
            {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "b", "wait_seconds": 0.1},
        )
        self.assertFalse(duplicate["result"]["isError"])
        self.assertEqual("b", duplicate["result"]["structuredContent"]["answer"]["choice"])
        conflict = self.client.call(
            "answer_and_wait",
            {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "a", "wait_seconds": 0.1},
        )
        self.assertTrue(conflict["result"]["isError"])
        removed = cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        self.assertEqual(
            [self.sandbox.state / "pitwall" / "agents" / "runs" / dispatch_id], removed
        )
        self.assertFalse(
            (self.sandbox.state / "pitwall" / "agents" / "launches" / dispatch_id).exists()
        )

    def test_managed_operations_require_full_uuid(self) -> None:
        response = self.client.call(
            "wait_dispatch", {"dispatch_id": "00000000", "wait_seconds": 0.1}
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("full dispatch UUID", response["result"]["content"][0]["text"])

    def test_concurrent_children_return_out_of_order_asks_to_their_own_runs(self) -> None:
        target = self.sandbox.bin / "codex"
        target.write_text(_ORDERED_HARNESS.format(launcher=str(PITWALL)), encoding="utf-8")
        target.chmod(0o755)
        slow_request = self.client.send(
            "tools/call",
            {
                "name": "dispatch_and_wait",
                "arguments": {
                    "provider": "codex",
                    "prompt": "slow child",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        fast_request = self.client.send(
            "tools/call",
            {
                "name": "dispatch_and_wait",
                "arguments": {
                    "provider": "codex",
                    "prompt": "fast child",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        assert slow_request is not None and fast_request is not None
        fast = self.client.wait(fast_request, timeout=HANG_GUARD_SECS)["result"][
            "structuredContent"
        ]
        slow = self.client.wait(slow_request, timeout=HANG_GUARD_SECS)["result"][
            "structuredContent"
        ]
        self.assertEqual("ask", fast["event"])
        self.assertEqual("ask", slow["event"])
        self.assertEqual("fast suffix?", fast["ask"]["question"])
        self.assertEqual("slow suffix?", slow["ask"]["question"])
        self.assertNotEqual(fast["dispatch_id"], slow["dispatch_id"])

        fast_answer = self.client.send(
            "tools/call",
            {
                "name": "answer_and_wait",
                "arguments": {
                    "dispatch_id": fast["dispatch_id"],
                    "ask_id": fast["ask_id"],
                    "choice": "a",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        slow_answer = self.client.send(
            "tools/call",
            {
                "name": "answer_and_wait",
                "arguments": {
                    "dispatch_id": slow["dispatch_id"],
                    "ask_id": slow["ask_id"],
                    "choice": "a",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        assert fast_answer is not None and slow_answer is not None
        fast_terminal = self.client.wait(fast_answer, timeout=HANG_GUARD_SECS)["result"][
            "structuredContent"
        ]
        slow_terminal = self.client.wait(slow_answer, timeout=HANG_GUARD_SECS)["result"][
            "structuredContent"
        ]
        self.assertEqual(
            ("terminal", fast["dispatch_id"]),
            (fast_terminal["event"], fast_terminal["dispatch_id"]),
        )
        self.assertEqual(
            ("terminal", slow["dispatch_id"]),
            (slow_terminal["event"], slow_terminal["dispatch_id"]),
        )

    def test_parent_restart_reattaches_to_an_unanswered_ask(self) -> None:
        request_id = self.client.send(
            "tools/call",
            {
                "name": "dispatch_and_wait",
                "arguments": {
                    "provider": "codex",
                    "prompt": "restart",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        dispatch_id, _ask_file = self._wait_for_ask()
        self.client.close()
        self.client = McpTestClient(self.env)
        self.client.initialize()
        reattached = self.client.call(
            "wait_dispatch", {"reattach_handle": dispatch_id, "wait_seconds": 2}
        )
        event = reattached["result"]["structuredContent"]
        self.assertEqual("ask", event["event"])
        self.assertEqual(dispatch_id, event["dispatch_id"])
        answer = self.client.call(
            "answer_and_wait",
            {
                "dispatch_id": dispatch_id,
                "ask_id": "0001",
                "choice": "a",
                "wait_seconds": EVENT_WAIT_SECS,
            },
        )
        self.assertEqual("terminal", answer["result"]["structuredContent"]["event"])
        # The old outstanding call is intentionally not read after the parent
        # closes; MCP cancellation is allowed to discard its response.
        del request_id

    def test_unregistered_child_fails_before_launch(self) -> None:
        self.client.close()
        # The registration file is the only capability evidence used for the
        # managed preflight; remove it and start a fresh parent server.
        config = self.sandbox.home / ".codex" / "config.toml"
        config.unlink()
        self.client = McpTestClient(self.env)
        self.client.initialize()
        response = self.client.call(
            "dispatch_and_wait", {"provider": "codex", "prompt": "must refuse", "wait_seconds": 1}
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("registered", response["result"]["content"][0]["text"])
        launches = self.sandbox.state / "pitwall" / "agents" / "launches"
        self.assertFalse(launches.exists())

    def test_registered_non_mcp_command_fails_the_real_preflight(self) -> None:
        self.client.close()
        config = self.sandbox.home / ".codex" / "config.toml"
        config.write_text(
            '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\nargs = ["mcp"]\n',
            encoding="utf-8",
        )
        self.client = McpTestClient(self.env)
        self.client.initialize()
        response = self.client.call(
            "dispatch_and_wait", {"provider": "codex", "prompt": "must refuse", "wait_seconds": 1}
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("cannot verify the child channel", response["result"]["content"][0]["text"])
        self.assertFalse((self.sandbox.state / "pitwall" / "agents" / "launches").exists())

    def test_cancelled_wait_leaves_child_reattachable(self) -> None:
        self.client.close()
        self.env["FAKE_ASK_DELAY"] = "1"
        self.client = McpTestClient(self.env)
        self.client.initialize()
        request_id = self.client.send(
            "tools/call",
            {
                "name": "dispatch_and_wait",
                "arguments": {
                    "provider": "codex",
                    "prompt": "cancel",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        assert request_id is not None
        dispatch_id = self._wait_for_launch()
        self.client.send("notifications/cancelled", {"requestId": request_id}, notify=True)
        with self.assertRaises(TimeoutError):
            self.client.wait(request_id, timeout=0.5)
        self.client.close()
        self.client = McpTestClient(self.env)
        self.client.initialize()
        reattached = self.client.call(
            "wait_dispatch", {"dispatch_id": dispatch_id, "wait_seconds": EVENT_WAIT_SECS}
        )
        self.assertEqual("ask", reattached["result"]["structuredContent"]["event"])
        terminal = self.client.call(
            "answer_and_wait",
            {
                "dispatch_id": dispatch_id,
                "ask_id": "0001",
                "choice": "a",
                "wait_seconds": EVENT_WAIT_SECS,
            },
        )
        self.assertEqual("terminal", terminal["result"]["structuredContent"]["event"])

    def test_steer_acknowledgement_is_returned(self) -> None:
        target = self.sandbox.bin / "codex"
        target.write_text(
            _STEERING_HARNESS.replace("__LAUNCHER__", str(PITWALL)).replace(
                "__HANG_GUARD__", repr(HANG_GUARD_SECS)
            ),
            encoding="utf-8",
        )
        target.chmod(0o755)
        started = self.client.call(
            "dispatch_and_wait",
            {"provider": "codex", "prompt": "steer", "wait_seconds": 0.1},
        )
        started_event = started["result"]["structuredContent"]
        self.assertEqual("still_running", started_event["event"])
        dispatch_id = started_event["dispatch_id"]
        self._wait_for_state(dispatch_id, "running")
        steered = self.client.call(
            "steer_and_wait",
            {
                "dispatch_id": dispatch_id,
                "kind": "scope",
                "message": "keep the change focused",
                "deadline_s": int(EVENT_WAIT_SECS),
                "wait_seconds": EVENT_WAIT_SECS,
            },
            timeout=HANG_GUARD_SECS,
        )
        event = steered["result"]["structuredContent"]
        self.assertEqual("steer_ack", event["event"])
        self.assertTrue(event["steer_acknowledged"])
        self.assertEqual(event["steer"]["steer_id"], event["steer_id"])
        terminal = self.client.call(
            "wait_dispatch",
            {"dispatch_id": dispatch_id, "wait_seconds": EVENT_WAIT_SECS},
            timeout=HANG_GUARD_SECS,
        )
        self.assertEqual("terminal", terminal["result"]["structuredContent"]["event"])

    def test_unacknowledged_steer_is_reported_when_child_never_reads_steering(self) -> None:
        request_id = self.client.send(
            "tools/call",
            {
                "name": "dispatch_and_wait",
                "arguments": {
                    "provider": "codex",
                    "prompt": "ignore steer",
                    "wait_seconds": EVENT_WAIT_SECS,
                },
            },
        )
        assert request_id is not None
        dispatch_id, _ask_file = self._wait_for_ask()
        first = self.client.wait(request_id, timeout=HANG_GUARD_SECS)["result"]["structuredContent"]
        self.assertEqual("ask", first["event"])
        steered = self.client.call(
            "steer_and_wait",
            {
                "dispatch_id": dispatch_id,
                "kind": "note",
                "message": "this child does not poll steering",
                "deadline_s": 1,
                "wait_seconds": 0.2,
            },
        )
        event = steered["result"]["structuredContent"]
        self.assertEqual("ask", event["event"])
        self.assertFalse(event["steer_acknowledged"])
        terminal = self.client.call(
            "answer_and_wait",
            {
                "dispatch_id": dispatch_id,
                "ask_id": "0001",
                "choice": "a",
                "wait_seconds": EVENT_WAIT_SECS,
            },
        )
        self.assertEqual("terminal", terminal["result"]["structuredContent"]["event"])
        del request_id

    def test_child_deadline_applies_validated_default(self) -> None:
        self.client.close()
        self.env["FAKE_ASK_DEADLINE"] = "1"
        self.env["FAKE_ASK_DELAY"] = "0.1"
        self.client = McpTestClient(self.env)
        self.client.initialize()
        first = self.client.call(
            "dispatch_and_wait",
            {"provider": "codex", "prompt": "default", "wait_seconds": 0.5},
            timeout=HANG_GUARD_SECS,
        )
        event = first["result"]["structuredContent"]
        deadline = time.monotonic() + HANG_GUARD_SECS
        while event["event"] != "terminal" and time.monotonic() < deadline:
            self.assertIn(event["event"], {"ask", "still_running", "defaulted"})
            # A precise deadline check may still return an ask whose rounded
            # display countdown is zero until its actual epoch deadline.
            time.sleep(0.05)
            event = self.client.call(
                "wait_dispatch",
                {"dispatch_id": event["dispatch_id"], "wait_seconds": EVENT_WAIT_SECS},
                timeout=HANG_GUARD_SECS,
            )["result"]["structuredContent"]
        self.assertEqual("terminal", event["event"])
        self.assertEqual("succeeded", event["status"])
        answer_path = (
            self.sandbox.state
            / "pitwall"
            / "agents"
            / "runs"
            / event["dispatch_id"]
            / "mailbox"
            / "answers"
            / "0001.json"
        )
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
        self.assertEqual({"a", "default"}, {answer["choice"], answer["answered_by"]})

    def _call(self, name: str, arguments: dict) -> dict:
        return self.client.call(name, arguments, timeout=HANG_GUARD_SECS)["result"][
            "structuredContent"
        ]

    def test_default_is_reported_once_as_an_event(self) -> None:
        self.client.close()
        self.env["FAKE_ASK_DEADLINE"] = "1"
        # The child stays running after its default until the test releases it, so the
        # defaulted event is observable however slowly this test is scheduled.
        release = self.sandbox.root / "release-child"
        self.env["FAKE_RELEASE_FILE"] = str(release)
        self.client = McpTestClient(self.env)
        self.client.initialize()
        event = self._call(
            "dispatch_and_wait",
            {"provider": "codex", "prompt": "default", "wait_seconds": EVENT_WAIT_SECS},
        )
        dispatch_id = event["dispatch_id"]
        deadline = time.monotonic() + HANG_GUARD_SECS
        # The ask is visible until its 1 s deadline passes; the child then writes its default.
        while event["event"] != "defaulted" and time.monotonic() < deadline:
            self.assertIn(event["event"], {"ask", "still_running"})
            self.assertEqual([], event["defaults_applied"])
            time.sleep(0.05)
            event = self._call(
                "wait_dispatch", {"dispatch_id": dispatch_id, "wait_seconds": EVENT_WAIT_SECS}
            )
        self.assertEqual("defaulted", event["event"])
        self.assertEqual(("0001", "a"), (event["ask_id"], event["choice"]))
        again = self._call("wait_dispatch", {"dispatch_id": dispatch_id, "wait_seconds": 0.5})
        self.assertEqual("still_running", again["event"])
        self.assertEqual(["0001"], [d["ask_id"] for d in again["defaults_applied"]])
        release.touch()
        terminal = self._call(
            "wait_dispatch", {"dispatch_id": dispatch_id, "wait_seconds": EVENT_WAIT_SECS}
        )
        self.assertEqual("terminal", terminal["event"])

    def test_terminal_after_default_lists_it(self) -> None:
        self.client.close()
        self.env["FAKE_ASK_DEADLINE"] = "1"
        self.client = McpTestClient(self.env)
        self.client.initialize()
        event = self._call(
            "dispatch_and_wait",
            {"provider": "codex", "prompt": "default", "wait_seconds": EVENT_WAIT_SECS},
        )
        deadline = time.monotonic() + HANG_GUARD_SECS
        while event["event"] != "terminal" and time.monotonic() < deadline:
            time.sleep(0.05)
            event = self._call(
                "wait_dispatch",
                {"dispatch_id": event["dispatch_id"], "wait_seconds": EVENT_WAIT_SECS},
            )
        self.assertEqual("terminal", event["event"])
        self.assertEqual(
            [("0001", "a")], [(d["ask_id"], d["choice"]) for d in event["defaults_applied"]]
        )

    def test_conflicting_duplicate_answer_is_rejected_and_same_choice_is_idempotent(self) -> None:
        first = self._call(
            "dispatch_and_wait",
            {"provider": "codex", "prompt": "ask", "wait_seconds": EVENT_WAIT_SECS},
        )
        dispatch_id = first["dispatch_id"]
        done = self._call(
            "answer_and_wait",
            {
                "dispatch_id": dispatch_id,
                "ask_id": "0001",
                "choice": "b",
                "wait_seconds": EVENT_WAIT_SECS,
            },
        )
        self.assertEqual("terminal", done["event"])
        conflict = self.client.call(
            "answer_and_wait",
            {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "a", "wait_seconds": 1},
            timeout=HANG_GUARD_SECS,
        )["result"]
        self.assertTrue(conflict["isError"])
        self.assertIn("conflicting answer", conflict["content"][0]["text"])
        retry = self._call(
            "answer_and_wait",
            {"dispatch_id": dispatch_id, "ask_id": "0001", "choice": "b", "wait_seconds": 1},
        )
        self.assertEqual("b", retry["answer"]["choice"])
        self.assertEqual("terminal", retry["event"])

    def test_ask_over_the_cap_is_refused_without_hanging_the_parent(self) -> None:
        target = self.sandbox.bin / "codex"
        target.write_text(_TWO_ASK_HARNESS.format(launcher=str(PITWALL)), encoding="utf-8")
        target.chmod(0o755)
        first = self._call(
            "dispatch_and_wait",
            {"provider": "codex", "prompt": "cap", "max_asks": 1, "wait_seconds": EVENT_WAIT_SECS},
        )
        self.assertEqual("ask", first["event"])
        done = self._call(
            "answer_and_wait",
            {
                "dispatch_id": first["dispatch_id"],
                "ask_id": "0001",
                "choice": "a",
                "wait_seconds": EVENT_WAIT_SECS,
            },
        )
        self.assertEqual("terminal", done["event"])
        answers = (
            self.sandbox.state
            / "pitwall"
            / "agents"
            / "runs"
            / first["dispatch_id"]
            / "mailbox"
            / "answers"
        )
        self.assertEqual(["0001.json"], sorted(p.name for p in answers.glob("*.json")))

    def test_cleanup_keeps_live_managed_run_and_removes_sidecar_after_terminal(self) -> None:
        dispatch_id = str(uuid.uuid4())
        run_dir = state_root(self.env) / "runs" / dispatch_id
        run_dir.mkdir(parents=True)
        atomic_write_json(run_dir / "run.json", {"state": "running", "dispatchId": dispatch_id})
        launch_dir = state_root(self.env) / "launches" / dispatch_id
        launch_dir.mkdir(parents=True)
        atomic_write_json(
            launch_dir / "launcher.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "pid": os.getpid(),
                "pidStartIdentity": None,
            },
        )
        self.assertEqual([], cleanup_runs(self.env, older_than_seconds=0, remove_all=False))
        self.assertTrue(run_dir.exists())
        atomic_write_json(run_dir / "run.json", {"state": "succeeded", "dispatchId": dispatch_id})
        removed = cleanup_runs(self.env, older_than_seconds=0, remove_all=False)
        self.assertEqual([run_dir], removed)
        self.assertFalse(launch_dir.exists())

        live_id = str(uuid.uuid4())
        live_sidecar = state_root(self.env) / "launches" / live_id
        live_sidecar.mkdir(parents=True)
        atomic_write_json(live_sidecar / "launcher.json", {"pid": os.getpid()})
        failed_id = str(uuid.uuid4())
        failed_sidecar = state_root(self.env) / "launches" / failed_id
        failed_sidecar.mkdir(parents=True)
        atomic_write_json(failed_sidecar / "launch_error.json", {"error": "test"})
        cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        self.assertTrue(live_sidecar.exists())
        self.assertFalse(failed_sidecar.exists())

    def test_failed_launch_scrubs_staged_prompt_unless_explicitly_retained(self) -> None:
        failed_id = str(uuid.uuid4())
        failed_request = LaunchRequest(
            dispatch_id=failed_id,
            route=None,
            harness="codex",
            prompt="private launch prompt",
        )
        with (
            mock.patch(
                "pitwall.agents.managed_channel._execution_argv", return_value=["/missing/launcher"]
            ),
            self.assertRaises(ManagedChannelError),
        ):
            start_dispatch(self.env, failed_request)
        failed_dir = state_root(self.env) / "launches" / failed_id
        self.assertTrue((failed_dir / "launch_error.json").is_file())
        self.assertFalse((failed_dir / "prompt.md").exists())

        retained_id = str(uuid.uuid4())
        retained_request = LaunchRequest(
            dispatch_id=retained_id,
            route=None,
            harness="codex",
            prompt="private retained launch prompt",
            retain_prompt=True,
        )
        with (
            mock.patch(
                "pitwall.agents.managed_channel._execution_argv", return_value=["/missing/launcher"]
            ),
            self.assertRaises(ManagedChannelError),
        ):
            start_dispatch(self.env, retained_request)
        retained_dir = state_root(self.env) / "launches" / retained_id
        self.assertTrue((retained_dir / "launch_error.json").is_file())
        self.assertTrue((retained_dir / "prompt.md").is_file())


if __name__ == "__main__":
    unittest.main()
