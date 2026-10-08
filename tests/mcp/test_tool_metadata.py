"""Every broker tool carries a title and honest annotations (F05, F06)."""

from __future__ import annotations

import pytest

from pitwall.mcp.registry import TOOL_NAMES
from pitwall.mcp.tool_metadata import TOOL_METADATA

pytestmark = pytest.mark.anyio

READ_PREFIXES = ("list", "get", "describe", "status", "read", "health", "doctor", "preview")


def test_metadata_covers_exactly_the_registry() -> None:
    assert set(TOOL_METADATA) == set(TOOL_NAMES)


def test_names_that_only_read_are_read_only() -> None:
    for name, meta in TOOL_METADATA.items():
        verb = name.removeprefix("pitwall_").removeprefix("runpod_").removeprefix("provider_ops_")
        if verb.startswith(READ_PREFIXES):
            assert meta.annotations.read_only_hint is True, name


def test_deletes_and_terminations_are_destructive() -> None:
    # grow_volume cannot be undone (the control plane marks it irreversible; RunPod never
    # shrinks a volume), so it is destructive although it only adds capacity.
    words = ("delete", "terminate", "stop_lease", "cancel_job", "grow_volume")
    for name, meta in TOOL_METADATA.items():
        if any(word in name for word in words):
            assert meta.annotations.read_only_hint is False, name
            assert meta.annotations.destructive_hint is True, name


def test_every_hint_is_explicit() -> None:
    for name, meta in TOOL_METADATA.items():
        a = meta.annotations
        assert None not in (a.read_only_hint, a.idempotent_hint, a.open_world_hint), name
        if a.read_only_hint is False:
            assert a.destructive_hint is not None, name
        assert meta.title and meta.title == meta.annotations.title, name


async def test_listed_tools_carry_title_and_annotations() -> None:
    from pitwall.mcp import mcp

    tools = await mcp.list_tools()
    assert len(tools) == 81
    for tool in tools:
        assert tool.title == TOOL_METADATA[tool.name].title
        assert tool.annotations == TOOL_METADATA[tool.name].annotations
