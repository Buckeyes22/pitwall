"""Phase B exit, scripted: a harness asks through MCP mid-run (plan Task 20)."""

from __future__ import annotations

import json
import subprocess
import time
import unittest
from pathlib import Path

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

DISPATCH_ID = "00000000-0000-4000-8000-0000000000e1"

# A fake codex that behaves like an MCP-capable harness: it starts the registered
# server with its own environment and calls ask_orchestrator once.
_MCP_HARNESS = """\
#!/usr/bin/env python3
import json, os, subprocess, sys
prompt = sys.stdin.read()
server = subprocess.Popen([{launcher!r}, "mcp", "serve", "channel"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, env=dict(os.environ))
def send(message):
    server.stdin.write((json.dumps(message) + "\\n").encode())
    server.stdin.flush()
def receive(request_id):
    for line in server.stdout:
        message = json.loads(line)
        if message.get("id") == request_id:
            return message
send({{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {{"protocolVersion": "2025-06-18",
      "capabilities": {{}}, "clientInfo": {{"name": "fake", "version": "0"}}}}}})
receive(1)
send({{"jsonrpc": "2.0", "method": "notifications/initialized"}})
send({{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
      "params": {{"name": "ask_orchestrator", "arguments": json.loads(os.environ["FAKE_ASK"])}}}})
reply = receive(2)
print("tier1-block" if "tier-1 ask tool" in prompt else "no-tier1-block")
print("ANSWER " + json.dumps(reply["result"]["structuredContent"], sort_keys=True), flush=True)
server.stdin.close()
server.wait()
"""


class Tier1IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_MCP_HARNESS.format(launcher=str(PITWALL)), encoding="utf-8")
        target.chmod(0o755)
        env = self.sandbox.environment()
        apply_plan(plan_registration("codex", env, self.sandbox.home, command=str(PITWALL)))
        self.run_dir = self.sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _start(self, deadline_s: int) -> subprocess.Popen[bytes]:
        ask = {
            "question": "Which suffix?",
            "blocked_on": "naming",
            "default": "a",
            "deadline_s": deadline_s,
            "options": [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}],
        }
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
            env=self.sandbox.environment(
                PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID, FAKE_ASK=json.dumps(ask)
            ),
        )
        return process

    def _finished_row(self) -> dict:
        return [row for row in self.sandbox.ledger_records() if row.get("event") == "finished"][-1]

    def test_operator_answer_reaches_a_running_subagent(self) -> None:
        process = self._start(600)
        ask_file = self.run_dir / "mailbox" / "asks" / "0001.json"
        deadline = time.monotonic() + HANG_GUARD_SECS
        while not ask_file.exists():
            self.assertLess(time.monotonic(), deadline, "the harness never asked")
            time.sleep(0.1)
        answered = subprocess.run(
            [str(PITWALL), "agents", "answer", DISPATCH_ID, "0001", "b", "--note", "beta"],
            capture_output=True,
            env=self.sandbox.environment(),
            check=False,
        )
        self.assertEqual(0, answered.returncode, answered.stderr)
        out, err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b"tier1-block", out)
        self.assertIn(b'"choice": "b"', out)
        self.assertTrue(out.endswith(b"SHIM-DONE exit=0\n"), out[-200:])
        self.assertFalse((self.run_dir / "pause.json").exists())
        row = self._finished_row()
        self.assertEqual((1, ["0001:operator"]), (row["askCount"], row["askResolutions"]))

    def test_unanswered_ask_resolves_to_its_default(self) -> None:
        process = self._start(1)
        out, err = process.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(0, process.returncode, err)
        self.assertIn(b'"resolved_by": "default"', out)
        self.assertEqual(["0001:default"], self._finished_row()["askResolutions"])


if __name__ == "__main__":
    unittest.main()
