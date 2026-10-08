"""Workbench deadlines (port of ``timeouts.ts``)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, MutableMapping
from typing import Any

_SHELL_TOOLS = frozenset({"bash", "powershell"})


class ResponseIdleTimeout(TimeoutError):
    """A provider response body stopped yielding chunks after its headers arrived."""


def apply_solo_tool_deadline(event: MutableMapping[str, Any], timeout_ms: float | None) -> bool:
    """Cap a solo shell tool call at the profile deadline; return True when the input changed.

    Pi's built-in shell tools express their timeout in seconds, while Workbench profiles keep
    policy values in milliseconds. ``event`` carries ``toolName`` and a mutable ``input`` mapping,
    matching Pi's ``tool_call`` boundary.
    """
    if timeout_ms is None or event.get("toolName") not in _SHELL_TOOLS:
        return False
    maximum_seconds = timeout_ms / 1000
    tool_input = event["input"]
    requested = tool_input.get("timeout")
    if requested is None or (
        isinstance(requested, int | float)
        and not isinstance(requested, bool)
        and requested > maximum_seconds
    ):
        tool_input["timeout"] = maximum_seconds
        return True
    return False


def with_response_idle_timeout(
    body: AsyncIterable[bytes], timeout_ms: float | None
) -> AsyncIterable[bytes]:
    """Wrap a response body so a gap longer than ``timeout_ms`` between chunks raises.

    Headers are governed by the HTTP client; this covers a body that stops after headers.
    Chunks are pulled one at a time, so the wrapper applies backpressure to the source.
    """
    if timeout_ms is None or timeout_ms <= 0:
        return body
    return _idle_guarded(body, timeout_ms / 1000)


async def _idle_guarded(body: AsyncIterable[bytes], seconds: float) -> AsyncIterator[bytes]:
    iterator = aiter(body)
    try:
        while True:
            try:
                async with asyncio.timeout(seconds):
                    chunk = await anext(iterator)
            except StopAsyncIteration:
                return
            except TimeoutError:
                raise ResponseIdleTimeout("provider response inactivity timeout") from None
            yield chunk
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
