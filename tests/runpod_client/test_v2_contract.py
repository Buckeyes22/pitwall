from __future__ import annotations

import json
import shlex
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from pitwall.runpod_client import mounts, pods, registry, serverless, templates
from pitwall.runpod_client.workloads import WorkloadConfig

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "runpod_v2_contract_2026-08-31.json"
)


def _contract() -> dict[str, object]:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def _schema_fields(model: type[object]) -> set[str]:
    schema = model.model_json_schema(by_alias=True)  # type: ignore[attr-defined]  # reason: model is typed as object here; model_json_schema exists on the pydantic models passed in
    return set(schema["properties"])


def test_strict_request_models_only_expose_official_v2_fields() -> None:
    allowed = _contract()["allowedWriteFields"]
    assert isinstance(allowed, dict)

    assert _schema_fields(pods.V2CreatePodRequest) <= set(allowed["podCreate"])
    assert _schema_fields(pods.UpdatePodRequest) <= set(allowed["podUpdate"])
    assert _schema_fields(pods.V2PodActionRequest) == set(allowed["podAction"])
    assert _schema_fields(templates.V2CreateTemplateRequest) <= set(allowed["templateCreate"])
    assert _schema_fields(templates.V2UpdateTemplateRequest) <= set(allowed["templateUpdate"])
    assert _schema_fields(mounts.V2CreateNetworkVolumeRequest) <= set(
        allowed["networkVolumeCreate"]
    )
    assert _schema_fields(mounts.V2UpdateNetworkVolumeRequest) <= set(
        allowed["networkVolumeUpdate"]
    )
    assert _schema_fields(registry.RegistryAuthCreateInput) == set(allowed["registryCreate"])
    assert _schema_fields(serverless.V2CreateEndpointRequest) <= set(allowed["serverlessCreate"])


def test_v1_pod_field_is_rejected_by_strict_v2_model() -> None:
    payload = {
        "name": "strict",
        "image": "example.test/image:sha",
        "cloud": "SECURE",
        "gpu": {"id": "NVIDIA L4", "count": 1},
        "disk": 20,
        "imageName": "must-not-leak",
    }

    with pytest.raises(ValidationError, match="imageName"):
        pods.V2CreatePodRequest.model_validate(payload)


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-c", "print('spaces and quotes')"],
        ["", "two words", "quote'and\"double"],
        ["Unicode-λ", "$(touch /tmp/no)", "; rm -rf /not-run", "*"],
    ],
)
def test_command_argv_round_trips_through_v2_args(argv: list[str]) -> None:
    encoded = pods.argv_to_v2_args(argv)

    assert shlex.split(encoded) == argv


def test_nul_command_fails_before_any_provider_write(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[object] = []
    monkeypatch.setattr(pods, "_rest_request", lambda *args, **kwargs: writes.append(args))
    workload = WorkloadConfig(
        name="strict-v2",
        capability="test",
        gpu_types=["NVIDIA L4"],
        container_disk_gb=20,
        min_vcpu=None,
        min_memory_gb=None,
        cloud_type="SECURE",
    )

    with pytest.raises(pods.RunPodError, match="NUL"):
        pods._create_pod_with_fallback_sync(
            name="strict-v2",
            template_id=None,
            image_name="example.test/image:sha",
            workload=workload,
            env={},
            docker_start_cmd=["python", "bad\x00argument"],
            wait_for_readiness=False,
        )

    assert writes == []


def test_v2_gpu_and_cloud_fallback_order_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[str, str]] = []

    def create(
        method: str,
        path: str,
        *,
        json_body: dict[str, object],
        **_: object,
    ) -> dict[str, object]:
        assert (method, path) == ("POST", "pods")
        gpu = json_body["gpu"]
        assert isinstance(gpu, dict)
        assert set(gpu) <= {"allowedCudaVersions", "count", "id", "minCudaVersion"}
        gpu_id = str(gpu["id"])
        cloud = str(json_body["cloud"])
        attempts.append((gpu_id, cloud))
        if gpu_id == "NVIDIA L4":
            raise pods.RunPodRestError(
                method,
                path,
                503,
                '{"detail":"insufficient capacity"}',
            )
        return {"id": "pod-v2", "gpu": {"id": gpu_id, "count": 1}, "cloud": cloud}

    monkeypatch.setattr(pods, "_rest_request", create)
    monkeypatch.setattr(
        pods,
        "_legacy_rest_request",
        lambda *args, **kwargs: pytest.fail("floorless custom ordering must use REST v2"),
    )
    workload = WorkloadConfig(
        name="strict-v2",
        capability="test",
        gpu_types=["NVIDIA L4", "NVIDIA RTX A4000"],
        container_disk_gb=20,
        min_vcpu=None,
        min_memory_gb=None,
        cloud_type="ALL",
    )

    result = pods._create_pod_with_fallback_sync(
        name="strict-v2",
        template_id=None,
        image_name="example.test/image:sha",
        workload=workload,
        env={},
        wait_for_readiness=False,
    )

    assert result["id"] == "pod-v2"
    assert attempts == [
        ("NVIDIA L4", "COMMUNITY"),
        ("NVIDIA L4", "SECURE"),
        ("NVIDIA RTX A4000", "COMMUNITY"),
    ]


