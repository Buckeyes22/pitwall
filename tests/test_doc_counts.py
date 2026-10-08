"""Documented surface counts follow the code."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from pitwall.audit import checks
from pitwall.mcp.registry import TOOL_REGISTRY

ROOT = Path(__file__).resolve().parents[1]


_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations(spec: dict[str, object]) -> set[str]:
    paths = spec["paths"]
    assert isinstance(paths, dict)
    return {
        f"{method} {path}" for path, item in paths.items() for method in item if method in _METHODS
    }


def _live_operations(tmp_path: Path) -> set[str]:
    out = tmp_path / "openapi.json"
    subprocess.run(
        [sys.executable, "tools/ci/export_openapi.py", "--output", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return _operations(json.loads(out.read_text(encoding="utf-8")))


def test_openapi_baseline_lists_every_live_operation(tmp_path: Path) -> None:
    baseline = _operations(
        json.loads((ROOT / "docs/api/openapi-baseline.json").read_text(encoding="utf-8"))
    )
    assert sorted(_live_operations(tmp_path) - baseline) == []


def test_testing_strategy_states_the_live_operation_count(tmp_path: Path) -> None:
    text = " ".join((ROOT / "docs/sdlc/17-testing-strategy.md").read_text(encoding="utf-8").split())
    assert f"all {len(_live_operations(tmp_path))} current operations" in text


def test_overview_states_the_audit_check_count() -> None:
    count = len([name for name in dir(checks) if re.match(r"check_\d{2}_", name)])
    text = (ROOT / "docs/sdlc/00-overview.md").read_text(encoding="utf-8")
    for match in re.finditer(r"(\d+)-(?:point|check) readiness audit", text):
        assert int(match.group(1)) == count, match.group(0)


def test_changelog_does_not_state_stale_mcp_tool_counts() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for match in re.finditer(r"(\d+) MCP tools", text):
        assert int(match.group(1)) == len(TOOL_REGISTRY), match.group(0)
