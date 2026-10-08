"""Pilot coverage for the binding-derived TUI help overlay."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from textual.widgets import Static

from pitwall.models import load_catalogue
from pitwall.tui import PitwallApp
from pitwall.tui.hardware_fit import HardwareFitSnapshot, StaticHardwareFitSource
from pitwall.tui.help import HelpScreen
from pitwall.tui.leases import LeasesSnapshot, StaticLeasesSource
from pitwall.tui.models import ModelDisplayRow, ModelsSnapshot, StaticModelCatalogueSource
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource

pytestmark = pytest.mark.anyio


def app() -> PitwallApp:
    now = dt.datetime(2026, 8, 27, tzinfo=dt.UTC)
    dossier = load_catalogue().models()[0]
    overview = StaticOverviewSource(OverviewSnapshot(0, 0, {}, {}, 0, Decimal("0"), 0, 0, now))
    models = StaticModelCatalogueSource(
        ModelsSnapshot(
            (
                ModelDisplayRow(
                    dossier.model_id,
                    dossier.model_id,
                    dossier.vendor,
                    dossier.family,
                    str(len(dossier.variants)),
                    dossier.variants[0].id,
                    "yes" if dossier.openai_chat else "no",
                ),
            )
        ),
        dossiers={dossier.model_id: dossier},
    )
    leases = StaticLeasesSource(LeasesSnapshot((), now))
    fit = StaticHardwareFitSource(HardwareFitSnapshot((), now, "fallback"))
    return PitwallApp(
        overview_source=overview,
        models_source=models,
        leases_source=leases,
        hardware_fit_source=fit,
    )


@pytest.mark.parametrize("key, screen", [("o", "overview"), ("m", "models"), ("l", "leases")])
async def test_help_opens_and_closes_on_top_level_screens(key: str, screen: str) -> None:
    console = app()
    async with console.run_test(size=(100, 30)) as pilot:
        await pilot.press(key)
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(console.screen, HelpScreen)
        assert "Global" in str(console.screen.query_one("#help-global", Static).content)
        assert "Command" in str(console.screen.query_one("#help-global", Static).content)
        assert "search overlays" in str(console.screen.query_one("#help-overlays", Static).content)
        await pilot.press("escape")
        await pilot.pause()
        assert console.screen.name == screen


async def test_help_opens_and_closes_on_fit_screen() -> None:
    console = app()
    async with console.run_test(size=(100, 30)) as pilot:
        await pilot.press("m", "enter", "g")
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(console.screen, HelpScreen)
        assert "Details" in str(console.screen.query_one("#help-screen", Static).content)
        await pilot.press("escape")
        await pilot.pause()
        assert console.screen.name == "hardware-fit"
