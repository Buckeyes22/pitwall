"""`pitwall agents install` writes an absolute MCP command or refuses; it never writes bare `pitwall`."""

from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import installation
from pitwall.agents.installation import InstallationError, install


class McpCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="pitwall-brittle-install-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "PATH": "/nonexistent"}

    def run_install(self) -> None:
        install(self.env, self.home, harnesses=("codex",), plugin_hosts=(), runner=lambda _a: 0)

    def test_off_path_install_writes_the_running_executable_not_bare_pitwall(self) -> None:
        binary = self.root / "venv" / "bin" / "pitwall"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
        with mock.patch.object(installation.sys, "argv", [str(binary), "agents", "install"]):
            self.run_install()
        config = tomllib.loads((self.home / ".codex" / "config.toml").read_text(encoding="utf-8"))
        command = config["mcp_servers"]["pitwall-channel"]["command"]
        self.assertTrue(os.path.isabs(command), command)
        self.assertEqual(str(binary.resolve()), command)

    def test_install_refuses_when_no_pitwall_executable_can_be_found(self) -> None:
        with (
            mock.patch.object(installation.sys, "argv", ["python", "-m", "pitwall"]),
            mock.patch.object(installation.sys, "executable", str(self.root / "py" / "python")),
            self.assertRaisesRegex(InstallationError, "uv tool install --python 3.14 "),
        ):
            self.run_install()
        self.assertFalse((self.home / ".codex" / "config.toml").exists())
        self.assertFalse((self.home / ".claude" / "scripts").exists())
