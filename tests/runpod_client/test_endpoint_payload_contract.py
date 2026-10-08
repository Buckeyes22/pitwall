"""RunPod REST v2 serverless request and response contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pitwall.runpod_client.serverless import (
    EndpointScalingConfig,
    V2CreateEndpointRequest,
    V2EndpointGpuRequest,
    _parse_endpoint,
)


def test_scaling_serializes_nested_v2_shape() -> None:
    payload = EndpointScalingConfig(workers_min=0, workers_max=2, idle_timeout=5).to_request_json()

    assert payload == {
        "workers": {"min": 0, "max": 2, "idleTimeout": 5},
        "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4.0},
        "flashboot": "OFF",
    }


def test_create_request_forbids_v1_endpoint_fields() -> None:
    with pytest.raises(ValidationError):
        V2CreateEndpointRequest.model_validate(
            {
                "name": "endpoint",
                "type": "QUEUE",
                "gpu": {"pools": ["ADA_24"]},
                "workers": {"min": 0, "max": 2, "idleTimeout": 5},
                "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
                "flashboot": "OFF",
                "templateId": "tmpl",
                "gpuTypeIds": ["NVIDIA L4"],
            }
        )


def test_nested_request_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        V2CreateEndpointRequest.model_validate(
            {
                "name": "endpoint",
                "type": "QUEUE",
                "gpu": {"pools": ["ADA_24"], "gpuTypeIds": ["NVIDIA L4"]},
                "workers": {"min": 0, "max": 2, "idleTimeout": 5},
                "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
                "flashboot": "OFF",
                "templateId": "tmpl",
            }
        )


def test_gpu_selection_uses_pool_and_exclusion_shape() -> None:
    payload = V2EndpointGpuRequest(
        pools=["ADA_24"],
        excluded_types=["NVIDIA RTX 4090"],
    ).model_dump(by_alias=True)

    assert payload == {
        "pools": ["ADA_24"],
        "excludedTypes": ["NVIDIA RTX 4090"],
        "count": 1,
    }


def test_parse_reads_nested_v2_response() -> None:
    endpoint = _parse_endpoint(
        {
            "id": "ep_1",
            "name": "n",
            "workers": {"min": 0, "max": 1, "idleTimeout": 5},
            "scaling": {"type": "QUEUE_DELAY", "queueDelay": 4},
            "flashboot": "FLASHBOOT",
        }
    )

    assert endpoint.scaling.workers_max == 1
    assert endpoint.scaling.idle_timeout == 5
    assert endpoint.scaling.scaler_type == "QUEUE_DELAY"
    assert endpoint.scaling.flashboot is True
