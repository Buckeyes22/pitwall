"""The broker serves both MCP eras: 2026-07-28 stateless and the legacy handshake (F01)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.mcp.raw_stdio import MODERN_META, RawStdio


@pytest.fixture()
def broker() -> Iterator[RawStdio]:
    client = RawStdio.start()
    try:
        yield client
    finally:
        client.close()


def test_server_discover_advertises_modern_version(broker: RawStdio) -> None:
    reply = broker.request("server/discover", {"_meta": MODERN_META})
    result = reply["result"]
    assert result["resultType"] == "complete"
    assert "2026-07-28" in result["supportedVersions"]
    assert "tools" in result["capabilities"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall"
    assert isinstance(result["ttlMs"], int) and result["ttlMs"] >= 0
    assert result["cacheScope"] in {"public", "private"}


def test_modern_tools_list_without_initialize(broker: RawStdio) -> None:
    reply = broker.request("tools/list", {"_meta": MODERN_META})
    result = reply["result"]
    assert result["resultType"] == "complete"
    assert any(tool["name"] == "pitwall_health" for tool in result["tools"])
    assert result["ttlMs"] >= 0 and result["cacheScope"] in {"public", "private"}


def test_modern_unsupported_version_is_rejected(broker: RawStdio) -> None:
    meta = {**MODERN_META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    reply = broker.request("tools/list", {"_meta": meta})
    assert reply["error"]["code"] == -32022
    assert "2026-07-28" in reply["error"]["data"]["supported"]
    assert reply["error"]["data"]["requested"] == "1900-01-01"


def test_modern_missing_client_capabilities_is_invalid_params(broker: RawStdio) -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    reply = broker.request("tools/list", {"_meta": meta})
    assert reply["error"]["code"] == -32602


def test_modern_tools_call_without_initialize(broker: RawStdio) -> None:
    called = broker.request(
        "tools/call", {"_meta": MODERN_META, "name": "pitwall_health", "arguments": {}}
    )["result"]
    assert called["resultType"] == "complete"
    assert called["isError"] is False
    assert set(called["structuredContent"]) == {"ok", "database", "redis", "provider_registry"}

    refused = broker.request(
        "tools/call", {"_meta": MODERN_META, "name": "pitwall_health", "arguments": {"evil": 1}}
    )["result"]
    assert refused["resultType"] == "complete"
    assert refused["isError"] is True
    assert refused["structuredContent"] == {"error": "invalid_tool_arguments", "allowed": []}

    unknown = broker.request(
        "tools/call", {"_meta": MODERN_META, "name": "no_such_tool", "arguments": {}}
    )
    assert unknown["error"]["code"] == -32602
    assert unknown["error"]["data"] == {"error": "unknown_tool"}


def test_legacy_initialize_negotiates_handshake_version(broker: RawStdio) -> None:
    reply = broker.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "legacy", "version": "0"},
        },
    )
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    broker.notify("notifications/initialized")
    listed = broker.request("tools/list")
    assert any(tool["name"] == "pitwall_health" for tool in listed["result"]["tools"])
