"""Child reasoning fixture, translated from packages/pi-workbench/tests/comparison-reasoning-fixture.test.ts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pitwall.workbench.comparison.reasoning_fixture import (
    write_comparison_child_reasoning_fixture,
)


@pytest.mark.parity
def test_pins_the_private_b_c_explore_definition_to_the_parent_reasoning_level(
    tmp_path: Path,
) -> None:
    """Source: comparison-reasoning-fixture.test.ts 'pins the private B/C Explore definition to the parent reasoning level'."""
    fixture = write_comparison_child_reasoning_fixture(tmp_path, "low")
    settings = json.loads(fixture.settings_path.read_text())
    definition = fixture.agent_definition_path.read_text()

    assert fixture.level == "low"
    assert settings["subagents"]["defaultThinking"] == "low"
    assert settings["subagents"]["agentOverrides"]["Explore"]["thinking"] == "low"
    assert "name: Explore" in definition
    assert "tools: read,grep,find,ls" in definition
    assert "thinking: low" in definition
    assert "thinking: high" not in definition
    assert "model:" not in definition
    assert (fixture.settings_path.stat().st_mode & 0o777) == 0o600
