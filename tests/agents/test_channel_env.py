"""Channel variables reach the harness; tier-1 prompt block when registered (plan Task 11)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pitwall.agents.channel import (
    load_channel_config,
)
from tests.agents.shim_test_support import (
    ShimSandbox,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000d1"


class ChannelEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_harness("codex")
        self.sandbox.install_harness("claude")

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _run(self, harness: str, *extra: str) -> dict:
        env = self.sandbox.environment(PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID)
        result = self.sandbox.run(harness, [str(self.sandbox.prompt()), *extra], env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        return self.sandbox.captured_env()

    def test_every_dispatch_carries_the_channel_variables(self) -> None:
        captured = self._run("codex")
        self.assertEqual(DISPATCH_ID, captured["PITWALL_AGENTS_CHANNEL_DISPATCH_ID"])
        self.assertEqual(
            str(self.sandbox.state / "pitwall" / "agents"),
            captured["PITWALL_AGENTS_CHANNEL_STATE_ROOT"],
        )
        self.assertEqual("1", captured["PITWALL_AGENTS_CHANNEL_ATTEMPT"])
        self.assertIsNone(captured["MCP_TOOL_TIMEOUT"])

    def test_claude_gets_a_tool_timeout_that_outlasts_an_ask(self) -> None:
        self.assertEqual("3660000", self._run("claude")["MCP_TOOL_TIMEOUT"])

    def test_registered_harness_gets_the_tier1_block_and_tier_record(self) -> None:
        (self.sandbox.home / ".codex").mkdir()
        (self.sandbox.home / ".codex/config.toml").write_text(
            '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\nargs = ["mcp"]\n',
            encoding="utf-8",
        )
        self._run("codex", "--routing-ask-support")
        prompt = self.sandbox.captured_stdin().decode()
        self.assertLess(
            prompt.index("# Orchestrator channel (tier-1 ask tool)"),
            prompt.index("# Orchestrator channel (tier-4 ask contract)"),
        )
        run_dir = self.sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual("1", config.tier)

    def test_unregistered_harness_gets_only_the_file_contract(self) -> None:
        self._run("codex", "--routing-ask-support")
        prompt = self.sandbox.captured_stdin().decode()
        self.assertNotIn("tier-1 ask tool", prompt)
        self.assertIn("tier-4 ask contract", prompt)

    def test_identity_variables_stay_with_the_dispatcher(self) -> None:
        captured = self._run("codex")
        for key in (
            "PITWALL_AGENTS_DISPATCH_ID",
            "PITWALL_AGENTS_ATTEMPT",
            "PITWALL_AGENTS_WORKFLOW_ID",
            "PITWALL_AGENTS_TASK_ID",
        ):
            self.assertIsNone(captured[key], key)

    def test_a_harness_can_dispatch_again_without_colliding(self) -> None:
        env = self.sandbox.environment(
            PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
            FAKE_NESTED_DISPATCH=str(ROOT / "tests/agents/fixtures/agents_launcher.py"),
            # The fake harness runs under the system python3; the nested launcher must
            # use the interpreter that satisfies the package's Python requirement.
            FAKE_NESTED_PYTHON=sys.executable,
        )
        result = self.sandbox.run("codex", [str(self.sandbox.prompt())], env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(self.sandbox.run_directories()))


if __name__ == "__main__":
    unittest.main()
