"""Property checks for the persisted burn-rate read contract."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitwall.finops.burn_rate import read_burn_rate

pytestmark = pytest.mark.property

_NOW = dt.datetime(2026, 6, 20, 12, 0, tzinfo=dt.UTC)
_USD_QUANTUM = Decimal("0.000001")
_PERCENT_QUANTUM = Decimal("0.0001")


class _Connection:
    def __init__(
        self,
        rows: list[dict[str, object]],
        mtd_spend: Decimal,
        last_rollup_day: dt.date | None,
    ) -> None:
        self._rows = rows
        fetchrows: tuple[dict[str, object], ...] = (
            {"spend_to_date_usd": mtd_spend},
            {"last_rollup_day": last_rollup_day},
        )
        self._fetchrows: Iterator[dict[str, object]] = iter(fetchrows)

    async def fetch(self, *_args: object) -> list[dict[str, object]]:
        return self._rows

    async def fetchrow(self, *_args: object) -> dict[str, object]:
        return next(self._fetchrows)


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self._connection)


decimal_usd = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("10000"),
    allow_nan=False,
    allow_infinity=False,
    places=6,
).map(lambda value: Decimal(str(value)))


@given(
    daily_costs=st.lists(decimal_usd, min_size=0, max_size=30),
    budget=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("1000000"),
        allow_nan=False,
        allow_infinity=False,
        places=6,
    ).map(lambda value: Decimal(str(value))),
    mtd_spend=decimal_usd,
)
def test_persisted_read_keeps_decimal_budget_invariants(
    daily_costs: list[Decimal], budget: Decimal, mtd_spend: Decimal
) -> None:
    start = _NOW.date() - dt.timedelta(days=len(daily_costs) - 1)
    rows = [
        {"day": start + dt.timedelta(days=index), "cost_usd": cost}
        for index, cost in enumerate(daily_costs)
    ]
    last_rollup_day = _NOW.date() if rows else None
    pool = _Pool(_Connection(rows, mtd_spend, last_rollup_day))

    result = asyncio.run(
        read_burn_rate(
            pool,
            budget_usd=budget,
            now=_NOW,
            window_days=30,
        )
    )

    assert result.spend_to_date_usd >= Decimal("0")
    assert result.daily_rate_usd >= Decimal("0")
    assert result.remaining_budget_usd >= Decimal("0")
    assert result.confidence >= Decimal("0")
    assert result.confidence <= Decimal("1")
    if not rows:
        assert result.forecast_total_usd is None
    else:
        assert result.forecast_total_usd is not None
        assert result.forecast_total_usd >= Decimal("0")

    if budget == 0:
        assert result.percent_consumed is None
    else:
        assert result.percent_consumed == (mtd_spend / budget * Decimal("100")).quantize(
            _PERCENT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    if mtd_spend >= budget:
        assert result.projected_breach_at == _NOW
        assert result.projected_breach_eta_days == Decimal("0")
    elif result.daily_rate_usd == 0:
        assert result.projected_breach_at is None
        assert result.projected_breach_eta_days is None
    else:
        assert result.projected_breach_eta_days is not None
        assert result.projected_breach_eta_days > 0
        if result.projected_breach_at is not None:
            assert result.projected_breach_at > _NOW

    assert result.to_dict()["daily_rate_usd"] == format(
        result.daily_rate_usd.quantize(_USD_QUANTUM), "f"
    )
