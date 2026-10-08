"""The settings defaults discovery cannot read literally resolve to their pinned values.

Discovery records a default as dynamic when its expression is a call or a name
(``Decimal("50.0")``, ``DEFAULT_R2_TEMP_CREDENTIAL_TTL_S``, ``default_factory=dict``).
With no environment and no ``pitwall.toml``, each resolves to the value pinned here; J37
(``tests/release/test_config_keys_journey.py``) proves the environment and TOML override it.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from pitwall.config import PitwallSettings, RoutingWeights

DEFAULTS = {
    "r2_temp_credential_ttl_s": 21_600,
    "r2_temp_credential_permission": "object-read-write",
    "pitwall_routing_weights": {},
    "pitwall_monthly_budget_usd": Decimal("50.0"),
    "pitwall_per_request_max_usd": Decimal("10.0"),
    "pitwall_budget_breach_kill_headroom_floor_usd": Decimal("0.0"),
}
WEIGHT_DEFAULTS = {
    "cost": Decimal("1"),
    "latency": Decimal("0.001"),
    "w_quota": Decimal("10"),
    "w_reset": Decimal("2.5"),
}


@pytest.fixture
def unset(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    for name in tuple(os.environ):
        if name.startswith(("PITWALL_", "R2_", "RUNPOD_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(str(tmp_path))  # no pitwall.toml in the working directory


@pytest.mark.usefixtures("unset")
@pytest.mark.parametrize(("field", "expected"), sorted(DEFAULTS.items()))
def test_settings_default(field: str, expected: object) -> None:
    assert getattr(PitwallSettings(), field) == expected


@pytest.mark.parametrize(("field", "expected"), sorted(WEIGHT_DEFAULTS.items()))
def test_routing_weight_default(field: str, expected: Decimal) -> None:
    assert getattr(RoutingWeights(), field) == expected
