"""Feature-local MCP contracts for MC-01 provider operator reads."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import MCPError

from pitwall.mcp import provider_operations_specs
from pitwall.mcp.tools import provider_operations
from tests.providers._provider_operations import (
    StubProviderOperationsService,
    availability,
    descriptor,
    health,
)

pytestmark = pytest.mark.anyio


async def test_mcp_provider_operations_share_the_service_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = StubProviderOperationsService()
    factory = AsyncMock(return_value=service)
    monkeypatch.setattr(provider_operations, "get_provider_operations_service", factory)

    listing = await provider_operations.pitwall_provider_ops_list_descriptors(
        capability_id="cap-gpu", enabled_only=True, limit=7
    )
    described = await provider_operations.pitwall_provider_ops_describe("prov-vast")
    observed = await provider_operations.pitwall_provider_ops_availability("prov-vast", limit=3)
    probed = await provider_operations.pitwall_provider_ops_health("prov-vast", probe=True)

    assert listing == {"items": [descriptor().as_dict()], "total": 1}
    assert described == descriptor().as_dict()
    assert observed == availability().as_dict()
    assert probed == health().as_dict()
    assert service.calls == [
        ("list", "cap-gpu", True, 7),
        ("describe", "prov-vast"),
        ("availability", "prov-vast", 3, None),
        ("health", "prov-vast", True, None),
    ]
    assert factory.await_count == 4


async def test_mcp_provider_operations_rejects_hostile_input_without_service_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = AsyncMock()
    monkeypatch.setattr(provider_operations, "get_provider_operations_service", factory)

    with pytest.raises(MCPError) as invalid:
        await provider_operations.pitwall_provider_ops_availability("prov-vast", limit=101)

    assert invalid.value.error.data == {"error": "provider_operations_invalid_request"}
    assert factory.await_count == 0


async def test_mcp_provider_operations_redacts_unexpected_service_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_canary = "mcp-provider-credential-canary"
    monkeypatch.setattr(
        provider_operations,
        "get_provider_operations_service",
        AsyncMock(side_effect=RuntimeError(credential_canary)),
    )

    with pytest.raises(MCPError) as unavailable:
        await provider_operations.pitwall_provider_ops_health("prov-vast", probe=True)

    assert unavailable.value.error.data == {"error": "provider_operations_unavailable"}
    assert credential_canary not in unavailable.value.error.message
    assert credential_canary not in repr(unavailable.value.error.data)


async def test_mcp_provider_operations_unknown_provider_has_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        provider_operations,
        "get_provider_operations_service",
        AsyncMock(return_value=StubProviderOperationsService()),
    )

    with pytest.raises(MCPError) as missing:
        await provider_operations.pitwall_provider_ops_describe("missing")

    assert missing.value.error.data == {"error": "provider_not_found", "id": "missing"}


def test_registered_specs_cover_every_mc01_mcp_operation() -> None:
    assert [spec.name for spec in provider_operations_specs.PROVIDER_OPERATIONS_TOOL_SPECS] == [
        "pitwall_provider_ops_list_descriptors",
        "pitwall_provider_ops_describe",
        "pitwall_provider_ops_availability",
        "pitwall_provider_ops_health",
    ]


def test_feature_local_specs_generate_bounded_credential_free_mcp_schemas() -> None:
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("provider-ops-contract")
    for spec in provider_operations_specs.PROVIDER_OPERATIONS_TOOL_SPECS:
        server.tool(name=spec.name, description=spec.description)(spec.handler)
    registered = {tool.name: tool for tool in server._tool_manager.list_tools()}

    availability_schema = registered["pitwall_provider_ops_availability"].parameters
    assert availability_schema["properties"]["limit"] == {
        "default": 100,
        "description": "Bounded provider or availability result limit.",
        "maximum": 100,
        "minimum": 1,
        "title": "Limit",
        "type": "integer",
    }
    rendered = json.dumps({name: tool.parameters for name, tool in registered.items()}).lower()
    assert "api_key" not in rendered
    assert "authorization" not in rendered
