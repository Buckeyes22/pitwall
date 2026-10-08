"""The "Admin-only" MCP tools must not claim an enforcement the server does not have.

Any local process that can start the stdio server has full access; there is no per-tool scope.
"""

from __future__ import annotations

import dataclasses
import re

from pitwall.mcp.registry import TOOL_REGISTRY, ToolSpec

_ADMIN_TOOLS = {
    "pitwall_create_capability",
    "pitwall_update_capability",
    "pitwall_create_provider",
    "pitwall_update_provider",
    "pitwall_disable_provider",
    "pitwall_hibernate_provider",
}
_ENFORCEMENT_CLAIM = re.compile(r"admin[- ]only|requires admin|admin scope", re.IGNORECASE)


def test_no_tool_claims_enforcement() -> None:
    assert "scope" not in {field.name for field in dataclasses.fields(ToolSpec)}
    by_name = {spec.name: spec for spec in TOOL_REGISTRY}
    for name in _ADMIN_TOOLS:
        description = by_name[name].description
        assert "any local process that can start the stdio server has full access" in (
            description.lower()
        ), name
    for spec in TOOL_REGISTRY:
        assert not _ENFORCEMENT_CLAIM.search(spec.description), spec.name
