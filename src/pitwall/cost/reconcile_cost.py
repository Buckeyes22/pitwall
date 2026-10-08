"""Provider billing truth-up for Pitwall cost ledgers.

The pure reconciler compares broker-recorded spend against provider-reported
actual billing for the same ``cost_daily`` window. It emits structured
adjustments that can be inspected, exported, or applied by the thin asyncpg
adapter below.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, DecimalException
from typing import Any, Literal, Protocol, runtime_checkable

import asyncpg

_USD_QUANTUM = Decimal("0.000001")
_ZERO_USD = Decimal("0.000000")
PITWALL_COST_TRUTH_UP_LOCK_KEY = int.from_bytes(b"PITWCOST", "big")

_FETCH_PROVIDER_WORKLOADS_SQL = """
    SELECT
        w.id,
        w.provider_id,
        w.state,
        DATE(w.submitted_at AT TIME ZONE 'UTC') AS day,
        c.class AS capability_class,
        p.provider_type,
        w.cost_actual_usd,
        w.cost_actual_provenance
    FROM pitwall.workloads w
    JOIN pitwall.capabilities c ON c.id = w.capability_id
    JOIN pitwall.providers p ON p.id = w.provider_id
    WHERE w.id = ANY($1::text[])
    ORDER BY w.id
    FOR UPDATE OF w
"""

_APPLY_WORKLOAD_ACTUAL_SQL = """
    UPDATE pitwall.workloads
    SET cost_actual_usd = $1,
        cost_actual_provenance = $2,
        cost_reconciled_at = $3
    WHERE id = $4
"""

_REFRESH_COST_DAILY_WINDOW_SQL = """
    INSERT INTO pitwall.cost_daily
        (day, capability_class, provider_type, workload_count, cost_usd)
    SELECT
        DATE(w.submitted_at AT TIME ZONE 'UTC') AS day,
        c.class AS capability_class,
        p.provider_type,
        COUNT(*) AS workload_count,
        COALESCE(SUM(w.cost_actual_usd), 0) AS cost_usd
    FROM pitwall.workloads w
    JOIN pitwall.capabilities c ON c.id = w.capability_id
    JOIN pitwall.providers p ON p.id = w.provider_id
    WHERE w.state IN ('completed', 'failed', 'cancelled', 'timed_out')
      AND DATE(w.submitted_at AT TIME ZONE 'UTC') = $1
      AND c.class = $2
      AND p.provider_type = $3
    GROUP BY day, c.class, p.provider_type
    ON CONFLICT (day, capability_class, provider_type)
    DO UPDATE SET
        workload_count = EXCLUDED.workload_count,
        cost_usd = EXCLUDED.cost_usd
