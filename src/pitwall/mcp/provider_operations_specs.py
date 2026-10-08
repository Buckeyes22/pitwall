"""Feature-local MCP registration metadata for provider operations.

The canonical registry is a serialized hotspot.  Its integrator adapts these
entries one-for-one into ``ToolSpec`` without importing this module back from
the handlers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pitwall.mcp.tools.provider_operations import (
    pitwall_provider_ops_availability,
    pitwall_provider_ops_describe,
    pitwall_provider_ops_health,
    pitwall_provider_ops_list_descriptors,
)


@dataclass(frozen=True, slots=True)
class ProviderOperationsToolSpec:
    """A registry-compatible, feature-local MCP tool declaration."""

    name: str
    description: str
    handler: Callable[..., dict[str, Any]] | Callable[..., Awaitable[dict[str, Any]]]


PROVIDER_OPERATIONS_TOOL_SPECS: tuple[ProviderOperationsToolSpec, ...] = (
    ProviderOperationsToolSpec(
        name="pitwall_provider_ops_list_descriptors",
        description="List safe persisted provider descriptors without provider-network egress.",
        handler=pitwall_provider_ops_list_descriptors,
    ),
    ProviderOperationsToolSpec(
        name="pitwall_provider_ops_describe",
        description="Describe one safe persisted provider configuration without provider-network egress.",
        handler=pitwall_provider_ops_describe,
    ),
    ProviderOperationsToolSpec(
        name="pitwall_provider_ops_availability",
        description="Perform one explicit, bounded read-only provider availability probe.",
        handler=pitwall_provider_ops_availability,
    ),
    ProviderOperationsToolSpec(
        name="pitwall_provider_ops_health",
        description="Read persisted provider health, optionally with one explicit bounded live probe.",
        handler=pitwall_provider_ops_health,
    ),
)


__all__ = ["PROVIDER_OPERATIONS_TOOL_SPECS", "ProviderOperationsToolSpec"]
