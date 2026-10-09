"""The documented tool count is a test, not an import-time assertion."""

from __future__ import annotations

from pitwall.mcp.registry import TOOL_NAMES, TOOL_REGISTRY
from tests.mcp.test_registry_health import EXPECTED_TOOL_COUNT as DOCUMENTED_TOOL_COUNT


def test_registry_has_documented_tool_count() -> None:
    names = [spec.name for spec in TOOL_REGISTRY]
    assert len(names) == len(set(names)) == DOCUMENTED_TOOL_COUNT
    assert set(names) == TOOL_NAMES
    assert len(TOOL_NAMES) == DOCUMENTED_TOOL_COUNT
