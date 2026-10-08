"""The per-account cache: intervals, private files, and unreadable content."""

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.usage import (
    cache,
)

ROOT = Path(__file__).resolve().parents[2]

ROW = {
    "plan": "glm",
    "account": "",
    "routes": [],
    "label": "GLM",
    "tier": "Pro",
    "windows": [],
    "status": "ok",
    "detail": "",
    "observed_at": "2026-09-28T12:00:00Z",
}


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name, "XDG_STATE_HOME": str(Path(self.tmp.name) / "state")}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_paths_carry_the_account(self) -> None:
        base = Path(self.tmp.name) / "state" / "pitwall" / "agents" / "usage"
        self.assertEqual(base / "claude.json", cache.path_for(self.env, "claude", ""))
        self.assertEqual(base / "claude-home.json", cache.path_for(self.env, "claude", "home"))

    def test_round_trip_and_private_modes(self) -> None:
        self.assertIsNone(cache.load(self.env, "glm", ""))
        cache.store(self.env, "glm", "", 1000.0, row=ROW, error=None)
        self.assertEqual(
            {"attemptedAt": 1000.0, "row": ROW, "error": None}, cache.load(self.env, "glm", "")
        )
        path = cache.path_for(self.env, "glm", "")
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))

    def test_intervals(self) -> None:
        self.assertTrue(cache.due(None, "glm", 0.0))
        entry = {"attemptedAt": 1000.0, "row": None, "error": None}
        self.assertFalse(cache.due(entry, "glm", 1059.9))
        self.assertTrue(cache.due(entry, "glm", 1060.0))
        self.assertFalse(cache.due(entry, "claude", 1299.9))
        self.assertTrue(cache.due(entry, "claude", 1300.0))

    def test_a_damaged_file_reads_as_no_cache(self) -> None:
        path = cache.path_for(self.env, "glm", "")
        path.parent.mkdir(parents=True)
        for content in (
            "",
            '{"attemptedAt": 1000.0, "row": {"pl',
            "[]",
            '{"attemptedAt": "soon"}',
            '{"attemptedAt": true}',
        ):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                self.assertIsNone(cache.load(self.env, "glm", ""))

    def test_odd_fields_are_dropped_not_trusted(self) -> None:
        path = cache.path_for(self.env, "glm", "")
        path.parent.mkdir(parents=True)
        path.write_text('{"attemptedAt": 5, "row": "text", "error": 7}', encoding="utf-8")
        self.assertEqual(
            {"attemptedAt": 5.0, "row": None, "error": None}, cache.load(self.env, "glm", "")
        )


if __name__ == "__main__":
    unittest.main()
