"""Every broker tool parameter has a model-facing description (F06)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_every_parameter_is_described() -> None:
    from pitwall.mcp import mcp

    missing = [
        f"{tool.name}.{name}"
        for tool in await mcp.list_tools()
        for name, schema in tool.input_schema.get("properties", {}).items()
        if not str(schema.get("description", "")).strip()
    ]
    assert missing == []


async def test_handler_annotated_descriptions_are_not_overwritten() -> None:
    from pitwall.mcp import mcp
    from pitwall.mcp.tool_metadata import PARAMETER_OVERRIDES

    assert "ttl_minutes" not in PARAMETER_OVERRIDES.get("pitwall_serve_model", {})
    tool = next(t for t in await mcp.list_tools() if t.name == "pitwall_serve_model")
    assert (
        tool.input_schema["properties"]["ttl_minutes"]["description"]
        == "Lease duration used for expiry and cost estimates."
    )
