from __future__ import annotations

import asyncio

import pytest

from pitwall.api.leases import launch as launch_mod
from tests.hang_guard import HANG_GUARD_SECS


class _Boom(RuntimeError):
    """Stands in for the audit write that failed in production."""


@pytest.mark.asyncio
async def test_arm_failure_terminates_the_pod_it_created() -> None:
    terminated: list[str] = []

    async def _fail_arm() -> None:
        raise _Boom("audit write failed")

    def _terminate(pod_id: str) -> None:
        terminated.append(pod_id)

    with pytest.raises(_Boom):
        await launch_mod._abandon_on_failure(
            pool=None,
            lease_id="lease_x",
            pod_id="pod_x",
            terminate=_terminate,
            operation=_fail_arm,
        )

    assert terminated == ["pod_x"], "the pod created by this launch must be terminated"


@pytest.mark.asyncio
async def test_cleanup_finishes_after_cancellation() -> None:
    release = asyncio.Event()
    finished = False

    async def cleanup() -> str:
        nonlocal finished
        await release.wait()
        finished = True
        return "cleaned"

    task = asyncio.create_task(launch_mod._await_cleanup(cleanup()))
    await asyncio.sleep(0)
    task.cancel()
    release.set()

    assert await task == "cleaned"
    assert finished is True


@pytest.mark.asyncio
async def test_already_cancelled_cleanup_does_not_spin() -> None:
    async def cleanup() -> None:
        raise asyncio.CancelledError

    assert (
        await asyncio.wait_for(launch_mod._await_cleanup(cleanup()), timeout=HANG_GUARD_SECS)
        is None
    )


def test_launch_failure_payload_redacts_secret_assignments() -> None:
    payload = launch_mod._launch_failure_payload(
        RuntimeError("upstream failed PITWALL_ENDPOINT_KEY=synthetic-secret")
    )

    assert payload["type"] == "RuntimeError"
    assert "synthetic-secret" not in payload["message"]
    assert "[REDACTED]" in payload["message"]
