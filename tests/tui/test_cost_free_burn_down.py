"""Hermetic tests for the Cost screen free-tier burn-down panel (Task 13)."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from textual.widgets import Static

from pitwall.cost.read_models import RecentWorkloadsRead
from pitwall.cost.simulator import WhatIfBatchProjection
from pitwall.cost.sub_budgets import ChargebackReport
from pitwall.finops.burn_rate import BurnRateRead
from pitwall.tui import PitwallApp
from pitwall.tui.cost import (
    CostSnapshot,
    FreeBurnDownRow,
    StaticCostSource,
    format_free_burn_down_table,
)
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.providers import ProvidersSnapshot, StaticProvidersSource

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _overview_snapshot() -> OverviewSnapshot:
    return OverviewSnapshot(
        provider_total=3,
        provider_enabled=3,
        provider_health_counts={"healthy": 3},
        lease_state_counts={},
        active_leases=0,
        total_cost_usd=Decimal("0"),
        cost_entry_count=0,
        recent_workload_count=0,
        refreshed_at=_NOW,
    )


def _cost_snapshot() -> CostSnapshot:
    return CostSnapshot(
        runway=BurnRateRead(
            now=_NOW,
            observation_window_start=dt.date(2026, 9, 3),
            observation_window_end=_NOW.date(),
            observation_window_days=7,
            observed_day_count=7,
            spend_to_date_usd=Decimal("50.000000"),
            daily_rate_usd=Decimal("12.500000"),
            forecast_total_usd=Decimal("100.000000"),
            trend="stable",
            confidence=Decimal("0.860000"),
            budget_usd=Decimal("100.000000"),
            remaining_budget_usd=Decimal("50.000000"),
            percent_consumed=Decimal("50.0000"),
            projected_breach_at=dt.datetime(2026, 9, 17, 12, 0, tzinfo=dt.UTC),
            projected_breach_eta_days=Decimal("4.000000"),
            data_sufficiency="sufficient",
            stale=False,
            last_rollup_day=_NOW.date(),
            already_breached=False,
            at_budget=False,
            projection_overflow=False,
        ),
        chargeback=ChargebackReport(
            total_spend_usd=Decimal("45.000000"),
            line_items=(),
            unallocated_spend_usd=Decimal("5.000000"),
        ),
        what_if=WhatIfBatchProjection(
            projections=(),
            total_reserved_usd=Decimal("1.250000"),
            starting_spend_usd=Decimal("45.000000"),
            projected_spend_usd=Decimal("46.250000"),
            budget_usd=Decimal("100.000000"),
            budget_headroom_usd=Decimal("53.750000"),
            would_exceed_budget=False,
        ),
        refreshed_at=_NOW,
        recent_workloads=RecentWorkloadsRead(workloads=()),
        free_burn_down=(
            FreeBurnDownRow(
                pool_key="alpha-pool",
                provider_id="prov-alpha",
                used=Decimal("1000000"),
                budget=Decimal("5000000"),
                reset_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
            ),
            FreeBurnDownRow(
                pool_key="beta-pool",
                provider_id="prov-beta",
                used=Decimal("400000"),
                budget=Decimal("200000"),
                reset_at=None,
            ),
        ),
    )


def test_cost_snapshot_exposes_free_burn_down_rows() -> None:
    snapshot = _cost_snapshot()

    assert len(snapshot.free_burn_down) == 2
    assert snapshot.free_burn_down[0].pool_key == "alpha-pool"
    assert snapshot.free_burn_down[0].used == Decimal("1000000")
    assert snapshot.free_burn_down[0].budget == Decimal("5000000")


def test_format_free_burn_down_table_renders_pool_used_budget_and_reset_date() -> None:
    table = format_free_burn_down_table(_cost_snapshot().free_burn_down)

    assert "Pool" in table
    assert "Used" in table
    assert "Budget" in table
    assert "Reset" in table
    assert "alpha-pool" in table
    assert "1.0M/5.0M" in table
    assert "beta-pool" in table
    assert "resets 2026-10-01" in table


async def test_cost_screen_renders_free_tier_burn_down_panel() -> None:
    source = StaticCostSource(_cost_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=source,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        rendered = str(app.screen.query_one("#cost-free-burn-down", Static).content)

    assert "free-tier burn-down" in rendered
    assert "alpha-pool" in rendered
    assert "1.0M/5.0M" in rendered
    assert "resets 2026-10-01" in rendered
    assert "beta-pool" in rendered


async def test_cost_screen_safe_when_free_burn_down_is_empty() -> None:
    snapshot = replace(_cost_snapshot(), free_burn_down=())
    source = StaticCostSource(snapshot)
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=source,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        rendered = str(app.screen.query_one("#cost-free-burn-down", Static).content)

    assert "free-tier burn-down" in rendered
    assert "no free pools configured" in rendered
