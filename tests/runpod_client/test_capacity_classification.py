"""RunPod answers client mistakes and permanent conditions with HTTP 500.

Three separate live cases returned 500 for something a retry cannot fix: pod
create with no capacity, deleting a volume that was already gone, and creating a
volume in an unknown datacenter. A substring match over the body cannot tell
those apart from a genuine outage, and "unavailable" matches almost any 5xx.
"""

from __future__ import annotations

import pytest

from pitwall.runpod_client import pods as pods_mod
from pitwall.runpod_client.pods import RunPodRestError, classify_runpod_failure
from pitwall.runpod_client.workloads import WorkloadConfig


def _rest_error(status: int, body: str) -> RunPodRestError:
    return RunPodRestError("POST", "pods", status, body)


def test_explicit_no_capacity_is_capacity() -> None:
    exc = _rest_error(500, '{"error":"There are no longer any instances available"}')
    assert classify_runpod_failure(exc) == "capacity"


def test_unknown_datacenter_is_permanent_not_capacity() -> None:
    exc = _rest_error(
        500, '{"error":"create network volume: Data center \\"NOT-A-DC-1\\" not found"}'
    )
    assert classify_runpod_failure(exc) == "permanent"


def test_a_bare_500_is_transient_not_capacity() -> None:
    """The live failure: a 500 with no capacity phrase burned the whole GPU list."""
    exc = _rest_error(500, '{"error":"Internal Server Error"}')
    assert classify_runpod_failure(exc) == "transient"


def test_a_4xx_is_permanent() -> None:
    exc = _rest_error(400, '{"error":"Extra input keys provided in request body"}')
    assert classify_runpod_failure(exc) == "permanent"


@pytest.mark.parametrize("status", [502, 503, 504])
def test_gateway_errors_are_transient(status: int) -> None:
    assert classify_runpod_failure(_rest_error(status, "")) == "transient"


def _workload(*gpu_types: str) -> WorkloadConfig:
    return WorkloadConfig(
        name="test",
        capability="test",
        gpu_types=list(gpu_types),
        container_disk_gb=10,
        min_vcpu=1,
        min_memory_gb=1,
        cloud_type="SECURE",
    )


def test_capacity_failure_skips_to_the_next_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted_gpu_types: list[list[str]] = []

    def create(method: str, path: str, *, json_body: dict[str, object], **kwargs: object) -> object:
        assert (method, path) == ("POST", "pods")
        gpu_types = json_body["gpuTypeIds"]
        assert isinstance(gpu_types, list)
        attempted_gpu_types.append(gpu_types)
        if len(attempted_gpu_types) == 1:
            raise _rest_error(500, '{"error":"There are no longer any instances available"}')
        return {"id": "pod_ok"}

    monkeypatch.setattr(pods_mod, "_rest_request", create)
    monkeypatch.setattr(pods_mod, "_legacy_rest_request", create)

    pod = pods_mod._create_pod_with_fallback_sync(
        name="test",
        template_id=None,
        image_name="image:sha",
        workload=_workload("NVIDIA L4", "NVIDIA RTX A4000"),
        env={},
        wait_for_readiness=False,
    )

    assert pod["id"] == "pod_ok"
    assert attempted_gpu_types == [["NVIDIA L4"], ["NVIDIA RTX A4000"]]


def test_transient_failure_retries_the_same_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    attempted_gpu_types: list[list[str]] = []
    backoffs: list[float] = []

    def create(method: str, path: str, *, json_body: dict[str, object], **kwargs: object) -> object:
        assert (method, path) == ("POST", "pods")
        gpu_types = json_body["gpuTypeIds"]
        assert isinstance(gpu_types, list)
        attempted_gpu_types.append(gpu_types)
        if len(attempted_gpu_types) == 1:
            raise _rest_error(500, '{"error":"Internal Server Error"}')
        return {"id": "pod_ok"}

    monkeypatch.setattr(pods_mod, "_rest_request", create)
    monkeypatch.setattr(pods_mod, "_legacy_rest_request", create)
    monkeypatch.setattr(pods_mod.time, "sleep", backoffs.append)

    pod = pods_mod._create_pod_with_fallback_sync(
        name="test",
        template_id=None,
        image_name="image:sha",
        workload=_workload("NVIDIA L4", "NVIDIA RTX A4000"),
        env={},
        wait_for_readiness=False,
    )

    assert pod["id"] == "pod_ok"
    assert attempted_gpu_types == [["NVIDIA L4"], ["NVIDIA L4"]]
    assert backoffs == [pods_mod.TRANSIENT_CREATE_BACKOFF_S]


def test_permanent_failure_raises_without_trying_another_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_gpu_types: list[list[str]] = []

    def create(method: str, path: str, *, json_body: dict[str, object], **kwargs: object) -> object:
        assert (method, path) == ("POST", "pods")
        gpu_types = json_body["gpuTypeIds"]
        assert isinstance(gpu_types, list)
        attempted_gpu_types.append(gpu_types)
        raise _rest_error(500, '{"error":"Data center NOT-A-DC-1 not found"}')

    monkeypatch.setattr(pods_mod, "_rest_request", create)
    monkeypatch.setattr(pods_mod, "_legacy_rest_request", create)

    with pytest.raises(RunPodRestError, match="NOT-A-DC-1 not found"):
        pods_mod._create_pod_with_fallback_sync(
            name="test",
            template_id=None,
            image_name="image:sha",
            workload=_workload("NVIDIA L4", "NVIDIA RTX A4000"),
            env={},
            wait_for_readiness=False,
        )

    assert attempted_gpu_types == [["NVIDIA L4"]]
