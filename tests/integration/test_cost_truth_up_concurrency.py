"""Real-Postgres concurrency proof for idempotent provider cost truth-up."""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal

import pytest

from pitwall.cost.reconcile_cost import (
    AsyncpgCostTruthUpRepository,
    ProviderActualCostResult,
    ProviderActualWorkloadCost,
    reconcile_provider_actual_cost,
)
from pitwall.reconciler.cost_daily_rollup import run_rollup
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_concurrent_provider_truth_up_applies_absolute_actual_once(pg_pool) -> None:
    day = dt.date(2026, 9, 1)
    capability_id = "cap-cost-truth-up"
    provider_id = "prov-cost-truth-up"
    workload_id = "wkl-cost-truth-up"
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config)
            VALUES ($1, $1, '1.0.0', 'llm', 'per_token', '{}')
            """,
            capability_id,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, config, priority)
            VALUES ($1, $2, $1, 'public_endpoint', '{}', 1)
            """,
            provider_id,
            capability_id,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, submitted_at,
                 cost_estimate_usd, cost_ceiling_usd, cost_actual_usd)
            VALUES ($1, $2, $3, 'inference', 'completed', $4, $5, $5, $5)
            """,
            workload_id,
            capability_id,
            provider_id,
            dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC),
            Decimal("1.000000"),
        )
    await run_rollup(pg_pool)

    source = "provider-billing-fixture"
    provider_actual = ProviderActualCostResult(
        provider_id=provider_id,
        availability="available",
        source=source,
        observed_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        workloads=(
            ProviderActualWorkloadCost(
                workload_id=workload_id,
                actual_usd=Decimal("2.500000"),
                source=source,
            ),
        ),
    )
    repository = AsyncpgCostTruthUpRepository(pg_pool)
    start = asyncio.Event()

    async def reconcile_once():
        await start.wait()
        return await reconcile_provider_actual_cost(
            repository,
            start_day=day,
            end_day=day + dt.timedelta(days=1),
            provider_actual=provider_actual,
        )

    tasks = [asyncio.create_task(reconcile_once()) for _ in range(2)]
    start.set()
    results = await asyncio.gather(*tasks)

    assert sorted(result.status for result in results) == ["in_sync", "reconciled"]
    assert sum(result.applied_count for result in results) == 1
    async with pg_pool.acquire() as conn:
        stored = await conn.fetchrow(
            """
            SELECT cost_actual_usd, cost_actual_provenance,
                   cost_reconciled_at IS NOT NULL AS reconciled
            FROM pitwall.workloads
            WHERE id = $1
            """,
            workload_id,
        )
        daily = await conn.fetchval(
            """
            SELECT cost_usd
            FROM pitwall.cost_daily
            WHERE day = $1 AND capability_class = 'llm'
              AND provider_type = 'public_endpoint'
            """,
            day,
        )
    assert tuple(stored) == (Decimal("2.500000"), source, True)
    assert daily == Decimal("2.500000")

    # cost_daily is derived; its normal rollup must preserve the durable
    # workload actual written by truth-up.
    await run_rollup(pg_pool)
    async with pg_pool.acquire() as conn:
        replayed_daily = await conn.fetchval(
            """
            SELECT cost_usd
            FROM pitwall.cost_daily
            WHERE day = $1 AND capability_class = 'llm'
              AND provider_type = 'public_endpoint'
            """,
            day,
        )
    assert replayed_daily == Decimal("2.500000")
