"""An explicit datacenter must be honoured with or without a network volume.

A volume is bound to one datacenter, so a volume-backed provider must launch
there. That is why the region takes precedence. It is not a reason to discard an
explicit datacenter when there is no volume at all.
"""

from __future__ import annotations

from types import SimpleNamespace

from pitwall.api.leases.launch import _data_center_id


def _provider(config: dict[str, object], region: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id="prov_x", config=config, region=region)


def test_explicit_datacenter_is_honoured_without_a_volume() -> None:
    provider = _provider({"data_center_id": "EU-RO-1"})
    assert _data_center_id(provider) == "EU-RO-1"


def test_volume_region_still_wins() -> None:
    provider = _provider(
        {"data_center_id": "EU-RO-1", "network_volume_id": "vol_1"}, region="US-KS-2"
    )
    assert _data_center_id(provider) == "US-KS-2"


def test_no_datacenter_configured_stays_unpinned() -> None:
    assert _data_center_id(_provider({})) is None