"""

_TERMINAL_WORKLOAD_STATES = frozenset({"completed", "failed", "cancelled", "timed_out"})

AdjustmentDirection = Literal["increase", "decrease"]
ProviderActualAvailability = Literal["available", "unavailable"]
CostTruthUpStatus = Literal["reconciled", "in_sync", "actual_unavailable"]


@dataclass(frozen=True, slots=True, order=True)
class CostReconcileWindow:
    """One ``pitwall.cost_daily`` billing window."""

    day: dt.date
    capability_class: str
    provider_type: str

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


@dataclass(frozen=True, slots=True)
class RecordedCostWindow:
    """Broker-recorded cost for one reconciliation window."""

    window: CostReconcileWindow
    recorded_usd: Decimal
    workload_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.window, CostReconcileWindow):
            raise TypeError("window must be CostReconcileWindow")
        object.__setattr__(
            self,
            "recorded_usd",
            _non_negative_usd(self.recorded_usd, "recorded_usd"),
        )
        if not isinstance(self.workload_count, int) or isinstance(self.workload_count, bool):
            raise TypeError("workload_count must be int")
        if self.workload_count < 0:
            raise ValueError("workload_count must be non-negative")


@dataclass(frozen=True, slots=True)
class ProviderActualCostWindow:
    """Provider-reported actual cost for one reconciliation window."""

    window: CostReconcileWindow
    actual_usd: Decimal
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.window, CostReconcileWindow):
            raise TypeError("window must be CostReconcileWindow")
        object.__setattr__(self, "actual_usd", _non_negative_usd(self.actual_usd, "actual_usd"))
        object.__setattr__(self, "source", _non_empty_string(self.source, "source"))


@dataclass(frozen=True, slots=True, order=True)
class ProviderActualWorkloadCost:
    """Authoritative provider cost mapped to one persisted Pitwall workload."""

    workload_id: str
    actual_usd: Decimal
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workload_id",
            _non_empty_string(self.workload_id, "workload_id"),
        )
        object.__setattr__(self, "actual_usd", _non_negative_usd(self.actual_usd, "actual_usd"))
        object.__setattr__(self, "source", _non_empty_string(self.source, "source"))


@dataclass(frozen=True, slots=True)
class ProviderActualCostResult:
    """Provider-reported workload actuals, or an explicit unavailable result."""

    provider_id: str
    availability: ProviderActualAvailability
    source: str
    observed_at: dt.datetime
    workloads: tuple[ProviderActualWorkloadCost, ...] = ()
    unavailable_reason: str | None = None
    currency: Literal["USD"] = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _non_empty_string(self.provider_id, "provider_id"))
        object.__setattr__(self, "source", _non_empty_string(self.source, "source"))
        if self.availability not in {"available", "unavailable"}:
            raise ValueError("availability must be 'available' or 'unavailable'")
        if self.currency != "USD":
            raise ValueError("currency must be USD")
        if not isinstance(self.observed_at, dt.datetime):
            raise TypeError("observed_at must be datetime.datetime")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(dt.UTC))
        workloads = tuple(self.workloads)
        if any(not isinstance(item, ProviderActualWorkloadCost) for item in workloads):
            raise TypeError("workloads must contain ProviderActualWorkloadCost")
        workloads = tuple(sorted(workloads, key=lambda item: item.workload_id))
        workload_ids = [item.workload_id for item in workloads]
        if len(workload_ids) != len(set(workload_ids)):
            raise ValueError("provider actual workloads must have unique workload_id values")
        object.__setattr__(self, "workloads", workloads)
        if self.availability == "available":
            if not workloads:
                raise ValueError("available provider actual cost requires at least one workload")
            if self.unavailable_reason is not None:
                raise ValueError("available provider actual cost cannot have unavailable_reason")
        else:
            if workloads:
                raise ValueError("unavailable provider actual cost cannot contain workloads")
            if self.unavailable_reason is None:
                raise ValueError("unavailable provider actual cost requires unavailable_reason")
            object.__setattr__(
                self,
                "unavailable_reason",
                _non_empty_string(self.unavailable_reason, "unavailable_reason"),
            )
        if any(item.source != self.source for item in workloads):
            raise ValueError("provider actual workload source must match result source")

    @classmethod
    def unavailable(
        cls,
        *,
        provider_id: str,
        source: str,
        observed_at: dt.datetime,
        reason: str,
    ) -> ProviderActualCostResult:
        return cls(
            provider_id=provider_id,
            availability="unavailable",
            source=source,
            observed_at=observed_at,
            unavailable_reason=reason,
        )

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "availability": self.availability,
            "source": self.source,
            "observed_at": self.observed_at.isoformat(),
            "currency": self.currency,
            "workload_count": len(self.workloads),
            "unavailable_reason": self.unavailable_reason,
            "workloads": [
                {
                    "workload_id": item.workload_id,
                    "actual_usd": str(item.actual_usd),
                }
                for item in self.workloads
            ],
        }


@dataclass(frozen=True, slots=True)
class CostReconcileAdjustment:
    """One ledger correction required to match provider actual billing."""

    window: CostReconcileWindow
    recorded_usd: Decimal
    provider_actual_usd: Decimal
    adjustment_usd: Decimal
    sources: tuple[str, ...] = ()
    workload_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.window, CostReconcileWindow):
            raise TypeError("window must be CostReconcileWindow")
        object.__setattr__(
            self,
            "recorded_usd",
            _non_negative_usd(self.recorded_usd, "recorded_usd"),
        )
        object.__setattr__(
            self,
            "provider_actual_usd",
            _non_negative_usd(self.provider_actual_usd, "provider_actual_usd"),
        )
        object.__setattr__(
            self,
            "adjustment_usd",
            _finite_usd(self.adjustment_usd, "adjustment_usd"),
        )
        sources = tuple(sorted({_non_empty_string(source, "source") for source in self.sources}))
        object.__setattr__(self, "sources", sources)
        if self.workload_id is not None:
            object.__setattr__(
                self,
                "workload_id",
                _non_empty_string(self.workload_id, "workload_id"),
            )

    @property
    def direction(self) -> AdjustmentDirection:
        """Return whether the ledger needs to increase or decrease."""
        if self.adjustment_usd > 0:
            return "increase"
        return "decrease"

    def to_serializable_dict(self) -> dict[str, str | list[str]]:
        """Return a stdlib-JSON-safe dict with Decimal values as strings."""
        result: dict[str, str | list[str]] = {
            "day": self.window.day.isoformat(),
            "capability_class": self.window.capability_class,
            "provider_type": self.window.provider_type,
            "recorded_usd": str(self.recorded_usd),
            "provider_actual_usd": str(self.provider_actual_usd),
            "adjustment_usd": str(self.adjustment_usd),
            "direction": self.direction,
            "sources": list(self.sources),
        }
        if self.workload_id is not None:
            result["workload_id"] = self.workload_id
        return result


@dataclass(frozen=True, slots=True)
class CostReconcilePlan:
    """Deterministic set of cost ledger corrections."""

    adjustments: tuple[CostReconcileAdjustment, ...]
    window_count: int

    def __post_init__(self) -> None:
        adjustments = tuple(self.adjustments)
        for adjustment in adjustments:
            if not isinstance(adjustment, CostReconcileAdjustment):
                raise TypeError("adjustments must contain CostReconcileAdjustment")
        object.__setattr__(self, "adjustments", adjustments)
        if not isinstance(self.window_count, int) or isinstance(self.window_count, bool):
            raise TypeError("window_count must be int")
        if self.window_count < 0:
            raise ValueError("window_count must be non-negative")

    @property
    def adjustment_count(self) -> int:
        """Number of emitted ledger corrections."""
        return len(self.adjustments)

    @property
    def total_adjustment_usd(self) -> Decimal:
        """Signed sum of all emitted corrections."""
        total = _ZERO_USD
        for adjustment in self.adjustments:
            total = _usd(total + adjustment.adjustment_usd)
        return total

    def to_serializable_dict(self) -> dict[str, int | str | list[dict[str, str | list[str]]]]:
        """Return a stdlib-JSON-safe dict with Decimal values as strings."""
        return {
            "window_count": self.window_count,
            "adjustment_count": self.adjustment_count,
            "total_adjustment_usd": str(self.total_adjustment_usd),
            "adjustments": [item.to_serializable_dict() for item in self.adjustments],
        }


@dataclass(frozen=True, slots=True)
class CostTruthUpResult:
    """Auditable outcome of reconciling one provider-actual read."""

    status: CostTruthUpStatus
    provider_actual: ProviderActualCostResult
    plan: CostReconcilePlan
    applied_count: int
    start_day: dt.date
    end_day: dt.date

    def __post_init__(self) -> None:
        if self.status not in {"reconciled", "in_sync", "actual_unavailable"}:
            raise ValueError("unsupported truth-up status")
        if not isinstance(self.provider_actual, ProviderActualCostResult):
            raise TypeError("provider_actual must be ProviderActualCostResult")
        if not isinstance(self.plan, CostReconcilePlan):
            raise TypeError("plan must be CostReconcilePlan")
        if not isinstance(self.applied_count, int) or isinstance(self.applied_count, bool):
            raise TypeError("applied_count must be int")
        if self.applied_count < 0:
            raise ValueError("applied_count must be non-negative")
        _validate_date_window(self.start_day, self.end_day)
        if self.status == "actual_unavailable":
            if self.provider_actual.availability != "unavailable":
                raise ValueError("actual_unavailable requires an unavailable provider result")
            if self.plan.adjustment_count or self.applied_count:
                raise ValueError("actual_unavailable cannot apply adjustments")
        elif self.provider_actual.availability != "available":
            raise ValueError("reconciled and in_sync require available provider actuals")
        if self.status == "reconciled" and self.applied_count == 0:
            raise ValueError("reconciled requires at least one durable workload update")
        if self.status == "in_sync" and (self.plan.adjustment_count or self.applied_count):
            raise ValueError("in_sync cannot contain applied adjustments")

    @property
    def idempotent_noop(self) -> bool:
        """Whether the available provider read required no ledger change."""

        return self.status == "in_sync"

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "idempotent_noop": self.idempotent_noop,
            "applied_count": self.applied_count,
            "window": {
                "start_day": self.start_day.isoformat(),
                "end_day": self.end_day.isoformat(),
            },
            "provider_actual": self.provider_actual.to_serializable_dict(),
            "reconciliation": self.plan.to_serializable_dict(),
        }


@runtime_checkable
class CostTruthUpRepository(Protocol):
    """Transactional seam for durable workload-actual truth-up."""

    async def truth_up(
        self,
        *,
        provider_id: str,
        observed_at: dt.datetime,
        start_day: dt.date,
        end_day: dt.date,
        provider_actuals: Iterable[ProviderActualWorkloadCost],
        tolerance_usd: Decimal,
    ) -> tuple[CostReconcilePlan, int]: ...


class AsyncpgCostTruthUpRepository:
    """Persist provider actuals on workloads and refresh derived daily windows."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def truth_up(
        self,
        *,
        provider_id: str,
        observed_at: dt.datetime,
        start_day: dt.date,
        end_day: dt.date,
        provider_actuals: Iterable[ProviderActualWorkloadCost],
        tolerance_usd: Decimal = _ZERO_USD,
    ) -> tuple[CostReconcilePlan, int]:
        """Set sourced workload actuals and recompute derived rollups atomically."""

        _validate_date_window(start_day, end_day)
        actuals = tuple(provider_actuals)
        _validate_workload_actuals(actuals)
        provider_record_id = _non_empty_string(provider_id, "provider_id")
        observed = _aware_utc(observed_at, "observed_at")
        tolerance = _non_negative_usd(tolerance_usd, "tolerance_usd")
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                PITWALL_COST_TRUTH_UP_LOCK_KEY,
            )
            rows = await conn.fetch(
                _FETCH_PROVIDER_WORKLOADS_SQL,
                [item.workload_id for item in actuals],
            )
            plan, update_params, affected_windows = _plan_workload_truth_up(
                rows=rows,
                provider_id=provider_record_id,
                actuals=actuals,
                start_day=start_day,
                end_day=end_day,
                observed_at=observed,
                tolerance_usd=tolerance,
            )
            if update_params:
                await conn.executemany(_APPLY_WORKLOAD_ACTUAL_SQL, update_params)
                await conn.executemany(
                    _REFRESH_COST_DAILY_WINDOW_SQL,
                    [
                        (window.day, window.capability_class, window.provider_type)
                        for window in affected_windows
                    ],
                )
        return plan, len(update_params)


