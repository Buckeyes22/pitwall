"""The plugin family is named pitwall."""

from __future__ import annotations

import json
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT


class PluginIdentityTests(unittest.TestCase):
    def test_plugin_ids_are_pitwall(self) -> None:
        names = {
            json.loads((ROOT / "plugins/claude/.claude-plugin/plugin.json").read_text())["name"],
            json.loads((ROOT / "plugins/codex/.codex-plugin/plugin.json").read_text())["name"],
            json.loads((ROOT / "plugins/copilot/plugin.json").read_text())["name"],
        }
        self.assertEqual({"pitwall", "pitwall-codex", "pitwall-copilot"}, names)
        market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
        self.assertEqual("pitwall-local", market["name"])
        self.assertEqual(["pitwall"], [plugin["name"] for plugin in market["plugins"]])

    def test_no_old_namespace_remains(self) -> None:
        legacy_ids = "\\|".join(
            "subagent-model-routing-" + suffix for suffix in ("claude", "codex", "copilot")
        )
        hits = subprocess.run(
            [
                "git",
                "grep",
                "-l",
                legacy_ids,
                "--",
                ".",
                ":!docs/evidence",
                ":!docs/superpowers",
                ":!CHANGELOG.md",
                ":!docs/agents/CHANGELOG.md",
                # Release notes carry the migration steps, which must name the old id.
                ":!docs/agents/releases",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        self.assertEqual([], hits)

    def test_only_one_plugin_script(self) -> None:
        scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertNotIn("pitwall-agent-routing", scripts)
        self.assertNotIn("subagent-model-routing", scripts)


if __name__ == "__main__":
    unittest.main()
