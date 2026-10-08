"""Pure Anthropic Messages <-> OpenAI chat translation (Decision Q1).

No I/O: ``anthropic_to_openai`` builds an OpenAI chat-completion body from an
Anthropic ``/v1/messages`` body, ``openai_to_anthropic`` maps OpenAI execution
output onto Anthropic's message envelope, and ``sse_openai_to_anthropic``
rewrites an OpenAI SSE byte stream into Anthropic's event stream.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from pitwall.security.redaction import redact_text

_STOP_REASON_BY_FINISH: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
}


class AnthropicInvalidRequest(ValueError):
    """Raised when an Anthropic body cannot be translated to OpenAI shape."""


def _block_type(block: Any) -> object:
    return block.get("type") if isinstance(block, Mapping) else None


def _unsupported_block(block: Any) -> AnthropicInvalidRequest:
    """Refuse a block the broker cannot translate rather than bill an emptier prompt."""

    return AnthropicInvalidRequest(
        f"unsupported content block type {_block_type(block)!r}: "
        "only text, tool_use, and tool_result blocks are supported"
    )


def _text_of_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if _block_type(block) != "text":
                raise _unsupported_block(block)
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    raise AnthropicInvalidRequest("message content must be a string or a list of blocks")


def _system_text(system: Any) -> str:
    return _text_of_content(system)


def _openai_tool(tool: Any) -> dict[str, Any]:
    if not isinstance(tool, Mapping) or not isinstance(tool.get("name"), str):
        raise AnthropicInvalidRequest("each tool requires a string name")
    function: dict[str, Any] = {"name": tool["name"]}
    description = tool.get("description")
    if description is not None:
        function["description"] = description
    parameters = tool.get("input_schema")
    if parameters is not None:
        function["parameters"] = parameters
    return {"type": "function", "function": function}


def _tool_call_of_block(block: Mapping[str, Any]) -> dict[str, Any]:
    tool_id = block.get("id")
    name = block.get("name")
    if not isinstance(tool_id, str) or not isinstance(name, str):
        raise AnthropicInvalidRequest("tool_use blocks require string id and name")
    arguments = block.get("input")
    return {
        "id": tool_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments if arguments is not None else {}),
        },
    }


def _openai_messages_for(role: str, content: Any) -> list[dict[str, Any]]:
    """Expand one Anthropic message into OpenAI messages.

    ``tool_result`` blocks become ``role: tool`` messages, assistant ``tool_use`` blocks
    become ``tool_calls``, and text blocks keep their position relative to the tool
    results so the model sees the same order the client produced.
    """

    if not isinstance(content, list):
        return [{"role": role, "content": _text_of_content(content)}]
    out: list[dict[str, Any]] = []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    def flush_text() -> None:
        if text_parts or tool_calls:
            message: dict[str, Any] = {"role": role, "content": "".join(text_parts)}
            if tool_calls:
                message["tool_calls"] = list(tool_calls)
            out.append(message)
        text_parts.clear()
        tool_calls.clear()

    for block in content:
        kind = _block_type(block)
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif kind == "tool_use":
            tool_calls.append(_tool_call_of_block(block))
        elif kind == "tool_result":
            flush_text()
            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str):
                raise AnthropicInvalidRequest("tool_result blocks require a string tool_use_id")
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "content": _text_of_content(block.get("content")),
                }
            )
        else:
            raise _unsupported_block(block)
    flush_text()
    if not out:
        out.append({"role": role, "content": ""})
    return out


def _tool_use_blocks(tool_calls: Any) -> list[dict[str, Any]]:
    """Map OpenAI ``tool_calls`` onto Anthropic ``tool_use`` blocks."""

    blocks: list[dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return blocks
    for call in tool_calls:
        if not isinstance(call, Mapping):
            continue
        function = call.get("function")
        function_map: Mapping[str, Any] = function if isinstance(function, Mapping) else {}
        name = function_map.get("name")
        raw_arguments = function_map.get("arguments")
        arguments: Any = {}
        malformed = False
        if isinstance(raw_arguments, str) and raw_arguments.strip():
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                malformed = True
        elif isinstance(raw_arguments, Mapping):
            arguments = dict(raw_arguments)
        if not isinstance(arguments, dict):
            malformed = True
            arguments = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") if isinstance(call.get("id"), str) else "",
                "name": name if isinstance(name, str) else "",
                "input": arguments,
            }
        )
        if malformed:
            blocks.append(
                {
                    "type": "text",
                    "text": f"tool call arguments were not valid JSON: {raw_arguments}",
                }
            )
    return blocks


def anthropic_to_openai(body: Mapping[str, Any]) -> dict[str, Any]:
    """Translate one Anthropic ``/v1/messages`` body to OpenAI chat shape."""

    if not isinstance(body, Mapping):
        raise AnthropicInvalidRequest("request body must be a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise AnthropicInvalidRequest("model is required and must be a non-empty string")
    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise AnthropicInvalidRequest("max_tokens is required and must be a positive integer")

    messages: list[dict[str, Any]] = []
    if body.get("system") is not None:
        system = _system_text(body["system"])
        if system:
            messages.append({"role": "system", "content": system})

    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise AnthropicInvalidRequest("messages must be a list")
    for item in raw_messages:
        if not isinstance(item, Mapping) or not isinstance(item.get("role"), str):
            raise AnthropicInvalidRequest("each message requires a string role")
        messages.extend(_openai_messages_for(item["role"], item.get("content")))

    out: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
    stop_sequences = body.get("stop_sequences")
    if stop_sequences is not None:
        if not isinstance(stop_sequences, list) or not all(
            isinstance(sequence, str) for sequence in stop_sequences
        ):
            raise AnthropicInvalidRequest("stop_sequences must be a list of strings")
        out["stop"] = stop_sequences
    if body.get("temperature") is not None:
        out["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        out["top_p"] = body["top_p"]
    tools = body.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise AnthropicInvalidRequest("tools must be a list")
        out["tools"] = [_openai_tool(tool) for tool in tools]
    return out


def openai_to_anthropic(body: Mapping[str, Any], *, request_model: str) -> dict[str, Any]:
    """Map one OpenAI chat-completion output onto Anthropic's message envelope."""

    choices = body.get("choices") if isinstance(body, Mapping) else None
    choice: Mapping[str, Any] = (
        choices[0]
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping)
        else {}
    )
    message_value = choice.get("message")
    message: Mapping[str, Any] = message_value if isinstance(message_value, Mapping) else {}
    text = message.get("content") if isinstance(message.get("content"), str) else ""

    usage_value = body.get("usage") if isinstance(body, Mapping) else None
    usage: Mapping[str, Any] = usage_value if isinstance(usage_value, Mapping) else {}
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    finish_reason = choice.get("finish_reason")

    content: list[dict[str, Any]] = []
    tool_blocks = _tool_use_blocks(message.get("tool_calls"))
    if text or not tool_blocks:
        content.append({"type": "text", "text": text})
    content.extend(tool_blocks)

    return {
        "id": body.get("id") if isinstance(body.get("id"), str) else "",
        "type": "message",
        "role": "assistant",
        "model": request_model,
        "content": content,
        "stop_reason": _STOP_REASON_BY_FINISH.get(str(finish_reason)) if finish_reason else None,
        "usage": {
            "input_tokens": input_tokens if isinstance(input_tokens, int) else 0,
            "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
        },
    }