def reconcile_cost(
    *,
    recorded: Iterable[RecordedCostWindow],
    provider_actuals: Iterable[ProviderActualCostWindow],
    tolerance_usd: Decimal = _ZERO_USD,
) -> CostReconcilePlan:
    """Compare recorded and provider-actual costs and emit ledger corrections.

    Windows are grouped by ``(day, capability_class, provider_type)``. Duplicate
    rows are summed, missing sides are treated as zero, and emitted adjustments
    are sorted by window for deterministic output.
    """
    tolerance = _non_negative_usd(tolerance_usd, "tolerance_usd")
    recorded_by_window = _aggregate_recorded(recorded)
    actual_by_window, sources_by_window = _aggregate_actuals(provider_actuals)
    windows = sorted(set(recorded_by_window) | set(actual_by_window))
    adjustments: list[CostReconcileAdjustment] = []
    for window in windows:
        recorded_usd = recorded_by_window.get(window, _ZERO_USD)
        provider_actual_usd = actual_by_window.get(window, _ZERO_USD)
        adjustment_usd = _usd(provider_actual_usd - recorded_usd)
        if abs(adjustment_usd) <= tolerance:
            continue
        adjustments.append(
            CostReconcileAdjustment(
                window=window,
                recorded_usd=recorded_usd,
                provider_actual_usd=provider_actual_usd,
                adjustment_usd=adjustment_usd,
                sources=tuple(sorted(sources_by_window.get(window, ()))),
            )
        )
    return CostReconcilePlan(adjustments=tuple(adjustments), window_count=len(windows))


