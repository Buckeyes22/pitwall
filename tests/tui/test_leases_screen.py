"""Hermetic tests for the Textual leases screen."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from textual.widgets import DataTable, Static

from pitwall.tui import PitwallApp
from pitwall.tui.leases import (
    LeaseDisplayRow,
    LeasesScreen,
    LeasesSnapshot,
    StaticLeasesSource,
    format_optional_usd,
    normalized_status,
    readiness_label,
)
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 6, 2, 16, 30, tzinfo=dt.UTC)
_EXPIRY = dt.datetime(2026, 6, 2, 18, 45, tzinfo=dt.UTC)


def _row(
    *,
    lease_id: str = "lease_alpha",
    provider_id: str = "provider_l4",
    pod_id: str = "pod_alpha",
    served_model: str = "org/model",
    engine: str = "vllm",
    variant: str = "bf16",
    state: str = "active",
    readiness: str = "ready",
    expires_at: dt.datetime = _EXPIRY,
    cost_accrued_usd: Decimal | None = Decimal("1.234"),
    last_traffic_at: dt.datetime | None = None,
    idle_timeout_min: int | None = None,
    renewal_policy: str = "manual",
    max_usd_per_hour: Decimal | None = None,
) -> LeaseDisplayRow:
    return LeaseDisplayRow(
        lease_id=lease_id,
        provider_id=provider_id,
        pod_id=pod_id,
        served_model=served_model,
        engine=engine,
        variant=variant,
        state=state,
        readiness=readiness,
        expires_at=expires_at,
        cost_accrued_usd=cost_accrued_usd,
        last_traffic_at=last_traffic_at,
        idle_timeout_min=idle_timeout_min,
        renewal_policy=renewal_policy,
        max_usd_per_hour=max_usd_per_hour,
    )


def _snapshot(*rows: LeaseDisplayRow) -> LeasesSnapshot:
    return LeasesSnapshot(rows=tuple(rows), refreshed_at=_NOW)


def _overview_source() -> StaticOverviewSource:
    return StaticOverviewSource(
        OverviewSnapshot(
            provider_total=0,
            provider_enabled=0,
            provider_health_counts={},
            lease_state_counts={},
            active_leases=0,
            total_cost_usd=Decimal("0"),
            cost_entry_count=0,
            recent_workload_count=0,
            refreshed_at=_NOW,
        )
    )


def test_format_optional_usd_rounds_costs_and_marks_missing() -> None:
    assert format_optional_usd(Decimal("1.235")) == "$1.24"
    assert format_optional_usd(None) == "pending"


@pytest.mark.property
@given(st.text(max_size=16))
def test_normalized_status_returns_display_safe_non_empty_key(value: str) -> None:
    status = normalized_status(value)

    assert status
    assert status == status.strip().lower()


def test_readiness_label_distinguishes_ready_partial_and_pending() -> None:
    assert (
        readiness_label(
            {
                "runtime_seen_at": "2026-06-02T16:00:00Z",
                "port_mappings_seen_at": "2026-06-02T16:01:00Z",
                "probe_passed_at": "2026-06-02T16:02:00Z",
            }
        )
        == "ready"
    )
    assert readiness_label({"runtime_seen_at": "2026-06-02T16:00:00Z"}) == "partial"
    assert readiness_label(None) == "pending"


def test_resource_label_shows_both_ids_when_they_differ() -> None:
    row = _row(pod_id="pod-9f2")
    both = replace(row, external_resource_id="ep-abc123")

    assert both.resource_label == "ep-abc123 · pod pod-9f2"
    assert row.resource_label == "pod-9f2"


async def test_static_leases_source_counts_refreshes() -> None:
    source = StaticLeasesSource(_snapshot(_row()))

    assert await source.load_leases() == _snapshot(_row())
    assert await source.load_leases() == _snapshot(_row())
    assert source.load_count == 2


async def test_leases_screen_keyboard_navigation_shows_automation_columns() -> None:
    source = StaticLeasesSource(
        _snapshot(
            _row(
                last_traffic_at=dt.datetime(2026, 6, 2, 16, 20, tzinfo=dt.UTC),
                idle_timeout_min=20,
                renewal_policy="activity",
                max_usd_per_hour=Decimal("2.5000"),
            )
        )
    )
    app = PitwallApp(overview_source=_overview_source(), leases_source=source)

    async with app.run_test(size=(180, 32)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        table = app.screen.query_one("#leases-table", DataTable)
        assert [str(column.label) for column in table.columns.values()] == [
            "Lease",
            "Resource",
            "Provider",
            "Model",
            "Engine",
            "Variant",
            "State",
            "Ready",
            "Last traffic",
            "Idle",
            "Policy",
            "Max $/h",
            "Expires",
            "Cost",
        ]
        assert table.get_row_at(0)[8:12] == [
            "2026-06-02 16:20 UTC",
            "20m",
            "activity",
            "$2.5000",
        ]
        await pilot.press("/")
        await pilot.press("a", "c", "t", "i", "v", "i", "t", "y", "enter")
        await pilot.pause()
        assert table.row_count == 1
        await pilot.press("escape", "r")
        await pilot.pause()
        assert source.load_count == 2
        await pilot.press("o")
        await pilot.pause()
        assert app.screen.name == "overview"


async def test_leases_screen_marks_absent_automation_values() -> None:
    source = StaticLeasesSource(
        _snapshot(
            _row(
                last_traffic_at=None,
                idle_timeout_min=None,
                renewal_policy="manual",
                max_usd_per_hour=None,
            )
        )
    )
    app = PitwallApp(overview_source=_overview_source(), leases_source=source)

    async with app.run_test(size=(180, 32)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        row = app.screen.query_one("#leases-table", DataTable).get_row_at(0)
        assert row[8:12] == ["—", "—", "manual", "—"]


async def test_leases_screen_renders_empty_state() -> None:
    source = StaticLeasesSource(_snapshot())
    app = PitwallApp(overview_source=_overview_source(), leases_source=source)

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        await pilot.pause()

        assert str(app.screen.query_one("#leases-summary", Static).content) == (
            "0 active pod leases"
        )
        assert str(app.screen.query_one("#leases-empty", Static).content) == (
            "No active pod leases"
        )
        assert app.screen.query_one("#leases-table", DataTable).row_count == 0


async def test_leases_screen_renders_no_matching_state_for_nonempty_filtered_snapshot() -> None:
    app = PitwallApp(
        overview_source=_overview_source(), leases_source=StaticLeasesSource(_snapshot(_row()))
    )

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        app.screen.apply_filter("missing")
        await pilot.pause()

        assert str(app.screen.query_one("#leases-summary", Static).content) == "1 active pod leases"
        assert (
            str(app.screen.query_one("#leases-empty", Static).content) == "No matching pod leases"
        )


async def test_leases_filter_matches_pod_id_when_external_resource_id_is_set() -> None:
    row = replace(_row(pod_id="pod-9f2"), external_resource_id="ep-abc123")
    app = PitwallApp(
        overview_source=_overview_source(), leases_source=StaticLeasesSource(_snapshot(row))
    )

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        app.screen.apply_filter("pod-9f2")
        await pilot.pause()

        assert str(app.screen.query_one("#leases-summary", Static).content) == (
            "1 active pod leases"
        )
        assert "No matching" not in str(app.screen.query_one("#leases-empty", Static).content)


async def test_leases_refresh_reloads_source() -> None:
    source = StaticLeasesSource(_snapshot(_row()))
    app = PitwallApp(overview_source=_overview_source(), leases_source=source)

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        assert source.load_count == 2


async def test_leases_screen_shows_source_failure_without_stale_rows() -> None:
    class FailingSource:
        async def load_leases(self) -> LeasesSnapshot:
            raise RuntimeError("boom")

    app = PitwallApp(overview_source=_overview_source(), leases_source=FailingSource())

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        await pilot.pause()

        assert str(app.screen.query_one("#leases-error", Static).content) == (
            "Pods / leases unavailable: boom"
        )
        assert app.screen.query_one("#leases-table", DataTable).row_count == 0
        assert str(app.screen.query_one("#leases-summary", Static).content) == "Unavailable"


async def test_failed_refresh_keeps_the_last_known_count_marked_stale() -> None:
    class FlakySource:
        def __init__(self) -> None:
            self.calls = 0

        async def load_leases(self) -> LeasesSnapshot:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("boom")
            return _snapshot(_row(lease_id="lease_a"), _row(lease_id="lease_b"))

    app = PitwallApp(overview_source=_overview_source(), leases_source=FlakySource())

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        assert str(app.screen.query_one("#leases-summary", Static).content) == (
            "2 active pod leases"
        )

        await pilot.press("r")
        await pilot.pause()

        assert str(app.screen.query_one("#leases-summary", Static).content) == (
            "2 active pod leases (stale)"
        )
        assert app.screen.query_one("#leases-table", DataTable).row_count == 2
        assert str(app.screen.query_one("#leases-error", Static).content) == (
            "Pods / leases unavailable: boom"
        )


async def test_app_navigates_between_overview_and_leases() -> None:
    source = StaticLeasesSource(_snapshot(_row()))
    app = PitwallApp(overview_source=_overview_source(), leases_source=source)

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        assert app.screen.name == "leases"

        await pilot.press("o")
        await pilot.pause()
        assert app.screen.name == "overview"


async def test_lease_rows_survive_terminal_resize() -> None:
    source = StaticLeasesSource(_snapshot(_row()))
    app = PitwallApp(overview_source=_overview_source(), leases_source=source)
    async with app.run_test(size=(180, 32)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        table = app.screen.query_one("#leases-table", DataTable)
        original = table.get_row_at(0)
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert table.get_row_at(0) == original
        await pilot.resize_terminal(180, 32)
        await pilot.pause()
        assert table.get_row_at(0) == original
        assert table.row_count == 1
        assert source.load_count == 1


async def test_stale_marking_survives_filtering_until_a_refresh_succeeds() -> None:
    class FlakySource:
        def __init__(self) -> None:
            self.calls = 0

        async def load_leases(self) -> LeasesSnapshot:
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("boom")
            return _snapshot(_row(lease_id="lease_a"), _row(lease_id="lease_b"))

    app = PitwallApp(overview_source=_overview_source(), leases_source=FlakySource())

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("l")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, LeasesScreen)
        summary = screen.query_one("#leases-summary", Static)
        assert str(summary.content) == "2 active pod leases (stale)"

        screen.apply_filter("lease_a")
        assert str(summary.content) == "2 active pod leases (stale)"
        assert screen.query_one("#leases-table", DataTable).row_count == 1
        screen.apply_filter("")
        assert str(summary.content) == "2 active pod leases (stale)"

        await pilot.press("r")
        await pilot.pause()
        assert str(summary.content) == "2 active pod leases"
        screen.apply_filter("lease_a")
        assert str(summary.content) == "2 active pod leases"
