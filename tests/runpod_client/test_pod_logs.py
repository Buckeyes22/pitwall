"""Hermetic bounded contracts for the public provider pod-log reader."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from pitwall.runpod_client.pod_logs import BoundedPodLogClient
from pitwall.runpod_client.pods import RunPodError, RunPodRestError

pytestmark = pytest.mark.anyio


class _ContinuousLogStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"first\n"
        yield b"second\n"
        yield b"third\n"
        raise AssertionError("bounded reader waited for SSE EOF")


class _SlowContinuousLogStream(httpx.AsyncByteStream):
    def __init__(self, chunk: bytes = b"keepalive") -> None:
        self.chunk = chunk

    async def __aiter__(self):
        while True:
            await asyncio.sleep(0.01)
            yield self.chunk


async def test_bounded_log_client_returns_from_continuous_sse_at_line_cap() -> None:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=_ContinuousLogStream())
        ),
    )
    try:
        payload = await client.read("pod-1", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert payload.text == "first\nsecond\n"
    assert payload.bytes_read == 13
    assert payload.truncated


async def test_bounded_log_client_enforces_total_deadline_for_continuous_chunks() -> None:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        timeout_s=0.05,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=_SlowContinuousLogStream())
        ),
    )
    try:
        with pytest.raises(RunPodError, match="total timeout"):
            await client.read("pod-1", max_lines=100, max_bytes=4096)
    finally:
        await client.aclose()


async def test_bounded_log_client_uses_existing_log_endpoint_and_line_cap() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"first\nsecond\n")

    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(handler),
    )
    try:
        payload = await client.read("pod-1", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert payload.text == "first\nsecond\n"
    assert payload.bytes_read == 13
    assert not payload.truncated
    assert seen[0].url.path == "/v2/pods/pod-1/logs"
    assert seen[0].url.params["lines"] == "2"
    assert seen[0].headers["authorization"] == "Bearer test-key"


async def test_bounded_log_client_drops_partial_line_after_exact_newline_budget() -> None:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"first\nsecond")
        ),
    )
    try:
        payload = await client.read("pod-1", max_lines=1, max_bytes=64)
    finally:
        await client.aclose()

    assert payload.text == "first\n"
    assert payload.bytes_read == 6
    assert payload.truncated


async def test_bounded_log_client_drops_partial_line_after_exact_newline_budget_over_stream() -> (
    None
):
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"one\ntwo\nthree")
        ),
    )
    try:
        payload = await client.read("pod-1", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert payload.text == "one\ntwo\n"
    assert payload.bytes_read == 8
    assert payload.truncated


async def test_bounded_log_client_truncates_bytes_without_unbounded_buffering() -> None:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"abcdefgh")),
    )
    try:
        payload = await client.read("pod-1", max_lines=2, max_bytes=3)
    finally:
        await client.aclose()

    assert payload.text == "abc"
    assert payload.bytes_read == 3
    assert payload.truncated


async def test_provider_error_is_bounded_and_redacts_the_control_plane_token() -> None:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, text="Authorization: Bearer test-key")
        ),
    )
    try:
        with pytest.raises(RunPodRestError) as exc_info:
            await client.read("pod-1", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert exc_info.value.status_code == 503
    assert "test-key" not in (exc_info.value.body or "")


async def test_provider_error_body_has_the_same_total_deadline() -> None:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        timeout_s=0.05,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, stream=_SlowContinuousLogStream(b"error"))
        ),
    )
    try:
        with pytest.raises(RunPodError, match="total timeout") as exc_info:
            await client.read("pod-1", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert "test-key" not in str(exc_info.value)


async def test_log_client_rejects_unsafe_ids_before_transport() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ValueError, match="pod_id is invalid"):
            await client.read("../pod", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert not called


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


async def _read(*chunks: bytes, max_lines: int = 100, max_bytes: int = 1024) -> tuple[str, bool]:
    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=_Chunks(*chunks))
        ),
    )
    try:
        payload = await client.read("pod-1", max_lines=max_lines, max_bytes=max_bytes)
    finally:
        await client.aclose()
    return payload.text, payload.truncated


@pytest.mark.parametrize(
    ("chunks", "max_bytes", "text", "truncated"),
    [
        ((b"abc",), 3, "abc", False),
        ((b"abcd",), 3, "abc", True),
        ((b"ab", b"c"), 3, "abc", False),
        ((b"ab", b"cd"), 3, "abc", True),
        ((b"abc", b"d"), 3, "abc", True),
        ((b"abc", b""), 3, "abc", False),
        ((b"abc",), 4, "abc", False),
    ],
)
async def test_byte_cap_reports_truncation_exactly(
    chunks: tuple[bytes, ...], max_bytes: int, text: str, truncated: bool
) -> None:
    assert await _read(*chunks, max_bytes=max_bytes) == (text, truncated)


@pytest.mark.parametrize(
    ("chunks", "max_lines", "text", "truncated"),
    [
        ((b"a\nb\n",), 2, "a\nb\n", False),
        ((b"a\nb\nc\n",), 2, "a\nb\n", True),
        ((b"a\nb\nc",), 2, "a\nb\n", True),
        ((b"a\n", b"b\n"), 2, "a\nb\n", False),
        ((b"a\n", b"b\n", b"c"), 2, "a\nb\n", True),
        ((b"a\nb\n", b""), 2, "a\nb\n", False),
        ((b"a\nb",), 2, "a\nb", False),
    ],
)
async def test_line_cap_reports_truncation_exactly(
    chunks: tuple[bytes, ...], max_lines: int, text: str, truncated: bool
) -> None:
    assert await _read(*chunks, max_lines=max_lines) == (text, truncated)


async def test_exact_cap_on_an_open_silent_stream_peeks_briefly_and_reports_truncated() -> None:
    """A live stream may still produce content: do not claim completeness, do not wait 30 s."""

    class _OpenAfterCap(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first\nsecond\n"
            await asyncio.sleep(3600)
            yield b"never"

    client = BoundedPodLogClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_base_url="https://api.runpod.test/v2",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, stream=_OpenAfterCap())),
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        payload = await client.read("pod-1", max_lines=2, max_bytes=64)
    finally:
        await client.aclose()

    assert loop.time() - started < 5.0
    assert payload.text == "first\nsecond\n"
    assert payload.truncated


async def test_exact_cap_then_eof_is_not_truncated() -> None:
    assert await _read(b"first\n", b"second\n", max_lines=2) == ("first\nsecond\n", False)
    assert await _read(b"abc", max_bytes=3) == ("abc", False)
