"""Decimal-authoritative read models for persisted Pitwall cost data."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Any, Literal

from pitwall.cost.estimator import CostComponent

_USD_QUANTUM = Decimal("0.000001")

type WorkloadReconciliationStatus = Literal[
    "unpriced",
    "estimate_only",
    "actual_recorded",
    "reconciled",
]
type CostReadConfidence = Literal["exact", "bounded", "estimated", "unknown"]
type ActualCostKind = Literal["none", "recorded", "usage_derived", "provider_reported"]

_QUOTE_KEYS = frozenset(
    {
        "model",
        "components",
        "estimate",
        "ceiling",
        "confidence",
        "provenance",
        "currency",
        "assumptions",
    }
)
_COMPONENT_KEYS = frozenset(
    {
        "name",
        "unit",
        "rate",
        "ceiling_rate",
        "count",
        "ceiling_count",
        "estimate",
        "ceiling",
    }
)


@dataclass(frozen=True, slots=True)
class WorkloadCostRead:
    """Estimate-versus-actual semantics for one persisted workload.

    Truth-up provenance is retained separately from the amount. Provider
    billing, provider-usage-derived, and broker-observed amounts remain
    distinct, and none is promoted to an invoice because the current contract
    does not ingest or verify provider invoices.
    """

    estimate: Decimal | None
    actual: Decimal | None
    ceiling: Decimal | None = None
    confidence: CostReadConfidence = "unknown"
    provenance: str = "pitwall.workloads"
    model: str = "persisted_workload_cost"
    components: tuple[CostComponent, ...] = ()
    assumptions: tuple[str, ...] = (
        "persisted cost_estimate_usd is the available pre-spend amount",
        "quote components and original confidence are not persisted",
    )
    stored_actual_provenance: str | None = None
    reconciled_at: dt.datetime | None = None
    currency: Literal["USD"] = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "estimate", _optional_usd(self.estimate, "estimate"))
        object.__setattr__(self, "actual", _optional_usd(self.actual, "actual"))
        object.__setattr__(self, "ceiling", _optional_usd(self.ceiling, "ceiling"))
        if self.confidence not in {"exact", "bounded", "estimated", "unknown"}:
            raise ValueError("unsupported cost read confidence")
        object.__setattr__(self, "provenance", _non_empty_string(self.provenance, "provenance"))
        object.__setattr__(self, "model", _non_empty_string(self.model, "model"))
        components = tuple(self.components)
        if any(not isinstance(component, CostComponent) for component in components):
            raise TypeError("components must contain CostComponent")
        object.__setattr__(self, "components", components)
        assumptions = tuple(
            _non_empty_string(assumption, "assumption") for assumption in self.assumptions
        )
        object.__setattr__(self, "assumptions", assumptions)
        if self.stored_actual_provenance is not None:
            object.__setattr__(
                self,
                "stored_actual_provenance",
                _non_empty_string(
                    self.stored_actual_provenance,
                    "stored_actual_provenance",
                ),
            )
        if self.currency != "USD":
            raise ValueError("currency must be USD")
        if self.estimate is None and self.ceiling is not None:
            raise ValueError("ceiling requires an estimate")
        if self.estimate is not None and self.ceiling is not None and self.ceiling < self.estimate:
            raise ValueError("ceiling must be greater than or equal to estimate")
        if self.actual is None and self.stored_actual_provenance is not None:
            raise ValueError("actual provenance requires an actual amount")
        if self.reconciled_at is not None:
            if not isinstance(self.reconciled_at, dt.datetime):
                raise TypeError("reconciled_at must be datetime.datetime")
            if self.reconciled_at.tzinfo is None or self.reconciled_at.utcoffset() is None:
                raise ValueError("reconciled_at must be timezone-aware")
            object.__setattr__(self, "reconciled_at", self.reconciled_at.astimezone(dt.UTC))
            if self.actual is None or self.stored_actual_provenance is None:
                raise ValueError("reconciled_at requires sourced actual cost")

    @classmethod
    def from_persisted(
        cls,
        *,
        cost_estimate_usd: Decimal | None,
        cost_ceiling_usd: Decimal | None = None,
        cost_quote: Mapping[str, Any] | None = None,
        cost_actual_usd: Decimal | None,
        cost_actual_provenance: str | None = None,
        cost_reconciled_at: dt.datetime | None = None,
    ) -> WorkloadCostRead:
        """Map persisted quote/actual fields without inventing legacy semantics."""

        if cost_quote is None:
            return cls(
                estimate=cost_estimate_usd,
                ceiling=cost_ceiling_usd if cost_ceiling_usd is not None else cost_estimate_usd,
                actual=cost_actual_usd,
                confidence="unknown",
                stored_actual_provenance=cost_actual_provenance,
                reconciled_at=cost_reconciled_at,
            )

        if not isinstance(cost_quote, Mapping):
            raise TypeError("cost_quote must be a mapping")
        unknown = set(cost_quote) - _QUOTE_KEYS
        missing = _QUOTE_KEYS - set(cost_quote)
        if unknown or missing:
            raise ValueError(
                "cost_quote must contain exactly the structured quote fields; "
                f"missing={sorted(missing)!r}, unknown={sorted(unknown)!r}"
            )
        quote_estimate = _stored_decimal(cost_quote["estimate"], "cost_quote.estimate")
        quote_ceiling = _stored_decimal(cost_quote["ceiling"], "cost_quote.ceiling")
        column_estimate = _optional_usd(cost_estimate_usd, "cost_estimate_usd")
        column_ceiling = _optional_usd(cost_ceiling_usd, "cost_ceiling_usd")
        if column_estimate != quote_estimate:
            raise ValueError("cost_quote estimate must match cost_estimate_usd")
        if column_ceiling != quote_ceiling:
            raise ValueError("cost_quote ceiling must match cost_ceiling_usd")

        raw_components = cost_quote["components"]
        if not isinstance(raw_components, list):
            raise TypeError("cost_quote.components must be a list")
        components = tuple(
            _stored_component(component, index=index)
            for index, component in enumerate(raw_components)
        )
        confidence = cost_quote["confidence"]
        if confidence not in {"exact", "bounded", "estimated"}:
            raise ValueError("cost_quote.confidence must be exact, bounded, or estimated")
        assumptions = cost_quote["assumptions"]
        if not isinstance(assumptions, list):
            raise TypeError("cost_quote.assumptions must be a list")
        if cost_quote["currency"] != "USD":
            raise ValueError("cost_quote.currency must be USD")

        return cls(
            model=_non_empty_string(cost_quote["model"], "cost_quote.model"),
            components=components,
            estimate=quote_estimate,
            ceiling=quote_ceiling,
            confidence=confidence,
            provenance=_non_empty_string(
                cost_quote["provenance"],
                "cost_quote.provenance",
            ),
            currency="USD",
            assumptions=tuple(
                _non_empty_string(assumption, "cost_quote.assumption") for assumption in assumptions
            ),
            actual=cost_actual_usd,
            stored_actual_provenance=cost_actual_provenance,
            reconciled_at=cost_reconciled_at,
        )

    @property
    def reconciliation_status(self) -> WorkloadReconciliationStatus:
        if self.actual is not None:
            return "reconciled" if self.reconciled_at is not None else "actual_recorded"
        if self.estimate is not None:
            return "estimate_only"
        return "unpriced"

    @property
    def effective(self) -> Decimal | None:
        if self.actual is not None:
            return self.actual
        return self.estimate if self.estimate is not None else self.ceiling

    @property
    def variance(self) -> Decimal | None:
        if self.actual is None or self.estimate is None:
            return None
        return _usd(self.actual - self.estimate)

    @property
    def actual_provenance(self) -> str | None:
        if self.actual is None:
            return None
        return self.stored_actual_provenance or "recorded_actual_source_unspecified"

    @property
    def actual_kind(self) -> ActualCostKind:
        if self.actual is None:
            return "none"
        if self.reconciled_at is not None and self.stored_actual_provenance is not None:
            if self.stored_actual_provenance.startswith("broker:provider_usage:"):
                return "usage_derived"
            if self.stored_actual_provenance.startswith("broker"):
                return "recorded"
            return "provider_reported"
        return "recorded"

    @property
    def provider_invoice(self) -> bool:
        return False

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, object]:
        if mode not in {"python", "json"}:
            raise ValueError(f"unsupported dump mode: {mode!r}")

        def money(value: Decimal | None) -> Decimal | str | None:
            if mode == "json" and value is not None:
                return str(value)
            return value

        return {
            "model": self.model,
            "components": [component.model_dump(mode=mode) for component in self.components],
            "estimate": money(self.estimate),
            "ceiling": money(self.ceiling),
            "confidence": self.confidence,
            "provenance": self.provenance,
            "currency": self.currency,
            "assumptions": list(self.assumptions),
            "actual": money(self.actual),
            "variance": money(self.variance),
            "effective": money(self.effective),
            "reconciliation_status": self.reconciliation_status,
            "actual_kind": self.actual_kind,
            "actual_provenance": self.actual_provenance,
            "reconciled_at": self.reconciled_at.isoformat()
            if self.reconciled_at is not None
            else None,
            "provider_invoice": self.provider_invoice,
        }

    def to_serializable_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class CostSummaryEntry:
    day: dt.date
    capability_class: str
    provider_type: str
    workload_count: int
    cost_usd: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.day, dt.date) or isinstance(self.day, dt.datetime):
            raise TypeError("day must be datetime.date")
        object.__setattr__(
            self,
            "capability_class",
            _non_empty_string(self.capability_class, "capability_class"),
        )
        object.__setattr__(
            self,
            "provider_type",
            _non_empty_string(self.provider_type, "provider_type"),
        )
        if not isinstance(self.workload_count, int) or isinstance(self.workload_count, bool):
            raise TypeError("workload_count must be int")
        if self.workload_count < 0:
            raise ValueError("workload_count must be non-negative")
        object.__setattr__(self, "cost_usd", _usd_value(self.cost_usd, "cost_usd"))

    def model_dump(self) -> dict[str, object]:
        return {
            "day": self.day.isoformat(),
            "capability_class": self.capability_class,
            "provider_type": self.provider_type,
            "workload_count": self.workload_count,
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True, slots=True)
class CostSummaryRead:
    """Decimal-first aggregate; transports carry money as decimal strings."""

    total_usd: Decimal
    entries: tuple[CostSummaryEntry, ...]
    currency: Literal["USD"] = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_usd", _usd_value(self.total_usd, "total_usd"))
        if self.currency != "USD":
            raise ValueError("currency must be USD")
        entries = tuple(self.entries)
        if any(not isinstance(entry, CostSummaryEntry) for entry in entries):
            raise TypeError("entries must contain CostSummaryEntry")
        object.__setattr__(self, "entries", entries)

    def to_legacy_serializable_dict(self) -> dict[str, object]:
        """Serialise money as decimal strings at the transport boundary (REST, MCP, CLI)."""

        return {
            "total_usd": str(self.total_usd),
            "entries": [
                {**entry.model_dump(), "cost_usd": str(entry.cost_usd)} for entry in self.entries
            ],
        }


@dataclass(frozen=True, slots=True)
class WorkloadCostRecord:
    """One recent-workload row with a shared cost semantic model."""

    fields: Mapping[str, Any]
    cost: WorkloadCostRead

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", dict(self.fields))
        if not isinstance(self.cost, WorkloadCostRead):
            raise TypeError("cost must be WorkloadCostRead")

    def to_legacy_serializable_dict(self) -> dict[str, object]:
        return {
            **self.fields,
            # Decimal strings: a JSON float would round the money on the wire.
            "cost_estimate_usd": str(self.cost.estimate)
            if self.cost.estimate is not None
            else None,
            "cost_actual_usd": str(self.cost.actual) if self.cost.actual is not None else None,
            "cost": self.cost.to_serializable_dict(),
        }


@dataclass(frozen=True, slots=True)
class RecentWorkloadsRead:
    workloads: tuple[WorkloadCostRecord, ...]

    def __post_init__(self) -> None:
        workloads = tuple(self.workloads)
        if any(not isinstance(item, WorkloadCostRecord) for item in workloads):
            raise TypeError("workloads must contain WorkloadCostRecord")
        object.__setattr__(self, "workloads", workloads)

    def to_legacy_serializable_dict(self) -> dict[str, object]:
        return {"workloads": [item.to_legacy_serializable_dict() for item in self.workloads]}


def _stored_component(value: object, *, index: int) -> CostComponent:
    name = f"cost_quote.components[{index}]"
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    unknown = set(value) - _COMPONENT_KEYS
    missing = _COMPONENT_KEYS - set(value)
    if unknown or missing:
        raise ValueError(
            f"{name} must contain exactly the component fields; "
            f"missing={sorted(missing)!r}, unknown={sorted(unknown)!r}"
        )
    return CostComponent(
        name=_non_empty_string(value["name"], f"{name}.name"),
        unit=_non_empty_string(value["unit"], f"{name}.unit"),
        rate=_stored_decimal(value["rate"], f"{name}.rate"),
        ceiling_rate=_stored_decimal(value["ceiling_rate"], f"{name}.ceiling_rate"),
        estimated_count=_stored_decimal(value["count"], f"{name}.count"),
        ceiling_count=_stored_decimal(
            value["ceiling_count"],
            f"{name}.ceiling_count",
        ),
        estimate=_stored_decimal(value["estimate"], f"{name}.estimate"),
        ceiling=_stored_decimal(value["ceiling"], f"{name}.ceiling"),
    )


def _stored_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a decimal string")
    try:
        amount = Decimal(value)
    except DecimalException as exc:
        raise ValueError(f"{name} must be a decimal string") from exc
    if not amount.is_finite():
        raise ValueError(f"{name} must be finite")
    return amount


def _optional_usd(value: object, name: str) -> Decimal | None:
    if value is None:
        return None
    return _usd_value(value, name)


def _usd_value(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return _usd(value)


def _non_empty_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must be non-empty")
    return stripped


def _usd(value: Decimal) -> Decimal:
    try:
        return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError(f"cost is out of representable USD range: {value}") from exc


__all__ = [
    "ActualCostKind",
    "CostReadConfidence",
    "CostSummaryEntry",
    "CostSummaryRead",
    "RecentWorkloadsRead",
    "WorkloadCostRead",
    "WorkloadCostRecord",
    "WorkloadReconciliationStatus",
]