def _sse_event(name: str, data: Mapping[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n".encode()


_ANTHROPIC_ERROR_TYPES = frozenset(
    {
        "invalid_request_error",
        "authentication_error",
        "permission_error",
        "not_found_error",
        "request_too_large",
        "rate_limit_error",
        "api_error",
        "overloaded_error",
    }
)
ERROR_MESSAGE_LIMIT = 4096
_STREAM_FAILURE_MESSAGE = "upstream stream failure"
_STREAM_FAILURE_FRAME = b'data: {"error":"' + _STREAM_FAILURE_MESSAGE.encode() + b'"}'


def _split_events(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a buffer of ``\\n``-terminated lines into events at each blank line."""

    frames: list[bytes] = []
    while b"\n\n" in buffer:
        frame, buffer = buffer.split(b"\n\n", 1)
        frames.append(frame)
    return frames, buffer


async def _sse_frames(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Yield each complete SSE event from *chunks*, with lines ended by CRLF, LF, or CR.

    WHATWG HTML section 9.2 ends a line with CRLF, LF, or CR, and an event with a blank
    line. Line ends are normalised to LF, and a CR that closes a chunk ends its line at
    once (the LF of a CRLF pair that opens the next chunk is dropped), so every event a
    chunk completes is yielded before the next chunk is read. A source that fails
    mid-stream ends in one synthetic error event, in the shape the upstream relay emits.
    """

    buffer = b""
    skip_lf = False
    try:
        async for chunk in chunks:
            if skip_lf and chunk:
                skip_lf = False
                if chunk.startswith(b"\n"):
                    chunk = chunk[1:]
            if not chunk:
                continue
            skip_lf = chunk.endswith(b"\r")
            buffer += chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            frames, buffer = _split_events(buffer)
            for frame in frames:
                yield frame
    except Exception:  # reason: any source failure must reach the client as one error event
        yield _STREAM_FAILURE_FRAME


def safe_error_message(message: str, secrets: Sequence[str]) -> str:
    """Redact credentials from upstream error text and bound its length.

    Upstream text can echo a credential whatever the error's type or status, so every
    message that reaches a client passes through here: the broker's own provider
    credentials (*secrets*) and the shared credential patterns are removed first.
    """

    return redact_text(message, secrets=secrets)[:ERROR_MESSAGE_LIMIT]


def _error_event(parsed: Mapping[str, Any], secrets: Sequence[str]) -> bytes:
    """Map one upstream error frame onto Anthropic's ``error`` event.

    The upstream message is relayed redacted and bounded, except that authentication
    and permission class errors carry a fixed message.
    """

    error = parsed.get("error")
    detail: Mapping[str, Any] = error if isinstance(error, Mapping) else {}
    kind = detail.get("type")
    error_type = kind if isinstance(kind, str) and kind in _ANTHROPIC_ERROR_TYPES else "api_error"
    message = detail.get("message") if isinstance(error, Mapping) else error
    if (
        error_type in {"authentication_error", "permission_error"}
        or not isinstance(message, str)
        or not message
    ):
        message = _STREAM_FAILURE_MESSAGE
    message = safe_error_message(message, secrets)
    return _sse_event("error", {"type": "error", "error": {"type": error_type, "message": message}})


def _failure_event() -> bytes:
    return _sse_event(
        "error",
        {"type": "error", "error": {"type": "api_error", "message": _STREAM_FAILURE_MESSAGE}},
    )


class StreamOutcome:
    """How one translated stream ended, readable by the upstream relay's bookkeeping.

    The relay settles the workload when its source is exhausted, before the translator
    returns, so :meth:`failure` reflects every event the translator has handled by then.
    """

    def __init__(self) -> None:
        self.errored = False
        self.malformed = False
        self.complete = False

    def failure(self) -> str | None:
        """The error-class reason the stream failed, or ``None`` when it finished."""

        if self.errored:
            return "provider_stream_error"
        if self.malformed:
            return "provider_stream_malformed"
        if not self.complete:
            return "provider_stream_truncated"
        return None


async def _drain(chunks: AsyncIterator[bytes]) -> None:
    """Read the source to its end so the upstream relay settles as completed."""

    with contextlib.suppress(Exception):
        async for _ in chunks:
            pass


def _data_payloads(frame: bytes) -> list[bytes]:
    payloads: list[bytes] = []
    for line in frame.split(b"\n"):
        if line.startswith(b"data:"):
            payloads.append(line[len(b"data:") :].strip())
    return payloads


async def sse_openai_to_anthropic(
    chunks: AsyncIterator[bytes],
    *,
    request_model: str,
    secrets: Sequence[str] = (),
    outcome: StreamOutcome | None = None,
) -> AsyncIterator[bytes]:
    """Rewrite an OpenAI SSE byte stream into Anthropic's message event stream.

    Text deltas stream into one text block; each OpenAI tool-call index opens its own
    ``tool_use`` block whose arguments stream as ``input_json_delta`` events. The source
    is always closed when this generator ends or is closed, so a client disconnect
    reaches the upstream relay and its terminal bookkeeping. Upstream error text is
    redacted with *secrets* (the broker's provider credentials) before it is relayed.
    A stream that errors, is malformed, or ends without a finish reason or ``[DONE]``
    ends in one ``error`` event and no ``message_stop``; *outcome* records which.
    """

    try:
        async for event in _translate_stream(
            chunks,
            request_model=request_model,
            secrets=secrets,
            outcome=outcome if outcome is not None else StreamOutcome(),
        ):
            yield event
    finally:
        aclose = getattr(chunks, "aclose", None)
        if aclose is not None:
            await aclose()


async def _translate_stream(
    chunks: AsyncIterator[bytes],
    *,
    request_model: str,
    secrets: Sequence[str],
    outcome: StreamOutcome,
) -> AsyncIterator[bytes]:

    message_started = False
    input_tokens = 0
    output_tokens = 0
    finish_reason: str | None = None
    done = False
    failed = False
    # Anthropic block bookkeeping: the text block index (once opened), the next free
    # index, the open block (only one is open at a time), and tool blocks by call index.
    text_index: int | None = None
    next_index = 0
    open_index: int | None = None
    tool_index_by_call: dict[int, int] = {}

    def message_start() -> bytes:
        return _sse_event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_{request_model}",
                    "type": "message",
                    "role": "assistant",
                    "model": request_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                },
            },
        )

    def block_stop(index: int) -> bytes:
        return _sse_event("content_block_stop", {"type": "content_block_stop", "index": index})

    async for frame in _sse_frames(chunks):
        for payload in _data_payloads(frame):
            if payload == b"[DONE]":
                done = True
                outcome.complete = True
                break
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            except UnicodeDecodeError:
                yield _failure_event()
                outcome.malformed = True
                failed = True
                break
            if not isinstance(parsed, Mapping):
                continue
            if parsed.get("error"):
                yield _error_event(parsed, secrets)
                outcome.errored = True
                failed = True
                break
            usage_value = parsed.get("usage")
            if isinstance(usage_value, Mapping):
                prompt_tokens = usage_value.get("prompt_tokens")
                completion_tokens = usage_value.get("completion_tokens")
                if isinstance(prompt_tokens, int):
                    input_tokens = prompt_tokens
                if isinstance(completion_tokens, int):
                    output_tokens = completion_tokens
            choices = parsed.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, Mapping):
                continue
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]
                outcome.complete = True
            delta_value = choice.get("delta")
            delta: Mapping[str, Any] = delta_value if isinstance(delta_value, Mapping) else {}

            text = delta.get("content")
            if isinstance(text, str) and text != "":
                if not message_started:
                    yield message_start()
                    message_started = True
                if text_index is None or open_index != text_index:
                    # First text, or text after a tool call: a text block is opened
                    # fresh rather than appended to one that was already closed.
                    if open_index is not None:
                        yield block_stop(open_index)
                    text_index = next_index
                    next_index += 1
                    open_index = text_index
                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": text_index,
                            "content_block": {"type": "text", "text": ""},
                        },
                    )
                yield _sse_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_index,
                        "delta": {"type": "text_delta", "text": text},
                    },
                )

            tool_calls = delta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, Mapping):
                    continue
                call_index = call.get("index")
                if isinstance(call_index, bool) or not isinstance(call_index, int):
                    call_index = len(tool_index_by_call)
                function = call.get("function")
                function_map: Mapping[str, Any] = function if isinstance(function, Mapping) else {}
                if not message_started:
                    yield message_start()
                    message_started = True
                block_index = tool_index_by_call.get(call_index)
                if block_index is None:
                    if open_index is not None:
                        yield block_stop(open_index)
                    block_index = next_index
                    next_index += 1
                    tool_index_by_call[call_index] = block_index
                    open_index = block_index
                    name = function_map.get("name")
                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": call.get("id") if isinstance(call.get("id"), str) else "",
                                "name": name if isinstance(name, str) else "",
                                "input": {},
                            },
                        },
                    )
                arguments = function_map.get("arguments")
                if isinstance(arguments, str) and arguments != "":
                    yield _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {"type": "input_json_delta", "partial_json": arguments},
                        },
                    )
        if done or failed:
            break

    if failed:
        await _drain(chunks)
        return
    if not outcome.complete:
        yield _failure_event()
        return
    if not message_started:
        yield message_start()
        message_started = True
    if open_index is not None:
        yield block_stop(open_index)
    yield _sse_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": _STOP_REASON_BY_FINISH.get(finish_reason or ""),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": output_tokens},
        },
    )
    yield _sse_event("message_stop", {"type": "message_stop"})
    await _drain(chunks)


__all__ = [
    "ERROR_MESSAGE_LIMIT",
    "AnthropicInvalidRequest",
    "anthropic_to_openai",
    "openai_to_anthropic",
    "StreamOutcome",
    "safe_error_message",
    "sse_openai_to_anthropic",
]
