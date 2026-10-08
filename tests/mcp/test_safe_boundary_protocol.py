"""Boundary protocol behavior: unknown tool, argument feedback, rate limits (F03, F08, F09)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel

from pitwall.mcp import safe_boundary
from pitwall.mcp.safe_boundary import install_safe_call_boundary
from tests.mcp.conftest import safe_call_tool_for

pytestmark = pytest.mark.anyio


class _Strict(BaseModel):
    count: int


def _server() -> MCPServer[Any]:
    server: MCPServer[Any] = MCPServer("t")

    def fit(model: str, ttl_minutes: int = 120) -> dict[str, Any]:
        return {"model": model, "ttl_minutes": ttl_minutes}

    def boom() -> dict[str, Any]:
        raise MCPError(code=-31002, message="spent", data={"error": "budget_exhausted"})

    def inner_validation() -> dict[str, Any]:
        _Strict.model_validate({"count": "INNER-SECRET"})
        return {}

    server.add_tool(fit, name="fit", description="fit")
    server.add_tool(boom, name="boom", description="boom")
    server.add_tool(inner_validation, name="inner_validation", description="inner")
    install_safe_call_boundary(server)
    return server


async def test_unknown_tool_is_a_jsonrpc_invalid_params_error() -> None:
    with pytest.raises(MCPError) as raised:
        await safe_call_tool_for(_server(), "no_such_tool", {})
    assert raised.value.error.code == -32602
    assert raised.value.error.data == {"error": "unknown_tool"}
    assert "no_such_tool" not in raised.value.error.message


async def test_validation_failure_names_declared_fields_without_values() -> None:
    result = await safe_call_tool_for(_server(), "fit", {"model": {"nested": "SECRET"}})
    assert result.is_error is True
    assert result.structured_content == {"error": "invalid_tool_arguments", "fields": ["model"]}
    assert "SECRET" not in result.content[0].text


async def test_undeclared_argument_lists_allowed_names_only() -> None:
    result = await safe_call_tool_for(_server(), "fit", {"model": "m", "evil key": 1})
    assert result.structured_content == {
        "error": "invalid_tool_arguments",
        "allowed": ["model", "ttl_minutes"],
    }
    assert "evil key" not in result.content[0].text


async def test_mcp_error_from_tool_becomes_tool_result() -> None:
    result = await safe_call_tool_for(_server(), "boom", {})
    assert result.is_error is True
    assert result.structured_content == {"error": "budget_exhausted"}


async def test_validation_error_inside_tool_body_is_an_execution_failure() -> None:
    # A pydantic failure the tool itself raises is a crash, not the caller's bad arguments.
    result = await safe_call_tool_for(_server(), "inner_validation", {})
    assert result.is_error is True
    assert result.structured_content == {"error": "tool_execution_failed"}
    assert "INNER-SECRET" not in result.content[0].text


async def test_burst_beyond_bucket_is_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_BURST", 3)
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_PER_SECOND", 0.001)
    server = _server()
    for _ in range(3):
        ok = await safe_call_tool_for(server, "fit", {"model": "m"})
        assert ok.is_error is False
    limited = await safe_call_tool_for(server, "fit", {"model": "m"})
    assert limited.is_error is True
    assert limited.structured_content["error"] == "rate_limited"
    assert limited.structured_content["retry_after_s"] > 0


async def test_refused_calls_draw_from_the_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_BURST", 3)
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_PER_SECOND", 0.001)
    server = _server()
    with pytest.raises(MCPError):
        await safe_call_tool_for(server, "no_such_tool", {})
    undeclared = await safe_call_tool_for(server, "fit", {"model": "m", "evil": 1})
    assert undeclared.structured_content["error"] == "invalid_tool_arguments"
    ok = await safe_call_tool_for(server, "fit", {"model": "m"})
    assert ok.is_error is False
    # Three calls (one unknown, one undeclared, one good) emptied the bucket of 3.
    for name, arguments in (
        ("fit", {"model": "m"}),
        ("fit", {"model": "m", "evil": 1}),
        ("no_such_tool", {}),
    ):
        limited = await safe_call_tool_for(server, name, arguments)
        assert limited.structured_content["error"] == "rate_limited"


def test_wait_is_never_rounded_down_to_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    # A refill rate so fast that the exact wait rounds to 0.0 must still answer 0.001, because a
    # caller reads 0.0 as "proceed".
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_BURST", 1)
    monkeypatch.setattr(safe_boundary, "RATE_LIMIT_PER_SECOND", 1_000_000.0)
    monkeypatch.setattr(safe_boundary, "time", SimpleNamespace(monotonic=lambda: 100.0))
    limiter = safe_boundary._RateLimiter()
    assert limiter.acquire() == 0.0
    assert limiter.acquire() == 0.001