def test_v2_create_does_not_retry_ambiguous_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def ambiguous_failure(*args: object, **kwargs: object) -> object:
        nonlocal attempts
        attempts += 1
        raise pods.RunPodRestError("POST", "pods", 503, '{"detail":"unknown outcome"}')

    monkeypatch.setattr(pods, "_rest_request", ambiguous_failure)
    monkeypatch.setattr(
        pods,
        "_legacy_rest_request",
        lambda *args, **kwargs: pytest.fail("floorless request must use REST v2"),
    )
    workload = WorkloadConfig(
        name="strict-v2",
        capability="test",
        gpu_types=["NVIDIA L4"],
        container_disk_gb=20,
        min_vcpu=None,
        min_memory_gb=None,
        cloud_type="SECURE",
    )

    with pytest.raises(pods.RunPodRestError, match="unknown outcome"):
        pods._create_pod_with_fallback_sync(
            name="strict-v2",
            template_id=None,
            image_name="example.test/image:sha",
            workload=workload,
            env={},
            wait_for_readiness=False,
        )

    assert attempts == 1


def test_unsupported_resource_floors_select_bounded_v1_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def legacy_create(
        method: str,
        path: str,
        *,
        json_body: dict[str, object],
        **_: object,
    ) -> dict[str, object]:
        calls.append(json_body)
        return {"id": "legacy-pod", "gpuTypeId": "NVIDIA L4", "gpuCount": 1}

    monkeypatch.setattr(pods, "_legacy_rest_request", legacy_create)
    monkeypatch.setattr(
        pods,
        "_rest_request",
        lambda *args, **kwargs: pytest.fail("resource floors must not leak into REST v2"),
    )
    workload = WorkloadConfig(
        name="floored",
        capability="test",
        gpu_types=["NVIDIA L4"],
        container_disk_gb=20,
        min_vcpu=4,
        min_memory_gb=16,
        cloud_type="SECURE",
    )

    result = pods._create_pod_with_fallback_sync(
        name="floored",
        template_id=None,
        image_name="example.test/image:sha",
        workload=workload,
        env={},
        wait_for_readiness=False,
    )

    assert result["id"] == "legacy-pod"
    assert calls[0]["minVCPUPerGPU"] == 4
    assert calls[0]["minRAMPerGPU"] == 16


def test_availability_data_center_priority_selects_bounded_v1_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def legacy_create(
        method: str,
        path: str,
        *,
        json_body: dict[str, object],
        **_: object,
    ) -> dict[str, object]:
        calls.append(json_body)
        return {"id": "legacy-pod", "gpuTypeId": "NVIDIA L4", "gpuCount": 1}

    monkeypatch.setattr(pods, "_legacy_rest_request", legacy_create)
    monkeypatch.setattr(
        pods,
        "_rest_request",
        lambda *args, **kwargs: pytest.fail("data-center priority must not be discarded"),
    )
    workload = WorkloadConfig(
        name="priority",
        capability="test",
        gpu_types=["NVIDIA L4"],
        container_disk_gb=20,
        min_vcpu=None,
        min_memory_gb=None,
        cloud_type="SECURE",
        data_center_priority="availability",
    )

    result = pods._create_pod_with_fallback_sync(
        name="priority",
        template_id=None,
        image_name="example.test/image:sha",
        workload=workload,
        env={},
        data_center_id="US-TX-3",
        wait_for_readiness=False,
    )

    assert result["id"] == "legacy-pod"
    assert calls[0]["dataCenterIds"] == ["US-TX-3"]
    assert calls[0]["dataCenterPriority"] == "availability"


@respx.mock
def test_safe_get_retries_once_and_validates_v2_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    route = respx.get("https://api.runpod.test/v2/pods").mock(
        side_effect=[
            httpx.Response(503, json={"title": "temporary"}),
            httpx.Response(200, json={"pods": []}),
        ]
    )

    assert pods._rest_request(
        "GET",
        "pods",
        rest_api_url="https://api.runpod.test/v2",
    ) == {"pods": []}
    assert route.call_count == 2


@respx.mock
def test_rest_error_redacts_credentials_and_bounds_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "rpa_test_secret_123456789"
    monkeypatch.setenv("RUNPOD_API_KEY", token)
    respx.get("https://api.runpod.test/v2/pods").mock(
        return_value=httpx.Response(400, text=f'{{"detail":"Bearer {token}"}}')
    )

    with pytest.raises(pods.RunPodRestError) as exc_info:
        pods._rest_request(
            "GET",
            "pods",
            rest_api_url="https://api.runpod.test/v2",
        )

    assert token not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)
