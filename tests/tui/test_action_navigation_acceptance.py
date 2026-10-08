"""Remaining exposed keyboard actions, with static sources and zero mutation checks."""

import pytest
from textual.widgets import DataTable, Input

from pitwall.tui import PitwallApp
from pitwall.tui.cost import StaticCostSource
from pitwall.tui.leases import StaticLeasesSource
from pitwall.tui.models import StaticModelCatalogueSource
from pitwall.tui.operations import StaticOperationsSource
from pitwall.tui.personal import StaticPersonalSource
from pitwall.tui.providers import StaticProvidersSource
from pitwall.tui.resources import StaticResourcesSource
from tests.tui.test_cost_screen import _cost_snapshot
from tests.tui.test_leases_screen import _overview_source, _row, _snapshot
from tests.tui.test_operations_screen import _operations_snapshot
from tests.tui.test_personal_screens import _models
from tests.tui.test_providers_screen import _providers_snapshot
from tests.tui.test_resources_screen import _resources_snapshot

pytestmark = pytest.mark.anyio


def _app():
    overview = _overview_source()
    leases = StaticLeasesSource(_snapshot(_row()))
    models = StaticModelCatalogueSource(_models())
    personal = StaticPersonalSource(leases=[])
    resources = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=overview,
        leases_source=leases,
        models_source=models,
        personal_source=personal,
        resources_source=resources,
        providers_source=StaticProvidersSource(_providers_snapshot()),
        cost_source=StaticCostSource(_cost_snapshot()),
        operations_source=StaticOperationsSource(_operations_snapshot()),
    )
    return app, overview, leases, models, personal, resources


@pytest.mark.parametrize(("key", "screen"), [("l", "leases"), ("m", "models"), ("o", "overview")])
async def test_current_view_shortcut_refreshes_without_stacking_screens(key, screen):
    app, overview, leases, models, personal, resources = _app()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        stack = tuple(app.screen_stack)
        before = (overview.load_count, leases.load_count, models.refresh_count)
        await pilot.press(key)
        await pilot.pause()
        assert app.screen.name == screen
        assert tuple(app.screen_stack) == stack
        after = (overview.load_count, leases.load_count, models.refresh_count)
        index = {"o": 0, "l": 1, "m": 2}[key]
        assert after[index] == before[index] + 1
        assert all(after[i] == before[i] for i in range(3) if i != index)
        if screen in {"leases", "models"}:
            assert app.screen.query_one(f"#{screen}-table", DataTable).row_count == 1
        assert personal.serve_calls == 0 and personal.stopped == []
        assert resources.calls == []


@pytest.mark.parametrize(
    ("key", "screen"),
    [
        ("p", "providers"),
        ("c", "cost"),
        ("e", "resources"),
        ("a", "operations"),
        ("l", "leases"),
        ("m", "models"),
    ],
)
async def test_app_overview_shortcut_returns_from_other_views(key, screen):
    app, overview, _, _, personal, resources = _app()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert app.screen.name == screen
        stack_size = len(app.screen_stack)
        await pilot.press("o")
        await pilot.pause()
        assert app.screen.name == "overview"
        assert len(app.screen_stack) == stack_size
        assert overview.load_count >= 1
        assert personal.serve_calls == 0 and personal.stopped == []
        assert resources.calls == []


@pytest.mark.parametrize(
    ("key", "screen", "destination"),
    [("d", "pods", "serve"), ("t", "routes", "serve"), ("s", "serve", "models")],
)
async def test_personal_escape_returns_to_documented_view_without_mutation(
    key, screen, destination
):
    app, _, _, _, personal, resources = _app()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert app.screen.name == screen
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == destination
        assert personal.serve_calls == 0 and personal.stopped == []
        assert resources.calls == []


async def test_resource_mutation_editor_escape_performs_no_preview_or_write():
    app, _, _, _, personal, resources = _app()
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-mutate")
        await pilot.pause()
        app.screen.query_one("#resource-mutation-json", Input).value = '{"name":"unused"}'
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == "resources"
        assert resources.calls == [] and resources.load_count == 1
        assert personal.serve_calls == 0 and personal.stopped == []
