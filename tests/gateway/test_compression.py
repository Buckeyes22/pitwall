"""Compression cases ported from the Node gateway tests (compression.test.ts, hardening.test.ts)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from pitwall.gateway.compression import CompressionPolicy, compress_request

SYSTEM_PROMPT = "\n".join(
    [
        "You are a careful assistant.",
        "You are a careful assistant.",
        "Rules:",
        "- cite sources",
        "- cite sources",
        "Example:",
        "```",
        "print('a')",
        "```",
        "Second example:",
        "```",
        "print('b')",
        "```",
        "Checklist:",
        "- cite sources",
        "- never guess",
    ]
)


def _policy(name: str) -> CompressionPolicy:
    return cast(CompressionPolicy, name)


@pytest.mark.parametrize("policy", ["rtk", "caveman", "stacked"])
def test_dedup_removes_only_consecutive_duplicate_lines(policy: str) -> None:
    # Source: compression.test.ts "dedupSystemPrompt (R12) > <policy>: removes only consecutive duplicate lines"
    out = compress_request(
        {"messages": [{"role": "system", "content": SYSTEM_PROMPT}]}, _policy(policy)
    )
    lines = out["messages"][0]["content"].split("\n")
    assert lines.count("You are a careful assistant.") == 1
    assert lines[:4] == ["You are a careful assistant.", "Rules:", "- cite sources", "Example:"]
    assert lines.count("```") == 4
    assert "print('a')" in lines
    assert "print('b')" in lines
    assert lines.count("- cite sources") == 2
    assert lines[-1] == "- never guess"


def test_policy_off_is_a_no_op() -> None:
    # Source: hardening.test.ts "translation + compression > compression policy off is a no-op"
    body = {"messages": [{"role": "user", "content": "hello   "}]}
    assert compress_request(body, "off") is body


def test_policy_rtk_collapses_whitespace() -> None:
    # Source: hardening.test.ts "translation + compression > compression policy rtk collapses whitespace"
    body = {"messages": [{"role": "user", "content": "hello   \n\n\n\n  world"}]}
    out = compress_request(body, "rtk")
    assert out["messages"][0]["content"] == "hello\n\nworld"
    # The caller's body is not mutated.
    assert body["messages"][0]["content"] == "hello   \n\n\n\n  world"


@pytest.mark.parametrize("policy", ["off", "rtk", "caveman", "stacked"])
def test_compression_applies_only_documented_lite_normalization(policy: str) -> None:
    # Source: hardening.test.ts "inbound shape projections > compression %s over HTTP applies only the lite normalization it documents"
    body = {
        "model": "fixture",
        "messages": [
            {"role": "system", "content": "rule one\nrule one\nrule two"},
            {"role": "user", "content": "hello   \n\n\n\n  world"},
        ],
    }
    out = compress_request(body, _policy(policy))
    if policy == "off":
        assert out["messages"][0]["content"] == "rule one\nrule one\nrule two"
        assert out["messages"][1]["content"] == "hello   \n\n\n\n  world"
    else:
        assert out["messages"][0]["content"] == "rule one\nrule two"
        assert out["messages"][1]["content"] == "hello\n\nworld"


def test_preserves_null_content_tool_calls_and_vendor_fields() -> None:
    # Source: hardening.test.ts "request-shape boundary > preserves content:null, tool calls, and vendor fields through compression"
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }
    body: dict[str, Any] = {
        "model": "fixture",
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [tool_call]},
            {"role": "user", "content": "hello   \n\n\n\n  world"},
        ],
        "response_format": {"type": "json_object"},
        "metadata": {"trace": "vendor-extra"},
    }
    out = compress_request(body, "rtk")
    assert out["messages"][0] == {"role": "assistant", "content": None, "tool_calls": [tool_call]}
    assert out["messages"][1]["content"] == "hello\n\nworld"
    assert out["response_format"] == {"type": "json_object"}
    assert out["metadata"] == {"trace": "vendor-extra"}


def test_unchanged_body_is_returned_as_is() -> None:
    # Source: compression.ts applyLiteCompression "!mutated" branch (applied: false)
    body = {"messages": [{"role": "user", "content": "already tidy"}]}
    assert compress_request(body, "stacked") is body


def test_malformed_messages_do_not_raise() -> None:
    # Source: hardening.test.ts "request-shape boundary" (compression must not dereference bad shapes)
    assert compress_request({"model": "m"}, "rtk") == {"model": "m"}
    body: dict[str, Any] = {
        "messages": ["not an object", None, {"role": "user", "content": "a   \n\n\n\nb"}]
    }
    out = compress_request(body, "rtk")
    assert out["messages"][:2] == ["not an object", None]
    assert out["messages"][2]["content"] == "a\n\nb"
    assert compress_request({"messages": "nope"}, "rtk") == {"messages": "nope"}