async def reconcile_provider_actual_cost(
    repository: CostTruthUpRepository,
    *,
    start_day: dt.date,
    end_day: dt.date,
    provider_actual: ProviderActualCostResult,
    tolerance_usd: Decimal = _ZERO_USD,
) -> CostTruthUpResult:
    """Truth up an available provider result or safely record unavailability."""

    _validate_date_window(start_day, end_day)
    if not isinstance(provider_actual, ProviderActualCostResult):
        raise TypeError("provider_actual must be ProviderActualCostResult")
    if provider_actual.availability == "unavailable":
        return CostTruthUpResult(
            status="actual_unavailable",
            provider_actual=provider_actual,
            plan=CostReconcilePlan(adjustments=(), window_count=0),
            applied_count=0,
            start_day=start_day,
            end_day=end_day,
        )

    plan, applied_count = await repository.truth_up(
        provider_id=provider_actual.provider_id,
        observed_at=provider_actual.observed_at,
        start_day=start_day,
        end_day=end_day,
        provider_actuals=provider_actual.workloads,
        tolerance_usd=tolerance_usd,
    )
    status: CostTruthUpStatus = "reconciled" if applied_count else "in_sync"
    return CostTruthUpResult(
        status=status,
        provider_actual=provider_actual,
        plan=plan,
        applied_count=applied_count,
        start_day=start_day,
        end_day=end_day,
    )


