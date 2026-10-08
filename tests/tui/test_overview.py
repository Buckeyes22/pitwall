"""Hermetic tests for the Textual overview shell."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from textual.geometry import Region
from textual.widgets import Static

from pitwall.tui import PitwallApp
from pitwall.tui.overview import (
    OverviewSnapshot,
    StaticOverviewSource,
    count_statuses,
    format_usd,
)

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 6, 2, 16, 30, tzinfo=dt.UTC)


def _snapshot() -> OverviewSnapshot:
    return OverviewSnapshot(
        provider_total=4,
        provider_enabled=3,
        provider_health_counts={"healthy": 2, "unhealthy": 1, "unknown": 1},
        lease_state_counts={"active": 2, "terminated": 1},
        active_leases=2,
        total_cost_usd=Decimal("12.345"),
        cost_entry_count=5,
        recent_workload_count=7,
        refreshed_at=_NOW,
    )


def test_format_usd_rounds_to_cents() -> None:
    assert format_usd(Decimal("12.345")) == "$12.35"
    assert format_usd(Decimal("0")) == "$0.00"


@pytest.mark.property
@given(st.lists(st.text(max_size=12), max_size=50))
def test_count_statuses_normalizes_non_empty_keys_and_preserves_total(
    statuses: list[str],
) -> None:
    counts = count_statuses(statuses)

    assert sum(counts.values()) == len(statuses)
    assert all(key == key.strip().lower() for key in counts)
    assert all(key for key in counts)


def test_snapshot_summaries_are_stable_and_sorted() -> None:
    snapshot = _snapshot()

    assert snapshot.provider_summary == "4 providers, 3 enabled"
    assert snapshot.provider_health_summary == "healthy 2 | unhealthy 1 | unknown 1"
    assert snapshot.lease_state_summary == "active 2 | terminated 1"
    assert snapshot.cost_summary == "$12.35 across 5 daily entries"


async def test_pitwall_app_mounts_overview_screen() -> None:
    source = StaticOverviewSource(_snapshot())
    app = PitwallApp(overview_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()

        assert app.screen.name == "overview"
        assert str(app.screen.query_one("#overview-title", Static).content) == "Pitwall Overview"
        assert (
            str(app.screen.query_one("#provider-count", Static).content) == "4 providers, 3 enabled"
        )
        assert str(app.screen.query_one("#lease-count", Static).content) == "2 active leases"
        assert str(app.screen.query_one("#cost-total", Static).content) == "$12.35"
        assert "healthy 2 | unhealthy 1 | unknown 1" in str(
            app.screen.query_one("#provider-health", Static).content
        )


async def test_unreachable_database_is_a_screen_error_not_a_crash() -> None:
    async def refuse() -> Any:
        raise ConnectionRefusedError(111, "Connect call failed ('127.0.0.1', 5444)")

    app = PitwallApp(pool_factory=refuse)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.screen.name == "overview"
        overview_error = str(app.screen.query_one("#overview-error", Static).content)
        assert overview_error.startswith("Overview unavailable:")
        assert "Connect call failed" in overview_error
        for metric_id in ("provider-count", "lease-count", "cost-total", "workload-count"):
            metric = app.screen.query_one(f"#{metric_id}", Static)
            assert str(metric.content) == "Unavailable", metric_id

        await pilot.press("l")
        await pilot.pause()
        assert app.screen.name == "leases"
        leases_error = str(app.screen.query_one("#leases-error", Static).content)
        assert leases_error.startswith("Pods / leases unavailable:")


async def test_failed_refresh_retains_prior_overview_values() -> None:
    class _FlakyOverviewSource:
        def __init__(self, snapshot: OverviewSnapshot) -> None:
            self._snapshot = snapshot
            self.calls = 0

        async def load_overview(self) -> OverviewSnapshot:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("refresh failed")
            return self._snapshot

    app = PitwallApp(overview_source=_FlakyOverviewSource(_snapshot()))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert str(app.screen.query_one("#cost-total", Static).content) == "$12.35"

        await pilot.press("r")
        await pilot.pause()

        assert str(app.screen.query_one("#cost-total", Static).content) == "$12.35"
        assert (
            str(app.screen.query_one("#provider-count", Static).content) == "4 providers, 3 enabled"
        )
        overview_error = str(app.screen.query_one("#overview-error", Static).content)
        assert overview_error.startswith("Overview unavailable:")


async def test_crash_report_does_not_print_frame_locals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = PitwallApp(overview_source=StaticOverviewSource(_snapshot()))

    def crash() -> None:
        # Built at run time so only a printed local, never the source context, can show it.
        dsn = "postgresql://operator:" + "-".join(("do", "not", "print")) + "@db.example.invalid/p"
        raise RuntimeError(f"crashed holding a {len(dsn)}-character value")

    with pytest.raises(RuntimeError):
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.call_later(crash)
            await pilot.pause()

    report = capsys.readouterr().err
    assert "RuntimeError" in report
    assert "do-not-print" not in report


async def test_overview_refresh_reloads_source() -> None:
    source = StaticOverviewSource(_snapshot())
    app = PitwallApp(overview_source=source)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        assert source.load_count == 2


_METRIC_EXPECTATIONS = (
    ("#provider-count", "4 providers, 3 enabled"),
    ("#lease-count", "2 active leases"),
    ("#cost-total", "$12.35"),
    ("#workload-count", "7 recent workloads"),
)


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_overview_metric_cards_render_content_at_narrow_and_wide_sizes(
    size: tuple[int, int],
) -> None:
    """Every metric card keeps a nonzero, on-screen content region and renders its value.

    The card border and vertical padding used to consume the whole fixed
    height, leaving zero rows for content at both terminal sizes.
    """
    app = PitwallApp(overview_source=StaticOverviewSource(_snapshot()))

    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        assert app.screen.name == "overview"

        screen_region = app.screen.region
        metrics = app.screen.query(".metric")
        assert len(metrics) == len(_METRIC_EXPECTATIONS)

        for selector, expected in _METRIC_EXPECTATIONS:
            metric = app.screen.query_one(selector, Static)
            region = metric.content_region
            assert region.height > 0, selector
            assert region.width > 0, selector
            assert screen_region.contains_region(metric.region), (selector, metric.region)

            crop = Region(
                region.x - metric.region.x,
                region.y - metric.region.y,
                region.width,
                region.height,
            )
            rendered = "".join(
                segment.text for line in metric.render_lines(crop) for segment in line
            )
            compact_rendered = "".join(rendered.split())
            assert "".join(expected.split()) in compact_rendered, (selector, rendered)


async def test_empty_overview_renders_zero_metrics_without_loading_or_error() -> None:
    source = StaticOverviewSource(
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
    app = PitwallApp(overview_source=source)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert (
            str(app.screen.query_one("#provider-count", Static).content) == "0 providers, 0 enabled"
        )
        assert str(app.screen.query_one("#lease-count", Static).content) == "0 active leases"
        assert str(app.screen.query_one("#cost-total", Static).content) == "$0.00"
        assert str(app.screen.query_one("#overview-error", Static).content) == ""
        assert "Loading" not in str(app.screen.query_one("#provider-health", Static).content)
