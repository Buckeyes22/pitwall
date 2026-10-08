"""R7: tool calls round-trip through the Anthropic translator in both directions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from pitwall.api.anthropic_translate import (
    AnthropicInvalidRequest,
    anthropic_to_openai,
    openai_to_anthropic,
    sse_openai_to_anthropic,
)

pytestmark = pytest.mark.anyio


def _body(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"model": "coding.chat", "max_tokens": 32, "messages": messages}


def test_assistant_tool_calls_become_tool_use_blocks() -> None:
    output = {
        "id": "chatcmpl-1",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"city":"Oslo"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }

    message = openai_to_anthropic(output, request_model="coding.chat")

    assert message["stop_reason"] == "tool_use"
    assert message["content"] == [
        {"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Oslo"}}
    ]


def test_text_and_tool_calls_keep_text_first_and_malformed_arguments_degrade() -> None:
    output = {
        "choices": [
            {
                "message": {
                    "content": "Let me check.",
                    "tool_calls": [
                        {"id": "call_2", "function": {"name": "lookup", "arguments": "{not json"}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }

    content = openai_to_anthropic(output, request_model="m")["content"]

    assert content[0] == {"type": "text", "text": "Let me check."}
    assert content[1]["type"] == "tool_use"
    assert content[1]["input"] == {}
    assert "{not json" in content[2]["text"]


def test_plain_text_reply_keeps_single_text_block() -> None:
    output = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
    assert openai_to_anthropic(output, request_model="m")["content"] == [
        {"type": "text", "text": "hi"}
    ]


def test_tool_result_blocks_become_openai_tool_messages() -> None:
    body = _body(
        [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Checking."},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "get_weather",
                        "input": {"city": "Oslo"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": [{"type": "text", "text": "12C"}],
                    },
                    {"type": "text", "text": "and tomorrow?"},
                ],
            },
        ]
    )

    messages = anthropic_to_openai(body)["messages"]

    assert messages[0] == {"role": "user", "content": "weather?"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Checking."
    assert messages[1]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Oslo"}'},
        }
    ]
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "12C"}
    assert messages[3] == {"role": "user", "content": "and tomorrow?"}


def test_tool_result_string_content_and_error_flag() -> None:
    body = _body(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_9",
                        "content": "boom",
                        "is_error": True,
                    }
                ],
            }
        ]
    )
    assert anthropic_to_openai(body)["messages"] == [
        {"role": "tool", "tool_call_id": "call_9", "content": "boom"}
    ]


async def _chunks(frames: list[bytes]) -> AsyncIterator[bytes]:
    for frame in frames:
        yield frame


def _events(raw: bytes) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for frame in raw.decode().split("\n\n"):
        if not frame.strip():
            continue
        name = frame.split("event: ", 1)[1].split("\n", 1)[0]
        data = json.loads(frame.split("data: ", 1)[1])
        out.append((name, data))
    return out


async def test_streaming_tool_call_deltas_become_tool_use_blocks() -> None:
    frames = [
        b'data: {"choices":[{"delta":{"content":"Sure."}}]}\n\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
        b'"type":"function","function":{"name":"get_weather","arguments":""}}]}}]}\n\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"{\\"city\\":"}}]}}]}\n\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
        b'"function":{"arguments":"\\"Oslo\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    raw = b"".join([c async for c in sse_openai_to_anthropic(_chunks(frames), request_model="m")])
    events = _events(raw)
    names = [name for name, _ in events]

    assert names == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    tool_start = events[4][1]
    assert tool_start["index"] == 1
    assert tool_start["content_block"] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "get_weather",
        "input": {},
    }
    deltas = [data["delta"] for name, data in events if name == "content_block_delta"]
    assert deltas[0] == {"type": "text_delta", "text": "Sure."}
    assert deltas[1] == {"type": "input_json_delta", "partial_json": '{"city":'}
    assert deltas[2] == {"type": "input_json_delta", "partial_json": '"Oslo"}'}
    assert events[-2][1]["delta"]["stop_reason"] == "tool_use"


async def test_streaming_text_only_is_unchanged() -> None:
    frames = [
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    raw = b"".join([c async for c in sse_openai_to_anthropic(_chunks(frames), request_model="m")])
    names = [name for name, _ in _events(raw)]
    assert names == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
        {"type": "document", "source": {"type": "text", "data": "x"}},
        {"type": "thinking", "thinking": "hmm"},
        {"text": "no type"},
    ],
)
def test_unsupported_content_blocks_are_refused_by_name(block: dict[str, Any]) -> None:
    body = _body([{"role": "user", "content": [block]}])

    with pytest.raises(AnthropicInvalidRequest, match=str(block.get("type"))):
        anthropic_to_openai(body)


def test_unsupported_block_beside_text_is_refused_not_dropped() -> None:
    body = _body(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}},
                ],
            }
        ]
    )

    with pytest.raises(AnthropicInvalidRequest, match="image"):
        anthropic_to_openai(body)


def test_unsupported_block_inside_tool_result_and_system_is_refused() -> None:
    image = {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}
    in_result = _body(
        [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "c1", "content": [image]}],
            }
        ]
    )
    in_system = {**_body([{"role": "user", "content": "hi"}]), "system": [image]}

    for body in (in_result, in_system):
        with pytest.raises(AnthropicInvalidRequest, match="image"):
            anthropic_to_openai(body)


async def _translate(frames: list[bytes]) -> list[tuple[str, dict[str, Any]]]:
    raw = b"".join([c async for c in sse_openai_to_anthropic(_chunks(frames), request_model="m")])
    return _events(raw)


async def test_upstream_error_frame_ends_the_stream_with_an_error_event() -> None:
    events = await _translate(
        [
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
            b'data: {"error":{"message":"context length exceeded","type":"invalid_request_error"}}'
            b"\n\n",
            b"data: [DONE]\n\n",
        ]
    )

    names = [name for name, _ in events]
    assert names[-1] == "error"
    assert "message_stop" not in names
    assert events[-1][1] == {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "context length exceeded"},
    }


async def test_relay_style_string_error_frame_becomes_an_api_error_event() -> None:
    events = await _translate(
        [
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
            b'data: {"error":"upstream stream failure"}\n\n',
        ]
    )

    assert [name for name, _ in events][-1] == "error"
    assert "message_stop" not in [name for name, _ in events]
    assert events[-1][1]["error"] == {"type": "api_error", "message": "upstream stream failure"}


async def test_auth_class_error_frame_never_echoes_the_upstream_message() -> None:
    events = await _translate(
        [
            b'data: {"error":{"message":"Incorrect API key provided: sk-secret",'
            b'"type":"authentication_error"}}\n\n',
        ]
    )

    assert events[-1][0] == "error"
    assert "sk-secret" not in json.dumps(events)


async def test_failing_source_iterator_ends_the_stream_with_an_error_event() -> None:
    async def failing() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        raise RuntimeError("socket reset with secret-detail")

    raw = b"".join([c async for c in sse_openai_to_anthropic(failing(), request_model="m")])
    events = _events(raw)

    assert [name for name, _ in events][-1] == "error"
    assert "message_stop" not in [name for name, _ in events]
    assert "secret-detail" not in raw.decode()


_LF_FRAMES = [
    b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n',
    b"data: [DONE]\n\n",
]


@pytest.mark.parametrize("terminator", [b"\r\n", b"\r", b"\n"])
async def test_every_line_terminator_frames_like_lf(terminator: bytes) -> None:
    frames = [frame.replace(b"\n", terminator) for frame in _LF_FRAMES]

    assert await _translate(frames) == await _translate(_LF_FRAMES)


async def test_crlf_frame_boundary_split_across_chunks_matches_lf() -> None:
    crlf = b"".join(frame.replace(b"\n", b"\r\n") for frame in _LF_FRAMES)
    for cut in range(1, len(crlf)):
        assert await _translate([crlf[:cut], crlf[cut:]]) == await _translate(_LF_FRAMES), cut


@pytest.mark.parametrize(
    "error_type", ["invalid_request_error", "api_error", "weird_vendor_error", "overloaded_error"]
)
async def test_error_frame_text_is_redacted_for_the_given_secrets(error_type: str) -> None:
    secret = "zq9RotatedBrokerCredentialValue0451"
    frame = (
        b'data: {"error":{"message":"bad key '
        + secret.encode()
        + b' given","type":"'
        + error_type.encode()
        + b'"}}\n\n'
    )

    stream = sse_openai_to_anthropic(_chunks([frame]), request_model="m", secrets=(secret,))
    raw = b"".join([c async for c in stream])

    assert secret.encode() not in raw
    assert _events(raw)[-1][0] == "error"


async def test_error_frame_credential_shapes_are_redacted_without_explicit_secrets() -> None:
    frame = (
        b'data: {"error":{"message":"Authorization: Bearer abcdef0123456789xyz",'
        b'"type":"api_error"}}\n\n'
    )

    raw = b"".join([c async for c in sse_openai_to_anthropic(_chunks([frame]), request_model="m")])

    assert b"abcdef0123456789xyz" not in raw


async def test_error_frame_message_is_bounded() -> None:
    frame = b'data: {"error":{"message":"' + b"y" * 100_000 + b'","type":"api_error"}}\n\n'

    events = await _translate([frame])

    assert len(events[-1][1]["error"]["message"]) <= 4096


_TEXT_FRAME = b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'


async def test_stream_ending_without_a_finish_reason_is_one_error_event() -> None:
    events = await _translate([_TEXT_FRAME])

    names = [name for name, _ in events]
    assert names[-1] == "error" and names.count("error") == 1
    assert "message_stop" not in names and "message_delta" not in names
    assert events[-1][1]["error"] == {"type": "api_error", "message": "upstream stream failure"}


async def test_empty_stream_is_one_error_event() -> None:
    events = await _translate([])

    assert [name for name, _ in events] == ["error"]


async def test_finish_reason_without_done_marker_keeps_the_success_path() -> None:
    events = await _translate(
        [b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n']
    )

    assert [name for name, _ in events][-1] == "message_stop"
    assert "error" not in [name for name, _ in events]


async def test_done_marker_without_finish_reason_keeps_the_success_path() -> None:
    events = await _translate([_TEXT_FRAME, b"data: [DONE]\n\n"])

    assert [name for name, _ in events][-1] == "message_stop"


async def test_invalid_utf8_in_a_frame_is_one_bounded_error_event() -> None:
    raw = b"".join(
        [
            c
            async for c in sse_openai_to_anthropic(
                _chunks([_TEXT_FRAME, b'data: {"choices":[{"delta":{"content":"\xff"}}]}\n\n']),
                request_model="m",
            )
        ]
    )
    events = _events(raw)

    names = [name for name, _ in events]
    assert names[-1] == "error" and names.count("error") == 1
    assert "message_stop" not in names
    assert events[-1][1]["error"] == {"type": "api_error", "message": "upstream stream failure"}


async def test_outcome_reports_the_reason_a_stream_failed() -> None:
    from pitwall.api.anthropic_translate import StreamOutcome

    cases: dict[str, list[bytes]] = {
        "provider_stream_error": [_TEXT_FRAME, b'data: {"error":{"message":"x"}}\n\n'],
        "provider_stream_truncated": [_TEXT_FRAME],
        "provider_stream_malformed": [b'data: {"x":"\xff"}\n\n'],
    }
    for reason, frames in cases.items():
        outcome = StreamOutcome()
        stream = sse_openai_to_anthropic(_chunks(frames), request_model="m", outcome=outcome)
        _ = [c async for c in stream]
        assert outcome.failure() == reason

    outcome = StreamOutcome()
    stream = sse_openai_to_anthropic(_chunks(_LF_FRAMES), request_model="m", outcome=outcome)
    _ = [c async for c in stream]
    assert outcome.failure() is None
