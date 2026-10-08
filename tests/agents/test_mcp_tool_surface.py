"""Channel tool annotations, argument validation, rate limits, sanitized errors (F04, F05, F09, F16)."""

from __future__ import annotations

import io
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pitwall.agents import capability_inventory, mcp_server
from pitwall.agents import registry as registry_module
from pitwall.agents.mcp_server import ToolError
from pitwall.agents.mcp_tools import (
    ACK_STEER_TOOL,
    ANSWER_AND_WAIT_TOOL,
    ANSWER_ASK_TOOL,
    ASK_TOOL,
    DISPATCH_AND_WAIT_TOOL,
    INBOX_TOOL,
    READ_STEERING_TOOL,
    STEER_AND_WAIT_TOOL,
    WAIT_DISPATCH_TOOL,
    OrchestratorTools,
)
from tests.agents.mcp_test_client import McpTestClient

ALL_TOOLS = (
    ASK_TOOL,
    READ_STEERING_TOOL,
    ACK_STEER_TOOL,
    DISPATCH_AND_WAIT_TOOL,
    WAIT_DISPATCH_TOOL,
    ANSWER_AND_WAIT_TOOL,
    STEER_AND_WAIT_TOOL,
    INBOX_TOOL,
    ANSWER_ASK_TOOL,
)


def test_every_channel_tool_is_annotated() -> None:
    for tool in ALL_TOOLS:
        hints = tool["annotations"]
        assert set(hints) >= {"readOnlyHint", "idempotentHint", "openWorldHint"}, tool["name"]
        assert hints["title"] == tool["title"], tool["name"]
        if hints["readOnlyHint"] is False:
            assert "destructiveHint" in hints, tool["name"]
    assert INBOX_TOOL["annotations"]["readOnlyHint"] is True
    # read_steering delivers each advisory note once and wait_dispatch consumes events and writes
    # run-store files, so neither is read-only.
    assert READ_STEERING_TOOL["annotations"]["readOnlyHint"] is False
    assert READ_STEERING_TOOL["annotations"]["destructiveHint"] is False
    assert READ_STEERING_TOOL["annotations"]["idempotentHint"] is True
    assert WAIT_DISPATCH_TOOL["annotations"]["readOnlyHint"] is False
    assert WAIT_DISPATCH_TOOL["annotations"]["destructiveHint"] is False
    assert WAIT_DISPATCH_TOOL["annotations"]["idempotentHint"] is False
    # Only dispatch_and_wait launches an external harness.
    open_world = {tool["name"] for tool in ALL_TOOLS if tool["annotations"]["openWorldHint"]}
    assert open_world == {"dispatch_and_wait"}
    assert DISPATCH_AND_WAIT_TOOL["annotations"]["destructiveHint"] is True


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[McpTestClient]:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    env["PITWALL_AGENTS_STATE_HOME"] = str(tmp_path)
    c = McpTestClient(env)
    c.initialize()
    try:
        yield c
    finally:
        c.close()


def test_channel_rejects_undeclared_arguments(client: McpTestClient) -> None:
    result = client.call("inbox", {"bogus": 1})["result"]
    assert result["isError"] is True
    assert "dispatch_id" in result["content"][0]["text"]
    assert "bogus" not in result["content"][0]["text"]


def test_internal_errors_do_not_echo_exception_text(capsys: pytest.CaptureFixture[str]) -> None:
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)

    def broken(_args: dict[str, Any], _cancel: threading.Event, _progress: Any) -> dict[str, Any]:
        raise RuntimeError("SECRET-PATH /home/x")

    server.register({"name": "inbox", "inputSchema": {"type": "object", "properties": {}}}, broken)
    server._start_call(1, {"name": "inbox", "arguments": {}})
    for worker in server._workers:
        worker.join(timeout=5)
    reply = json.loads(out.getvalue().splitlines()[-1])
    assert reply["result"]["isError"] is True
    assert "SECRET" not in reply["result"]["content"][0]["text"]
    stderr = capsys.readouterr().err
    assert "tool inbox request 1 failed" in stderr
    assert "SECRET-PATH" in stderr  # the detail goes to the log, not the client


def test_channel_rate_limits_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "CALL_BURST", 2)
    monkeypatch.setattr(mcp_server, "CALLS_PER_SECOND", 0.001)
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)
    server.register(
        {"name": "inbox", "inputSchema": {"type": "object", "properties": {}}},
        lambda _a, _c, _p: {"ok": True},
    )
    for request_id in (1, 2, 3):
        server._start_call(request_id, {"name": "inbox", "arguments": {}})
        for worker in server._workers:
            worker.join(timeout=5)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["result"]["isError"] for r in replies] == [False, False, True]
    assert "rate limited" in replies[2]["result"]["content"][0]["text"]


