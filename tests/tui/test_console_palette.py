"""Keyboard coverage for the console command and search overlays."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from textual.widgets import DataTable

from pitwall.tui import PitwallApp
from pitwall.tui.models import ModelDisplayRow, ModelsSnapshot, StaticModelCatalogueSource
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource

pytestmark = pytest.mark.anyio


def app() -> PitwallApp:
    now = dt.datetime(2026, 8, 28, tzinfo=dt.UTC)
    return PitwallApp(
        overview_source=StaticOverviewSource(
            OverviewSnapshot(0, 0, {}, {}, 0, Decimal("0"), 0, 0, now)
        ),
        models_source=StaticModelCatalogueSource(
            ModelsSnapshot(
                (
                    ModelDisplayRow(
                        "acme/alpha", "acme/alpha", "acme", "Alpha", "1", "bf16", "yes"
                    ),
                    ModelDisplayRow("acme/beta", "acme/beta", "acme", "Beta", "1", "bf16", "yes"),
                )
            )
        ),
    )


async def test_palette_dispatches_named_view_and_escape_restores_focus() -> None:
    console = app()
    async with console.run_test(size=(100, 30)) as pilot:
        await pilot.press(":")
        assert console.screen.name == "console-command"
        await pilot.press(*list("models"), "enter")
        await pilot.pause()
        assert console.screen.name == "models"

        await pilot.press(":", "escape")
        await pilot.pause()
        assert console.screen.name == "models"
        assert console.screen.query_one("#models-table", DataTable).has_focus


async def test_search_filters_active_models_list_and_escape_restores_rows() -> None:
    console = app()
    async with console.run_test(size=(100, 30)) as pilot:
        await pilot.press("m", "/")
        assert console.screen.name == "console-search"
        await pilot.press(*list("beta"))
        await pilot.pause()
        table = console.screen_stack[-2].query_one("#models-table", DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "acme/beta"

        await pilot.press("escape")
        await pilot.pause()
        table = console.screen.query_one("#models-table", DataTable)
        assert table.row_count == 2
        assert table.has_focus


async def test_search_enter_keeps_filter_and_announces_it_on_list_screen() -> None:
    console = app()
    async with console.run_test(size=(100, 30)) as pilot:
        await pilot.press("m", "/", *list("beta"), "enter")
        await pilot.pause()

        table = console.screen.query_one("#models-table", DataTable)
        assert table.row_count == 1
        assert console.sub_title == "filter: beta — / to change, Esc to clear"
