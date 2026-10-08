"""Hermetic tests for the Textual Cost screen."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from hypothesis import given
from hypothesis import strategies as st
from textual.widgets import Static

from pitwall.cost.read_models import RecentWorkloadsRead, WorkloadCostRead, WorkloadCostRecord
from pitwall.cost.simulator import WhatIfBatchProjection
from pitwall.cost.sub_budgets import ChargebackLineItem, ChargebackReport
from pitwall.finops.burn_rate import BurnRateRead
from pitwall.tui import PitwallApp
from pitwall.tui.cost import (
    CostSnapshot,
    PostgresCostSource,
    StaticCostSource,
    format_cost_table,
    format_data_state,
    format_days,
    format_percent,
    format_workload_cost_table,
)
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.providers import ProvidersSnapshot, StaticProvidersSource
from tests.finops._burn_rate import sample_burn_rate_read

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 6, 2, 16, 30, tzinfo=dt.UTC)


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
            observation_window_start=dt.date(2026, 5, 27),
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
            projected_breach_at=dt.datetime(2026, 6, 10, 16, 30, tzinfo=dt.UTC),
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
            line_items=(
                ChargebackLineItem(
                    tag="ml",
                    allocation_usd=Decimal("60.000000"),
                    spend_usd=Decimal("30.000000"),
                    remaining_usd=Decimal("30.000000"),
                ),
                ChargebackLineItem(
                    tag="infra",
                    allocation_usd=Decimal("30.000000"),
                    spend_usd=Decimal("15.000000"),
                    remaining_usd=Decimal("15.000000"),
                ),
            ),
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
        recent_workloads=RecentWorkloadsRead(
            workloads=(
                WorkloadCostRecord(
                    fields={"id": "wkl-cost"},
                    cost=WorkloadCostRead(
                        estimate=Decimal("1.250000"),
                        ceiling=Decimal("1.500000"),
                        confidence="bounded",
                        actual=Decimal("1.000000"),
                        stored_actual_provenance="provider_report",
                        reconciled_at=_NOW,
                    ),
                ),
            )
        ),
    )


def test_cost_snapshot_summaries_are_stable() -> None:
    snapshot = _cost_snapshot()

    assert snapshot.runway_summary == (
        "Burn: $12.50/day | Remaining: $50.00 | Forecast: $100.00 | "
        "Projected breach: 2026-06-10 16:30 UTC | Breach ETA: 4.0 days | Trend: stable | "
        "Confidence: 86.0% | Data: sufficient"
    )
    assert snapshot.sub_budget_summary == (
        "Sub-budgets: $45.00 spend across 2 tags | $5.00 unallocated"
    )
    assert snapshot.what_if_summary == (
        "What-if: reserves $1.25 | projected spend $46.25 | headroom $53.75 | within budget"
    )
    assert snapshot.refreshed_label == "2026-06-02 16:30 UTC"


@pytest.mark.parametrize(
    ("runway_days", "expected"),
    [
        (None, "unavailable"),
        (Decimal("1"), "1.0 day"),
        (Decimal("2.50"), "2.5 days"),
    ],
)
def test_format_days_handles_unknown_and_pluralization(
    runway_days: Decimal | None,
    expected: str,
) -> None:
    assert format_days(runway_days) == expected


def test_format_data_state_preserves_no_data_and_stale_rollup_context() -> None:
    fresh = sample_burn_rate_read()
    no_data = replace(
        fresh,
        observed_day_count=0,
        data_sufficiency="no_data",
        forecast_total_usd=None,
        daily_rate_usd=Decimal("0"),
    )
    no_data_stale = replace(no_data, stale=True, last_rollup_day=dt.date(2026, 6, 7))
    stale = replace(fresh, stale=True, last_rollup_day=dt.date(2026, 6, 7))

    assert format_data_state(no_data) == "no data"
    assert format_data_state(no_data_stale) == "no data; stale (last rollup 2026-06-07)"
    assert format_data_state(stale) == "stale (last rollup 2026-06-07)"


@pytest.mark.property
@given(
    spend_cents=st.integers(min_value=0, max_value=10_000_000),
    allocation_cents=st.integers(min_value=1, max_value=10_000_000),
)
def test_format_percent_is_bounded_for_non_negative_spend(
    spend_cents: int,
    allocation_cents: int,
) -> None:
    percent = format_percent(
        Decimal(spend_cents) / Decimal(100),
        Decimal(allocation_cents) / Decimal(100),
    )

    numeric = Decimal(percent.removesuffix("%"))
    assert Decimal("0.0") <= numeric <= Decimal("100.0")


def test_format_cost_table_renders_sub_budget_rows() -> None:
    table = format_cost_table(_cost_snapshot().chargeback.line_items)

    assert "Tag" in table
    assert "Allocation" in table
    assert "Spend" in table
    assert "Remaining" in table
    assert "Used" in table
    assert "ml" in table
    assert "$60.00" in table
    assert "$30.00" in table
    assert "50.0%" in table


def test_format_workload_cost_table_preserves_shared_cost_semantics() -> None:
    table = format_workload_cost_table(_cost_snapshot().recent_workloads.workloads)

    assert "Estimate" in table
    assert "Ceiling" in table
    assert "Confidence" in table
    assert "Actual" in table
    assert "Kind" in table
    assert "State" in table
    assert "wkl-cost" in table
    assert "1.250000" in table
    assert "1.500000" in table
    assert "provider_reported" in table
    assert "reconciled" in table


async def test_static_cost_source_loads_snapshot_and_counts_refreshes() -> None:
    source = StaticCostSource(_cost_snapshot())

    first = await source.load_cost()
    second = await source.load_cost()

    assert first == second
    assert source.load_count == 2


async def test_postgres_cost_source_uses_the_shared_burn_rate_read_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = object()
    monkeypatch.setattr(
        "pitwall.tui.cost._gate_month_to_date_spend", AsyncMock(return_value=Decimal("3.5"))
    )
    runway = sample_burn_rate_read()
    read_burn_rate = AsyncMock(return_value=runway)
    monkeypatch.setattr("pitwall.tui.cost.read_burn_rate", read_burn_rate)
    monkeypatch.setattr("pitwall.tui.cost._fetch_month_workload_costs", AsyncMock(return_value=()))
    recent = AsyncMock(return_value=RecentWorkloadsRead(workloads=()))
    monkeypatch.setattr("pitwall.tui.cost.recent_workloads_read", recent)

    async def pool_factory() -> object:
        return pool

    source = PostgresCostSource(
        pool_factory=pool_factory,
        now=lambda: runway.now,
        monthly_budget_usd=runway.budget_usd,
        window_days=7,
    )
    snapshot = await source.load_cost()

    assert snapshot.runway is runway
    # The what-if projection starts from the spend the budget gate enforces.
    assert snapshot.what_if.starting_spend_usd == Decimal("3.5")
    read_burn_rate.assert_awaited_once_with(
        pool,
        budget_usd=runway.budget_usd,
        now=runway.now,
        window_days=7,
    )
    recent.assert_awaited_once_with(pool, limit=10)


async def test_postgres_cost_source_uses_configured_shared_model_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = object()
    monkeypatch.setattr(
        "pitwall.tui.cost._gate_month_to_date_spend", AsyncMock(return_value=Decimal("3.5"))
    )
    runway = sample_burn_rate_read()
    configured_read = AsyncMock(return_value=runway)
    monkeypatch.setattr("pitwall.tui.cost.read_configured_burn_rate", configured_read)
    monkeypatch.setattr("pitwall.tui.cost._fetch_month_workload_costs", AsyncMock(return_value=()))
    monkeypatch.setattr(
        "pitwall.tui.cost.recent_workloads_read",
        AsyncMock(return_value=RecentWorkloadsRead(workloads=())),
    )

    async def pool_factory() -> object:
        return pool

    snapshot = await PostgresCostSource(
        pool_factory=pool_factory,
        now=lambda: runway.now,
        window_days=7,
    ).load_cost()

    assert snapshot.runway is runway
    configured_read.assert_awaited_once_with(pool, now=runway.now, window_days=7)


async def test_postgres_cost_source_keeps_zero_budget_reads_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = object()
    monkeypatch.setattr(
        "pitwall.tui.cost._gate_month_to_date_spend", AsyncMock(return_value=Decimal("3.5"))
    )
    base_runway = sample_burn_rate_read()
    runway = replace(
        base_runway,
        budget_usd=Decimal("0"),
        remaining_budget_usd=Decimal("0"),
        percent_consumed=None,
        forecast_total_usd=Decimal("0"),
        at_budget=True,
        projected_breach_at=base_runway.now,
        projected_breach_eta_days=Decimal("0"),
    )
    monkeypatch.setattr("pitwall.tui.cost.read_burn_rate", AsyncMock(return_value=runway))
    monkeypatch.setattr(
        "pitwall.tui.cost._fetch_month_workload_costs",
        AsyncMock(
            return_value=(
                {
                    "input": {"budget_tag": "team-a"},
                    "cost_actual_usd": Decimal("1.000000"),
                    "cost_estimate_usd": None,
                },
            )
        ),
    )
    monkeypatch.setattr(
        "pitwall.tui.cost.recent_workloads_read",
        AsyncMock(return_value=RecentWorkloadsRead(workloads=())),
    )

    async def pool_factory() -> object:
        return pool

    snapshot = await PostgresCostSource(
        pool_factory=pool_factory,
        now=lambda: runway.now,
        monthly_budget_usd=Decimal("0"),
    ).load_cost()

    assert snapshot.runway.budget_usd == Decimal("0")
    assert snapshot.chargeback.line_items == ()
    assert snapshot.chargeback.total_spend_usd == Decimal("1.000000")
    assert snapshot.chargeback.unallocated_spend_usd == Decimal("1.000000")


async def test_pitwall_app_switches_to_cost_screen() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=StaticCostSource(_cost_snapshot()),
    )

    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()

        assert app.screen.name == "cost"
        assert str(app.screen.query_one("#cost-title", Static).content) == "Cost"


async def test_cost_screen_renders_snapshot() -> None:
    source = StaticCostSource(_cost_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=source,
    )

    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("c")
        await pilot.pause()

        assert source.load_count == 1
        assert "Burn: $12.50/day" in str(app.screen.query_one("#runway-summary", Static).content)
        assert "Forecast: $100.00" in str(app.screen.query_one("#runway-summary", Static).content)
        assert "Projected breach: 2026-06-10 16:30 UTC" in str(
            app.screen.query_one("#runway-summary", Static).content
        )
        assert "Breach ETA: 4.0 days" in str(
            app.screen.query_one("#runway-summary", Static).content
        )
        assert "Data: sufficient" in str(app.screen.query_one("#runway-summary", Static).content)
        assert "Sub-budgets: $45.00 spend across 2 tags" in str(
            app.screen.query_one("#sub-budget-summary", Static).content
        )
        assert "What-if: reserves $1.25" in str(
            app.screen.query_one("#what-if-summary", Static).content
        )
        assert "ml" in str(app.screen.query_one("#sub-budget-table", Static).content)
        workload_table = str(app.screen.query_one("#workload-cost-table", Static).content)
        assert "wkl-cost" in workload_table
        assert "provider_reported" in workload_table
        assert "Last refreshed: 2026-06-02 16:30 UTC" in str(
            app.screen.query_one("#cost-refreshed", Static).content
        )


async def test_cost_screen_pilot_renders_no_data_and_stale_state() -> None:
    snapshot = _cost_snapshot()
    no_data_runway = replace(
        snapshot.runway,
        observed_day_count=0,
        daily_rate_usd=Decimal("0"),
        forecast_total_usd=None,
        projected_breach_at=None,
        projected_breach_eta_days=None,
        data_sufficiency="no_data",
        stale=True,
        last_rollup_day=dt.date(2026, 5, 30),
    )
    source = StaticCostSource(replace(snapshot, runway=no_data_runway))
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=source,
    )

    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("c")
        await pilot.pause()

        summary = str(app.screen.query_one("#runway-summary", Static).content)
        assert "Forecast: unavailable" in summary
        assert "Projected breach: not projected" in summary
        assert "Breach ETA: unavailable" in summary
        assert "Data: no data; stale (last rollup 2026-05-30)" in summary


async def test_cost_screen_refresh_reloads_source() -> None:
    source = StaticCostSource(_cost_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=source,
    )

    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        assert source.load_count == 2


async def test_cost_screen_reports_source_failure() -> None:
    class FailingCostSource:
        async def load_cost(self) -> CostSnapshot:
            raise RuntimeError("boom")

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        cost_source=FailingCostSource(),
    )

    async with app.run_test(size=(110, 32)) as pilot:
        await pilot.press("c")
        await pilot.pause()

        assert str(app.screen.query_one("#cost-error", Static).content) == "Cost unavailable: boom"
        for widget_id in (
            "runway-summary",
            "sub-budget-table",
            "workload-cost-table",
            "cost-free-burn-down",
        ):
            assert str(app.screen.query_one(f"#{widget_id}", Static).content) == "Unavailable"