def test_modern_era_rejections_are_complete_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "CALL_BURST", 1)
    monkeypatch.setattr(mcp_server, "CALLS_PER_SECOND", 0.001)
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)
    server.register(
        {"name": "inbox", "inputSchema": {"type": "object", "properties": {}}},
        lambda _a, _c, _p: {"ok": True},
    )
    server._start_call(1, {"name": "inbox", "arguments": {"bogus": 1}}, modern=True)
    server._start_call(2, {"name": "inbox", "arguments": {}}, modern=True)
    for worker in server._workers:  # call 2's reply is written before call 3's rejection
        worker.join(timeout=5)
    server._start_call(3, {"name": "inbox", "arguments": {}}, modern=True)
    replies = [json.loads(line)["result"] for line in out.getvalue().splitlines()]
    assert [r["isError"] for r in replies] == [True, False, True]
    assert all(r["resultType"] == "complete" for r in replies)
    assert "unknown argument" in replies[0]["content"][0]["text"]
    assert "rate limited" in replies[2]["content"][0]["text"]


@pytest.mark.parametrize("arguments", ["x", [1], 7, True])
def test_channel_rejects_non_object_arguments(arguments: Any) -> None:
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)
    ran: list[bool] = []
    server.register(
        {
            "name": "inbox",
            "inputSchema": {"type": "object", "properties": {"dispatch_id": {"type": "string"}}},
        },
        lambda _a, _c, _p: ran.append(True) or {},
    )
    server._start_call(1, {"name": "inbox", "arguments": arguments})
    for worker in server._workers:
        worker.join(timeout=5)
    result = json.loads(out.getvalue().splitlines()[-1])["result"]
    assert result["isError"] is True
    assert "dispatch_id" in result["content"][0]["text"]
    assert ran == []


def test_missing_or_null_arguments_still_run() -> None:
    out = io.BytesIO()
    server = mcp_server.ChannelServer({}, io.BytesIO(), out)
    server.register(
        {"name": "inbox", "inputSchema": {"type": "object", "properties": {}}},
        lambda _a, _c, _p: {"ok": True},
    )
    server._start_call(1, {"name": "inbox"})
    server._start_call(2, {"name": "inbox", "arguments": None})
    for worker in server._workers:
        worker.join(timeout=5)
    results = [json.loads(line)["result"] for line in out.getvalue().splitlines()]
    assert [r["isError"] for r in results] == [False, False]


def _tools(tmp_path: Path) -> OrchestratorTools:
    return OrchestratorTools({"HOME": str(tmp_path)})


def test_harness_validation_does_not_echo_unexpected_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> dict[str, Any]:
        raise OSError("/secret/path/registry.json")

    monkeypatch.setattr(registry_module, "load_registry", broken)
    with pytest.raises(ToolError) as caught:
        _tools(tmp_path)._harness_for_request({"provider": "codex"})
    assert "/secret/path" not in str(caught.value)
    assert "'codex'" in str(caught.value)  # the caller's own requested name stays
    stderr = capsys.readouterr().err
    assert "/secret/path" in stderr
    assert "'codex'" in stderr


def test_route_resolution_does_not_echo_unexpected_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> dict[str, Any]:
        raise OSError("/secret/path/registry.json")

    monkeypatch.setattr(registry_module, "load_registry", broken)
    with pytest.raises(ToolError) as caught:
        _tools(tmp_path)._harness_for_request({"route": "anything"})
    assert "/secret/path" not in str(caught.value)
    assert "'anything'" in str(caught.value)
    stderr = capsys.readouterr().err
    assert "/secret/path" in stderr
    assert "'anything'" in stderr


def test_channel_capability_check_does_not_echo_unexpected_exception_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(*_args: Any) -> bool:
        raise OSError("/secret/path/config.toml")

    tools = _tools(tmp_path)
    monkeypatch.setattr(tools, "_harness_for_request", lambda _args: "codex")
    monkeypatch.setattr(capability_inventory, "mcp_channel_registered", broken)
    with pytest.raises(ToolError) as caught:
        tools._require_child_channel({"provider": "codex"})
    assert "/secret/path" not in str(caught.value)
    assert "'codex'" in str(caught.value)
    stderr = capsys.readouterr().err
    assert "/secret/path" in stderr
    assert "'codex'" in stderr


def test_harness_validation_keeps_pitwall_validation_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def invalid() -> dict[str, Any]:
        raise registry_module.RegistryError("harness registry field is invalid")

    monkeypatch.setattr(registry_module, "load_registry", invalid)
    with pytest.raises(ToolError, match="harness registry field is invalid"):
        _tools(tmp_path)._harness_for_request({"provider": "codex"})
    with pytest.raises(ToolError, match="harness registry field is invalid"):
        _tools(tmp_path)._harness_for_request({"route": "anything"})
