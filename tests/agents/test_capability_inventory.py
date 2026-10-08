"""Tests for secret-safe cross-harness capability inventory."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

from pitwall.agents import (
    capability_inventory,
)
from pitwall.agents.registry import (
    load_registry,
)
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class CapabilityInventoryTests(unittest.TestCase):
    registry: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def test_detected_harnesses_collect_names_without_config_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            executable(binary_dir / "codex")
            executable(binary_dir / "opencode")
            executable(binary_dir / "copilot")
            codex = home / "custom-codex"
            codex.mkdir()
            (codex / "config.toml").write_text(
                '[mcp_servers.github]\ncommand = "server"\n'
                '[mcp_servers.github.env]\nACCESS_TOKEN = "super-secret-value"\n',
                encoding="utf-8",
            )
            (codex / "skills" / "reviewer").mkdir(parents=True)
            agents = home / ".agents/skills"
            agents.mkdir(parents=True)
            (agents / "shared.md").write_text("private skill instructions", encoding="utf-8")
            opencode = home / "config/opencode"
            opencode.mkdir(parents=True)
            (opencode / "opencode.json").write_text(
                json.dumps(
                    {
                        "mcp": {"browser": {"url": "https://secret.invalid/key"}},
                        "plugin": ["singular-plugin"],
                        "plugins": ["oh-my-opencode", {"name": "review-pack"}],
                    }
                ),
                encoding="utf-8",
            )
            (opencode / "agents" / "security.md").parent.mkdir()
            (opencode / "agents" / "security.md").write_text("agent secret", encoding="utf-8")
            copilot = home / ".copilot"
            copilot.mkdir()
            (copilot / "mcp-config.json").write_text(
                json.dumps({"mcpServers": {"filesystem": {"token": "do-not-copy"}}}),
                encoding="utf-8",
            )
            undetected = home / ".kimi"
            undetected.mkdir()
            (undetected / "mcp.json").write_text(
                json.dumps({"mcpServers": {"must-not-appear": {}}}), encoding="utf-8"
            )
            env = {
                "HOME": str(home),
                "CODEX_HOME": str(codex),
                "XDG_CONFIG_HOME": str(home / "config"),
                "PATH": str(binary_dir),
            }

            inventory = capability_inventory.build_inventory(
                self.registry, env, generated_at="2026-09-04T00:00:00.000Z"
            )

        rows = {row["id"]: row for row in inventory["harnesses"]}
        self.assertEqual({"codex", "copilot", "opencode"}, set(rows))
        self.assertEqual(["github"], rows["codex"]["capabilities"]["mcps"])
        self.assertEqual(["reviewer", "shared"], rows["codex"]["capabilities"]["skills"])
        self.assertEqual(["browser"], rows["opencode"]["capabilities"]["mcps"])
        self.assertEqual(
            ["oh-my-opencode", "review-pack", "singular-plugin"],
            rows["opencode"]["capabilities"]["plugins"],
        )
        self.assertEqual(["security"], rows["opencode"]["capabilities"]["agents"])
        self.assertEqual(["filesystem"], rows["copilot"]["capabilities"]["mcps"])
        serialized = json.dumps(inventory)
        for forbidden in (
            "super-secret-value",
            "secret.invalid",
            "do-not-copy",
            "private skill instructions",
            "agent secret",
            "must-not-appear",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_yaml_names_warnings_rendering_and_private_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            executable(binary_dir / "hermes")
            executable(binary_dir / "qwen")
            hermes = home / ".hermes"
            hermes.mkdir()
            (hermes / "config.yaml").write_text(
                "mcp_servers:\n  github:\n    command: server\n  docs:\n    url: https://example.invalid\ntools:\n  terminal:\n    enabled: true\n",
                encoding="utf-8",
            )
            qwen = home / ".qwen"
            qwen.mkdir()
            (qwen / "settings.json").write_text("{not-json", encoding="utf-8")
            env = {
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / "config"),
                "PATH": str(binary_dir),
            }

            inventory, json_path, markdown_path = capability_inventory.write_inventory(
                self.registry, env
            )
            markdown = markdown_path.read_text(encoding="utf-8")

            rows = {row["id"]: row for row in inventory["harnesses"]}
            self.assertEqual(["docs", "github"], rows["hermes"]["capabilities"]["mcps"])
            self.assertEqual(["terminal"], rows["hermes"]["capabilities"]["tools"])
            self.assertEqual(
                [{"harness": "qwen", "path": "~/.qwen/settings.json", "reason": "malformed JSON"}],
                inventory["errors"],
            )
            self.assertIn("availability context, not authorization", markdown)
            self.assertIn("- MCP servers: `docs`, `github`", markdown)
            self.assertEqual(0o600, stat.S_IMODE(json_path.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(markdown_path.stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(json_path.parent.stat().st_mode))

    def test_oversized_config_is_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            executable(binary_dir / "qwen")
            qwen = home / ".qwen"
            qwen.mkdir()
            (qwen / "settings.json").write_bytes(
                b'{"mcpServers":{"must-not-appear":{}},"padding":"'
                + b"x" * capability_inventory.MAX_CONFIG_BYTES
                + b'"}'
            )
            inventory = capability_inventory.build_inventory(
                self.registry,
                {"HOME": str(home), "PATH": str(binary_dir)},
                generated_at="2026-09-04T00:00:00.000Z",
            )
        self.assertEqual(
            [{"harness": "qwen", "path": "~/.qwen/settings.json", "reason": "too large"}],
            inventory["errors"],
        )
        self.assertNotIn("must-not-appear", json.dumps(inventory))

    def test_setup_inventory_cli_writes_snapshot_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            executable(binary_dir / "codex")
            env = {
                **os.environ,
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / "config"),
                "PATH": f"{binary_dir}:{os.environ.get('PATH', '')}",
            }
            result = subprocess.run(
                [
                    str(PITWALL),
                    "agents",
                    "setup",
                    "inventory",
                    "--json",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=HANG_GUARD_SECS,
            )
            payload = json.loads(result.stdout)
            snapshot = home / "config/pitwall/agents/harness-capabilities.json"
            snapshot_exists = snapshot.is_file()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("codex", {row["id"] for row in payload["harnesses"]})
        self.assertTrue(snapshot_exists)


class ChannelRegistrationPathTests(unittest.TestCase):
    def test_paths_follow_each_harness_and_cline_3(self) -> None:
        home = Path("/h")
        path = capability_inventory.channel_config_path
        self.assertEqual(Path("/h/.claude.json"), path("claude", {}, home))
        self.assertEqual(Path("/c/.claude.json"), path("claude", {"CLAUDE_CONFIG_DIR": "/c"}, home))
        self.assertEqual(Path("/h/.codex/config.toml"), path("codex", {}, home))
        self.assertEqual(Path("/x/config.toml"), path("codex", {"CODEX_HOME": "/x"}, home))
        self.assertEqual(Path("/h/.copilot/mcp-config.json"), path("copilot", {}, home))
        self.assertEqual(Path("/h/.config/opencode/opencode.json"), path("opencode", {}, home))
        self.assertEqual(Path("/h/.kimi-code/mcp.json"), path("kimi", {}, home))
        self.assertEqual(
            Path("/h/.cline/data/settings/cline_mcp_settings.json"), path("cline", {}, home)
        )
        self.assertEqual(
            Path("/d/settings/cline_mcp_settings.json"),
            path("cline", {"CLINE_DATA_DIR": "/d"}, home),
        )
        self.assertEqual(
            Path("/m.json"), path("cline", {"CLINE_MCP_SETTINGS_PATH": "/m.json"}, home)
        )
        self.assertEqual(Path("/h/.qwen/settings.json"), path("qwen", {}, home))
        self.assertEqual(Path("/q/settings.json"), path("qwen", {"QWEN_CODE_HOME": "/q"}, home))

    def test_registered_flag_reads_each_config_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text(
                '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\nargs = ["mcp"]\n',
                encoding="utf-8",
            )
            (home / ".kimi-code").mkdir()
            (home / ".kimi-code/mcp.json").write_text(
                '{"mcpServers": {"other": {}}}', encoding="utf-8"
            )
            self.assertTrue(capability_inventory.mcp_channel_registered("codex", {}, home))
            self.assertFalse(capability_inventory.mcp_channel_registered("kimi", {}, home))
            self.assertFalse(capability_inventory.mcp_channel_registered("grok", {}, home))

    def test_a_disabled_channel_entry_is_not_registered(self) -> None:
        from pitwall.agents.mcp_registration import render_entry

        json_hosts = {
            "zcode": ("nested", "enabled", False),
            "agy": ("mcpServers", "disabled", True),
            "cline": ("mcpServers", "disabled", True),
            "opencode": ("mcp", "enabled", False),
        }
        for harness, (section, flag, value) in json_hosts.items():
            with self.subTest(harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                path = capability_inventory.channel_config_path(harness, {}, home)
                path.parent.mkdir(parents=True, exist_ok=True)
                entry = render_entry(harness, "/bin/pitwall")
                data: dict[str, object] = (
                    {"mcp": {"servers": {"pitwall-channel": entry}}}
                    if section == "nested"
                    else {section: {"pitwall-channel": entry}}
                )
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertTrue(capability_inventory.mcp_channel_registered(harness, {}, home))
                entry[flag] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertFalse(capability_inventory.mcp_channel_registered(harness, {}, home))

    def test_a_disabled_yaml_or_toml_channel_entry_is_not_registered(self) -> None:
        cases = {
            "goose": (
                "extensions:\n  pitwall-channel:\n    enabled: {flag}\n    type: stdio\n"
                "    cmd: /bin/pitwall\n    args: [mcp, serve, channel]\n"
            ),
            "hermes": (
                "mcp_servers:\n  pitwall-channel:\n    enabled: {flag}\n"
                "    command: /bin/pitwall\n    args: [mcp, serve, channel]\n"
            ),
            "grok": (
                '[mcp_servers.pitwall-channel]\ncommand = "/bin/pitwall"\n'
                'args = ["mcp", "serve", "channel"]\nenabled = {flag}\n'
            ),
        }
        for harness, template in cases.items():
            with self.subTest(harness), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                path = capability_inventory.channel_config_path(harness, {}, home)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(template.format(flag="true"), encoding="utf-8")
                self.assertTrue(capability_inventory.mcp_channel_registered(harness, {}, home))
                path.write_text(template.format(flag="false"), encoding="utf-8")
                self.assertFalse(capability_inventory.mcp_channel_registered(harness, {}, home))

    def test_inventory_rows_carry_the_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            binary_dir = home / "bin"
            executable(binary_dir / "codex")
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text(
                '[mcp_servers.pitwall-channel]\ncommand = "/bin/true"\n', encoding="utf-8"
            )
            inventory = capability_inventory.build_inventory(
                load_registry(),
                {"HOME": str(home), "PATH": str(binary_dir)},
                generated_at="2026-09-10T00:00:00.000Z",
            )
            row = {r["id"]: r for r in inventory["harnesses"]}["codex"]
            self.assertTrue(row["mcpChannel"])
            self.assertIn(
                "MCP server pitwall-channel registered; interactive delivery unverified",
                capability_inventory.render_context(inventory),
            )


if __name__ == "__main__":
    unittest.main()
