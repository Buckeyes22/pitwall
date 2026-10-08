"""One retry helper serves every RunPod HTTP client; ambiguous failures are not replayed."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

from pitwall.runpod_client.queue import QueueClient

pytestmark = pytest.mark.anyio

_CLIENT_DIR = Path(__file__).resolve().parents[2] / "src" / "pitwall" / "runpod_client"


class _Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def _client(handler: httpx.MockTransport, recorder: _Recorder) -> QueueClient:
    return QueueClient(
        api_key="k",
        retry_delays=(0.5, 1.5),
        sleep=recorder.sleep,
        transport=handler,
    )


def _job() -> httpx.Response:
    return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})


async def test_run_post_not_retried_after_read_timeout() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("no response", request=request)

    recorder = _Recorder()
    client = _client(httpx.MockTransport(handler), recorder)

    with pytest.raises(httpx.ReadTimeout):
        await client.run("ep1", input={"x": 1})

    assert calls == 1
    assert recorder.sleeps == []


async def test_run_post_not_retried_on_5xx() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    recorder = _Recorder()
    client = _client(httpx.MockTransport(handler), recorder)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await client.run("ep1", input={"x": 1})

    assert raised.value.response.status_code == 503
    assert calls == 1


async def test_run_post_retried_on_connect_error() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("refused", request=request)
        return _job()

    recorder = _Recorder()
    client = _client(httpx.MockTransport(handler), recorder)

    job = await client.run("ep1", input={"x": 1})

    assert job.id == "job-1"
    assert calls == 2
    assert recorder.sleeps == [0.5]


async def test_run_post_retried_on_429_with_retry_after() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return _job()

    recorder = _Recorder()
    client = _client(httpx.MockTransport(handler), recorder)

    job = await client.run("ep1", input={"x": 1})

    assert job.id == "job-1"
    assert calls == 2
    assert recorder.sleeps == [2.0]


def _loop_uses_retry_machinery(node: ast.For | ast.AsyncFor | ast.While) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and ("sleep" in child.id or "attempt" in child.id):
            return True
        if isinstance(child, ast.Attribute) and "sleep" in child.attr:
            return True
    return False


@pytest.mark.parametrize("module", ["queue", "lb", "serverless", "serverless_lb", "mounts"])
def test_all_clients_use_shared_helper(module: str) -> None:
    source = (_CLIENT_DIR / f"{module}.py").read_text()
    tree = ast.parse(source)

    loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)) and _loop_uses_retry_machinery(node)
    ]
    assert loops == [], f"{module}.py still has a retry loop"
    assert "retry" in source and "send_with_retry" in source, f"{module}.py must call the helper"
    assert "on_429" not in source
