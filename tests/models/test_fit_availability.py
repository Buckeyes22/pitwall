"""Fit must not rank a card the requested cloud cannot sell.

Live: fit ranked NVIDIA RTX A5000 cheapest and said it fits; the launch failed
with "all GPU types exhausted" because that card has no secure-cloud capacity.
_max_count fell back to max_gpu_count and then to 1, so zero capacity still
looked like one GPU.
"""

from __future__ import annotations

from decimal import Decimal

from pitwall.models.fit import _cloud_max_count
from pitwall.runpod_client.graphql import RunpodGpuType


def _gpu(**overrides: object) -> RunpodGpuType:
    base: dict[str, object] = {
        "id": "NVIDIA RTX A5000",
        "memoryInGb": 24,
        "secureCloud": True,
        "communityCloud": True,
        "securePrice": Decimal("0.27"),
        "communityPrice": Decimal("0.16"),
        "maxGpuCount": 8,
    }
    base.update(overrides)
    return RunpodGpuType.model_validate(base)


def test_zero_secure_capacity_is_not_masked_by_the_global_max() -> None:
    gpu = _gpu(maxGpuCountSecureCloud=0)
    assert _cloud_max_count(gpu, "secure") is None


def test_a_closed_cloud_lane_is_filtered_before_fit_sees_it() -> None:
    """Lane openness is the snapshot builder's job, not fit's.

    fit must not read the cloud flag itself: it defaults to False on the model, so
    every synthetic row — the whole fallback snapshot sets no flags — would be read
    as a closed lane and the advisory table would come back empty whenever live
    pricing is unavailable.
    """
    from pitwall.models.prices import _live_rows

    closed = _gpu(secureCloud=False, maxGpuCountSecureCloud=4)
    open_lane = _gpu(id="NVIDIA L4", secureCloud=True, maxGpuCountSecureCloud=4)

    kept = {row.id for row in _live_rows([closed, open_lane], "secure")}
    assert kept == {"NVIDIA L4"}


def test_a_row_with_no_flags_still_yields_a_count() -> None:
    """The fallback snapshot sets no cloud flags; it must still advise."""
    from pitwall.runpod_client.graphql import RunpodGpuType as _Row

    bare = _Row.model_validate({"id": "NVIDIA L4", "memoryInGb": 24, "maxGpuCount": 8})
    assert _cloud_max_count(bare, "secure") == 8


def test_real_capacity_is_reported() -> None:
    gpu = _gpu(maxGpuCountSecureCloud=4)
    assert _cloud_max_count(gpu, "secure") == 4


def test_an_unknown_count_falls_back_to_the_global_max() -> None:
    """None means "not reported", which is different from zero."""
    gpu = _gpu(maxGpuCountSecureCloud=None)
    assert _cloud_max_count(gpu, "secure") == 8


def test_nothing_reported_anywhere_keeps_the_advisory_default() -> None:
    """Absent counts are not evidence of no stock.

    Dropping these rows emptied the whole fit table for the fallback snapshot and
    every synthetic fixture, which report no counts at all.
    """
    from pitwall.runpod_client.graphql import RunpodGpuType as _Row

    bare = _Row.model_validate({"id": "NVIDIA L4", "memoryInGb": 24})
    assert _cloud_max_count(bare, "secure") == 1


def test_an_explicit_global_zero_is_still_unavailable() -> None:
    gpu = _gpu(maxGpuCount=0, maxGpuCountSecureCloud=None)
    assert _cloud_max_count(gpu, "secure") is None
