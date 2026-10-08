"""Opt-in live reads, one per reader. Skipped unless PITWALL_AGENTS_USAGE_LIVE=1.

Each check makes one read-only request with the credential already on this machine and
prints only the status and the percentages.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles, usage
from pitwall.agents.registry import (
    load_registry,
)

ROOT = Path(__file__).resolve().parents[2]

LIVE = os.environ.get("PITWALL_AGENTS_USAGE_LIVE") == "1"
MEASURED = ("claude", "codex", "glm", "minimax", "model-studio")


@unittest.skipUnless(LIVE, "set PITWALL_AGENTS_USAGE_LIVE=1 to read live usage")
class LiveUsageTests(unittest.TestCase):
    def test_every_configured_reader_answers(self) -> None:
        registry = load_registry()
        home = Path(os.environ["HOME"])
        with tempfile.TemporaryDirectory() as state:
            env = {**os.environ, "XDG_STATE_HOME": state}
            env.pop("PITWALL_AGENTS_STATE_HOME", None)
            rows = usage.collect(
                env,
                registry=registry,
                routes_config=profiles.load_profiles(env, registry=registry),
                home=home,
            )
        measured = [row for row in rows if row.plan in MEASURED]
        self.assertTrue(measured, "no plan with a reader is configured on this machine")
        for row in measured:
            print(
                f"{row.plan} {row.account or '-'}: {row.status} {[window.used_pct for window in row.windows]} {row.detail}"
            )
            self.assertIn(
                row.status, ("ok", "warn", "limit", "unknown"), f"{row.plan}: {row.detail}"
            )


if __name__ == "__main__":
    unittest.main()
