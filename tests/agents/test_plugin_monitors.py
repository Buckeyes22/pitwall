"""The Claude plugin declares the watcher that wakes a session on child questions."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "plugins/claude/monitors/monitors.json"
ALLOWED_KEYS = {"name", "command", "description", "when"}


class PluginMonitorTests(unittest.TestCase):
    def test_monitor_manifest_is_strict_and_runs_the_watcher(self) -> None:
        monitors: Any = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertIsInstance(monitors, list)
        self.assertEqual(1, len(monitors))
        monitor = monitors[0]
        self.assertIsInstance(monitor, dict)
        self.assertLessEqual(set(monitor), ALLOWED_KEYS)
        self.assertEqual("pitwall-asks", monitor["name"])
        self.assertEqual("always", monitor["when"])
        self.assertEqual("pitwall agents inbox --watch", monitor["command"])
        self.assertIn("questions", monitor["description"])


if __name__ == "__main__":
    unittest.main()
