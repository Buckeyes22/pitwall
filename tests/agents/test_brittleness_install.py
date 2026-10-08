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


class SkippedHostsTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="pitwall-skipped-hosts-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        pitwall = self.bin / "pitwall"
        pitwall.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        pitwall.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": str(self.bin)}

    def test_install_records_every_host_and_channel_skipped_for_a_missing_cli(self) -> None:
        result = install(self.env, self.home, runner=lambda _a: 0, lister=lambda _a: "")
        skipped = " | ".join(result.skipped)
        for host in ("claude", "codex", "copilot"):
            self.assertIn(f"plugins: {host}", skipped)
        for harness in ("claude", "codex", "copilot", "opencode"):
            self.assertIn(f"channel server: {harness}", skipped)
        self.assertEqual([], result.mcp_harnesses)

    def test_explicit_selections_report_nothing_as_skipped(self) -> None:
        result = install(self.env, self.home, harnesses=(), plugin_hosts=(), runner=lambda _a: 0)
        self.assertEqual([], result.skipped)

    def test_cli_install_output_lists_the_skipped_hosts(self) -> None:
        import contextlib
        import io

        from pitwall.agents import cli

        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            contextlib.redirect_stdout(out),
        ):
            code = cli.main(["install"])
        self.assertEqual(0, code)
        text = out.getvalue()
        self.assertIn("skipped (CLI not found):", text)
        self.assertIn("plugins: claude", text)
        self.assertIn("pitwall agents install", text)
