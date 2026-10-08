"""Quota evidence snapshot plus Stage-2 gate and Stage-3 terms (research §9.3)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pitwall.cost.estimator import parse_pricing_model


@dataclass(frozen=True, slots=True)
class QuotaRecord:
    provider_id: str
    pool_key: str
    free_type: str
    window_start: dt.datetime | None
    reset_at: dt.datetime | None
    budget_units: Decimal | None
    used_units: Decimal
    tos_verdict: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    updated_at: dt.datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "pool_key": self.pool_key,
            "free_type": self.free_type,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "budget_units": str(self.budget_units) if self.budget_units is not None else None,
            "used_units": str(self.used_units),
            "tos_verdict": self.tos_verdict,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> QuotaRecord:
        def parse(value: object) -> dt.datetime | None:
            if not value:
                return None
            if isinstance(value, dt.datetime):
                return value
            return dt.datetime.fromisoformat(str(value))

        return cls(
            provider_id=raw["provider_id"],
            pool_key=raw.get("pool_key") or "",
            free_type=raw["free_type"],
            window_start=parse(raw.get("window_start")),
            reset_at=parse(raw.get("reset_at")),
            budget_units=Decimal(raw["budget_units"])
            if raw.get("budget_units") is not None
            else None,
            used_units=Decimal(raw.get("used_units", "0")),
            tos_verdict=raw["tos_verdict"],
            evidence=dict(raw.get("evidence", {})),
        )


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Immutable quota view captured at planning time; part of the plan identity."""

    records: tuple[QuotaRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "records", tuple(sorted(self.records, key=lambda r: (r.provider_id, r.pool_key)))
        )

    @classmethod
    def empty(cls) -> QuotaSnapshot:
        return cls()

    def get(self, provider_id: str, pool_key: str | None) -> QuotaRecord | None:
        key = pool_key or ""
        for record in self.records:
            if record.provider_id == provider_id and record.pool_key == key:
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> QuotaSnapshot:
        return cls(records=tuple(QuotaRecord.from_dict(r) for r in raw.get("records", [])))


@dataclass(frozen=True, slots=True)
class QuotaTerms:
    headroom: float = 0.0
    reset_proximity: float = 0.0


def _pricing_kind(provider: Any) -> str:
    try:
        return parse_pricing_model(provider).kind
    except TypeError, ValueError:
        return "unknown"


def _pool_key(provider: Any) -> str | None:
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    gateway = config.get("gateway", {}) if isinstance(config, Mapping) else {}
    catalog = gateway.get("catalog", {}) if isinstance(gateway, Mapping) else {}
    value = catalog.get("pool_key")
    return str(value) if value else None


def _provider_id(provider: Any) -> str:
    value = getattr(provider, "id", None)
    if value is None and isinstance(provider, Mapping):
        value = provider.get("id")
    if value is None:
        raise ValueError("provider must include an id")
    return str(value)


def record_ineligibility(record: QuotaRecord, now: dt.datetime) -> str | None:
    """Why a quota record makes its pool ineligible right now, or ``None``.

    Single source of truth shared by the Stage-2 gate and the ``/v1/quotas``
    lockout column so the two never disagree.
    """
    if record.tos_verdict == "avoid":
        return "tos-avoid"
    if record.reset_at is not None and record.reset_at <= now and record.budget_units is not None:
        return None  # window rolled; the poller re-zeroes used_units on its next tick
    if record.budget_units is not None and record.used_units >= record.budget_units:
        return "quota-exhausted"
    return None


def quota_eligible(
    provider: Any, snapshot: QuotaSnapshot, now: dt.datetime
) -> tuple[bool, str | None]:
    if _pricing_kind(provider) != "zero":
        return True, None
    record = snapshot.get(_provider_id(provider), _pool_key(provider))
    if record is None:
        return False, "zero-priced without catalog evidence"
    reason = record_ineligibility(record, now)
    return (reason is None), reason


def quota_score_terms(provider: Any, snapshot: QuotaSnapshot, now: dt.datetime) -> QuotaTerms:
    if _pricing_kind(provider) != "zero":
        return QuotaTerms()
    record = snapshot.get(_provider_id(provider), _pool_key(provider))
    if record is None:
        return QuotaTerms()
    headroom = 1.0
    if record.budget_units is not None and record.budget_units > 0:
        headroom = max(
            0.0, min(1.0, float((record.budget_units - record.used_units) / record.budget_units))
        )
    proximity = 0.0
    if (
        record.window_start is not None
        and record.reset_at is not None
        and record.reset_at > record.window_start
    ):
        window_seconds = (record.reset_at - record.window_start).total_seconds()
        if window_seconds > 0:
            window_min = window_seconds / 60
            to_reset_min = max(0.0, (record.reset_at - now).total_seconds() / 60)
            proximity = max(0.0, min(1.0, 1 - to_reset_min / window_min))
    return QuotaTerms(headroom=headroom, reset_proximity=proximity)


__all__ = ["QuotaRecord", "QuotaSnapshot", "QuotaTerms", "quota_eligible", "quota_score_terms"]
