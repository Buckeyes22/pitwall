"""Request-shape boundary cases ported from hardening.test.ts "request-shape boundary"."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.gateway.test_app_support import AUTH, JSON_HEADERS, running_gateway

POLICIES = ["off", "rtk", "caveman", "stacked"]


def assert_structured_400(response: httpx.Response) -> None:
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["request_id"] == response.headers["x-request-id"]


@pytest.mark.parity
@pytest.mark.parametrize("policy", POLICIES)
async def test_malformed_messages_object_under_each_policy_is_a_structured_400(
    policy: str,
) -> None:
    # Source: hardening.test.ts "request-shape boundary > malformed messages object under policy %s is a structured 400 with zero upstream calls"
    async with running_gateway() as gateway:
        response = await gateway.chat(
            {"model": "fixture", "messages": {}}, **{"x-pitwall-compression": policy}
        )
    assert_structured_400(response)
    assert gateway.upstream.captured == []


MALFORMED_ROOTS: list[tuple[str, Any]] = [
    ("null", None),
    ("array", []),
    ("array with entries", [{"model": "fixture"}]),
    ("string", "fixture"),
    ("number", 42),
    ("boolean", True),
]


@pytest.mark.parity
@pytest.mark.parametrize(("label", "root"), MALFORMED_ROOTS, ids=[c[0] for c in MALFORMED_ROOTS])
async def test_top_level_json_that_is_not_an_object_is_a_structured_400(
    label: str, root: Any
) -> None:
    # Source: hardening.test.ts "request-shape boundary > top-level JSON %s is a structured 400 with zero upstream calls"
    async with running_gateway() as gateway:
        response = await gateway.chat(root, **{"x-pitwall-compression": "rtk"})
    assert_structured_400(response)
    assert gateway.upstream.captured == []


MALFORMED_MESSAGES: list[tuple[str, Any]] = [
    ("object", {}),
    ("string", "not-an-array"),
    ("null entry", [None]),
    ("array entry", [["nested"]]),
    ("string entry", ["user"]),
    ("number entry", [7]),
]


@pytest.mark.parity
@pytest.mark.parametrize(
    ("label", "messages"), MALFORMED_MESSAGES, ids=[c[0] for c in MALFORMED_MESSAGES]
)
async def test_malformed_messages_are_a_structured_400_with_zero_upstream_calls(
    label: str, messages: Any
) -> None:
    # Source: hardening.test.ts "request-shape boundary > messages %s is a structured 400 with zero upstream calls"
    async with running_gateway() as gateway:
        response = await gateway.chat(
            {"model": "fixture", "messages": messages}, **{"x-pitwall-compression": "stacked"}
        )
    assert_structured_400(response)
    assert gateway.upstream.captured == []


@pytest.mark.parity
async def test_embeddings_top_level_json_null_is_a_structured_400() -> None:
    # Source: hardening.test.ts "request-shape boundary > embeddings top-level JSON null is a structured 400 with zero upstream calls"
    async with running_gateway() as gateway:
        response = await gateway.http.post(
            "/v1/embeddings", content="null", headers={**AUTH, **JSON_HEADERS}
        )
    assert_structured_400(response)
    assert gateway.upstream.captured == []


@pytest.mark.parity
async def test_preserves_null_content_tool_calls_and_vendor_fields_through_compression() -> None:
    # Source: hardening.test.ts "request-shape boundary > preserves content:null, tool calls, and vendor fields through compression"
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }
    async with running_gateway() as gateway:
        response = await gateway.chat(
            {
                "model": "fixture",
                "messages": [
                    {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                    {"role": "user", "content": "hello   \n\n\n\n  world"},
                ],
                "response_format": {"type": "json_object"},
                "metadata": {"trace": "vendor-extra"},
            },
            **{"x-pitwall-compression": "rtk"},
        )
    assert response.status_code == 200
    outbound = gateway.upstream.last_body()
    assert outbound["messages"][0] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [tool_call],
    }
    assert outbound["messages"][1]["content"] == "hello\n\nworld"
    assert outbound["response_format"] == {"type": "json_object"}
    assert outbound["metadata"] == {"trace": "vendor-extra"}


@pytest.mark.parametrize("body", ["{not json", "", "\xff\xfe", "[1, 2"])
async def test_unparseable_json_is_a_structured_400(body: str) -> None:
    async with running_gateway() as gateway:
        response = await gateway.http.post(
            "/v1/chat/completions",
            content=body.encode("latin-1"),
            headers={**AUTH, **JSON_HEADERS},
        )
    assert_structured_400(response)
    assert gateway.upstream.captured == []


async def test_unsupported_inbound_shape_is_a_structured_400() -> None:
    async with running_gateway() as gateway:
        response = await gateway.chat(**{"x-pitwall-inbound-shape": "cobol"})
    assert_structured_400(response)
    assert response.json()["error"]["code"] == "unsupported_endpoint"
    assert gateway.upstream.captured == []


async def test_claude_shape_without_messages_is_a_structured_400() -> None:
    async with running_gateway() as gateway:
        response = await gateway.chat({"model": "m"}, **{"x-pitwall-inbound-shape": "claude"})
    assert_structured_400(response)
    assert gateway.upstream.captured == []


async def test_messages_may_be_absent_on_passthrough_shapes() -> None:
    async with running_gateway() as gateway:
        response = await gateway.chat(json.loads('{"model": "m", "input": "x"}'))
    assert response.status_code == 200
