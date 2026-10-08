"""Root marketplace placement, identity, and host-specific source syntax."""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "packages" / "agent-routing"


def _load(relative_path: str) -> dict[str, Any]:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _package_version() -> str:
    """The one package version every plugin and marketplace manifest carries."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


class MarketplaceManifestTests(unittest.TestCase):
    def test_root_manifests_are_the_only_marketplace_authorities(self) -> None:
        root_manifests = (
            ROOT / ".agents/plugins/marketplace.json",
            ROOT / ".claude-plugin/marketplace.json",
            ROOT / ".github/plugin/marketplace.json",
        )
        nested_manifests = (
            COMPONENT / ".agents/plugins/marketplace.json",
            COMPONENT / ".claude-plugin/marketplace.json",
            COMPONENT / ".github/plugin/marketplace.json",
        )

        self.assertTrue(all(path.is_file() for path in root_manifests))
        self.assertFalse(any(path.exists() for path in nested_manifests))

    def test_claude_marketplace_uses_claude_source_syntax(self) -> None:
        manifest = _load(".claude-plugin/marketplace.json")
        plugin = manifest["plugins"][0]

        self.assertEqual(manifest["name"], "pitwall-local")
        self.assertIn("Pitwall Agent Routing", manifest["description"])
        self.assertEqual(plugin["name"], "pitwall")
        self.assertEqual(
            plugin["source"],
            "./plugins/claude",
        )
        self.assertIn("Pitwall Agent Routing", plugin["description"])
        self._assert_plugin_identity(plugin["source"], ".claude-plugin/plugin.json", plugin)

    def test_codex_marketplace_uses_codex_local_source_syntax(self) -> None:
        manifest = _load(".agents/plugins/marketplace.json")
        plugin = manifest["plugins"][0]

        self.assertEqual(manifest["name"], "pitwall-local")
        self.assertEqual(manifest["interface"], {"displayName": "Pitwall Agent Routing"})
        self.assertEqual(plugin["name"], "pitwall-codex")
        self.assertEqual(
            plugin["source"],
            {
                "source": "local",
                "path": "./plugins/codex",
            },
        )
        source = plugin["source"]
        self._assert_plugin_identity(source["path"], ".codex-plugin/plugin.json", plugin)

    def test_copilot_marketplace_uses_copilot_source_syntax(self) -> None:
        manifest = _load(".github/plugin/marketplace.json")
        plugin = manifest["plugins"][0]

        self.assertEqual(manifest["name"], "pitwall-local")
        self.assertIn("Pitwall Agent Routing", manifest["metadata"]["description"])
        self.assertEqual(plugin["name"], "pitwall-copilot")
        self.assertEqual(
            plugin["source"],
            "plugins/copilot",
        )
        self.assertIn("Pitwall Agent Routing", plugin["description"])
        self._assert_plugin_identity(plugin["source"], "plugin.json", plugin)

    def test_claude_agent_ids_are_unchanged(self) -> None:
        agents = ROOT / "plugins/claude/agents"
        self.assertEqual(
            {path.stem for path in agents.glob("*.md")},
            {
                "agy-shim",
                "cline-shim",
                "codex-shim",
                "dsh-shim",
                "goose-shim",
                "grok-shim",
                "hermes-shim",
                "kimi-shim",
                "muse-shim",
                "opencode-shim",
                "pi-shim",
                "qwen-shim",
                "zcode-shim",
                "route-shim",
            },
        )

    def _assert_plugin_identity(
        self, source: object, manifest: str, marketplace_entry: dict[str, Any]
    ) -> None:
        self.assertIsInstance(source, str)
        source_path = ROOT / str(source).removeprefix("./")
        plugin_manifest = json.loads((source_path / manifest).read_text(encoding="utf-8"))
        self.assertEqual(plugin_manifest["name"], marketplace_entry["name"])
        self.assertEqual(plugin_manifest["version"], marketplace_entry["version"])
        self.assertEqual(_package_version(), marketplace_entry["version"])


if __name__ == "__main__":
    unittest.main()
