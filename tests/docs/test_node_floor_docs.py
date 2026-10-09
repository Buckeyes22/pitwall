"""Every user-facing statement of the Node floor names the one the workbench doctor enforces."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pitwall.workbench.doctor import MIN_NODE

ROOT = Path(__file__).resolve().parents[2]
_FLOOR = re.compile(r"Node(?:\.js)? (\d+\.\d+\.\d+) or (?:later|newer)")
_HISTORICAL = ("docs/superpowers/", "docs/evidence/", "CHANGELOG.md")


def _tracked_text_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "*.md", "*.json"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    return [path for path in listed if not path.startswith(_HISTORICAL)]


def test_every_node_floor_statement_matches_min_node() -> None:
    stale = []
    for path in _tracked_text_files():
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        stale += [f"{path}: Node {found}" for found in _FLOOR.findall(text) if found != MIN_NODE]
    assert not stale, f"update these to Node {MIN_NODE}: {stale}"


def test_the_floor_statement_pattern_finds_the_known_docs() -> None:
    found = [
        p
        for p in _tracked_text_files()
        if _FLOOR.search((ROOT / p).read_text(encoding="utf-8", errors="replace"))
    ]
    assert "CONTRIBUTING.md" in found
    assert "docs/operator/pi-workbench.md" in found
