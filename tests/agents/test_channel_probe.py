"""The child-channel handshake probe tells a busy machine from a broken child."""

from __future__ import annotations

import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import managed_channel
from pitwall.agents.managed_channel import ManagedChannelError, verify_child_channel

_FAKE_CHILD = textwrap.dedent(
    """\
    import json, sys, time
    time.sleep({delay})
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") == 1:
            result = {{"protocolVersion": "2025-06-18", "capabilities": {{}},
                       "serverInfo": {{"name": "fake", "version": "0"}}}}
        elif message.get("id") == 2:
            result = {{"tools": [{{"name": name}} for name in
                       ("ask_orchestrator", "read_steering", "ack_steer")]}}
        else:
            continue
        print(json.dumps({{"jsonrpc": "2.0", "id": message["id"], "result": result}}), flush=True)
    """
)
_HUNG_CHILD = "import time\ntime.sleep(600)\n"


class ChannelProbeTests(unittest.TestCase):
    def _verify(self, script: str) -> float:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "child.py"
            path.write_text(script, encoding="utf-8")
            argv = [sys.executable, str(path)]
            started = time.monotonic()
            with mock.patch.object(managed_channel, "_registered_channel_argv", return_value=argv):
                verify_child_channel("codex", {"HOME": tmp, "XDG_STATE_HOME": tmp}, Path(tmp))
            return time.monotonic() - started

    def test_slow_but_healthy_child_is_accepted(self) -> None:
        # A child that answers after the old 2 s bound models a busy workstation.
        elapsed = self._verify(_FAKE_CHILD.format(delay=3.0))
        self.assertGreater(elapsed, 2.0)

    def test_hung_child_is_still_refused(self) -> None:
        # The default bound is a hang guard: generous, but finite enough for one dispatch.
        self.assertGreaterEqual(managed_channel.CHANNEL_PROBE_TIMEOUT_SECONDS, 10.0)
        self.assertLessEqual(managed_channel.CHANNEL_PROBE_TIMEOUT_SECONDS, 60.0)
        with mock.patch.object(managed_channel, "CHANNEL_PROBE_TIMEOUT_SECONDS", 1.0):
            started = time.monotonic()
            with self.assertRaisesRegex(ManagedChannelError, "MCP handshake failed"):
                self._verify(_HUNG_CHILD)
        self.assertLess(time.monotonic() - started, 30.0)

    def test_a_disabled_entry_is_refused_without_starting_the_server(self) -> None:
        import json
        import stat

        from pitwall.agents.mcp_registration import render_entry

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            marker = home / "started"
            command = home / "pitwall"
            command.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
            command.chmod(command.stat().st_mode | stat.S_IXUSR)
            config = home / ".zcode" / "cli" / "config.json"
            config.parent.mkdir(parents=True)
            entry = render_entry("zcode", str(command))
            entry["enabled"] = False
            config.write_text(
                json.dumps({"mcp": {"servers": {"pitwall-channel": entry}}}), encoding="utf-8"
            )
            env = {"HOME": tmp, "XDG_STATE_HOME": tmp, "PATH": tmp}
            with self.assertRaisesRegex(ManagedChannelError, "disabled"):
                verify_child_channel("zcode", env, home)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
