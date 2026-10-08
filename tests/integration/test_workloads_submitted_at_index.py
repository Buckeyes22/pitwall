"""The budget gate's month-to-date query can use the submitted_at index from migration 0037."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.cost.budget_gate import MONTH_TO_DATE_SPEND_SQL
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_the_gate_query_can_use_the_index(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        indexes = {
            row["indexname"]
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'pitwall' AND tablename = 'workloads'"
            )
        }
        assert "idx_workloads_submitted_at" in indexes
        assert "idx_workloads_month_spend" not in indexes
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            plan = "\n".join(
                row[0] for row in await conn.fetch(f"EXPLAIN {MONTH_TO_DATE_SPEND_SQL}")
            )
        assert "idx_workloads_submitted_at" in plan, plan
