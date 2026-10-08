"""Task 11: pure Anthropic <-> OpenAI translation for ``POST /v1/messages``.

These tests pin the pure translator contract before any route exists:
``anthropic_to_openai`` builds OpenAI chat bodies, ``openai_to_anthropic``
maps OpenAI execution output onto Anthropic message envelopes.
"""

from __future__ import annotations

import pytest

from pitwall.api.anthropic_translate import (
    AnthropicInvalidRequest,
    anthropic_to_openai,
    openai_to_anthropic,
)


def test_system_and_blocks_become_openai_messages() -> None:
    out = anthropic_to_openai(
        {
            "model": "gw/m",
            "max_tokens": 64,
            "system": "be terse",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "stop_sequences": ["END"],
            "temperature": 0.2,
        }
    )
    assert out["messages"][0] == {"role": "system", "content": "be terse"}
    assert out["messages"][1] == {"role": "user", "content": "hi"}
    assert out["max_tokens"] == 64 and out["stop"] == ["END"] and out["temperature"] == 0.2


def test_missing_max_tokens_is_invalid_request() -> None:
    with pytest.raises(AnthropicInvalidRequest, match="max_tokens"):
        anthropic_to_openai({"model": "gw/m", "messages": []})


def test_openai_response_maps_finish_reason_and_usage() -> None:
    out = openai_to_anthropic(
        {
            "id": "x",
            "choices": [
                {"message": {"content": "ok"}, "finish_reason": "length"},
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
        request_model="gw/m",
    )
    assert out["stop_reason"] == "max_tokens" and out["usage"] == {
        "input_tokens": 3,
        "output_tokens": 1,
    }
    assert out["content"] == [{"type": "text", "text": "ok"}]


def test_stop_finish_reason_maps_to_end_turn() -> None:
    out = openai_to_anthropic(
        {
            "id": "x",
            "choices": [{"message": {"content": "done"}, "finish_reason": "stop"}],
            "usage": {},
        },
        request_model="gw/m",
    )
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert out["type"] == "message" and out["role"] == "assistant"
    assert out["model"] == "gw/m" and out["id"] == "x"


def test_anthropic_tools_become_openai_function_tools() -> None:
    out = anthropic_to_openai(
        {
            "model": "gw/m",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Look up weather",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        }
    )
    assert out["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Look up weather",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_string_content_passes_through_without_system() -> None:
    out = anthropic_to_openai(
        {
            "model": "gw/m",
            "max_tokens": 8,
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
            ],
        }
    )
    assert out["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
