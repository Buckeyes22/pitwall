"""Hermetic coverage for the persisted Decimal burn-rate read service."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from pitwall.finops.burn_rate import read_burn_rate, read_configured_burn_rate

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC)
_GOLDEN_PATH = Path(__file__).parents[2] / "fixtures" / "cost" / "burn_rate_boundaries.json"


def _pool(
    *,
    rows: list[dict[str, object]],
    mtd_spend: Decimal | str = Decimal("0"),
    last_rollup_day: dt.date | None = None,
    last_workload_day: dt.date | None = None,
) -> MagicMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    rows_in_order = [
        {"spend_to_date_usd": mtd_spend},
        {"last_rollup_day": last_rollup_day, "last_workload_day": last_workload_day},
    ]

    async def fetchrow(sql: str, *args: object) -> dict[str, object] | None:
        if "FROM pitwall.budget_limits" in sql:
            return None  # no runtime limits row: the configured budget applies
        return rows_in_order.pop(0)

    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    pool.conn = conn
    return pool


def _rows(*costs: str) -> list[dict[str, object]]:
    start = _NOW.date() - dt.timedelta(days=len(costs) - 1)
    return [
        {"day": start + dt.timedelta(days=index), "cost_usd": Decimal(cost)}
        for index, cost in enumerate(costs)
    ]


async def test_read_uses_persisted_daily_aggregates_and_serializes_golden_schema() -> None:
    pool = _pool(
        rows=_rows(*("10" for _ in range(7))),
        mtd_spend="100",
        last_rollup_day=_NOW.date(),
    )

    result = await read_burn_rate(
        pool,
        budget_usd=Decimal("200"),
        now=_NOW,
        window_days=7,
    )

    assert result.to_dict() == {
        "now": "2026-06-10T12:00:00Z",
        "observation_window": {
            "start": "2026-06-04",
            "end": "2026-06-10",
            "days": 7,
            "observed_days": 7,
        },
        "spend_to_date_usd": "100.000000",
        "daily_rate_usd": "10.000000",
        "forecast_total_usd": "305.000000",
        "budget_usd": "200.000000",
        "remaining_budget_usd": "100.000000",
        "percent_consumed": "50.0000",
        "projected_breach_at": "2026-06-20T12:00:00Z",
        "projected_breach_eta_days": "10.000000",
        "trend": "stable",
        "confidence": "1.000000",
        "data_sufficiency": "sufficient",
        "stale": False,
        "last_rollup_day": "2026-06-10",
        "already_breached": False,
        "at_budget": False,
        "projection_overflow": False,
    }

    query, start, end = pool.conn.fetch.await_args.args
    assert "SUM(cost_usd)" in query
    assert "GROUP BY day" in query
    assert "workloads" not in query
    assert (start, end) == (dt.date(2026, 6, 4), dt.date(2026, 6, 10))
    assert pool.conn.fetchrow.await_args_list[0].args[1:] == (
        dt.date(2026, 6, 1),
        dt.date(2026, 6, 10),
    )


@pytest.mark.parametrize(
    "vector",
    json.loads(_GOLDEN_PATH.read_text())["forecast_vectors"],
    ids=lambda vector: vector["name"],
)
async def test_boundary_forecasts_match_golden_vectors(vector: dict[str, object]) -> None:
    input_data = vector["input"]
    assert isinstance(input_data, dict)
    rows = input_data["rows"]
    assert isinstance(rows, list)
    parsed_rows = [
        {
            "day": dt.date.fromisoformat(str(row["day"])),
            "cost_usd": Decimal(str(row["cost_usd"])),
        }
        for row in rows
        if isinstance(row, dict)
    ]
    last_rollup_day = input_data["last_rollup_day"]

    result = await read_burn_rate(
        _pool(
            rows=parsed_rows,
            mtd_spend=str(input_data["mtd_spend"]),
            last_rollup_day=dt.date.fromisoformat(str(last_rollup_day)),
            last_workload_day=(
                dt.date.fromisoformat(str(input_data["last_workload_day"]))
                if "last_workload_day" in input_data
                else None
            ),
        ),
        budget_usd=str(input_data["budget_usd"]),
        now=dt.datetime.fromisoformat(str(input_data["now"]).replace("Z", "+00:00")),
        window_days=int(str(input_data["window_days"])),
    )

    assert result.to_dict() == vector["expected"]


async def test_no_data_is_explicit_and_has_no_month_end_projection() -> None:
    result = await read_burn_rate(
        _pool(rows=[], mtd_spend="0", last_rollup_day=None),
        budget_usd="100",
        now=_NOW,
    )

    assert result.data_sufficiency == "no_data"
    assert result.observed_day_count == 0
    assert result.daily_rate_usd == Decimal("0")
    assert result.forecast_total_usd is None
    assert result.projected_breach_at is None
    assert result.stale is False


async def test_sparse_data_remains_visible_but_is_low_confidence() -> None:
    result = await read_burn_rate(
        _pool(
            rows=[{"day": _NOW.date(), "cost_usd": Decimal("5")}],
            mtd_spend="5",
            last_rollup_day=_NOW.date(),
        ),
        budget_usd="100",
        now=_NOW,
    )

    assert result.data_sufficiency == "sparse"
    assert result.daily_rate_usd == Decimal("5.000000")
    assert result.confidence == Decimal("0")
    assert result.forecast_total_usd == Decimal("107.500000")
    assert result.projected_breach_at == dt.datetime(2026, 6, 29, 12, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("budget", "mtd_spend", "expected_already_breached", "expected_at_budget", "expected_percent"),
    [
        ("0", "0", False, True, None),
        ("100", "100", False, True, Decimal("100.0000")),
        ("100", "101", True, False, Decimal("101.0000")),
    ],
)
async def test_zero_at_and_already_breached_budget_states_are_explicit(
    budget: str,
    mtd_spend: str,
    expected_already_breached: bool,
    expected_at_budget: bool,
    expected_percent: Decimal | None,
) -> None:
    result = await read_burn_rate(
        _pool(
            rows=[{"day": _NOW.date(), "cost_usd": Decimal("0")}],
            mtd_spend=mtd_spend,
            last_rollup_day=_NOW.date(),
        ),
        budget_usd=budget,
        now=_NOW,
    )

    assert result.already_breached is expected_already_breached
    assert result.at_budget is expected_at_budget
    assert result.percent_consumed == expected_percent
    assert result.projected_breach_at == _NOW
    assert result.projected_breach_eta_days == Decimal("0")
    assert result.remaining_budget_usd == Decimal("0")


async def test_sub_quantum_future_breach_never_serializes_as_zero_eta() -> None:
    result = await read_burn_rate(
        _pool(
            rows=[{"day": _NOW.date(), "cost_usd": Decimal("2.000001")}],
            mtd_spend="0",
            last_rollup_day=_NOW.date(),
        ),
        budget_usd="0.000001",
        now=_NOW,
    )

    assert result.projected_breach_eta_days == Decimal("0.000001")
    assert result.projected_breach_at is not None
    assert result.projected_breach_at > _NOW


async def test_delayed_rollup_is_stale_after_one_calendar_day() -> None:
    result = await read_burn_rate(
        _pool(
            rows=[{"day": _NOW.date() - dt.timedelta(days=2), "cost_usd": Decimal("10")}],
            mtd_spend="10",
            last_rollup_day=_NOW.date() - dt.timedelta(days=2),
            # Work finished yesterday has no rollup row yet: the rollup is behind.
            last_workload_day=_NOW.date() - dt.timedelta(days=1),
        ),
        budget_usd="100",
        now=_NOW,
    )

    assert result.stale is True
    assert result.last_rollup_day == dt.date(2026, 6, 8)


async def test_timezone_input_is_normalized_to_utc_before_window_queries() -> None:
    eastern_now = dt.datetime(2026, 6, 10, 8, 0, tzinfo=ZoneInfo("America/New_York"))
    pool = _pool(
        rows=[{"day": _NOW.date(), "cost_usd": Decimal("1")}],
        mtd_spend="1",
        last_rollup_day=_NOW.date(),
    )

    result = await read_burn_rate(pool, budget_usd="100", now=eastern_now, window_days=1)

    assert result.now == _NOW
    assert pool.conn.fetch.await_args.args[2] == _NOW.date()


async def test_month_boundary_keeps_mtd_spend_in_the_current_utc_month() -> None:
    now = dt.datetime(2026, 3, 1, 0, 30, tzinfo=dt.UTC)
    pool = _pool(
        rows=[
            {"day": dt.date(2026, 2, 28), "cost_usd": Decimal("5")},
            {"day": dt.date(2026, 3, 1), "cost_usd": Decimal("10")},
        ],
        mtd_spend="10",
        last_rollup_day=dt.date(2026, 3, 1),
    )

    result = await read_burn_rate(pool, budget_usd="100", now=now, window_days=3)

    assert result.observation_window_start == dt.date(2026, 2, 27)
    assert result.spend_to_date_usd == Decimal("10.000000")
    assert result.daily_rate_usd == Decimal("7.500000")
    assert result.forecast_total_usd == Decimal("242.343750")
    assert pool.conn.fetchrow.await_args_list[0].args[1:] == (
        dt.date(2026, 3, 1),
        dt.date(2026, 3, 1),
    )


async def test_projection_overflow_is_reported_instead_of_raising() -> None:
    now = dt.datetime(9999, 12, 31, 23, 59, tzinfo=dt.UTC)
    result = await read_burn_rate(
        _pool(
            rows=[{"day": now.date(), "cost_usd": Decimal("1")}],
            mtd_spend="1",
            last_rollup_day=now.date(),
        ),
        budget_usd="100",
        now=now,
    )

    assert result.forecast_total_usd is None
    assert result.projected_breach_at is None
    assert result.projection_overflow is True


@pytest.mark.parametrize(
    ("budget", "now", "window_days", "match"),
    [
        ("-1", _NOW, 30, "budget_usd must be non-negative"),
        ("100", dt.datetime(2026, 6, 10, 12, 0), 30, "now must include timezone"),
        ("100", _NOW, 0, "window_days must be between 1 and 366"),
    ],
)
async def test_invalid_read_inputs_fail_before_database_io(
    budget: str,
    now: dt.datetime,
    window_days: int,
    match: str,
) -> None:
    pool = _pool(rows=[])

    with pytest.raises(ValueError, match=match):
        await read_burn_rate(pool, budget_usd=budget, now=now, window_days=window_days)

    pool.acquire.assert_not_called()


async def test_negative_persisted_rollup_is_rejected() -> None:
    with pytest.raises(ValueError, match="cost_daily.cost_usd must be non-negative"):
        await read_burn_rate(
            _pool(
                rows=[{"day": _NOW.date(), "cost_usd": Decimal("-1")}],
                mtd_spend="0",
                last_rollup_day=_NOW.date(),
            ),
            budget_usd="100",
            now=_NOW,
        )


async def test_configured_read_reuses_the_existing_monthly_budget_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pitwall.config.get_settings",
        lambda: SimpleNamespace(pitwall_monthly_budget_usd=12.5),
    )

    result = await read_configured_burn_rate(
        _pool(
            rows=[{"day": _NOW.date(), "cost_usd": Decimal("1")}],
            mtd_spend="1",
            last_rollup_day=_NOW.date(),
        ),
        now=_NOW,
    )

    assert result.budget_usd == Decimal("12.500000")


@pytest.mark.parametrize(
    ("last_rollup_offset", "last_workload_offset", "stale"),
    [
        # An idle broker: nothing has run since its last rolled-up day. Not stale.
        (2, 2, False),
        # Only today's work is unrolled; the daily rollup has not been due yet. Not stale.
        (1, 0, False),
        # Work from two days ago has no rollup row. Stale.
        (3, 2, True),
        # Finished work but no rollup row at all. Stale.
        (None, 2, True),
        # No finished work at all. Not stale.
        (None, None, False),
    ],
)
async def test_staleness_measures_the_rollup_against_finished_work(
    last_rollup_offset: int | None, last_workload_offset: int | None, stale: bool
) -> None:
    def day(offset: int | None) -> dt.date | None:
        return None if offset is None else _NOW.date() - dt.timedelta(days=offset)

    result = await read_burn_rate(
        _pool(
            rows=[],
            last_rollup_day=day(last_rollup_offset),
            last_workload_day=day(last_workload_offset),
        ),
        budget_usd="100",
        now=_NOW,
    )

    assert result.stale is stale
