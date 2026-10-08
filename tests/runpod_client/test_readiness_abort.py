"""A container that cannot start must fail fast, not bill out the clock.

A pod whose image needs a newer CUDA than the host retried every ~16 s while
Pitwall polled readiness for the full startup_timeout_s at $0.74/hr. The pod
record shows it plainly: status RUNNING but runtime.uptime stuck at 0 with no
runtime ports, and the log tail names the reason.
"""

from __future__ import annotations

import pytest

from pitwall.runpod_client import pods as pods_mod
from pitwall.runpod_client.workloads import WorkloadConfig


def _pod(uptime: int | None, ports: list[dict[str, object]] | None) -> dict[str, object]:
    runtime: dict[str, object] = {}
    if uptime is not None:
        runtime["uptime"] = uptime
    if ports is not None:
        runtime["ports"] = ports
    return {"id": "pod_x", "status": "RUNNING", "runtime": runtime or None}


def test_uptime_stuck_at_zero_is_a_start_failure() -> None:
    reason = pods_mod._container_start_failure(_pod(0, []), seen_uptime=0)
    assert reason is not None
    assert "uptime" in reason


def test_pod_log_tail_reads_continuous_sse_without_waiting_for_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ContinuousResponse:
        status_code = 200

        def __enter__(self) -> ContinuousResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def iter_raw(self):
            yield b"first\n"
            yield b"CUDA driver version is insufficient\n"
            raise AssertionError("bounded readiness tail waited for SSE EOF")

    class Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> ContinuousResponse:
            del args, kwargs
            return ContinuousResponse()

    monkeypatch.setattr(pods_mod.httpx, "Client", Client)
    monkeypatch.setattr(pods_mod, "_require_api_key", lambda _api_key: "test-key")

    assert pods_mod._pod_log_tail("pod_x", lines=2) == (
        "first\nCUDA driver version is insufficient"
    )


def test_pod_log_tail_has_a_total_deadline_for_continuous_sub_line_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ContinuousResponse:
        status_code = 200

        def __enter__(self) -> ContinuousResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def iter_raw(self):
            for _ in range(20):
                yield b"keepalive"
            raise AssertionError("bounded readiness tail waited for SSE EOF")

    class Client:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def __enter__(self) -> Client:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> ContinuousResponse:
            del args, kwargs
            return ContinuousResponse()

    clock = iter((0.0, 0.01, 0.011, 0.02, 0.021, 0.03, 0.031, 0.06))
    monkeypatch.setattr(pods_mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(pods_mod.httpx, "Client", Client)
    monkeypatch.setattr(pods_mod, "_require_api_key", lambda _api_key: "test-key")
    monkeypatch.setattr(pods_mod, "POD_LOG_DIAGNOSTIC_TIMEOUT_S", 0.05)

    assert pods_mod._pod_log_tail("pod_x", lines=5) == "keepalivekeepalivekeepalive"


def test_a_pod_that_is_still_coming_up_is_not_a_failure() -> None:
    assert pods_mod._container_start_failure(_pod(None, None), seen_uptime=None) is None


def test_a_running_container_is_not_a_failure() -> None:
    running = _pod(42, [{"privatePort": 8000, "publicPort": 60533}])
    assert pods_mod._container_start_failure(running, seen_uptime=42) is None


def test_readiness_aborts_after_the_container_start_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck = _pod(0, [])
    monotonic_values = iter((0.0, 181.0, 601.0))
    monkeypatch.setattr(pods_mod.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(pods_mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(pods_mod, "_get_pod_sync_for_auth", lambda *args, **kwargs: stuck)
    monkeypatch.setattr(
        pods_mod,
        "_pod_log_tail",
        lambda pod_id, *, lines: "CUDA driver version is insufficient",
        raising=False,
    )

    with pytest.raises(pods_mod.ContainerStartFailure, match="CUDA driver version"):
        pods_mod._wait_for_pod_runtime_sync("pod_x", initial=stuck)


def test_create_terminates_a_pod_when_the_container_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[str] = []

    def create_pod(method: str, path: str, **kwargs: object) -> dict[str, object]:
        assert (method, path) == ("POST", "pods")
        return {"id": "pod_x", "status": "RUNNING"}

    def fail_readiness(pod_id: str, **kwargs: object) -> dict[str, object]:
        raise pods_mod.ContainerStartFailure(f"pod {pod_id} container failed")

    monkeypatch.setattr(pods_mod, "_rest_request", create_pod)
    monkeypatch.setattr(pods_mod, "_legacy_rest_request", create_pod)
    monkeypatch.setattr(pods_mod, "wait_for_pod_runtime_sync", fail_readiness)
    monkeypatch.setattr(
        pods_mod,
        "_terminate_pod_sync_for_auth",
        lambda pod_id, **kwargs: terminated.append(pod_id),
    )
    workload = WorkloadConfig(
        name="test",
        capability="test",
        gpu_types=["NVIDIA L4"],
        container_disk_gb=10,
        min_vcpu=1,
        min_memory_gb=1,
        cloud_type="SECURE",
    )

    with pytest.raises(pods_mod.ContainerStartFailure, match="pod pod_x container failed"):
        pods_mod._create_pod_with_fallback_sync(
            name="test",
            template_id=None,
            image_name="image:sha",
            workload=workload,
            env={},
        )

    assert terminated == ["pod_x"]


def _poll_sequence(monkeypatch: pytest.MonkeyPatch, uptimes: list[int]) -> None:
    import itertools

    pods = iter([_pod(uptime, None) for uptime in uptimes])
    clock = itertools.count(0.0, 1.0)
    monkeypatch.setattr(pods_mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(pods_mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(pods_mod, "_get_pod_sync_for_auth", lambda *args, **kwargs: next(pods))
    monkeypatch.setattr(
        pods_mod,
        "_pod_log_tail",
        lambda pod_id, *, lines: "exiting due to model loading error",
        raising=False,
    )


def test_a_container_that_keeps_restarting_fails_fast_with_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live defect 7: a crash-looping container burned the whole startup budget.

    The pod stays RUNNING and the container has run (uptime > 0), so only the uptime
    falling back is visible: each fall is a restart.
    """
    _poll_sequence(monkeypatch, [40, 5, 35, 4, 30, 3])
    with pytest.raises(pods_mod.ContainerStartFailure, match="restarted 2 times") as failure:
        pods_mod._wait_for_pod_runtime_sync("pod_x", initial=_pod(10, None), timeout_s=600)
    assert "model loading error" in str(failure.value)


def test_one_restart_during_a_slow_start_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One restart, then the container keeps running until the (fake) deadline.
    _poll_sequence(monkeypatch, [40, 5, *range(6, 700, 5)])
    with pytest.raises(pods_mod.PodStartupTimeout):
        pods_mod._wait_for_pod_runtime_sync("pod_x", initial=_pod(10, None), timeout_s=100)
