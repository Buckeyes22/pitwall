"""Agent state resolves through agents/paths.py under the shared pitwall state root."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from pitwall.agents import migrate_env, paths, run_store
from pitwall.agents.usage import cache


class StatePathTests(unittest.TestCase):
    def test_state_root_under_pitwall(self) -> None:
        root = paths.state_root({"HOME": "/srv/tester-home"})
        self.assertEqual(Path("/srv/tester-home/.local/state/pitwall/agents"), root)

    def test_xdg_override(self) -> None:
        env = {"HOME": "/srv/tester-home", "XDG_STATE_HOME": "/srv/xdg-state"}
        self.assertEqual(Path("/srv/xdg-state/pitwall/agents"), paths.state_root(env))

    def test_explicit_state_home_wins(self) -> None:
        env = {"XDG_STATE_HOME": "/srv/xdg-state", "PITWALL_AGENTS_STATE_HOME": "/tmp/isolated"}
        self.assertEqual(Path("/tmp/isolated"), paths.state_root(env))

    def test_run_store_and_usage_cache_resolve_through_paths(self) -> None:
        env = {"HOME": "/srv/tester-home"}
        self.assertEqual(paths.state_root(env), run_store.state_root(env))
        self.assertEqual(
            paths.state_root(env) / "usage" / "codex-work.json",
            cache.path_for(env, "codex", "work"),
        )


ROOT = Path(__file__).resolve().parents[2]
# A legacy path is spelled as a path: joined to a directory, or under ~/.config, ~/.claude, ...
LEGACY_PATH = re.compile(
    r"""(?:/ |join\([^)\n]*|/\.(?:config|claude|local/(?:state|share))/|~/\.(?:config|claude|local/(?:state|share))/)["']?subagent-model-routing"""
)


class ConfigAndLedgerPathTests(unittest.TestCase):
    def test_config_root_under_pitwall(self) -> None:
        self.assertEqual(
            Path("/srv/tester-home/.config/pitwall/agents"),
            paths.config_root({"HOME": "/srv/tester-home"}),
        )
        env = {"HOME": "/srv/tester-home", "XDG_CONFIG_HOME": "/srv/xdg-config"}
        self.assertEqual(Path("/srv/xdg-config/pitwall/agents"), paths.config_root(env))
        self.assertEqual(paths.config_root(env), run_store.config_root(env))

    def test_ledger_under_state_root(self) -> None:
        env = {"HOME": "/srv/tester-home", "XDG_STATE_HOME": "/srv/xdg-state"}
        self.assertEqual(
            Path("/srv/xdg-state/pitwall/agents/ledger/observations.jsonl"), paths.ledger_path(env)
        )
        override = {**env, "PITWALL_AGENTS_LEDGER": "/tmp/ledger.jsonl"}
        self.assertEqual(Path("/tmp/ledger.jsonl"), paths.ledger_path(override))
        self.assertEqual(paths.ledger_path(env), run_store.ledger_path(env))

    def test_no_legacy_path_literals(self) -> None:
        self.assertEqual(4, len(migrate_env.LEGACY_PATHS))
        offenders = []
        for base in (ROOT / "src" / "pitwall" / "agents", ROOT / "plugins"):
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.suffix not in {".py", ".sh", ".json", ".md", ".toml"}:
                    continue
                if path.name in {"paths.py", "migrate_env.py"} and path.parent.name == "agents":
                    continue
                if "node_modules" in path.parts or "__pycache__" in path.parts:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if "skills" not in line and LEGACY_PATH.search(line):
                        offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:100]}")
        self.maxDiff = None
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
