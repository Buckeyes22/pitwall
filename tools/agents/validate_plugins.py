#!/usr/bin/env python3
"""Validate package structure and host-native routing boundaries."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pitwall.agents.registry import (  # noqa: E402  # reason: sys.path bootstrap
    load_registry,
)

PACKAGES = {
    "claude": ROOT / "plugins" / "claude",
    "codex": ROOT / "plugins" / "codex",
    "copilot": ROOT / "plugins" / "copilot",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    manifests = {
        "claude": PACKAGES["claude"] / ".claude-plugin" / "plugin.json",
        "codex": PACKAGES["codex"] / ".codex-plugin" / "plugin.json",
        "copilot": PACKAGES["copilot"] / "plugin.json",
    }
    documents = {
        host: json.loads(path.read_text(encoding="utf-8")) for host, path in manifests.items()
    }
    package_version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    marketplaces = (
        ROOT / ".claude-plugin" / "marketplace.json",
        ROOT / ".agents" / "plugins" / "marketplace.json",
        ROOT / ".github" / "plugin" / "marketplace.json",
    )
    versions = {host: document["version"] for host, document in documents.items()}
    for marketplace in marketplaces:
        for entry in json.loads(marketplace.read_text(encoding="utf-8"))["plugins"]:
            versions[f"{marketplace.relative_to(ROOT)}:{entry['name']}"] = entry["version"]
    drifted = {name: found for name, found in versions.items() if found != package_version}
    require(
        not drifted,
        f"plugin versions must equal the package version {package_version}: {drifted}",
    )

    for host, package in PACKAGES.items():
        skill = package / "skills" / "subagent-model-routing" / "SKILL.md"
        readme = package / "README.md"
        require(
            skill.is_file() and readme.is_file(),
            f"{host}: required skill/README missing",
        )
        skill_text = skill.read_text(encoding="utf-8")
        require(
            skill_text.startswith("---\n") and "\n---\n" in skill_text[4:],
            f"{host}: invalid skill frontmatter",
        )
        readme_text = readme.read_text(encoding="utf-8")
        require(
            "Shared runtime prerequisite" in readme_text,
            f"{host}: runtime prerequisite undocumented",
        )
        require(
            "plugin package does not duplicate" in readme_text,
            f"{host}: package boundary undocumented",
        )
        require("pitwall agents install" in readme_text, f"{host}: installer undocumented")
        require(
            not (package / "scripts").exists(),
            f"{host}: runtime must remain single-sourced at repo root",
        )

    claude_skill = (PACKAGES["claude"] / "skills/subagent-model-routing/SKILL.md").read_text(
        encoding="utf-8"
    )
    codex_skill = (PACKAGES["codex"] / "skills/subagent-model-routing/SKILL.md").read_text(
        encoding="utf-8"
    )
    copilot_skill = (PACKAGES["copilot"] / "skills/subagent-model-routing/SKILL.md").read_text(
        encoding="utf-8"
    )
    require(
        "~/.claude/scripts/claude-shim.sh" not in claude_skill,
        "Claude package routes its native harness",
    )
    require(
        "~/.claude/scripts/codex-shim.sh" not in codex_skill,
        "Codex package routes its native harness",
    )
    require(
        "~/.claude/scripts/kimi-shim.sh" in claude_skill,
        "Claude package lacks Kimi route",
    )
    require(
        "~/.claude/scripts/qwen-shim.sh" in claude_skill,
        "Claude package lacks Qwen route",
    )
    require(
        "~/.claude/scripts/claude-shim.sh" in codex_skill,
        "Codex package lacks Claude route",
    )
    require(
        "~/.claude/scripts/kimi-shim.sh" in codex_skill,
        "Codex package lacks Kimi route",
    )
    require(
        "~/.claude/scripts/codex-shim.sh" in copilot_skill,
        "Copilot package lacks Codex route",
    )
    require(
        "~/.claude/scripts/claude-shim.sh" in copilot_skill,
        "Copilot package lacks Claude route",
    )
    require(
        "~/.claude/scripts/kimi-shim.sh" in copilot_skill,
        "Copilot package lacks Kimi route",
    )
    require(
        "~/.claude/scripts/qwen-shim.sh" in copilot_skill,
        "Copilot package lacks Qwen route",
    )
    harnesses = load_registry()["harnesses"]
    for harness_id in harnesses:
        agent = PACKAGES["claude"] / "agents" / f"{harness_id}-shim.md"
        if harness_id != "claude":
            require(agent.is_file(), f"Claude package lacks the {harness_id}-shim agent")
            require(
                f"~/.claude/scripts/{harness_id}-shim.sh" in claude_skill,
                f"Claude package lacks {harness_id} route",
            )
            require(
                f"~/.claude/scripts/{harness_id}-shim.sh" in copilot_skill,
                f"Copilot package lacks {harness_id} route",
            )
        if harness_id != "codex":
            require(
                f"~/.claude/scripts/{harness_id}-shim.sh" in codex_skill,
                f"Codex package lacks {harness_id} route",
            )
    require(
        (PACKAGES["claude"] / "agents" / "route-shim.md").is_file(),
        "Claude package lacks the route-shim agent",
    )
    require(
        (PACKAGES["claude"] / "agents" / "agy-shim.md").is_file(),
        "Claude package lacks the agy-shim agent",
    )
    for host, text in (
        ("claude", claude_skill),
        ("codex", codex_skill),
        ("copilot", copilot_skill),
    ):
        require(
            "~/.claude/scripts/route-shim.sh" in text,
            f"{host} package lacks the route-shim route",
        )
        require(
            "~/.claude/scripts/agy-shim.sh" in text,
            f"{host} package lacks the agy-shim route",
        )
        require(
            "pitwall agents profiles list" in text,
            f"{host} package does not document profiles list",
        )
        require(
            "pitwall agents harnesses" in text,
            f"{host} package does not document the harnesses inventory",
        )

    mythos_paths = [
        path
        for root in (ROOT / "docs/prompting", ROOT / "plugins")
        for path in root.rglob("*")
        if re.search("mythos", path.name, re.I)
    ]
    require(not mythos_paths, f"Mythos-specific surfaces are forbidden: {mythos_paths}")
    print("all plugin structures and host-native boundaries are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