def _aggregate_recorded(
    windows: Iterable[RecordedCostWindow],
) -> dict[CostReconcileWindow, Decimal]:
    totals: dict[CostReconcileWindow, Decimal] = {}
    for item in windows:
        if not isinstance(item, RecordedCostWindow):
            raise TypeError("recorded must contain RecordedCostWindow")
        totals[item.window] = _usd(totals.get(item.window, _ZERO_USD) + item.recorded_usd)
    return totals


def _plan_workload_truth_up(
    *,
    rows: Iterable[Mapping[str, Any]],
    provider_id: str,
    actuals: tuple[ProviderActualWorkloadCost, ...],
    start_day: dt.date,
    end_day: dt.date,
    observed_at: dt.datetime,
    tolerance_usd: Decimal,
) -> tuple[
    CostReconcilePlan,
    list[tuple[Decimal, str, dt.datetime, str]],
    tuple[CostReconcileWindow, ...],
]:
    rows_by_id: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        workload_id = _string_value(row["id"], "id")
        if workload_id in rows_by_id:
            raise RuntimeError(f"duplicate persisted workload row: {workload_id}")
        rows_by_id[workload_id] = row

    missing = sorted(item.workload_id for item in actuals if item.workload_id not in rows_by_id)
    if missing:
        raise ValueError(f"provider actual workloads were not found: {missing!r}")

    adjustments: list[CostReconcileAdjustment] = []
    update_params: list[tuple[Decimal, str, dt.datetime, str]] = []
    windows: set[CostReconcileWindow] = set()
    for actual in actuals:
        row = rows_by_id[actual.workload_id]
        row_provider_id = _string_value(row["provider_id"], "provider_id")
        if row_provider_id != provider_id:
            raise ValueError(
                f"workload {actual.workload_id!r} does not belong to provider {provider_id!r}"
            )
        state = _string_value(row["state"], "state")
        if state not in _TERMINAL_WORKLOAD_STATES:
            raise ValueError(
                f"workload {actual.workload_id!r} is not terminal and cannot be trued up"
            )
        day = _date_value(row["day"], "day")
        if day < start_day or day >= end_day:
            raise ValueError(f"workload {actual.workload_id!r} falls outside [start_day, end_day)")
        window = CostReconcileWindow(
            day=day,
            capability_class=_string_value(row["capability_class"], "capability_class"),
            provider_type=_string_value(row["provider_type"], "provider_type"),
        )
        windows.add(window)
        raw_recorded = row["cost_actual_usd"]
        recorded = (
            _ZERO_USD
            if raw_recorded is None
            else _non_negative_usd(raw_recorded, "cost_actual_usd")
        )
        raw_source = row["cost_actual_provenance"]
        recorded_source = (
            None if raw_source is None else _non_empty_string(raw_source, "cost_actual_provenance")
        )
        adjustment = _usd(actual.actual_usd - recorded)
        if abs(adjustment) > tolerance_usd:
            adjustments.append(
                CostReconcileAdjustment(
                    window=window,
                    workload_id=actual.workload_id,
                    recorded_usd=recorded,
                    provider_actual_usd=actual.actual_usd,
                    adjustment_usd=adjustment,
                    sources=(actual.source,),
                )
            )
        if (
            raw_recorded is None
            or recorded != actual.actual_usd
            or recorded_source != actual.source
        ):
            update_params.append(
                (
                    actual.actual_usd,
                    actual.source,
                    observed_at,
                    actual.workload_id,
                )
            )

    return (
        CostReconcilePlan(
            adjustments=tuple(adjustments),
            window_count=len(windows),
        ),
        update_params,
        tuple(sorted(windows)),
    )


