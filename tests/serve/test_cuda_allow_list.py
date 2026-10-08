"""RunPod's allowedCudaVersions is an exact set: offer every version at or above the floor."""

from __future__ import annotations

import pytest

from pitwall.api.exceptions import ServeTemplateInvalid
from pitwall.serve import RUNPOD_CUDA_VERSIONS, cuda_allow_list


def test_no_floor_leaves_placement_unconstrained() -> None:
    assert cuda_allow_list(None, ["12.4"]) is None


def test_live_offer_is_filtered_by_the_floor() -> None:
    assert cuda_allow_list("12.8", ["12.4", "12.8", "13.0", "13.2"]) == ["12.8", "13.0", "13.2"]


def test_unknown_offer_uses_every_known_version_at_or_above_the_floor() -> None:
    allowed = cuda_allow_list("12.8", None)
    assert allowed == [
        version
        for version in RUNPOD_CUDA_VERSIONS
        if tuple(map(int, version.split("."))) >= (12, 8)
    ]
    assert allowed is not None and "13.0" in allowed and "12.4" not in allowed


def test_nothing_offered_at_the_floor_refuses_before_launch() -> None:
    with pytest.raises(ServeTemplateInvalid, match="12.9"):
        cuda_allow_list("12.9", ["12.4", "12.8"])
