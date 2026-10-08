"""Strict zero-cost filter: ADR 0007 rule 2 as one pure function (research §9.5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pitwall.cost.estimator import parse_pricing_model


@dataclass(frozen=True, slots=True)
class ZeroCostVerdict:
    allowed: bool
    reason: str | None
    trains_on_prompts: bool


def _catalog(provider: Any) -> Mapping[str, Any]:
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    gateway = config.get("gateway", {}) if isinstance(config, Mapping) else {}
    catalog = gateway.get("catalog", {}) if isinstance(gateway, Mapping) else {}
    return catalog if isinstance(catalog, Mapping) else {}


def zero_cost_verdict(provider: Any) -> ZeroCostVerdict:
    catalog = _catalog(provider)
    trains = bool(catalog.get("trains_on_prompts", False))
    try:
        kind: str = parse_pricing_model(provider).kind
    except TypeError, ValueError:
        kind = "unknown"
    if kind != "zero":
        return ZeroCostVerdict(True, None, trains)
    if not catalog:
        return ZeroCostVerdict(False, "no-catalog-evidence", trains)
    tos = str(catalog.get("tos", "unknown"))
    if tos == "avoid":
        return ZeroCostVerdict(False, "tos-avoid", trains)
    if tos == "unknown":
        return ZeroCostVerdict(False, "tos-unknown", trains)
    if catalog.get("eligibility_gate"):
        return ZeroCostVerdict(False, "eligibility-gated", trains)
    free_type = str(catalog.get("free_type", ""))
    if free_type == "discontinued":
        return ZeroCostVerdict(False, "discontinued", trains)
    if free_type == "keyless" or bool(catalog.get("hard_stop_guaranteed", False)):
        return ZeroCostVerdict(True, None, trains)
    return ZeroCostVerdict(False, "hard-stop-not-guaranteed", trains)


__all__ = ["ZeroCostVerdict", "zero_cost_verdict"]
