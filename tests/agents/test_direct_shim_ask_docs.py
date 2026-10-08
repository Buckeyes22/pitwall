"""The codex and copilot skills must describe direct-shim asks as the code behaves."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = {
    host: ROOT / "plugins" / host / "skills" / "subagent-model-routing" / "SKILL.md"
    for host in ("claude", "codex", "copilot")
}


def _flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("host", sorted(SKILLS))
def test_direct_shim_ask_support_uses_tier_one_on_a_registered_channel(host: str) -> None:
    text = _flat(SKILLS[host])
    assert "it can then only pause on the file contract (exit 75)" not in text
    assert "registered `pitwall-channel` server" in text
    assert "`ask_orchestrator`" in text
    assert "Without a registered channel" in text
    assert "`pitwall agents runs resume`" in text
