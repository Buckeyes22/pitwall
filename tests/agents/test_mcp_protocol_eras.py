"""The channel serves 2026-07-28 statelessly and the legacy handshake (F02, F13)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.agents.mcp_test_client import McpTestClient
from tests.agents.shim_test_support import PITWALL
from tests.hang_guard import HANG_GUARD_SECS

META: dict[str, Any] = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "0"},
}


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[McpTestClient]:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    env["PITWALL_AGENTS_STATE_HOME"] = str(tmp_path)
    c = McpTestClient(env)
    try:
        yield c
    finally:
        c.close()


def test_channel_discover(client: McpTestClient) -> None:
    result = client.request("server/discover", {"_meta": META})["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28"]
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall-channel"
    assert result["ttlMs"] == 3_600_000 and result["cacheScope"] == "public"
    assert result["instructions"]


def test_channel_modern_tools_list_and_call_without_initialize(client: McpTestClient) -> None:
    listed = client.request("tools/list", {"_meta": META})["result"]
    assert listed["resultType"] == "complete"
    assert listed["ttlMs"] == 3_600_000 and listed["cacheScope"] == "public"
    assert {t["name"] for t in listed["tools"]} >= {"inbox", "dispatch_and_wait"}
    called = client.call("inbox", {}, meta=META)["result"]
    assert called["resultType"] == "complete" and called["isError"] is False
    assert called["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall-channel"


def test_channel_unsupported_version_is_rejected(client: McpTestClient) -> None:
    meta = {**META, "io.modelcontextprotocol/protocolVersion": "1900-01-01"}
    error = client.request("tools/list", {"_meta": meta})["error"]
    assert error["code"] == -32022
    assert error["data"] == {"supported": ["2026-07-28"], "requested": "1900-01-01"}


def test_channel_modern_request_without_capabilities_is_invalid(client: McpTestClient) -> None:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
    assert client.request("tools/list", {"_meta": meta})["error"]["code"] == -32602


def test_channel_legacy_handshake_unchanged(client: McpTestClient) -> None:
    reply = client.initialize("2025-06-18")["result"]
    assert reply["protocolVersion"] == "2025-06-18"
    assert "resultType" not in reply
    listed = client.request("tools/list")["result"]
    assert "resultType" not in listed and "ttlMs" not in listed


def test_channel_listen_acknowledges_an_empty_filter_and_closes_on_cancel(
    client: McpTestClient,
) -> None:
    listen_id = client.send(
        "subscriptions/listen", {"_meta": META, "notifications": {"toolsListChanged": True}}
    )
    client.wait_for_notification("notifications/subscriptions/acknowledged")
    ack = next(
        n for n in client.notifications if n["method"] == "notifications/subscriptions/acknowledged"
    )
    assert ack["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == listen_id
    assert ack["params"]["notifications"] == {}
    client.send("notifications/cancelled", {"requestId": listen_id}, notify=True)
    assert client.request("tools/list", {"_meta": META})["result"]["resultType"] == "complete"
    # The server completes every listen still open at end of input. A second, uncancelled
    # listen proves those completions are sent; the cancelled one must not get one.
    open_id = client.send("subscriptions/listen", {"_meta": META, "notifications": {}})
    client.finish_input()
    assert client.wait(open_id, timeout=0)["result"]["resultType"] == "complete"
    replied = [json.loads(line).get("id") for line in client.raw_lines]
    assert listen_id not in replied


def test_channel_null_id_is_invalid_request(client: McpTestClient) -> None:
    assert client.process.stdin is not None
    client.process.stdin.write(
        (json.dumps({"jsonrpc": "2.0", "id": None, "method": "tools/list"}) + "\n").encode()
    )
    client.process.stdin.flush()
    # McpTestClient._read files every response carrying an "id" key under that id, None included.
    assert client.wait(None)["error"]["code"] == -32600


def test_channel_listen_completes_gracefully_at_end_of_input(client: McpTestClient) -> None:
    listen_id = client.send("subscriptions/listen", {"_meta": META, "notifications": {}})
    client.wait_for_notification("notifications/subscriptions/acknowledged")
    assert client.process.stdin is not None
    client.process.stdin.close()
    done = client.wait(listen_id)["result"]
    assert done["resultType"] == "complete"
    assert done["_meta"]["io.modelcontextprotocol/subscriptionId"] == listen_id
    assert done["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall-channel"


def test_channel_modern_ping_is_complete(client: McpTestClient) -> None:
    result = client.request("ping", {"_meta": META})["result"]
    assert result["resultType"] == "complete"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "pitwall-channel"


def test_channel_listen_tolerates_closed_pipes_at_shutdown(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    env["PITWALL_AGENTS_STATE_HOME"] = str(tmp_path)
    process = subprocess.Popen(
        [str(PITWALL), "agents", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    listen = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "subscriptions/listen",
        "params": {"_meta": META},
    }
    process.stdin.write((json.dumps(listen) + "\n").encode())
    process.stdin.flush()
    ack = json.loads(process.stdout.readline())
    assert ack["method"] == "notifications/subscriptions/acknowledged"
    process.stdout.close()
    process.stdin.close()
    _, stderr = process.communicate(timeout=HANG_GUARD_SECS)
    assert process.returncode == 0, stderr.decode()
    assert b"Traceback" not in stderr and b"Exception ignored" not in stderr, stderr.decode()


def test_channel_unhashable_ids_are_rejected_without_killing_the_server(
    client: McpTestClient,
) -> None:
    assert client.process.stdin is not None
    for message in (
        {"jsonrpc": "2.0", "id": [1], "method": "tools/call", "params": {"name": "inbox"}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": {}}},
    ):
        client.process.stdin.write((json.dumps(message) + "\n").encode())
    client.process.stdin.flush()
    assert client.wait(None)["error"]["code"] == -32600
    assert client.request("ping", {"_meta": META})["result"]["resultType"] == "complete"


def _write_raw(client: McpTestClient, message: dict[str, Any]) -> None:
    assert client.process.stdin is not None
    client.process.stdin.write((json.dumps(message) + "\n").encode())
    client.process.stdin.flush()


def test_channel_boolean_id_is_invalid_request(client: McpTestClient) -> None:
    _write_raw(client, {"jsonrpc": "2.0", "id": True, "method": "ping"})
    reply = client.wait(None)
    assert reply["error"]["code"] == -32600
    assert reply["id"] is None


def test_channel_boolean_cancel_request_id_is_ignored(client: McpTestClient) -> None:
    listen_id = client.send("subscriptions/listen", {"_meta": META, "notifications": {}})
    assert listen_id == 1  # True == 1 in Python, so a boolean cancel would alias this listen
    client.wait_for_notification("notifications/subscriptions/acknowledged")
    _write_raw(
        client,
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": True}},
    )
    client.finish_input()
    assert client.wait(listen_id, timeout=0)["result"]["resultType"] == "complete"


@pytest.mark.parametrize("bad_id", [[1], {"a": 1}, True, 1.5])
def test_channel_invalid_envelope_replies_with_a_null_id(
    client: McpTestClient, bad_id: object
) -> None:
    _write_raw(client, {"jsonrpc": "1.0", "id": bad_id, "method": "ping"})
    reply = client.wait(None)
    assert reply["error"]["code"] == -32600
    assert reply["id"] is None
