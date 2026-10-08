"""Burn-rate forecasting and read models for Pitwall FinOps.

The pure forecaster projects a daily spend window.  The read service turns
persisted UTC ``cost_daily`` rollups into one transport-neutral operator
model.  It intentionally requires an explicit UTC ``now`` so replayed reads
are deterministic and never depend on a server-local timezone.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation, Overflow
from typing import Any, Literal

_USD_QUANTUM = Decimal("0.000001")
_PERCENT_QUANTUM = Decimal("0.0001")
_MICROSECONDS_PER_DAY = Decimal("86400000000")
_MINIMUM_SUFFICIENT_OBSERVATION_DAYS = 7
_MAX_WINDOW_DAYS = 366
_FRESH_ROLLUP_LAG_DAYS = 1

Trend = Literal["increasing", "decreasing", "stable", "insufficient_data"]
DataSufficiency = Literal["no_data", "sparse", "sufficient"]


@dataclass(frozen=True)
class SpendPoint:
    """One day of observed spend."""

    day: dt.date
    cost_usd: Decimal


@dataclass(frozen=True)
class BurnRateForecast:
    """Deterministic burn-rate projection from a spend window."""

    burn_rate_usd_per_day: Decimal
    projected_exhaustion: dt.datetime | None
    trend: Trend
    confidence: Decimal
    budget_usd: Decimal
    remaining_budget_usd: Decimal
    runway_days: Decimal | None


@dataclass(frozen=True, slots=True)
class BurnRateRead:
    """One stable, Decimal-exact burn-rate read for all operator surfaces.

    ``observation_window_*`` identifies the queried UTC day range.  The
    forecast uses month-to-date persisted rollups plus the daily rate through
    the next UTC calendar-month boundary.  Decimal fields are serialized as
    strings so REST, MCP, and CLI JSON preserve monetary precision.
    """

    now: dt.datetime
    observation_window_start: dt.date
    observation_window_end: dt.date
    observation_window_days: int
    observed_day_count: int
    spend_to_date_usd: Decimal
    daily_rate_usd: Decimal
    forecast_total_usd: Decimal | None
    budget_usd: Decimal
    remaining_budget_usd: Decimal
    percent_consumed: Decimal | None
    projected_breach_at: dt.datetime | None
    projected_breach_eta_days: Decimal | None
    trend: Trend
    confidence: Decimal
    data_sufficiency: DataSufficiency
    stale: bool
    last_rollup_day: dt.date | None
    already_breached: bool
    at_budget: bool
    projection_overflow: bool

    def to_dict(self) -> dict[str, object]:
        """Return the shared REST/MCP/CLI JSON shape.

        No raw workload input, provider configuration, or database details are
        included in this read-only operator result.
        """
        return {
            "now": _utc_iso(self.now),
            "observation_window": {
                "start": self.observation_window_start.isoformat(),
                "end": self.observation_window_end.isoformat(),
                "days": self.observation_window_days,
                "observed_days": self.observed_day_count,
            },
            "spend_to_date_usd": _decimal_to_json(self.spend_to_date_usd),
            "daily_rate_usd": _decimal_to_json(self.daily_rate_usd),
            "forecast_total_usd": _optional_decimal_to_json(self.forecast_total_usd),
            "budget_usd": _decimal_to_json(self.budget_usd),
            "remaining_budget_usd": _decimal_to_json(self.remaining_budget_usd),
            "percent_consumed": _optional_percent_to_json(self.percent_consumed),
            "projected_breach_at": (
                _utc_iso(self.projected_breach_at) if self.projected_breach_at is not None else None
            ),
            "projected_breach_eta_days": _optional_decimal_to_json(self.projected_breach_eta_days),
            "trend": self.trend,
            "confidence": _decimal_to_json(self.confidence),
            "data_sufficiency": self.data_sufficiency,
            "stale": self.stale,
            "last_rollup_day": (
                self.last_rollup_day.isoformat() if self.last_rollup_day is not None else None
            ),
            "already_breached": self.already_breached,
            "at_budget": self.at_budget,
            "projection_overflow": self.projection_overflow,
        }


class BurnRateForecaster:
    """Pure analytics: convert a window of daily spend into a forecast."""

    _TREND_THRESHOLD = Decimal("1.05")
    _TREND_DOWN_THRESHOLD = Decimal("0.95")
    _CONFIDENCE_WINDOW_DAYS = 7

    def forecast(
        self,
        points: Sequence[SpendPoint],
        *,
        budget_usd: Decimal,
        mtd_spend_usd: Decimal,
        now: dt.datetime,
    ) -> BurnRateForecast:
        """Return a :class:`BurnRateForecast` from *points*.

        The forecast is deterministic given the inputs.  *now* must be
        timezone-aware; it is normalised to UTC internally.
        """
        observed_at = _normalize_utc(now, field_name="now")
        sorted_points = sorted(points, key=lambda p: p.day)
        total_cost = sum((p.cost_usd for p in sorted_points), Decimal("0"))
        n = len(sorted_points)

        if n >= 2:
            first_day = sorted_points[0].day
            last_day = sorted_points[-1].day
            day_span = max(1, (last_day - first_day).days + 1)
        else:
            day_span = 1 if n == 1 else 0

        burn_rate = (
            (total_cost / Decimal(day_span)).quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
            if day_span > 0
            else Decimal("0")
        )

        trend = self._compute_trend(sorted_points)
        confidence = self._compute_confidence(sorted_points, total_cost)

        remaining = (budget_usd - mtd_spend_usd).quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
        if remaining < 0:
            remaining = Decimal("0")

        if remaining > 0 and burn_rate > 0:
            runway = (remaining / burn_rate).quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
            if runway == 0:
                runway = _USD_QUANTUM
            exhaustion = _add_decimal_days(observed_at, runway)
        else:
            runway = None
            exhaustion = None

        return BurnRateForecast(
            burn_rate_usd_per_day=burn_rate,
            projected_exhaustion=exhaustion,
            trend=trend,
            confidence=confidence,
            budget_usd=budget_usd,
            remaining_budget_usd=remaining,
            runway_days=runway,
        )

    def _compute_trend(self, points: Sequence[SpendPoint]) -> Trend:
        n = len(points)
        if n < 2:
            return "insufficient_data"

        mid = n // 2
        first_half = points[:mid]
        second_half = points[mid:]

        first_sum = sum((p.cost_usd for p in first_half), Decimal("0"))
        second_sum = sum((p.cost_usd for p in second_half), Decimal("0"))
        first_avg = first_sum / Decimal(len(first_half)) if first_half else Decimal("0")
        second_avg = second_sum / Decimal(len(second_half)) if second_half else Decimal("0")

        if first_avg == 0:
            return "increasing" if second_avg > 0 else "stable"

        ratio = second_avg / first_avg
        if ratio > self._TREND_THRESHOLD:
            return "increasing"
        if ratio < self._TREND_DOWN_THRESHOLD:
            return "decreasing"
        return "stable"

    def _compute_confidence(self, points: Sequence[SpendPoint], total_cost: Decimal) -> Decimal:
        n = len(points)
        if n < 2:
            return Decimal("0")

        mean = total_cost / Decimal(n)
        if mean == 0:
            # All-zero spend is perfectly predictable.
            point_factor = Decimal(min(1.0, math.sqrt(n) / math.sqrt(self._CONFIDENCE_WINDOW_DAYS)))
            return point_factor.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)

        variance = sum(((p.cost_usd - mean) ** 2 for p in points), Decimal("0")) / Decimal(n)
        std_dev = Decimal(math.sqrt(float(variance))) if variance > 0 else Decimal("0")
        cv = std_dev / mean

        point_factor = Decimal(min(1.0, math.sqrt(n) / math.sqrt(self._CONFIDENCE_WINDOW_DAYS)))
        variance_factor = max(Decimal("0"), Decimal("1") - cv)
        confidence = (point_factor * variance_factor).quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)

        if confidence < 0:
            confidence = Decimal("0")
        elif confidence > 1:
            confidence = Decimal("1")
        return confidence


def _normalize_utc(value: dt.datetime, *, field_name: str) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value.astimezone(dt.UTC)


def _add_decimal_days(origin: dt.datetime, days: Decimal) -> dt.datetime | None:
    """Add Decimal days without converting a monetary-derived value to float."""
    try:
        microseconds = (days * _MICROSECONDS_PER_DAY).to_integral_value(rounding=ROUND_HALF_UP)
        return origin + dt.timedelta(microseconds=int(microseconds))
    except InvalidOperation, Overflow, OverflowError, ValueError:
        return None


def _utc_iso(value: dt.datetime) -> str:
    return _normalize_utc(value, field_name="datetime").isoformat().replace("+00:00", "Z")


def _decimal_to_json(value: Decimal) -> str:
    return format(value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP), "f")


def _optional_decimal_to_json(value: Decimal | None) -> str | None:
    return _decimal_to_json(value) if value is not None else None


def _percent_to_json(value: Decimal) -> str:
    return format(value.quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP), "f")


def _optional_percent_to_json(value: Decimal | None) -> str | None:
    return _percent_to_json(value) if value is not None else None


def _usd(value: object, *, field_name: str, non_negative: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a Decimal-compatible value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a Decimal-compatible value") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if non_negative and parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    try:
        return parsed.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, Overflow) as exc:
        raise ValueError(f"{field_name} is outside the supported USD range") from exc


def _window_days(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("window_days must be an integer")
    if not 1 <= value <= _MAX_WINDOW_DAYS:
        raise ValueError(f"window_days must be between 1 and {_MAX_WINDOW_DAYS}")
    return value


def _data_sufficiency(observed_day_count: int) -> DataSufficiency:
    if observed_day_count == 0:
        return "no_data"
    if observed_day_count < _MINIMUM_SUFFICIENT_OBSERVATION_DAYS:
        return "sparse"
    return "sufficient"


def _month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def _next_month_start(now: dt.datetime) -> dt.datetime | None:
    year = now.year
    month = now.month
    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    try:
        return dt.datetime(year, month, 1, tzinfo=dt.UTC)
    except ValueError:
        return None


def _decimal_days_between(start: dt.datetime, end: dt.datetime) -> Decimal:
    delta = end - start
    microseconds = (
        Decimal(delta.days) * _MICROSECONDS_PER_DAY
        + Decimal(delta.seconds) * Decimal("1000000")
        + Decimal(delta.microseconds)
    )
    return microseconds / _MICROSECONDS_PER_DAY


async def read_burn_rate(
    pool: Any,
    *,
    budget_usd: Decimal | str | int,
    now: dt.datetime,
    window_days: int = 30,
) -> BurnRateRead:
    """Read a deterministic burn-rate model from persisted daily rollups.

    The service aggregates all capability/provider rows into one UTC daily
    spend series.  ``spend_to_date_usd`` is independently summed for the
    current UTC month, so a rolling observation window may span a prior month
    without contaminating the active budget period.
    """
    observed_at = _normalize_utc(now, field_name="now")
    requested_window_days = _window_days(window_days)
    parsed_budget = _usd(budget_usd, field_name="budget_usd")
    observation_window_end = observed_at.date()
    observation_window_start = observation_window_end - dt.timedelta(days=requested_window_days - 1)
    month_start = _month_start(observation_window_end)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT day, COALESCE(SUM(cost_usd), 0) AS cost_usd
               FROM pitwall.cost_daily
               WHERE day >= $1 AND day <= $2
               GROUP BY day
               ORDER BY day ASC""",
            observation_window_start,
            observation_window_end,
        )
        mtd_row = await conn.fetchrow(
            """SELECT COALESCE(SUM(cost_usd), 0) AS spend_to_date_usd
               FROM pitwall.cost_daily
               WHERE day >= $1 AND day <= $2""",
            month_start,
            observation_window_end,
        )
        # The rollup is stale when finished work from a day it should have covered has no row;
        # an idle broker whose last work is rolled up is current, however old that day is.
        last_rollup_row = await conn.fetchrow(
            """SELECT
                   (SELECT MAX(day) FROM pitwall.cost_daily WHERE day <= $1) AS last_rollup_day,
                   (SELECT MAX(DATE(submitted_at AT TIME ZONE 'UTC'))
                      FROM pitwall.workloads
                     WHERE state IN ('completed', 'failed', 'cancelled', 'timed_out')
                       AND DATE(submitted_at AT TIME ZONE 'UTC') <= $1) AS last_workload_day""",
            observation_window_end,
        )

    points = tuple(
        SpendPoint(
            day=row["day"],
            cost_usd=_usd(row["cost_usd"], field_name="cost_daily.cost_usd"),
        )
        for row in rows
    )
    spend_to_date = _usd(
        mtd_row["spend_to_date_usd"] if mtd_row is not None else Decimal("0"),
        field_name="spend_to_date_usd",
    )
    last_rollup_day = last_rollup_row["last_rollup_day"] if last_rollup_row is not None else None
    if last_rollup_day is not None and not isinstance(last_rollup_day, dt.date):
        raise ValueError("last_rollup_day must be a date")
    last_workload_day = (
        last_rollup_row.get("last_workload_day") if last_rollup_row is not None else None
    )
    if last_workload_day is not None and not isinstance(last_workload_day, dt.date):
        raise ValueError("last_workload_day must be a date")

    forecast = BurnRateForecaster().forecast(
        points,
        budget_usd=parsed_budget,
        mtd_spend_usd=spend_to_date,
        now=observed_at,
    )
    data_sufficiency = _data_sufficiency(len(points))
    stale_cutoff = observation_window_end - dt.timedelta(days=_FRESH_ROLLUP_LAG_DAYS)
    # The newest day the daily rollup should already cover: finished work, but not today's.
    due_day = None if last_workload_day is None else min(last_workload_day, stale_cutoff)
    stale = due_day is not None and (last_rollup_day is None or last_rollup_day < due_day)
    already_breached = spend_to_date > parsed_budget
    at_budget = spend_to_date == parsed_budget

    percent_consumed = (
        (spend_to_date / parsed_budget * Decimal("100")).quantize(
            _PERCENT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if parsed_budget > 0
        else None
    )

    projection_overflow = False
    forecast_total: Decimal | None = None
    if data_sufficiency != "no_data":
        month_end = _next_month_start(observed_at)
        if month_end is None:
            projection_overflow = True
        else:
            try:
                forecast_total = _usd(
                    spend_to_date
                    + forecast.burn_rate_usd_per_day
                    * _decimal_days_between(observed_at, month_end),
                    field_name="forecast_total_usd",
                )
            except ValueError:
                projection_overflow = True

    projected_breach_at: dt.datetime | None = None
    projected_breach_eta_days: Decimal | None = None
    if spend_to_date >= parsed_budget:
        projected_breach_at = observed_at
        projected_breach_eta_days = Decimal("0.000000")
    elif forecast.burn_rate_usd_per_day > 0:
        try:
            projected_breach_eta_days = (
                (parsed_budget - spend_to_date) / forecast.burn_rate_usd_per_day
            ).quantize(
                _USD_QUANTUM,
                # A genuinely future breach must never serialize as ETA zero.
                # Ceiling at the public six-decimal day precision also avoids
                # presenting a breach timestamp earlier than the projection.
                rounding=ROUND_CEILING,
            )
            projected_breach_at = _add_decimal_days(observed_at, projected_breach_eta_days)
            if projected_breach_at is None:
                projection_overflow = True
        except InvalidOperation, Overflow:
            projected_breach_eta_days = None
            projection_overflow = True

    return BurnRateRead(
        now=observed_at,
        observation_window_start=observation_window_start,
        observation_window_end=observation_window_end,
        observation_window_days=requested_window_days,
        observed_day_count=len(points),
        spend_to_date_usd=spend_to_date,
        daily_rate_usd=forecast.burn_rate_usd_per_day,
        forecast_total_usd=forecast_total,
        budget_usd=parsed_budget,
        remaining_budget_usd=forecast.remaining_budget_usd,
        percent_consumed=percent_consumed,
        projected_breach_at=projected_breach_at,
        projected_breach_eta_days=projected_breach_eta_days,
        trend=forecast.trend,
        confidence=forecast.confidence,
        data_sufficiency=data_sufficiency,
        stale=stale,
        last_rollup_day=last_rollup_day,
        already_breached=already_breached,
        at_budget=at_budget,
        projection_overflow=projection_overflow,
    )


def configured_monthly_budget_usd() -> Decimal:
    """Read the existing configured monthly budget as Decimal at the edge."""
    from pitwall.config import get_settings

    return _usd(
        get_settings().pitwall_monthly_budget_usd,
        field_name="PITWALL_MONTHLY_BUDGET_USD",
    )


async def read_configured_burn_rate(
    pool: Any,
    *,
    now: dt.datetime,
    window_days: int = 30,
) -> BurnRateRead:
    """Read the common model using Pitwall's effective (runtime, else configured) budget."""
    from pitwall.cost.budget_limits import effective_monthly_budget

    budget = await effective_monthly_budget(pool, default=configured_monthly_budget_usd())
    return await read_burn_rate(
        pool,
        budget_usd=budget,
        now=now,
        window_days=window_days,
    )


async def forecast_from_cost_daily(
    pool: Any,
    *,
    budget_usd: Decimal,
    mtd_spend_usd: Decimal,
    now: dt.datetime,
    window_days: int = 30,
) -> BurnRateForecast:
    """Read the last *window_days* from ``pitwall.cost_daily`` and forecast.

    The adapter queries daily aggregates ordered by day, then delegates to
    :class:`BurnRateForecaster`.
    """
    observed_at = _normalize_utc(now, field_name="now")
    requested_window_days = _window_days(window_days)
    # Pass a date object: the day column is DATE-typed and asyncpg rejects
    # ISO strings ("'str' object has no attribute 'toordinal'").
    cutoff = observed_at.date() - dt.timedelta(days=requested_window_days - 1)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT day, COALESCE(SUM(cost_usd), 0) AS cost_usd
               FROM pitwall.cost_daily
               WHERE day >= $1 AND day <= $2
               GROUP BY day
               ORDER BY day ASC""",
            cutoff,
            observed_at.date(),
        )

    points = [
        SpendPoint(
            day=row["day"],
            cost_usd=Decimal(str(row["cost_usd"])),
        )
        for row in rows
    ]

    return BurnRateForecaster().forecast(
        points=points,
        budget_usd=budget_usd,
        mtd_spend_usd=mtd_spend_usd,
        now=observed_at,
    )


__all__ = [
    "BurnRateRead",
    "BurnRateForecast",
    "BurnRateForecaster",
    "DataSufficiency",
    "SpendPoint",
    "configured_monthly_budget_usd",
    "forecast_from_cost_daily",
    "read_burn_rate",
    "read_configured_burn_rate",
]
