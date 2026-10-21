"""The listener heartbeat controls the default ask-channel choice."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

from pitwall.agents.channel import (
    WATCHER_STALE_S,
    load_channel_config,
    watcher_heartbeat_path,
)
from tests.agents.shim_test_support import PITWALL, ShimSandbox

DISPATCH_ID = "00000000-0000-4000-8000-0000000000e4"


class ChannelDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_harness("codex")
        self.state_home = self.sandbox.root / "agents-state"
        self.dispatch_number = 0
        self.dispatch_id = DISPATCH_ID

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _env(self, **overrides: str) -> dict[str, str]:
        self.dispatch_number += 1
        self.dispatch_id = f"00000000-0000-4000-8000-{self.dispatch_number:012x}"
        env = self.sandbox.environment(
            PITWALL_AGENTS_STATE_HOME=str(self.state_home),
            PITWALL_AGENTS_DISPATCH_ID=self.dispatch_id,
            CLAUDE_CODE_SESSION_ID="sess-a",
            **overrides,
        )
        # These tests model an inherited Claude session. A watcher in the real state
        # directory must never affect dispatches that use this isolated state root.
        return env

    def _heartbeat(self, session_id: str, *, age_s: float = 0) -> Path:
        path = watcher_heartbeat_path(self._env(), session_id)
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schemaVersion": 1, "session": session_id}), encoding="utf-8")
        if age_s:
            timestamp = time.time() - age_s
            os.utime(path, (timestamp, timestamp))
        return path

    def _dispatch(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(PITWALL), "agents", "dispatch", "codex", str(self.sandbox.prompt()), *args],
            capture_output=True,
            env=env or self._env(),
            check=False,
        )

    def _run_dir(self) -> Path:
        return self.state_home / "runs" / self.dispatch_id

    def _replace_sandbox(self) -> None:
        self.sandbox.cleanup()
        self.sandbox = ShimSandbox()
        self.state_home = self.sandbox.root / "agents-state"

    def test_fresh_listener_turns_the_channel_on_without_a_flag(self) -> None:
        self._heartbeat("sess-a")

        result = self._dispatch()

        self.assertEqual(0, result.returncode, result.stderr)
        config = load_channel_config(self._run_dir())
        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual("sess-a", config.orchestrator_session)

    def test_default_is_off_without_a_fresh_listener(self) -> None:
        result = self._dispatch()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNone(load_channel_config(self._run_dir()))
        delivered = self.sandbox.stdin_file.read_bytes()
        self.assertNotIn(b"tier-1 ask tool", delivered)
        self.assertNotIn(b"tier-4 ask contract", delivered)

    def test_stale_heartbeat_means_no_listener(self) -> None:
        self._heartbeat("sess-a", age_s=WATCHER_STALE_S + 1)

        result = self._dispatch()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNone(load_channel_config(self._run_dir()))

    def test_other_sessions_listener_does_not_count(self) -> None:
        self._heartbeat("sess-b")

        result = self._dispatch()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIsNone(load_channel_config(self._run_dir()))

    def test_explicit_choices_override_the_listener(self) -> None:
        self._heartbeat("sess-a")
        opted_out_flag = self._dispatch("--routing-no-ask-support")
        self.assertEqual(0, opted_out_flag.returncode, opted_out_flag.stderr)
        self.assertIsNone(load_channel_config(self._run_dir()))

        opted_out_env = self._dispatch(env=self._env(PITWALL_AGENTS_ASK_SUPPORT="0"))
        self.assertEqual(0, opted_out_env.returncode, opted_out_env.stderr)
        self.assertIsNone(load_channel_config(self._run_dir()))

        self._replace_sandbox()
        self.sandbox.install_harness("codex")
        forced_on = self._dispatch("--routing-ask-support")
        self.assertEqual(0, forced_on.returncode, forced_on.stderr)
        self.assertIsNotNone(load_channel_config(self._run_dir()))

    def test_tier4_default_pauses_only_with_a_listener(self) -> None:
        target = self.sandbox.bin / "pi"
        target.write_text(
            f"#!{sys.executable}\n"
            + "import os, sys\n"
            + f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2] / 'src')!r})\n"
            + "from pathlib import Path\n"
            + "prompt = sys.stdin.read() + ''.join(Path(arg.removeprefix('@')).read_text() for arg in sys.argv[1:] if Path(arg.removeprefix('@')).is_file())\n"
            + "if 'Orchestrator channel (tier-4 ask contract)' in prompt:\n"
            + "    from pitwall.agents.mailbox import Mailbox\n"
            + "    from pitwall.agents.run_store import state_root\n"
            + "    env = dict(os.environ)\n"
            + "    dispatch = env['PITWALL_AGENTS_CHANNEL_DISPATCH_ID']\n"
            + "    box = Mailbox(state_root(env) / 'runs' / dispatch, dispatch)\n"
            + "    box.write_ask(blocked_on='choice', question='Pick one', "
            + "options=[{'id': 'a', 'text': 'A'}], default='a', deadline_s=60)\n"
            + "    raise SystemExit(75)\n"
            + "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        target.chmod(0o755)

        self._heartbeat("sess-a")
        enabled = subprocess.run(
            [str(PITWALL), "agents", "dispatch", "pi", str(self.sandbox.prompt())],
            capture_output=True,
            env=self._env(),
            check=False,
        )
        self.assertEqual(75, enabled.returncode, enabled.stderr)

        self._replace_sandbox()
        self.sandbox.install_harness("pi")
        disabled = subprocess.run(
            [str(PITWALL), "agents", "dispatch", "pi", str(self.sandbox.prompt())],
            capture_output=True,
            env=self._env(),
            check=False,
        )
        self.assertEqual(0, disabled.returncode, disabled.stderr)
        self.assertIsNone(load_channel_config(self._run_dir()))


if __name__ == "__main__":
    unittest.main()
