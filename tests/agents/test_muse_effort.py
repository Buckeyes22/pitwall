"""Muse effort values match what Meta's models accept: `none` is rejected (HTTP 400)."""

import pytest

from pitwall.agents import profiles
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.registry import load_registry


def test_muse_rejects_none_effort_even_for_an_unregistered_model() -> None:
    registry = load_registry()
    with pytest.raises(Exception, match="effort"):
        profiles.validate_effort(registry, "muse", "muse-future-2.0", "none", "route.effort")


def test_muse_still_accepts_its_documented_efforts() -> None:
    registry = load_registry()
    for value in ("minimal", "high", "ultra"):
        assert profiles.validate_effort(registry, "muse", "muse-future-2.0", value, "e") == value


def test_muse_adapter_does_not_offer_none() -> None:
    assert "none" not in (get_adapter("muse").effort_values or ())
