"""Hermetic contract tests for the RunPod v2-only GPU catalogue client."""

from __future__ import annotations

import httpx
import pytest

from pitwall.runpod_client.catalog import list_gpu_types_v2
from pitwall.runpod_client.pods import _rest_base_url, _rest_v2_base_url


def _gpu_response() -> dict[str, object]:
    """A ``ListGpuTypesResponse`` fixture matching the published v2 schema."""

    return {
        "gpus": [
            {
                "id": "NVIDIA GeForce RTX 4090",
                "name": "RTX 4090",
                "pool": "ADA_24",
                "manufacturer": "NVIDIA",
                "memory": 24,
                "secure": True,
                "community": True,
                "price": {"secure": 0.44, "community": 0.31},
                "maxCount": {"secure": 8, "community": 4},
                "cudaVersions": [
                    {"version": "12.8", "available": True},
                    {"version": "12.4", "available": False},
                ],
            }
        ]
    }


def test_list_gpu_types_v2_uses_v2_host_and_parses_gpu_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNPOD_REST_V2_API_URL", raising=False)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_gpu_response())

    gpus = list_gpu_types_v2(api_key="test-key", transport=httpx.MockTransport(handler))

    assert requests[0].url == httpx.URL(
        "https://api.runpod.io/v2/catalog/gpus?include=AVAILABILITY&product=POD"
    )
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert [(gpu.id, gpu.memory) for gpu in gpus] == [("NVIDIA GeForce RTX 4090", 24)]
    assert [(cuda.version, cuda.available) for cuda in gpus[0].cuda_versions] == [
        ("12.8", True),
        ("12.4", False),
    ]


def test_v2_base_url_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_REST_V2_API_URL", "https://catalog.example.test/v2/")

    assert _rest_v2_base_url() == "https://catalog.example.test/v2"
    assert (
        _rest_v2_base_url("https://override.example.test/v2/") == "https://override.example.test/v2"
    )


def test_rest_base_url_defaults_to_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNPOD_REST_API_URL", raising=False)
    monkeypatch.setenv("RUNPOD_REST_V2_API_URL", "https://catalog.example.test/v2")

    assert _rest_base_url() == "https://api.runpod.io/v2"
