"""Writable-source resolution tests for the Claude /distill command."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOLVER = ROOT / "plugins/claude/hooks/resolve-distill-source.py"
SPEC = importlib.util.spec_from_file_location("resolve_distill_source", RESOLVER)
assert SPEC is not None and SPEC.loader is not None
resolver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resolver
SPEC.loader.exec_module(resolver)


class DistillSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.nowhere = self.root / "not-a-checkout"
        self.nowhere.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def checkout(self, name: str, *, layout: str, parent: Path | None = None) -> Path:
        root = (parent or self.root) / name
        plugin = "claude" if layout == "pitwall" else "pitwall"
        ledger = root / f"plugins/{plugin}/skills/subagent-model-routing/ledger"
        ledger.mkdir(parents=True)
        skill = ledger.parent / "SKILL.md"
        skill.write_text("skill\n", encoding="utf-8")
        (ledger / "codex.md").write_text("card\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        return root.resolve()

    def test_current_pitwall_development_clone_wins(self) -> None:
        development = self.checkout("pitwall", layout="pitwall")
        legacy = self.checkout("legacy", layout="standalone")
        result = resolver.resolve(
            {"HOME": str(self.root), "PITWALL_AGENTS_HOME": str(legacy)},
            cwd=development,
        )
        self.assertEqual("current-git", result["source"])
        self.assertEqual(str(development), result["componentRoot"])
        self.assertFalse(result["durablePayload"])

    def test_nested_durable_payload_layout_and_no_legacy_fallback(self) -> None:
        home = self.root / "home"
        durable = self.checkout(
            "pitwall",
            layout="pitwall",
            parent=home / ".local/share",
        )
        result = resolver.resolve({"HOME": str(home)}, cwd=self.nowhere)
        self.assertEqual("pitwall-home", result["source"])
        self.assertEqual(str(durable), result["componentRoot"])
        self.assertTrue(result["durablePayload"])

        other_home = self.root / "old-home"
        self.checkout("old-layout", layout="standalone", parent=other_home / ".local/share")
        with self.assertRaisesRegex(resolver.ResolutionError, "no writable"):
            resolver.resolve({"HOME": str(other_home)}, cwd=self.nowhere)

    def test_invalid_home_variable_is_rejected(self) -> None:
        invalid_new = self.root / "invalid-new"
        invalid_new.mkdir()
        with self.assertRaisesRegex(resolver.ResolutionError, "no writable"):
            resolver.resolve(
                {"HOME": str(self.root), "PITWALL_AGENTS_HOME": str(invalid_new)},
                cwd=self.nowhere,
            )

    def test_invalid_candidates_and_plugin_cache_are_rejected(self) -> None:
        with self.assertRaisesRegex(resolver.ResolutionError, "no writable"):
            resolver.resolve({"HOME": str(self.root)}, cwd=self.nowhere)

        cache_parent = self.root / "home/.claude/plugins/cache"
        cache = self.checkout("copied-plugin", layout="standalone", parent=cache_parent)
        with self.assertRaisesRegex(resolver.ResolutionError, "plugin caches"):
            resolver.resolve(
                {
                    "HOME": str(self.root / "home"),
                    "PITWALL_AGENTS_HOME": str(cache),
                },
                cwd=self.nowhere,
            )

    def test_cli_emits_only_resolved_source_paths_as_json(self) -> None:
        development = self.checkout("cli-pitwall", layout="pitwall")
        result = subprocess.run(
            [sys.executable, str(RESOLVER)],
            cwd=development,
            env={"HOME": str(self.root), "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(str(development), payload["root"])
        self.assertEqual(
            str(development),
            payload["componentRoot"],
        )


if __name__ == "__main__":
    unittest.main()
