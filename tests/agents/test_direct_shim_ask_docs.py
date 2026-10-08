"""The routing skills must name the ask surfaces the code actually has."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from pitwall.agents.cli import build_parser
from pitwall.agents.mcp_server import SERVER_NAME
from pitwall.agents.mcp_tools import ASK_TOOL

ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    host: ROOT / "plugins" / host / "skills" / "subagent-model-routing" / "SKILL.md"
    for host in ("claude", "codex", "copilot")
}


def _code_spans(path: Path) -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", path.read_text(encoding="utf-8")))


def _command_exists(path: tuple[str, ...]) -> bool:
    """Whether ``pitwall agents <path...>`` walks real sub-commands of the agents parser."""
    parser = build_parser()
    for word in path:
        choices = next(
            (
                action.choices
                for action in parser._actions
                if isinstance(action, argparse._SubParsersAction)
            ),
            None,
        )
        if choices is None or word not in choices:
            return False
        parser = choices[word]
    return True


@pytest.mark.parametrize("host", sorted(SKILLS))
def test_skill_names_the_channel_server_and_ask_tool_the_code_serves(host: str) -> None:
    spans = _code_spans(SKILLS[host])

    assert SERVER_NAME in spans
    assert ASK_TOOL["name"] in spans


@pytest.mark.parametrize("host", sorted(SKILLS))
def test_skill_resume_command_is_a_real_agents_subcommand(host: str) -> None:
    resumes = [
        span.split()[2:]
        for span in _code_spans(SKILLS[host])
        if span.startswith("pitwall agents runs resume")
    ]

    assert resumes
    for words in resumes:
        assert _command_exists(tuple(word for word in words if not word.startswith("<")))
