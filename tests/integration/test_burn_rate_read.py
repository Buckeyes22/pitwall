"""Real-Postgres proof for the Decimal persisted burn-rate read path."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.finops.burn_rate import read_burn_rate
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_read_aggregates_multi_dimension_decimal_rollups(pg_pool: Any) -> None:
    now = dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC)
    rows: list[tuple[dt.date, str, str, int, Decimal]] = []
    for offset in range(7):
        day = dt.date(2026, 6, 4) + dt.timedelta(days=offset)
        rows.extend(
            (
                (day, "embedding", "serverless_lb", 1, Decimal("1.000001")),
                (day, "llm", "serverless_queue", 1, Decimal("2.000002")),
            )
        )

    async with pg_pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO pitwall.cost_daily
                (day, capability_class, provider_type, workload_count, cost_usd)
            VALUES ($1, $2, $3, $4, $5)
            """,
            rows,
        )

    result = await read_burn_rate(pg_pool, budget_usd=Decimal("100"), now=now, window_days=7)

    assert result.observed_day_count == 7
    assert result.spend_to_date_usd == Decimal("21.000021")
    assert result.daily_rate_usd == Decimal("3.000003")
    assert result.forecast_total_usd == Decimal("82.500083")
    assert result.last_rollup_day == now.date()
    assert result.data_sufficiency == "sufficient"
