"""Bounded Explore child definition for the private B/C fixtures (port of ``comparison-reasoning-fixture.ts``)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pitwall.workbench.comparison._json import JsonObject

READ_ONLY_TOOLS = "read,grep,find,ls"


@dataclass(frozen=True)
class ComparisonChildReasoningFixture:
    level: str
    settings_path: Path
    agent_definition_path: Path

    def to_json(self) -> JsonObject:
        return {
            "level": self.level,
            "settingsPath": str(self.settings_path),
            "agentDefinitionPath": str(self.agent_definition_path),
        }


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)


def write_comparison_child_reasoning_fixture(
    cwd: Path | str, level: str
) -> ComparisonChildReasoningFixture:
    """Install the same bounded Explore child definition for private B/C fixtures.

    B reads ``subagents.defaultThinking``/``agentOverrides`` from project settings, while C reads
    the project agent definition. Keeping both supported forms in the disposable fixture prevents
    either backend's built-in Explore default from changing the comparison's reasoning control. The
    definition has only read-only tools and no model override, so the child inherits the parent's
    selected provider and model.
    """
    pi_dir = Path(cwd) / ".pi"
    agents_dir = pi_dir / "agents"
    settings_path = pi_dir / "settings.json"
    definition_path = agents_dir / "Explore.md"
    agents_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings = {
        "subagents": {"defaultThinking": level, "agentOverrides": {"Explore": {"thinking": level}}}
    }
    _write_private(settings_path, json.dumps(settings, indent=2) + "\n")
    _write_private(
        definition_path,
        f"""---
name: Explore
description: Bounded read-only comparison child
tools: {READ_ONLY_TOOLS}
thinking: {level}
prompt_mode: replace
---

Read-only comparison child. Use only the supplied read, grep, find, and ls tools. Do not create, edit, delete, or write files.
""",
    )
    return ComparisonChildReasoningFixture(level, settings_path, definition_path)
