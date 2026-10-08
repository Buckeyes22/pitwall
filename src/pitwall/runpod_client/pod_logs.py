"""Public, bounded RunPod pod-log reader.

The endpoint is the existing provider-supported ``GET pods/{pod_id}/logs``
diagnostic endpoint used by the private readiness helper in ``pods.py``.  This
client gives operator surfaces a deliberately narrow public contract without
adding remote execution or any new provider endpoint.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Final

import httpx

from pitwall.runpod_client.pods import RunPodError, RunPodRestError
from pitwall.runpod_credentials import DEFAULT_RUNPOD_REST_URL
from pitwall.security.redaction import redact_text

_POD_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ERROR_BODY_LIMIT: Final = 4096
_STREAM_CHUNK_BYTES: Final = 16 * 1024
_PEEK_S: Final = 1.0


@dataclass(frozen=True, slots=True)
class BoundedPodLogPayload:
    """Raw bounded log payload retained for service-side structured parsing."""

    text: str
    bytes_read: int
    truncated: bool


class BoundedPodLogClient:
    """Read one size- and time-bounded provider log response.

    ``api_key`` is intentionally kept only in this client boundary.  It is
    never represented by a public model, returned value, URL, or logger call.
    """

    def __init__(
        self,
        *,
        api_key: str,
        rest_base_url: str = DEFAULT_RUNPOD_REST_URL,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise RunPodError("RunPod API key is not configured for bounded pod logs")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        base_url = httpx.URL(rest_base_url)
        if base_url.scheme not in {"http", "https"} or base_url.host is None:
            raise ValueError("rest_base_url must be an absolute HTTP(S) URL")
        if base_url.username or base_url.password or base_url.query or base_url.fragment:
            raise ValueError("rest_base_url must not include credentials, query, or fragment")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._client = httpx.AsyncClient(
            base_url=f"{str(base_url).rstrip('/')}/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def read(
        self,
        pod_id: str,
        *,
        max_lines: int,
        max_bytes: int,
    ) -> BoundedPodLogPayload:
        """Read at most ``max_bytes`` / ``max_lines`` from the supported pod-log endpoint.

        ``truncated`` is true when content beyond a cap was seen.  Reaching a cap exactly
        peeks for one more chunk, for at most ``min(remaining deadline, 1 s)``: more content
        means ``True`` and end of stream means ``False``.  If the peek times out with the
        stream still open, the result is ``True``: a live stream may still produce content,
        so completeness is never claimed.
        """
        if not _POD_ID_RE.fullmatch(pod_id):
            raise ValueError("pod_id is invalid")
        if isinstance(max_lines, bool) or not isinstance(max_lines, int) or max_lines <= 0:
            raise ValueError("max_lines must be a positive integer")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")

        data = bytearray()
        truncated = False
        deciding = False
        try:
            # HTTPX's read timeout is an inactivity timeout.  A provider log
            # stream can therefore keep this request alive forever by sending
            # a byte periodically; the public contract is a total deadline.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._timeout_s
            async with asyncio.timeout_at(deadline):
                async with self._client.stream(
                    "GET",
                    f"pods/{pod_id}/logs",
                    params={"lines": max_lines},
                ) as response:
                    if response.status_code >= 400:
                        error_body = await _read_error_body(response)
                        raise RunPodRestError(
                            "GET",
                            f"pods/{pod_id}/logs",
                            response.status_code,
                            redact_text(error_body, secrets=(self._api_key,)),
                        )

                    # Do not ask HTTPX's ByteChunker to fill a large chunk:
                    # an open log stream may never produce that much data,
                    # even when the line/byte cap has already been reached.
                    chunks = response.aiter_bytes().__aiter__()
                    while True:
                        try:
                            if deciding:
                                peek_s = min(max(deadline - loop.time(), 0.0), _PEEK_S)
                                async with asyncio.timeout(peek_s):
                                    chunk = await anext(chunks)
                            else:
                                chunk = await anext(chunks)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            if not deciding:
                                raise
                            # Open and silent at the cap: content may still arrive.
                            truncated = True
                            break
                        if not chunk:
                            continue
                        if deciding:
                            # A cap was reached exactly and one more byte exists.
                            truncated = True
                            break
                        kept = chunk[: max_bytes - len(data)]
                        cut = len(kept) < len(chunk)
                        newline_budget = max_lines - data.count(b"\n")
                        newline_positions = [
                            index for index, value in enumerate(kept) if value == 0x0A
                        ]
                        if len(newline_positions) >= newline_budget:
                            # Keep whole lines only.  A trailing partial line
                            # after the last permitted newline is line number
                            # ``max_lines + 1``; drop it and report truncation
                            # instead of leaking it into the bounded payload.
                            boundary = newline_positions[newline_budget - 1] + 1
                            cut = cut or boundary < len(kept)
                            kept = kept[:boundary]
                        data.extend(kept)
                        if cut:
                            truncated = True
                            break
                        if len(data) >= max_bytes or data.count(b"\n") >= max_lines:
                            # Exactly at a cap: only another byte proves content
                            # remains, so read at most one more (within the deadline).
                            deciding = True
        except TimeoutError as exc:
            if not deciding:
                raise RunPodError("pod log request exceeded total timeout") from exc
            truncated = True  # the total deadline ran out while peeking past the cap

        return BoundedPodLogPayload(
            text=redact_text(data.decode("utf-8", errors="replace"), secrets=(self._api_key,)),
            bytes_read=len(data),
            truncated=truncated,
        )


async def _read_error_body(response: httpx.Response) -> str:
    """Read a small error prefix so error mapping cannot buffer a huge body."""
    data = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=_ERROR_BODY_LIMIT):
        remaining = _ERROR_BODY_LIMIT - len(data)
        if remaining <= 0:
            break
        data.extend(chunk[:remaining])
        if len(data) >= _ERROR_BODY_LIMIT:
            break
    return data.decode("utf-8", errors="replace")


__all__ = ["BoundedPodLogClient", "BoundedPodLogPayload"]
