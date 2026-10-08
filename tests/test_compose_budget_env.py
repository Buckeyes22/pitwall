from __future__ import annotations

from pathlib import Path

import yaml

_COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
_BUDGET_DEFAULTS = {
    "PITWALL_MONTHLY_BUDGET_USD": "${PITWALL_MONTHLY_BUDGET_USD:-50.0}",
    "PITWALL_PER_REQUEST_MAX_USD": "${PITWALL_PER_REQUEST_MAX_USD:-10.0}",
}


def test_compose_budget_gate_services_receive_both_fail_closed_config_values() -> None:
    """Compose must supply deployment defaults to every service that builds BudgetGate.

    BudgetGate intentionally rejects missing environment configuration.  The
    Compose deployment owns the documented defaults, so a normal API or
    reconciler launch cannot fail before admission merely because Compose
    omitted the settings.
    """

    document = yaml.safe_load(_COMPOSE_FILE.read_text(encoding="utf-8"))

    for service_name in ("api", "reconciler"):
        environment = document["services"][service_name]["environment"]
        assert {name: environment[name] for name in _BUDGET_DEFAULTS} == _BUDGET_DEFAULTS
