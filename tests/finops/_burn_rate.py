"""Shared immutable burn-rate fixture for transport-adapter tests."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pitwall.finops.burn_rate import BurnRateRead


def sample_burn_rate_read() -> BurnRateRead:
    """Return a sufficient, fresh read with a fixed Decimal schema."""
    return BurnRateRead(
        now=dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC),
        observation_window_start=dt.date(2026, 6, 4),
        observation_window_end=dt.date(2026, 6, 10),
        observation_window_days=7,
        observed_day_count=7,
        spend_to_date_usd=Decimal("100.000000"),
        daily_rate_usd=Decimal("10.000000"),
        forecast_total_usd=Decimal("305.000000"),
        budget_usd=Decimal("200.000000"),
        remaining_budget_usd=Decimal("100.000000"),
        percent_consumed=Decimal("50.0000"),
        projected_breach_at=dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC),
        projected_breach_eta_days=Decimal("10.000000"),
        trend="stable",
        confidence=Decimal("1.000000"),
        data_sufficiency="sufficient",
        stale=False,
        last_rollup_day=dt.date(2026, 6, 10),
        already_breached=False,
        at_budget=False,
        projection_overflow=False,
    )
