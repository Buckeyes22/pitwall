"""Phase C exit, scripted: a scope change and a stop land in a running dispatch (plan Task 24)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import unittest
from pathlib import Path

from pitwall.agents.channel import (
    ChannelConfig,
    load_channel_config,
    send_steer,
    write_channel_config,
)
from pitwall.agents.dispatch import _LegacyDispatch
from pitwall.agents.mcp_registration import (
    plan_registration,
)
from pitwall.agents.profiles_sync import apply_plan
from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000e2"

# A fake MCP-capable codex that polls read_steering and obeys the first scope or stop directive.
_STEERED_HARNESS = """\
#!/usr/bin/env python3
import json, os, subprocess, sys, time
sys.stdin.read()
server = subprocess.Popen([{launcher!r}, "mcp", "serve", "channel"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, env=dict(os.environ))
counter = [0]
def call(method, params=None):
    counter[0] += 1
    message = {{"jsonrpc": "2.0", "id": counter[0], "method": method}}
    if params is not None:
        message["params"] = params
    server.stdin.write((json.dumps(message) + "\\n").encode())
    server.stdin.flush()
    for line in server.stdout:
        reply = json.loads(line)
        if reply.get("id") == counter[0]:
            return reply
call("initialize", {{"protocolVersion": "2025-06-18", "capabilities": {{}},
                     "clientInfo": {{"name": "fake", "version": "0"}}}})
deadline = time.monotonic() + {hang_guard!r}
while time.monotonic() < deadline:
    reply = call("tools/call", {{"name": "read_steering", "arguments": {{}}}})
    for steer in reply["result"]["structuredContent"]["steers"]:
        if steer["kind"] in ("scope", "stop"):
            call("tools/call", {{"name": "ack_steer", "arguments": {{"steer_id": steer["steer_id"]}}}})
            print("stopping" if steer["kind"] == "stop" else "scope: " + steer["message"], flush=True)
            server.stdin.close()
            server.wait()
            sys.exit(0)
    time.sleep(0.2)
sys.exit(1)
"""


class SteerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(
            _STEERED_HARNESS.format(launcher=str(PITWALL), hang_guard=HANG_GUARD_SECS),
            encoding="utf-8",
        )
        target.chmod(0o755)
        apply_plan(
            plan_registration(
                "codex", self.sandbox.environment(), self.sandbox.home, command=str(PITWALL)
            )
        )
        self.run_dir = self.sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _start(self) -> subprocess.Popen[bytes]:
        process = self.sandbox.popen(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                "--routing-ask-support",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.sandbox.environment(PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID),
        )
        deadline = time.monotonic() + HANG_GUARD_SECS
        while True:
            self.assertLess(time.monotonic(), deadline, "the dispatch never reached running")
            try:
                if json.loads((self.run_dir / "run.json").read_text())["state"] == "running":
                    return process
            except OSError, json.JSONDecodeError, KeyError:
                pass
            time.sleep(0.05)

    def _cli(self, *args: str) -> None:
        subprocess.run(
            [str(PITWALL), "agents", *args],
            env=self.sandbox.environment(),
            check=True,
            capture_output=True,
        )

    def test_scope_change_lands_without_killing_the_run(self) -> None:
        process = self._start()
        self._cli("steer", DISPATCH_ID, "--kind", "scope", "--message", "SQLite only")
        out, err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b"scope: SQLite only", out)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=0\n"), out[-200:])
        self.assertEqual(
            "succeeded", json.loads((self.run_dir / "result.json").read_text())["status"]
        )
        row = [r for r in self.sandbox.ledger_records() if r.get("event") == "finished"][-1]
        self.assertEqual((1, 1), (row["steerCount"], len(row["steerAckLatencyS"])))
        events = [
            json.loads(line)["event"]
            for line in (self.run_dir / "events.jsonl").read_text().splitlines()
        ]
        self.assertLess(events.index("steer.sent"), events.index("steer.acked"))

    def test_honored_stop_is_a_clean_finish(self) -> None:
        process = self._start()
        self._cli("runs", "stop", DISPATCH_ID, "--grace", "30")
        out, err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b"stopping", out)
        self.assertEqual(
            "succeeded", json.loads((self.run_dir / "result.json").read_text())["status"]
        )
        self.assertFalse((self.run_dir / "abort.json").exists())


class EarlyChannelRecordTests(unittest.TestCase):
    """A channel dispatch records channel.json before any pre-launch state can be steered."""

    def _prepared(self, *flags: str) -> tuple[_LegacyDispatch, dict[str, str], Path]:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("codex")
        env = sandbox.environment(PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID)
        run = _LegacyDispatch(
            "codex", [str(sandbox.prompt()), *flags], env, None, threading.Event(), []
        )
        for step in (run.parse_request, run.open_run, run.check_binary, run.load_prompt):
            self.assertIsNone(step())
        run_dir = sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID
        self.assertEqual("ready", json.loads((run_dir / "run.json").read_text())["state"])
        return run, env, run_dir

    def test_a_channel_dispatch_accepts_a_steer_before_launch(self) -> None:
        before = time.time()
        _, env, run_dir = self._prepared("--routing-ask-support")
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual(DISPATCH_ID, config.dispatch_id)
        self.assertGreaterEqual(config.attempt_started_epoch, before)
        steer = send_steer(
            env,
            DISPATCH_ID,
            kind="scope",
            message="SQLite only",
            requires_ack=True,
            deadline_s=300,
            harness="operator",
        )
        self.assertEqual("0001", steer["steer_id"])

    def test_a_dispatch_without_the_channel_records_no_config(self) -> None:
        _, env, run_dir = self._prepared()
        self.assertFalse((run_dir / "channel.json").exists())
        with self.assertRaisesRegex(Exception, "is still preparing and has not recorded"):
            send_steer(
                env,
                DISPATCH_ID,
                kind="scope",
                message="SQLite only",
                requires_ack=True,
                deadline_s=300,
                harness="operator",
            )

    def test_a_resumed_channel_run_keeps_its_prior_wall_time_and_ask_cap(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("codex")
        prompt = str(sandbox.prompt())
        env = sandbox.environment(PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID)
        first = _LegacyDispatch(
            "codex", [prompt, "--routing-ask-support"], env, None, threading.Event(), []
        )
        for step in (first.parse_request, first.open_run, first.check_binary, first.load_prompt):
            self.assertIsNone(step())
        run_dir = sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID
        document = json.loads((run_dir / "run.json").read_text())
        document["state"] = "paused"
        (run_dir / "run.json").write_text(json.dumps(document))
        write_channel_config(
            first.store,
            ChannelConfig(
                DISPATCH_ID, max_asks=3, attempt_started_epoch=1.0, wall_seconds_prior=120
            ),
        )
        resumed = _LegacyDispatch(
            "codex",
            [prompt, "--routing-ask-support"],
            {**env, "PITWALL_AGENTS_RESUME": "1", "PITWALL_AGENTS_ATTEMPT": "2"},
            None,
            threading.Event(),
            [],
        )
        for step in (
            resumed.parse_request,
            resumed.open_run,
            resumed.check_binary,
            resumed.load_prompt,
        ):
            self.assertIsNone(step())
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual((3, 120), (config.max_asks, config.wall_seconds_prior))
        self.assertGreater(config.attempt_started_epoch, 1.0)


if __name__ == "__main__":
    unittest.main()
