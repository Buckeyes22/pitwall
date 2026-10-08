"""Every month-to-date spend reader agrees with the budget gate (review finding #5).

The gate counts COALESCE(actual, ceiling, estimate) for every workload submitted this month,
whatever its state; the alert, threshold, exporter, and TUI readers used to count only
``cost_actual_usd`` for a subset of states, so they saw a fraction of what admission enforced.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from pitwall.cost import alerts, exporter
from pitwall.cost.budget_gate import BudgetGate
from pitwall.cost.sub_budgets import SubBudgetConfig, generate_chargeback_report
from pitwall.tui import cost as tui_cost
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

EXPECTED = Decimal("6.75")  # 1.00 + 0.50 + 2.00 + 0.25 + 3.00; last month's 100 excluded


async def _seed(pool: Any) -> dt.datetime:
    now = dt.datetime.now(dt.UTC)
    last_month = (now.replace(day=1) - dt.timedelta(days=2)).replace(hour=12)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)"
            " VALUES ('cap_mtd', 'llm.mtd', '1.0.0', 'llm', 'per_token', '{}')"
        )
        await conn.execute(
            "INSERT INTO pitwall.providers (id, capability_id, name, provider_type, config, priority)"
            " VALUES ('prov_mtd', 'cap_mtd', 'prov_mtd', 'serverless_lb', '{}', 1)"
        )
        rows = [
            ("wkl_done", "cap_mtd", "prov_mtd", "completed", now, Decimal("1.00"), None, None),
            ("wkl_failed", "cap_mtd", "prov_mtd", "failed", now, Decimal("0.50"), None, None),
            (
                "wkl_queued",
                "cap_mtd",
                "prov_mtd",
                "queued",
                now,
                None,
                Decimal("2.00"),
                Decimal("1.00"),
            ),
            ("wkl_running", "cap_mtd", "prov_mtd", "running", now, None, None, Decimal("0.25")),
            (
                "wkl_raw",
                "runpod_direct",
                "runpod_direct",
                "completed",
                now,
                Decimal("3.00"),
                None,
                None,
            ),
            ("wkl_old", "cap_mtd", "prov_mtd", "completed", last_month, Decimal("100"), None, None),
        ]
        for wid, cap, prov, state, at, actual, ceiling, estimate in rows:
            await conn.execute(
                "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, submitted_at,"
                " cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)"
                " VALUES ($1, $2, $3, 'inference', $4, $5, $6, $7, $8)",
                wid,
                cap,
                prov,
                state,
                at,
                actual,
                ceiling,
                estimate,
            )
    return now


async def test_every_reader_matches_the_gate(pg_pool: Any) -> None:
    now = await _seed(pg_pool)
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("10"), per_request_max_usd=Decimal("10"))
    assert await gate.current_mtd_spend() == EXPECTED

    assert await alerts._compute_mtd_spend(pg_pool, now) == EXPECTED

    # A runtime limits row (pitwall budget set) overrides the exporter's startup budget.
    from pitwall.cost.budget_limits import set_limits

    await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("13.5"),
        per_request_max_usd=Decimal("10"),
        reason="test",
        actor="cli",
    )
    app = SimpleNamespace(state=SimpleNamespace(pool=pg_pool, budget=Decimal("10")))
    await exporter._refresh(app)  # type: ignore[arg-type]  # reason: test passes a SimpleNamespace stand-in where FastAPI is expected
    assert Decimal(str(exporter.cloud_spend_month_usd._value.get())) == EXPECTED
    assert exporter.cloud_budget_usd._value.get() == 13.5
    assert exporter.cloud_budget_pct._value.get() == 50.0
    by_provider = {
        sample.labels["provider"]: Decimal(str(sample.value))
        for metric in exporter.provider_spend_month_usd.collect()
        for sample in metric.samples
    }
    assert by_provider["prov_mtd"] == Decimal("3.75") and by_provider["runpod_direct"] == Decimal(
        "3.00"
    )

    workloads = await tui_cost._fetch_month_workload_costs(pg_pool)
    report = generate_chargeback_report(SubBudgetConfig(total_budget_usd=Decimal("10")), workloads)
    assert report.total_spend_usd == EXPECTED
