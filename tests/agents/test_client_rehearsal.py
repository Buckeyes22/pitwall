"""Isolated marketplace refresh rehearsal for the documented client refresh runbook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT
MARKETPLACE = "pitwall-local"
CLAUDE_ID = f"pitwall@{MARKETPLACE}"
CODEX_ID = f"pitwall-codex@{MARKETPLACE}"
COPILOT_ID = f"pitwall-copilot@{MARKETPLACE}"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


class ClientRehearsalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="pitwall-client-rehearsal-")
        self.root = Path(self.directory.name)
        self.home = self.root / "home"
        self.payload = self.root / "xdg-data" / "pitwall" / "plugins"
        self.stale = self.root / "stale-worktree"
        self.bin = self.root / "bin"
        self.claude_home = self.root / "claude-config"
        self.codex_home = self.root / "codex-home"
        for path in (self.home, self.bin, self.claude_home, self.codex_home):
            path.mkdir(parents=True)
        for destination in (self.payload, self.stale):
            for relative in (
                ".agents/plugins",
                ".claude-plugin",
                ".github/plugin",
                "plugins",
            ):
                shutil.copytree(REPO_ROOT / relative, destination / relative)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def environment(self, *client_dirs: Path) -> dict[str, str]:
        client_path = ":".join(str(path) for path in client_dirs)
        path = f"{self.bin}:{client_path}:/usr/local/bin:/usr/bin:/bin"
        return {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
            "XDG_DATA_HOME": str(self.root / "xdg-data"),
            "XDG_STATE_HOME": str(self.root / "xdg-state"),
            "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
            "CODEX_HOME": str(self.codex_home),
            "CLAUDE_CONFIG_DIR": str(self.claude_home),
            "PATH": path,
            "NO_COLOR": "1",
            "TERM": "dumb",
        }

    def run_client(
        self,
        command: list[str],
        env: dict[str, str],
        *,
        json_output: bool = False,
    ) -> subprocess.CompletedProcess[str] | object:
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout) if json_output else result

    def test_real_claude_and_codex_refresh_remove_stale_records(self) -> None:
        claude_value = os.environ.get("PITWALL_TEST_CLAUDE_BIN", "")
        codex_value = os.environ.get("PITWALL_TEST_CODEX_BIN", "")
        if not claude_value or not codex_value:
            self.skipTest(
                "set absolute PITWALL_TEST_CLAUDE_BIN and PITWALL_TEST_CODEX_BIN "
                "for the managed client evidence lane"
            )
        claude = Path(claude_value)
        codex = Path(codex_value)
        self.assertTrue(claude.is_absolute() and claude.is_file())
        self.assertTrue(codex.is_absolute() and codex.is_file())
        env = self.environment(claude.parent, codex.parent)

        self.run_client(
            [str(claude), "plugin", "marketplace", "add", "--scope", "user", str(self.stale)],
            env,
        )
        self.run_client([str(claude), "plugin", "install", "--scope", "user", CLAUDE_ID], env)
        self.run_client(
            [
                str(claude),
                "plugin",
                "uninstall",
                "--keep-data",
                "--scope",
                "user",
                CLAUDE_ID,
            ],
            env,
        )
        self.run_client(
            [
                str(claude),
                "plugin",
                "marketplace",
                "remove",
                "--scope",
                "user",
                MARKETPLACE,
            ],
            env,
        )
        self.run_client(
            [
                str(claude),
                "plugin",
                "marketplace",
                "add",
                "--scope",
                "user",
                str(self.payload),
            ],
            env,
        )
        self.run_client([str(claude), "plugin", "install", "--scope", "user", CLAUDE_ID], env)
        claude_markets = self.run_client(
            [str(claude), "plugin", "marketplace", "list", "--json"],
            env,
            json_output=True,
        )
        claude_plugins = self.run_client(
            [str(claude), "plugin", "list", "--json"], env, json_output=True
        )
        self.assertNotIn(str(self.stale), json.dumps(claude_markets))
        self.assertIn(str(self.payload), json.dumps(claude_markets))
        claude_plugin = next(row for row in claude_plugins if row.get("id") == CLAUDE_ID)
        self.assertEqual(VERSION, claude_plugin["version"])
        claude_cache = Path(claude_plugin["installPath"])
        self.assertTrue((claude_cache / ".claude-plugin" / "plugin.json").is_file())
        self.assertTrue((claude_cache / "agents" / "codex-shim.md").is_file())
        self.assertTrue((claude_cache / "skills" / "subagent-model-routing" / "SKILL.md").is_file())

        self.run_client(
            [str(codex), "plugin", "marketplace", "add", str(self.stale), "--json"],
            env,
        )
        self.run_client([str(codex), "plugin", "add", CODEX_ID, "--json"], env)
        self.run_client([str(codex), "plugin", "remove", CODEX_ID], env)
        self.run_client(
            [
                str(codex),
                "plugin",
                "marketplace",
                "remove",
                MARKETPLACE,
            ],
            env,
        )
        self.run_client([str(codex), "plugin", "marketplace", "add", str(self.payload)], env)
        installed = self.run_client(
            [str(codex), "plugin", "add", CODEX_ID, "--json"],
            env,
            json_output=True,
        )
        codex_markets = self.run_client(
            [str(codex), "plugin", "marketplace", "list", "--json"],
            env,
            json_output=True,
        )
        codex_plugins = self.run_client(
            [str(codex), "plugin", "list", "--available", "--json"],
            env,
            json_output=True,
        )
        self.assertNotIn(str(self.stale), json.dumps(codex_markets))
        self.assertIn(str(self.payload), json.dumps(codex_markets))
        codex_plugin = next(
            row for row in codex_plugins["installed"] if row.get("pluginId") == CODEX_ID
        )
        self.assertEqual(VERSION, codex_plugin["version"])
        self.assertEqual(VERSION, installed["version"])
        codex_cache = Path(installed["installedPath"])
        self.assertTrue((codex_cache / ".codex-plugin" / "plugin.json").is_file())
        self.assertTrue((codex_cache / "skills" / "subagent-model-routing" / "SKILL.md").is_file())

    def test_fake_copilot_refresh_records_exact_supported_commands(self) -> None:
        log = self.root / "copilot-commands.jsonl"
        fake = self.bin / "copilot"
        fake.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

with Path(os.environ["COPILOT_COMMAND_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = self.environment()
        env["COPILOT_COMMAND_LOG"] = str(log)
        commands = [
            ["plugin", "uninstall", "pitwall-copilot"],
            ["plugin", "marketplace", "remove", MARKETPLACE],
            ["plugin", "marketplace", "add", str(self.payload)],
            ["plugin", "install", COPILOT_ID],
        ]
        for arguments in commands:
            self.run_client([str(fake), *arguments], env)
        recorded = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(commands, recorded)

        marketplace = json.loads(
            (self.payload / ".github" / "plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        plugin = marketplace["plugins"][0]
        self.assertEqual("pitwall-copilot", plugin["name"])
        self.assertEqual(VERSION, plugin["version"])
        self.assertTrue((self.payload / plugin["source"]).is_dir())
        runbook = (REPO_ROOT / "docs" / "agent-routing" / "live-cutover.md").read_text(
            encoding="utf-8"
        )
        for command in (
            "copilot plugin uninstall pitwall-copilot",
            "copilot plugin marketplace remove pitwall-local",
            'copilot plugin marketplace add "${XDG_DATA_HOME:-$HOME/.local/share}/pitwall/plugins"',
            "copilot plugin install " + COPILOT_ID,
        ):
            self.assertIn(command, runbook)


if __name__ == "__main__":
    unittest.main()