def _validate_workload_actuals(
    actuals: tuple[ProviderActualWorkloadCost, ...],
) -> None:
    if not actuals:
        raise ValueError("provider_actuals must contain at least one workload")
    if any(not isinstance(item, ProviderActualWorkloadCost) for item in actuals):
        raise TypeError("provider_actuals must contain ProviderActualWorkloadCost")
    workload_ids = [item.workload_id for item in actuals]
    if len(workload_ids) != len(set(workload_ids)):
        raise ValueError("provider_actuals must have unique workload_id values")


def _aggregate_actuals(
    windows: Iterable[ProviderActualCostWindow],
) -> tuple[dict[CostReconcileWindow, Decimal], dict[CostReconcileWindow, set[str]]]:
    totals: dict[CostReconcileWindow, Decimal] = {}
    sources: dict[CostReconcileWindow, set[str]] = {}
    for item in windows:
        if not isinstance(item, ProviderActualCostWindow):
            raise TypeError("provider_actuals must contain ProviderActualCostWindow")
        totals[item.window] = _usd(totals.get(item.window, _ZERO_USD) + item.actual_usd)
        sources.setdefault(item.window, set()).add(item.source)
    return totals, sources


def _validate_date_window(start_day: dt.date, end_day: dt.date) -> None:
    if not isinstance(start_day, dt.date) or isinstance(start_day, dt.datetime):
        raise TypeError("start_day must be datetime.date")
    if not isinstance(end_day, dt.date) or isinstance(end_day, dt.datetime):
        raise TypeError("end_day must be datetime.date")
    if end_day <= start_day:
        raise ValueError("end_day must be after start_day")


def _aware_utc(value: object, name: str) -> dt.datetime:
    if not isinstance(value, dt.datetime):
        raise TypeError(f"{name} must be datetime.datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(dt.UTC)


def _date_value(value: object, name: str) -> dt.date:
    if not isinstance(value, dt.date) or isinstance(value, dt.datetime):
        raise TypeError(f"{name} must be datetime.date")
    return value


def _string_value(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    return value


def _decimal_value(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    return value


def _non_empty_string(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must be non-empty")
    return stripped


def _non_negative_usd(value: object, name: str) -> Decimal:
    money = _finite_usd(value, name)
    if money < 0:
        raise ValueError(f"{name} must be non-negative")
    return money


def _finite_usd(value: object, name: str) -> Decimal:
    money = _decimal_value(value, name)
    if not money.is_finite():
        raise ValueError(f"{name} must be finite")
    return _usd(money)


def _usd(value: Decimal) -> Decimal:
    try:
        return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError(f"cost is out of representable USD range: {value}") from exc


__all__ = [
    "AdjustmentDirection",
    "AsyncpgCostTruthUpRepository",
    "CostReconcileAdjustment",
    "CostReconcilePlan",
    "CostReconcileWindow",
    "CostTruthUpResult",
    "CostTruthUpStatus",
    "CostTruthUpRepository",
    "PITWALL_COST_TRUTH_UP_LOCK_KEY",
    "ProviderActualAvailability",
    "ProviderActualCostResult",
    "ProviderActualCostWindow",
    "ProviderActualWorkloadCost",
    "RecordedCostWindow",
    "reconcile_cost",
    "reconcile_provider_actual_cost",
]
