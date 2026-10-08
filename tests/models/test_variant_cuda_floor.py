"""A dossier must state the CUDA floor its image needs.

Two of four live RTX 4090 launches never started: the llama.cpp server-cuda image
requires CUDA >= 12.8 and the pods landed on 12.4 hosts, retrying the container
every ~16 s for the whole lease. RunPod publishes the driver versions each GPU
type offers, and the allowedCudaVersions plumbing already exists end to end — only
the value was missing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pitwall.models.catalogue import load_catalogue
from pitwall.serve import _allowed_cuda_versions, _market_cuda_versions

_CUDA_IMAGE_MARKERS = ("server-cuda", "vllm-openai", "sglang")


def _needs_cuda(image: str) -> bool:
    return any(marker in image for marker in _CUDA_IMAGE_MARKERS)


def test_variant_accepts_a_min_cuda_field() -> None:
    from pitwall.models.schema import Variant

    assert "min_cuda" in Variant.model_fields


@pytest.mark.parametrize("model", load_catalogue().models())
def test_cuda_images_declare_their_floor(model) -> None:
    for variant in model.variants:
        if _needs_cuda(variant.image):
            assert variant.min_cuda, (
                f"{model.id} variant {variant.id} runs {variant.image} but declares no min_cuda"
            )


def test_floor_selects_only_satisfying_versions() -> None:
    offered = ["12.2", "12.4", "12.8", "13.0", "13.2"]
    assert _allowed_cuda_versions("12.8", offered) == ["12.8", "13.0", "13.2"]


def test_no_floor_leaves_placement_unconstrained() -> None:
    assert _allowed_cuda_versions(None, ["12.4"]) is None


def test_no_satisfying_version_is_fail_closed() -> None:
    """Known incompatible offers must remain constrained, never unconstrained."""
    assert _allowed_cuda_versions("13.5", ["12.4", "12.8"]) == []


def test_market_cuda_selection_uses_available_rest_versions() -> None:
    market = SimpleNamespace(
        gpus=(
            SimpleNamespace(
                gpu_type_id="NVIDIA A40",
                rest_v2=SimpleNamespace(cuda_versions=(("12.8", False), ("13.0", True))),
            ),
        )
    )
    offered = _market_cuda_versions(market, "NVIDIA A40")
    assert offered == ("13.0",)
    assert _allowed_cuda_versions("12.8", offered) == ["13.0"]


def test_market_cuda_selection_does_not_infer_missing_rest_data() -> None:
    market = SimpleNamespace(gpus=())
    assert _market_cuda_versions(market, "NVIDIA A40") is None
