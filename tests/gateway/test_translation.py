"""Translation cases ported from the Node gateway tests (hardening.test.ts)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from pitwall.gateway.translation import InboundShape, translate_inbound, translate_outbound


def _shape(name: str) -> InboundShape:
    return cast(InboundShape, name)


def test_claude_moves_system_to_the_first_message() -> None:
    # Source: hardening.test.ts "translation + compression > claude translator moves `system` to the first message"
    out = translate_inbound(
        {
            "model": "m",
            "max_tokens": 8,
            "system": "terse",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        },
        "claude",
    )
    assert out["messages"][0] == {"role": "system", "content": "terse"}
    assert out["messages"][1] == {"role": "user", "content": "hi"}
    assert out["max_tokens"] == 8


def test_claude_inbound_projects_system_and_text_blocks() -> None:
    # Source: hardening.test.ts "inbound shape projections > claude inbound projects system + text blocks onto OpenAI messages"
    out = translate_inbound(
        {
            "model": "fixture",
            "max_tokens": 8,
            "system": "terse",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "stop_sequences": ["END"],
        },
        "claude",
    )
    assert out == {
        "messages": [
            {"role": "system", "content": "terse"},
            {"role": "user", "content": "hi"},
        ],
        "model": "fixture",
        "max_tokens": 8,
        "stop_sequences": ["END"],
    }


def test_claude_system_blocks_are_joined_and_non_text_blocks_dropped() -> None:
    # Source: translation.ts claudeSystemToString / claudeContentToString
    out = translate_inbound(
        {
            "system": [
                {"type": "text", "text": "a"},
                {"type": "image"},
                {"type": "text", "text": "b"},
            ],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "image"}]},
                {"role": "system", "content": "skipped"},
            ],
        },
        "claude",
    )
    assert out["messages"] == [
        {"role": "system", "content": "ab"},
        {"role": "user", "content": "x"},
    ]


@pytest.mark.parametrize("body", [{"model": "m"}, {"messages": "nope"}])
def test_claude_without_messages_array_is_rejected(body: dict[str, Any]) -> None:
    # Source: translation.ts "claude request must include a messages array"
    with pytest.raises(ValueError, match="messages array"):
        translate_inbound(body, "claude")


def test_openai_inbound_is_forwarded_unchanged() -> None:
    # Source: hardening.test.ts "inbound shape projections > openai inbound is forwarded byte-for-byte in OpenAI shape"
    body = {
        "model": "fixture",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "vendor_extra": {"keep": True},
    }
    assert translate_inbound(body, "openai") == body


def test_gemini_inbound_projects_system_instruction_and_contents() -> None:
    # Source: hardening.test.ts "inbound shape projections > gemini inbound projects systemInstruction + contents text only"
    body = {
        "model": "fixture",
        "systemInstruction": {"parts": [{"text": "be terse"}]},
        "contents": [
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "model", "parts": [{"text": "hello"}]},
        ],
        "generationConfig": {"temperature": 0.1},
    }
    assert translate_inbound(body, "gemini") == {
        **body,
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
    }


def test_responses_inbound_is_passed_through_without_messages() -> None:
    # Source: hardening.test.ts "inbound shape projections > responses inbound is passed through unchanged to the executor"
    body = {"model": "fixture", "input": "fixture"}
    out = translate_inbound(body, "responses")
    assert out == body
    assert "messages" not in out


def test_translate_inbound_does_not_mutate_its_input() -> None:
    # Source: translation.ts builds a fresh OpenAIRequest; gemini spreads the body
    body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    translate_inbound(body, "gemini")
    assert "messages" not in body


def test_unknown_shape_is_rejected() -> None:
    # Source: shim.ts parseInboundShape returns null for unknown shapes (unsupported_endpoint)
    with pytest.raises(ValueError, match="inbound shape"):
        translate_inbound({}, _shape("bogus"))


@pytest.mark.parametrize("shape", ["openai", "claude", "gemini", "responses"])
def test_outbound_responses_are_not_reformatted(shape: str) -> None:
    # Source: shim.ts header comment "no response reformatting"; upstream bodies relay as-is
    body = {"id": "x", "object": "chat.completion", "choices": []}
    assert translate_outbound(body, _shape(shape)) == body
