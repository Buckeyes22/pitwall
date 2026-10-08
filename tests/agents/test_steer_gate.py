"""Tier-2 PreToolUse steering gate (plan Task 23)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.channel import (
    ChannelConfig,
    write_channel_config,
)
from pitwall.agents.run_store import (
    RunStore,
)
from pitwall.agents.steer_gate import (
    decide,
    run_stdin,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000f3"
WRAPPERS = [ROOT / "plugins/claude/hooks/steer-gate.py", ROOT / "plugins/codex/hooks/steer-gate.py"]


class GateDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.state = Path(self._temp.name) / "state"
        self.store = RunStore(self.state, DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="1"))
        self.env = {
            "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": DISPATCH_ID,
            "PITWALL_AGENTS_CHANNEL_STATE_ROOT": str(self.state),
        }

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_blocking_steer_denies_other_tools_until_acked(self) -> None:
        steer = self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        decision = decide({"tool_name": "Bash", "tool_input": {}}, self.env)
        assert decision is not None
        output = decision["hookSpecificOutput"]
        self.assertEqual(
            ("PreToolUse", "deny"), (output["hookEventName"], output["permissionDecision"])
        )
        self.assertIn("[scope 0001] SQLite only", output["permissionDecisionReason"])
        self.assertIsNone(decide({"tool_name": "mcp__pitwall-channel__ack_steer"}, self.env))
        self.assertIsNone(decide({"tool_name": "mcp__pitwall-channel__read_steering"}, self.env))
        self.store.mailbox().write_ack(steer["steer_id"])
        self.assertIsNone(decide({"tool_name": "Bash"}, self.env))

    def test_codex_underscore_tool_names_are_exempt(self) -> None:
        # Codex replaces the hyphen in the server name with an underscore.
        self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        for name in (
            "mcp__pitwall_channel__read_steering",
            "mcp__pitwall_channel__ack_steer",
            "mcp__pitwall_channel__ask_orchestrator",
        ):
            with self.subTest(tool=name):
                self.assertIsNone(decide({"tool_name": name}, self.env))
        self.assertIsNotNone(decide({"tool_name": "mcp__other_server__read_steering"}, self.env))

    def test_only_the_exact_channel_server_and_tool_names_are_exempt(self) -> None:
        self.store.mailbox().write_steer(kind="scope", message="SQLite only")
        for name in (
            "mcp__pitwall_channel_x__read_steering",
            "mcp__my-pitwall-channel__ack_steer",
            "mcp__pitwall-channel-proxy__ask_orchestrator",
            "mcp__pitwall-channel__evil_ack_steer",
            "mcp__other__pitwall-channel__read_steering",
            "pitwall-channel__read_steering",
        ):
            with self.subTest(tool=name):
                self.assertIsNotNone(decide({"tool_name": name}, self.env))

    def test_allows_when_the_gate_does_not_apply(self) -> None:
        self.store.mailbox().write_steer(kind="note", message="fyi")
        self.assertIsNone(decide({"tool_name": "Bash"}, self.env))  # advisory kinds never block
        self.store.mailbox().write_steer(kind="scope", message="narrow")
        self.assertIsNone(decide({"tool_name": "Bash"}, {}))  # not a dispatched harness
        self.assertIsNone(
            decide(
                {"tool_name": "Bash"},
                {**self.env, "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "x"},
            )
        )
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, tier="4"))
        self.assertIsNone(decide({"tool_name": "Bash"}, self.env))  # no ack tool, so no gate


class GateCommandTests(unittest.TestCase):
    def test_gate_body_fails_closed_on_garbage(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        self.assertEqual(2, run_stdin(b"{not json", {}, stdout, stderr))
        self.assertEqual("", stdout.getvalue())

    def test_wrappers_are_identical_and_block_without_the_command(self) -> None:
        self.assertEqual(WRAPPERS[0].read_bytes(), WRAPPERS[1].read_bytes())
        result = subprocess.run(
            [sys.executable, str(WRAPPERS[0])],
            input=b"{}",
            capture_output=True,
            env={"PATH": "/nonexistent", "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": DISPATCH_ID},
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(
            "deny", json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
        )

    def test_both_bundles_register_the_pre_tool_use_gate(self) -> None:
        for hooks in (
            ROOT / "plugins/claude/hooks/hooks.json",
            ROOT / "plugins/codex/hooks/hooks.json",
        ):
            entries = json.loads(hooks.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
            commands = [h["command"] for entry in entries for h in entry["hooks"]]
            self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/steer-gate.py"', commands)
        manifest = json.loads(
            (ROOT / "plugins/codex/.codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual("./hooks/hooks.json", manifest["hooks"])


if __name__ == "__main__":
    unittest.main()
