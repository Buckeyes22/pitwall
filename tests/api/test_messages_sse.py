"""Task 11: OpenAI SSE -> Anthropic SSE translation.

Pins the event sequence (``message_start``, ``content_block_start``,
``content_block_delta``, ``content_block_stop``, ``message_delta``,
``message_stop``) and the usage/stop-reason data carried on those events.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from pitwall.api.anthropic_translate import sse_openai_to_anthropic

pytestmark = pytest.mark.anyio


async def _aiter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _event_names(events: list[bytes]) -> list[bytes]:
    return [line.split(b"event: ")[1].split(b"\n")[0] for line in events]


def _event_payloads(events: list[bytes]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for event in events:
        for line in event.split(b"\n"):
            if line.startswith(b"data: "):
                payloads.append(json.loads(line.removeprefix(b"data: ")))
    return payloads


async def test_sse_relay_emits_anthropic_event_sequence() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    events = [e async for e in sse_openai_to_anthropic(_aiter(chunks), request_model="gw/m")]
    assert _event_names(events) == [
        b"message_start",
        b"content_block_start",
        b"content_block_delta",
        b"content_block_delta",
        b"content_block_stop",
        b"message_delta",
        b"message_stop",
    ]


async def test_sse_relay_carries_model_deltas_and_stop_reason() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    events = [e async for e in sse_openai_to_anthropic(_aiter(chunks), request_model="gw/m")]
    payloads = _event_payloads(events)

    start = payloads[0]
    assert start["type"] == "message_start"
    message = start["message"]
    assert message["model"] == "gw/m" and message["role"] == "assistant"

    first_delta = payloads[2]
    assert first_delta == {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "he"},
    }
    second_delta = payloads[3]
    assert second_delta["delta"] == {"type": "text_delta", "text": "y"}

    message_delta = payloads[5]
    assert message_delta["type"] == "message_delta"
    assert message_delta["delta"] == {"stop_reason": "end_turn", "stop_sequence": None}

    assert payloads[6] == {"type": "message_stop"}


async def test_sse_relay_maps_usage_frame_to_message_usage() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    events = [e async for e in sse_openai_to_anthropic(_aiter(chunks), request_model="gw/m")]
    payloads = _event_payloads(events)

    message_delta = next(p for p in payloads if p.get("type") == "message_delta")
    assert message_delta["delta"] == {"stop_reason": "max_tokens", "stop_sequence": None}
    assert message_delta["usage"] == {"output_tokens": 2}


async def test_sse_relay_frames_split_across_chunks() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"conte',
        b'nt":"split"}}]}\n\ndata: [DONE]\n\n',
    ]
    events = [e async for e in sse_openai_to_anthropic(_aiter(chunks), request_model="gw/m")]
    assert _event_names(events) == [
        b"message_start",
        b"content_block_start",
        b"content_block_delta",
        b"content_block_stop",
        b"message_delta",
        b"message_stop",
    ]
    payloads = _event_payloads(events)
    assert payloads[2]["delta"] == {"type": "text_delta", "text": "split"}
