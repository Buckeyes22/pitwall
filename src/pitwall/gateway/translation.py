"""Inbound request-shape translation (port of the Node gateway src/translation.ts and the
gemini projection in shim.ts).

The gateway's upstream contract is an OpenAI Chat-Completions body. ``openai`` and ``responses``
bodies pass through untouched; ``claude`` and ``gemini`` bodies get a ``messages`` array projected
from their text content. Only text is projected; tool calls, images, and prompt-cache markers are
outside the gateway's surface.
"""

from __future__ import annotations

from typing import Any, Literal

InboundShape = Literal["openai", "claude", "gemini", "responses"]

INBOUND_SHAPES: tuple[InboundShape, ...] = ("openai", "claude", "gemini", "responses")
_CLAUDE_ROLES = frozenset({"user", "assistant", "tool"})
_CLAUDE_SHAPED_KEYS = frozenset({"model", "system", "messages", "max_tokens"})


def _claude_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


def _translate_claude(body: dict[str, Any]) -> dict[str, Any]:
    raw_messages = body.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("claude request must include a messages array")
    messages: list[dict[str, Any]] = []
    system = _claude_text(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    for message in raw_messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        role = role if isinstance(role, str) else "user"
        if role not in _CLAUDE_ROLES:
            continue
        messages.append({"role": role, "content": _claude_text(message.get("content"))})
    out: dict[str, Any] = {"messages": messages}
    if isinstance(body.get("model"), str):
        out["model"] = body["model"]
    max_tokens = body.get("max_tokens")
    if isinstance(max_tokens, (int, float)) and not isinstance(max_tokens, bool):
        out["max_tokens"] = max_tokens
    # Fields that do not change shape pass through for the upstream to normalize.
    out.update({key: value for key, value in body.items() if key not in _CLAUDE_SHAPED_KEYS})
    return out


def _gemini_text(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _translate_gemini(body: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    instruction = body.get("systemInstruction")
    if isinstance(instruction, dict):
        text = _gemini_text(instruction.get("parts"))
        if text:
            messages.append({"role": "system", "content": text})
    contents = body.get("contents")
    if isinstance(contents, list):
        for entry in contents:
            if not isinstance(entry, dict):
                continue
            role = "assistant" if entry.get("role") == "model" else "user"
            messages.append({"role": role, "content": _gemini_text(entry.get("parts"))})
    return {**body, "messages": messages}


def translate_inbound(body: dict[str, Any], shape: InboundShape) -> dict[str, Any]:
    """Project a client-shaped request body onto the OpenAI shape sent upstream."""
    if shape in ("openai", "responses"):
        return body
    if shape == "claude":
        return _translate_claude(body)
    if shape == "gemini":
        return _translate_gemini(body)
    raise ValueError(f"unsupported inbound shape: {shape!r}")


def translate_outbound(body: dict[str, Any], shape: InboundShape) -> dict[str, Any]:
    """Project an upstream response body back to the client's shape.

    The gateway does not reformat responses: the upstream OpenAI body is relayed as-is for every
    shape, matching the Node gateway (no response reformatting).
    """
    if shape not in INBOUND_SHAPES:
        raise ValueError(f"unsupported inbound shape: {shape!r}")
    return body
