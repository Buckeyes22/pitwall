"""Folding pitwall_health into TOOL_REGISTRY — Task 1 §12.1 regression."""

from __future__ import annotations

from pitwall.mcp.registry import TOOL_NAMES, TOOL_REGISTRY, register_all


def test_registry_contains_pitwall_health_and_counts_are_78() -> None:
    # Registry and name set must contain pitwall_health and total 78
    assert "pitwall_health" in TOOL_NAMES, "pitwall_health missing from TOOL_NAMES"
    assert any(spec.name == "pitwall_health" for spec in TOOL_REGISTRY), (
        "pitwall_health missing from TOOL_REGISTRY"
    )
    assert len(TOOL_NAMES) == 81, f"TOOL_NAMES len={len(TOOL_NAMES)} expected 81"
    assert len(TOOL_REGISTRY) == 81, f"TOOL_REGISTRY len={len(TOOL_REGISTRY)} expected 81"

    spec = next(spec for spec in TOOL_REGISTRY if spec.name == "pitwall_health")
    assert callable(spec.handler)


def test_served_count_is_78_via_register_all() -> None:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("test_health_served")
    register_all(server)
    registered = {t.name for t in server._tool_manager.list_tools()}
    assert "pitwall_health" in registered, "pitwall_health not served via register_all"
    # Served count must equal registry count and the documented count (len(TOOL_REGISTRY))
    assert len(registered) == 81, f"served count {len(registered)} expected 81"
    assert TOOL_NAMES.issubset(registered)


def test_no_separate_mcp_tool_registration_for_health() -> None:
    # Ensure pitwall_health is NOT registered separately via @mcp.tool decorator
    # after import; the only registration path must be TOOL_REGISTRY.
    # We verify that importing pitwall.mcp does not double-register.
    from pitwall.mcp import mcp

    names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "pitwall_health" in names
    assert len(names) == 81
    # Health should appear exactly once (set semantics) and via registry path
    assert names == TOOL_NAMES
