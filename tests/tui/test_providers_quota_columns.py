"""Hermetic tests for the Providers screen quota columns (Task 13)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from textual.widgets import Static

from pitwall.tui import PitwallApp
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.providers import (
    ProviderEntry,
    ProvidersSnapshot,
    StaticProvidersSource,
    format_providers_table,
)

pytestmark = pytest.mark.anyio


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
        refreshed_at=dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC),
    )


def _quota_snapshot() -> ProvidersSnapshot:
    return ProvidersSnapshot(
        entries=(
            ProviderEntry(
                provider_id="prov-alpha",
                status="healthy",
                pricing_model="zero",
                armed=True,
                active_pod_id="pod-alpha-1",
                quota_headroom=0.4,
                reset_in="2d 3h",
                tos="caution",
            ),
            ProviderEntry(
                provider_id="prov-beta",
                status="healthy",
                pricing_model="zero",
                armed=False,
                active_pod_id=None,
                quota_headroom=0.0,
                reset_in=None,
                tos="avoid",
            ),
            ProviderEntry(
                provider_id="prov-gamma",
                status="registered",
                pricing_model="tagged",
                armed=False,
                active_pod_id=None,
            ),
        )
    )


def test_format_providers_table_renders_headroom_reset_tos_columns() -> None:
    table = format_providers_table(_quota_snapshot().entries)

    assert "HEADROOM" in table
    assert "RESET" in table
    assert "TOS" in table
    assert "[####------] 40%" in table
    assert "2d 3h" in table
    assert "caution" in table
    assert "avoid" in table
    assert "prov-alpha" in table
    assert "prov-beta" in table
    assert "prov-gamma" in table


def test_provider_entry_as_row_grows_to_twelve_cells() -> None:
    entry = ProviderEntry(
        provider_id="prov-alpha",
        status="healthy",
        pricing_model="zero",
        armed=True,
        active_pod_id="pod-alpha-1",
        quota_headroom=0.4,
        reset_in="2d 3h",
        tos="caution",
    )

    row = entry.as_row()

    assert len(row) == 12
    assert row[0] == "prov-alpha"
    assert row[-3].endswith("40%")
    assert row[-2] == "2d 3h"
    assert row[-1] == "caution"


async def test_providers_screen_renders_quota_columns_through_static_source() -> None:
    source = StaticProvidersSource(_quota_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(140, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        table = str(app.screen.query_one("#providers-table", Static).content)

    assert "HEADROOM" in table
    assert "RESET" in table
    assert "TOS" in table
    assert "[####------] 40%" in table
    assert "2d 3h" in table
    assert "caution" in table
    assert "avoid" in table


async def test_providers_screen_renders_dash_for_unset_quota_fields() -> None:
    source = StaticProvidersSource(
        ProvidersSnapshot(
            entries=(
                ProviderEntry(
                    provider_id="prov-ungated",
                    status="registered",
                    pricing_model="tagged",
                    armed=False,
                    active_pod_id=None,
                ),
            )
        )
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(140, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        table = str(app.screen.query_one("#providers-table", Static).content)

    assert "prov-ungated" in table
    assert "—" in table
    assert "HEADROOM" in table
    assert "RESET" in table
    assert "TOS" in table
