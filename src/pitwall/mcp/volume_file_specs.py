"""Feature-local MCP registration metadata for RP-04.

This intentionally does not import the serialized global registry's ``ToolSpec``
to avoid an import cycle.  The integrator adapts these entries one-for-one when
adding the tools to the canonical registry.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pitwall.mcp.tools.volume_files import (
    pitwall_pod_logs,
    pitwall_volume_delete_object,
    pitwall_volume_list_objects,
    pitwall_volume_read_chunk,
    pitwall_volume_upload_object,
)


@dataclass(frozen=True, slots=True)
class VolumeFileToolSpec:
    """A registry-compatible feature-local MCP tool declaration."""

    name: str
    description: str
    handler: Callable[..., dict[str, Any]] | Callable[..., Awaitable[dict[str, Any]]]


VOLUME_FILE_TOOL_SPECS: tuple[VolumeFileToolSpec, ...] = (
    VolumeFileToolSpec(
        name="pitwall_volume_list_objects",
        description="List one bounded page of RunPod network-volume objects.",
        handler=pitwall_volume_list_objects,
    ),
    VolumeFileToolSpec(
        name="pitwall_volume_read_chunk",
        description="Read one bounded base64 object chunk from a RunPod network volume.",
        handler=pitwall_volume_read_chunk,
    ),
    VolumeFileToolSpec(
        name="pitwall_volume_upload_object",
        description=(
            "Upload one bounded base64 object with explicit upload intent, idempotency key, "
            "and overwrite confirmation."
        ),
        handler=pitwall_volume_upload_object,
    ),
    VolumeFileToolSpec(
        name="pitwall_volume_delete_object",
        description=(
            "Delete one RunPod network-volume object with explicit delete intent and confirmation."
        ),
        handler=pitwall_volume_delete_object,
    ),
    VolumeFileToolSpec(
        name="pitwall_pod_logs",
        description="Read ordered, redacted, byte- and line-bounded RunPod pod diagnostics.",
        handler=pitwall_pod_logs,
    ),
)


__all__ = ["VOLUME_FILE_TOOL_SPECS", "VolumeFileToolSpec"]
