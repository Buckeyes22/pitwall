"""Lite prompt compression for the gateway (port of the Node gateway src/compression.ts).

``off`` is the safe default. ``rtk``, ``caveman``, and ``stacked`` all run the same lite pass:
whitespace collapse on every string message, plus consecutive-duplicate-line removal on system
messages. The full upstream engines are not wired in.
"""

from __future__ import annotations

import re
from typing import Any, Literal

CompressionPolicy = Literal["off", "rtk", "caveman", "stacked"]

_BLANK_RUNS = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_LEADING_WS_AFTER_NEWLINE = re.compile(r"\n[ \t]+")


def _collapse_whitespace(value: str) -> str:
    value = _BLANK_RUNS.sub("\n\n", value)
    value = _TRAILING_WS.sub("", value)
    value = _LEADING_WS_AFTER_NEWLINE.sub("\n", value)
    return value.strip()


def _dedup_system_prompt(text: str) -> str:
    # Consecutive duplicates only. A global "seen" set would delete the closing line of every
    # later code fence and any bullet that legitimately recurs in a later section.
    kept: list[str] = []
    previous: str | None = None
    for raw in text.split("\n"):
        norm = raw.strip().lower()
        if not norm:
            kept.append(raw)
            previous = None
            continue
        if norm == previous:
            continue
        previous = norm
        kept.append(raw)
    return "\n".join(kept)


def compress_request(body: dict[str, Any], policy: CompressionPolicy) -> dict[str, Any]:
    """Return ``body`` compressed under ``policy``; the input is never mutated.

    The same object is returned when nothing changes (always for ``off``).
    """
    if policy == "off":
        return body
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    changed = False
    out: list[Any] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            out.append(message)
            continue
        content: str = message["content"]
        squeezed = _collapse_whitespace(content)
        if message.get("role") == "system":
            squeezed = _dedup_system_prompt(squeezed)
        if squeezed != content:
            changed = True
            out.append({**message, "content": squeezed})
        else:
            out.append(message)
    if not changed:
        return body
    return {**body, "messages": out}
