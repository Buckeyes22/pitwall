"""Inbound shape and compression projections over HTTP (hardening.test.ts, surfaces.test.ts)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.gateway.test_app_support import running_gateway

NOISY = {
    "model": "fixture",
    "messages": [
        {"role": "system", "content": "rule one\nrule one\nrule two"},
        {"role": "user", "content": "hello   \n\n\n\n  world"},
    ],
}
POLICIES = ["off", "rtk", "caveman", "stacked"]


async def sent_body(headers: dict[str, str], body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    async with running_gateway(upstream_base_url="http://upstream.invalid") as gateway:
        response = await gateway.chat(body, **headers)
    assert response.status_code == 200
    assert len(gateway.upstream.captured) == 1
    return gateway.upstream.captured[0].url, gateway.upstream.last_body()


def contents(body: dict[str, Any]) -> list[str]:
    return [message["content"] for message in body["messages"]]


# --- hardening.test.ts "inbound shape projections (actual HTTP)" --------------------------------


@pytest.mark.parity
async def test_openai_inbound_is_forwarded_byte_for_byte_in_openai_shape() -> None:
    # Source: hardening.test.ts "inbound shape projections (actual HTTP) > openai inbound is forwarded byte-for-byte in OpenAI shape"
    payload = {
        "model": "fixture",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "vendor_extra": {"keep": True},
    }
    url, body = await sent_body({"x-pitwall-inbound-shape": "openai"}, payload)
    assert url == "http://upstream.invalid/chat/completions"
    assert body == payload


@pytest.mark.parity
async def test_claude_inbound_projects_system_and_text_blocks_onto_openai_messages() -> None:
    # Source: hardening.test.ts "inbound shape projections (actual HTTP) > claude inbound projects system + text blocks onto OpenAI messages"
    _, body = await sent_body(
        {"x-pitwall-inbound-shape": "claude"},
        {
            "model": "fixture",
            "max_tokens": 8,
            "system": "terse",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "stop_sequences": ["END"],
        },
    )
    assert body == {
        "messages": [
            {"role": "system", "content": "terse"},
            {"role": "user", "content": "hi"},
        ],
        "model": "fixture",
        "max_tokens": 8,
        "stop_sequences": ["END"],
    }


@pytest.mark.parity
async def test_gemini_inbound_projects_system_instruction_and_contents_text_only() -> None:
    # Source: hardening.test.ts "inbound shape projections (actual HTTP) > gemini inbound projects systemInstruction + contents text only"
    payload = {
        "model": "fixture",
        "systemInstruction": {"parts": [{"text": "be terse"}]},
        "contents": [
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "model", "parts": [{"text": "hello"}]},
        ],
        "generationConfig": {"temperature": 0.1},
    }
    _, body = await sent_body({"x-pitwall-inbound-shape": "gemini"}, payload)
    assert body == {
        **payload,
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }


@pytest.mark.parity
async def test_responses_inbound_is_passed_through_unchanged() -> None:
    # Source: hardening.test.ts "inbound shape projections (actual HTTP) > responses inbound is passed through unchanged to the executor"
    payload = {"model": "fixture", "input": "fixture"}
    url, body = await sent_body({"x-pitwall-inbound-shape": "responses"}, payload)
    assert url == "http://upstream.invalid/chat/completions"
    assert body == payload
    assert "messages" not in body


@pytest.mark.parity
@pytest.mark.parametrize("policy", POLICIES)
async def test_compression_over_http_applies_only_the_lite_normalization_it_documents(
    policy: str,
) -> None:
    # Source: hardening.test.ts "inbound shape projections (actual HTTP) > compression %s over HTTP applies only the lite normalization it documents"
    _, body = await sent_body({"x-pitwall-compression": policy}, NOISY)
    if policy == "off":
        assert contents(body) == ["rule one\nrule one\nrule two", "hello   \n\n\n\n  world"]
    else:
        assert contents(body) == ["rule one\nrule two", "hello\n\nworld"]


async def test_shape_and_compression_headers_are_case_insensitive_values() -> None:
    _, body = await sent_body(
        {"x-pitwall-inbound-shape": "CLAUDE", "x-pitwall-compression": "rtk"},
        {
            "model": "m",
            "max_tokens": 1,
            "system": "s",
            "messages": [{"role": "user", "content": "a   \n\n\n\nb"}],
        },
    )
    assert contents(body) == ["s", "a\n\nb"]
