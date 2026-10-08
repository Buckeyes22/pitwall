"""Cancelling an awaited pod create cannot orphan the pod the worker thread still creates."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from pitwall.runpod_client import pods
from pitwall.runpod_client.workloads import WorkloadConfig
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.anyio


def _workload() -> WorkloadConfig:
    return WorkloadConfig(
        name="test",
        capability="test",
        gpu_types=["NVIDIA L4"],
        container_disk_gb=10,
        min_vcpu=1,
        min_memory_gb=1,
        cloud_type="SECURE",
        allowed_cuda_versions=["12.8"],
    )


async def test_cancelled_create_terminates_the_pod_the_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    terminated: list[str] = []

    def slow_create(**kwargs: Any) -> dict[str, Any]:
        started.set()
        release.wait(timeout=HANG_GUARD_SECS)
        return {"id": "pod-late"}

    async def fake_terminate(pod_id: str) -> None:
        terminated.append(pod_id)

    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", slow_create)
    monkeypatch.setattr(pods, "terminate_pod", fake_terminate)

    task = asyncio.ensure_future(
        pods.create_pod_with_fallback(
            name="pitwall-test",
            template_id=None,
            image_name="image:sha",
            workload=_workload(),
            env={},
        )
    )
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done(), "cancellation must wait for the create thread to settle"
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert terminated == ["pod-late"]


class _CountingAsyncio:
    """``asyncio`` as ``pods`` sees it, counting ``shield`` and ``wait`` calls (wake-ups)."""

    def __init__(self) -> None:
        self.shield_calls = 0

    def shield(self, future: Any) -> Any:
        self.shield_calls += 1
        return asyncio.shield(future)

    def wait(self, futures: Any, **kwargs: Any) -> Any:
        self.shield_calls += 1
        return asyncio.wait(futures, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


async def test_anyio_cancellation_waits_for_the_thread_without_spinning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP tool calls run in an anyio scope, whose cancellation is re-delivered every loop
    iteration. The cleanup must still wait for the thread, terminate its pod, and wake only
    a bounded number of times while it waits."""
    import anyio

    started = threading.Event()
    release = threading.Event()
    terminated: list[str] = []
    counting = _CountingAsyncio()

    def slow_create(**kwargs: Any) -> dict[str, Any]:
        started.set()
        release.wait(timeout=HANG_GUARD_SECS)
        return {"id": "pod-late"}

    async def fake_terminate(pod_id: str) -> None:
        terminated.append(pod_id)

    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", slow_create)
    monkeypatch.setattr(pods, "terminate_pod", fake_terminate)
    monkeypatch.setattr(pods, "asyncio", counting)
    scopes: list[anyio.CancelScope] = []

    async def call_in_scope() -> bool:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await pods.create_pod_with_fallback(
                name="pitwall-test",
                template_id=None,
                image_name="image:sha",
                workload=_workload(),
                env={},
            )
        return scope.cancelled_caught

    task = asyncio.ensure_future(call_in_scope())
    try:
        await asyncio.to_thread(started.wait, HANG_GUARD_SECS)
        scopes[0].cancel()
        for _ in range(200):  # anyio re-cancels on each of these iterations
            await asyncio.sleep(0)
        assert not task.done(), "the cleanup must wait for the create thread"
    finally:
        release.set()

    assert await task is True
    assert terminated == ["pod-late"]
    # One shield for the create, then a handful for the cleanup's waits; a spinning loop
    # would make one per event-loop iteration (hundreds here).
    assert counting.shield_calls <= 6, counting.shield_calls


@pytest.mark.parametrize("failure", ["thread", "terminate"])
async def test_anyio_cancellation_survives_a_failing_thread_or_terminate(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """The cancellation is still delivered when the thread or the terminate raises, and the
    log names a pod that survived its terminate."""
    import anyio

    started = threading.Event()
    release = threading.Event()

    def slow_create(**kwargs: Any) -> dict[str, Any]:
        started.set()
        release.wait(timeout=HANG_GUARD_SECS)
        if failure == "thread":
            raise RuntimeError("create failed THREAD-CANARY")
        return {"id": "pod-survivor"}

    async def failing_terminate(pod_id: str) -> None:
        raise RuntimeError("terminate failed")

    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", slow_create)
    monkeypatch.setattr(pods, "terminate_pod", failing_terminate)
    scopes: list[anyio.CancelScope] = []

    async def call_in_scope() -> bool:
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await pods.create_pod_with_fallback(
                name="pitwall-test",
                template_id=None,
                image_name="image:sha",
                workload=_workload(),
                env={},
            )
        return scope.cancelled_caught

    task = asyncio.ensure_future(call_in_scope())
    with caplog.at_level("WARNING", logger=pods.log.name):
        try:
            await asyncio.to_thread(started.wait, HANG_GUARD_SECS)
            scopes[0].cancel()
            for _ in range(50):
                await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        assert await task is True  # the scope caught the cancellation; nothing replaced it

    messages = [record.getMessage() for record in caplog.records]
    if failure == "thread":
        assert any("failed before producing a pod" in message for message in messages)
    else:
        assert any("terminating pod pod-survivor failed" in message for message in messages), (
            messages
        )
