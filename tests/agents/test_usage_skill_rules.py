"""Every host's routing skill teaches the usage rules, and each native host names its exception."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from pitwall.agents.usage.rows import STATUSES
from pitwall.cli.usage import _parse_usage_args

ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    "claude": ROOT / "plugins" / "claude" / "skills" / "subagent-model-routing" / "SKILL.md",
    "codex": ROOT / "plugins" / "codex" / "skills" / "subagent-model-routing" / "SKILL.md",
    "copilot": ROOT / "plugins" / "copilot" / "skills" / "subagent-model-routing" / "SKILL.md",
}
SECTION = "### Subscription usage"
# The 90 percent and 10 percent reserve figures are policy in the skill text, not constants in src.
USAGE_RULE_PHRASES = (
    "Do not choose an account whose status is `limit`.",
    "at or above 90 percent",
    "10 percent reserve",
    "use the route of the account with the most room",
    "Treat `error`, `stale`, and `unknown` as no information.",
    "say so to the user and continue",
)


def _usage_section(text: str) -> str:
    """The ``### Subscription usage`` section: its heading to the next ``### `` heading."""
    match = re.search(rf"^{re.escape(SECTION)}\n(.*?)(?=^### )", text, re.MULTILINE | re.DOTALL)
    assert match is not None, f"no {SECTION!r} section"
    return match.group(1)


class UsageSkillRuleTests(unittest.TestCase):
    def test_claude_copy_is_the_rule_block(self) -> None:
        claude = SKILLS["claude"].read_text(encoding="utf-8")
        self.assertEqual(1, claude.count(SECTION))
        body = _usage_section(claude)
        items = re.findall(r"^(\d+)\. (.+)$", body, re.MULTILINE)
        self.assertEqual([str(n) for n in range(1, len(items) + 1)], [n for n, _ in items])
        self.assertEqual(6, len(items))
        # The first rule runs the real command and names exactly the statuses the code emits.
        first = items[0][1]
        command = re.search(r"run `pitwall ([^`]+)`", first)
        assert command is not None
        self.assertTrue(command.group(1).startswith("usage "))
        args = _parse_usage_args(command.group(1).split()[1:])
        self.assertTrue(args.json_output)
        statuses = first.split("`status` is", 1)[1].split("`routes`", 1)[0]
        self.assertEqual(set(STATUSES), set(re.findall(r"`([a-z]+)`", statuses)))

    def test_every_host_carries_the_claude_rule_block_verbatim(self) -> None:
        reference = _usage_section(SKILLS["claude"].read_text(encoding="utf-8"))
        for host, path in SKILLS.items():
            with self.subTest(host=host):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(1, text.count(SECTION))
                self.assertEqual(reference, _usage_section(text))

    def test_every_host_teaches_the_selection_rules_in_these_words(self) -> None:
        # Guards the account-selection promises: skip `limit`, keep a 10 percent reserve below 90,
        # prefer the most room, and never treat missing data as a reason to avoid an account.
        for host, path in SKILLS.items():
            body = _usage_section(path.read_text(encoding="utf-8"))
            for rule in USAGE_RULE_PHRASES:
                with self.subTest(host=host, rule=rule):
                    self.assertEqual(1, body.count(rule))

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
