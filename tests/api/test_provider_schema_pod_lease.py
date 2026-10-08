"""Provider-config validation for serve-armed pod_lease providers."""

from __future__ import annotations

import pytest

from pitwall.api.provider_schemas import validate_provider_registration_config, warm_cache_matches
from pitwall.core.enums import ProviderType


def _validate(config: dict[str, object]) -> None:
    validate_provider_registration_config(
        provider_type=ProviderType.POD_LEASE,
        endpoint_id=None,
        cloud_type="SECURE",
        config=config,
    )


def test_serve_keys_are_accepted() -> None:
    _validate(
        {
            "ports": {"http": [8000]},
            "openai_proxy_port": 8000,
            "active_pod_id": "pod-serve-1",
            "active_lease_id": "lease_prov_serve_abc123def456",
            "docker_start_cmd": [
                "--model",
                "org/model",
                "--served-model-name",
                "m",
                "--port",
                "8000",
            ],
        }
    )


def test_revision_3_serve_keys_are_accepted() -> None:
    _validate(
        {
            "engine": "llama.cpp",
            "variant": "gguf:UD-Q4_K_XL",
            "gpu_count": 4,
            "template_id": "template-serve",
            "startup_timeout_s": 1_800,
            "ports": {"http": [8000]},
            "openai_proxy_port": 8000,
            "docker_start_cmd": ["--hf-repo", "org/model-gguf"],
        }
    )


def test_warm_cache_requires_a_complete_verified_record() -> None:
    _validate(
        {
            "network_volume_id": "volume-cache",
            "warm_cache": {
                "variant": "fp8",
                "verified_at": "2026-08-28T12:00:00+00:00",
                "volume_id": "volume-cache",
            },
        }
    )
    with pytest.raises(ValueError, match="warm_cache.volume_id"):
        _validate(
            {
                "warm_cache": {
                    "variant": "fp8",
                    "verified_at": "2026-08-28T12:00:00+00:00",
                    "volume_id": "",
                }
            }
        )


def test_warm_cache_match_requires_same_variant_and_volume() -> None:
    config = {
        "network_volume_id": "volume-cache",
        "warm_cache": {
            "variant": "fp8",
            "verified_at": "2026-08-28T12:00:00+00:00",
            "volume_id": "volume-cache",
        },
    }

    assert warm_cache_matches(config, variant="fp8") is True
    assert warm_cache_matches(config, variant="bf16") is False
    assert (
        warm_cache_matches({**config, "network_volume_id": "volume-other"}, variant="fp8") is False
    )


@pytest.mark.parametrize("engine", ["vllm", "llama.cpp", "sglang"])
def test_serve_engine_is_accepted(engine: str) -> None:
    _validate({"engine": engine})


@pytest.mark.parametrize("value", ["/health_generate", "/" + "x" * 127])
def test_readiness_path_is_accepted(value: str) -> None:
    _validate({"readiness_path": value})


@pytest.mark.parametrize("value", ["health", "/" + "x" * 128, 7, True])
def test_invalid_readiness_path_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="readiness_path"):
        _validate({"readiness_path": value})


@pytest.mark.parametrize("value", ["template-serve", "Official_Template_1", "a1"])
def test_template_id_shape_is_accepted(value: str) -> None:
    _validate({"template_id": value})


@pytest.mark.parametrize("value", ["", "a", " template-serve", "bad/id", "x" * 65])
def test_template_id_shape_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="template_id"):
        _validate({"template_id": value})


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"engine": 1}, "engine"),
        ({"engine": []}, "engine"),
        ({"variant": ""}, "variant"),
        ({"variant": " fp8"}, "variant"),
        ({"variant": 1}, "variant"),
        ({"gpu_count": True}, "gpu_count"),
        ({"gpu_count": 0}, "gpu_count"),
        ({"gpu_count": 1.0}, "gpu_count"),
    ],
)
def test_invalid_revision_3_keys(config: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate(config)


@pytest.mark.parametrize("value", [60, 600, 1_800, 7_200])
def test_startup_timeout_is_accepted(value: int) -> None:
    _validate({"startup_timeout_s": value})


@pytest.mark.parametrize("value", [True, 59, 7_201, 600.0, "600"])
def test_invalid_startup_timeout_is_rejected(value: object) -> None:
    with pytest.raises(ValueError, match="startup_timeout_s"):
        _validate({"startup_timeout_s": value})


@pytest.mark.parametrize(
    "config, message",
    [
        ({"ports": {"http": [8000]}, "openai_proxy_port": 9000}, "openai_proxy_port"),
        ({"openai_proxy_port": 8000}, "openai_proxy_port"),
        ({"ports": {"http": [8000]}, "openai_proxy_port": "8000"}, "openai_proxy_port"),
        ({"active_pod_id": "../evil"}, "active_pod_id"),
        ({"active_lease_id": ""}, "active_lease_id"),
        ({"docker_start_cmd": "--model x"}, "docker_start_cmd"),
        ({"docker_start_cmd": ["--model", ""]}, "docker_start_cmd"),
        (
            {"openai_base_url": "https://pod-serve-1-8000.proxy.runpod.net/v1"},
            "openai_base_url",
        ),
    ],
)
def test_invalid_serve_keys_are_rejected(config: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate(config)
