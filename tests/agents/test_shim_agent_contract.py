"""Every shim agent definition describes the self-connecting background transport (plan Task 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "plugins" / "claude" / "agents"

SHIM_AGENTS = (
    "agy",
    "cline",
    "codex",
    "dsh",
    "goose",
    "grok",
    "hermes",
    "kimi",
    "mcode",
    "muse",
    "opencode",
    "pi",
    "qwen",
    "route",
    "zcode",
)

CHANNEL_PARAGRAPH = (
    "Channel: the shim connects itself to this Claude session when the pitwall-asks monitor "
    "is running, so the command needs no channel flag. This Agent may itself run in the "
    "background; your one Bash call stays in the foreground. If the final line is SHIM-DONE "
    "exit=75, return the full stdout followed by the line: PAUSED — the child asked a "
    "question; answer it with answer_ask, then resume with pitwall agents runs resume "
    "<dispatch-id>"
)

FOREGROUND_RULES = ("Never background the work", "NEVER background the work")


@pytest.mark.parametrize("agent", SHIM_AGENTS)
def test_every_shim_agent_states_the_channel_contract(agent: str) -> None:
    body = (AGENTS / f"{agent}-shim.md").read_text(encoding="utf-8")

    assert CHANNEL_PARAGRAPH in body
    assert "Backgrounded Agent or Bash calls are forbidden" not in body
    assert any(rule in body for rule in FOREGROUND_RULES)
