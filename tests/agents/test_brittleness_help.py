"""`pitwall agents --help` describes every subcommand, at every nesting level."""

from __future__ import annotations

import argparse
import unittest

from pitwall.agents.cli import build_parser


def undocumented(parser: argparse.ArgumentParser, trail: str = "") -> list[str]:
    missing: list[str] = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        described = {choice.dest: choice.help for choice in action._choices_actions}
        for name, sub in action.choices.items():
            path = f"{trail} {name}".strip()
            if not described.get(name):
                missing.append(path)
            missing.extend(undocumented(sub, path))
    return missing


class HelpTests(unittest.TestCase):
    def test_every_subcommand_has_a_help_string(self) -> None:
        self.assertEqual([], undocumented(build_parser()))

    def test_top_level_help_lists_all_subcommands(self) -> None:
        parser = build_parser()
        text = parser.format_help()
        top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        for name in top.choices:
            self.assertRegex(text, rf"(?m)^    {name}\s+\S", name)
