"""Every host's routing skill teaches the usage rules, and each native host names its exception."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    "claude": ROOT / "plugins" / "claude" / "skills" / "subagent-model-routing" / "SKILL.md",
    "codex": ROOT / "plugins" / "codex" / "skills" / "subagent-model-routing" / "SKILL.md",
    "copilot": ROOT / "plugins" / "copilot" / "skills" / "subagent-model-routing" / "SKILL.md",
}
RULES = (
    "### Subscription usage",
    "run `pitwall usage --json`",
    "Do not choose an account whose status is `limit`.",
    "at or above 90 percent",
    "10 percent reserve",
    "use the route of the account with the most room",
    "Treat `error`, `stale`, and `unknown` as no information.",
    "say so to the user and continue",
)


class UsageSkillRuleTests(unittest.TestCase):
    def test_every_host_carries_the_same_rules(self) -> None:
        for host, path in SKILLS.items():
            text = path.read_text(encoding="utf-8")
            for rule in RULES:
                with self.subTest(host=host, rule=rule):
                    self.assertEqual(1, text.count(rule))

    def test_the_rules_come_before_the_self_hosted_section(self) -> None:
        for host, path in SKILLS.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(host=host):
                self.assertLess(
                    text.index("### Subscription usage"),
                    text.index("### Self-hosted through Pitwall"),
                )

    def test_each_native_host_names_its_second_account_exception(self) -> None:
        claude = SKILLS["claude"].read_text(encoding="utf-8")
        self.assertIn("a saved route whose `env` sets `CLAUDE_CONFIG_DIR`", claude)
        self.assertIn("Claude work stays native", claude)
        codex = SKILLS["codex"].read_text(encoding="utf-8")
        self.assertIn("a saved route whose `env` sets `CODEX_HOME`", codex)
        self.assertIn("keep GPT work inline", codex)


if __name__ == "__main__":
    unittest.main()
