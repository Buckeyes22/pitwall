"""J38: every console view in every source state, and the key bindings that drive them.

The real ``PitwallApp`` runs under Textual's pilot. In registry mode (``[personal] backend = "registry"`` in pitwall.toml)
all ten views are installed; in personal mode (the default) only Serve, Pods, Routes, and
Models are. For each view and each source state (``tui_fixtures``: loaded, empty, failing)
at 100x30 and 160x45, the view's key shows the view with its pinned rows and text, or its
named failure, and never a traceback. The binding sweep then presses every app and view
binding and asserts its observable effect: the view changes, the source reloads, an action
reaches its source, or an overlay opens and dismisses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button, DataTable, Input, Static

from pitwall.tui import PitwallApp
from pitwall.tui.console import CommandScreen, SearchScreen
from pitwall.tui.help import HelpScreen
from tests.release.tui_fixtures import STATES, console_sources
from tests.tui.test_personal_screens import _fill_spec

pytestmark = [pytest.mark.release, pytest.mark.anyio]

KEYS = {
    "overview": "o",
    "providers": "p",
    "leases": "l",
    "models": "m",
    "serve": "s",
    "pods": "d",
    "routes": "t",
    "cost": "c",
    "resources": "e",
    "operations": "a",
}
PERSONAL_VIEWS = ("serve", "pods", "routes", "models")
SIZES = ((100, 30), (160, 45))
DOWN = "j38 source down"

# (view, state) -> (tables that must hold rows, required text, forbidden text)
EXPECT: dict[tuple[str, str], tuple[dict[str, int], tuple[str, ...], tuple[str, ...]]] = {
    ("overview", "loaded"): ({}, ("4 providers, 3 enabled", "2 active leases"), ()),
    ("overview", "empty"): ({}, ("0 providers, 0 enabled", "Provider health: none"), ()),
    ("overview", "failing"): ({}, (f"Overview unavailable: {DOWN}",), ()),
    ("providers", "loaded"): ({}, ("3 registered providers",), ()),
    ("providers", "empty"): ({}, ("0 registered providers",), ()),
    ("providers", "failing"): ({}, ("Providers unavailable (RuntimeError).",), (DOWN,)),
    ("leases", "loaded"): ({"leases-table": 1}, ("1 active pod leases",), ()),
    ("leases", "empty"): ({"leases-table": 0}, ("No active pod leases",), ()),
    ("leases", "failing"): ({"leases-table": 0}, (f"Pods / leases unavailable: {DOWN}",), ()),
    ("models", "loaded"): ({"models-table": 1}, (), ("No models available",)),
    ("models", "empty"): ({"models-table": 0}, ("No models available",), ()),
    ("models", "failing"): ({"models-table": 0}, (f"Model catalogue unavailable: {DOWN}",), ()),
    ("serve", "loaded"): ({}, ("max spend",), ("refused", "preview failed")),
    ("serve", "empty"): ({}, ("refused: price_over_cap",), ("max spend",)),
    ("serve", "failing"): ({}, (f"preview failed: {DOWN}",), ("max spend",)),
    ("pods", "loaded"): ({"pods-table": 1}, (), ("No personal pods",)),
    ("pods", "empty"): ({"pods-table": 0}, ("No personal pods",), ()),
    ("pods", "failing"): ({"pods-table": 0}, (f"pods unavailable: {DOWN}",), ()),
    ("routes", "loaded"): ({"routes-table": 1}, ("route-shim.sh ornith prompt.md",), ()),
    ("routes", "empty"): ({"routes-table": 0}, ("No personal routes",), ()),
    ("routes", "failing"): ({"routes-table": 0}, (f"routes unavailable: {DOWN}",), ()),
    ("cost", "loaded"): ({}, ("Burn: $12.50/day", "wkl-cost"), ()),
    ("cost", "empty"): ({}, ("Burn: $12.50/day",), ("wkl-cost",)),
    ("cost", "failing"): ({}, (f"Cost unavailable: {DOWN}",), ()),
    ("resources", "loaded"): ({}, ("Resources: 1 pod | 2 endpoints",), ()),
    ("resources", "empty"): ({}, ("Resources: 0 pods | 0 endpoints",), ()),
    ("resources", "failing"): ({}, (f"Resources unavailable: {DOWN}",), ()),
    ("operations", "loaded"): ({}, ("Operations: 2 capabilities | 3 providers",), ()),
    ("operations", "empty"): ({}, ("Operations: 0 capabilities | 0 providers",), ()),
    ("operations", "failing"): ({}, (f"Operations unavailable: {DOWN}",), ()),
}


def _cases() -> list[Any]:
    cases = []
    for mode, views in (("registry", tuple(KEYS)), ("personal", PERSONAL_VIEWS)):
        for view in views:
            for state in STATES:
                for size in SIZES:
                    case_id = f"{mode}-{view}-{state}-{size[0]}x{size[1]}"
                    cases.append(pytest.param(mode, view, state, size, id=case_id))
    return cases


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, tmp_path: Path) -> str:
    mode: str = request.param
    # The backend is chosen only by `[personal] backend` in pitwall.toml; `DATABASE_URL` never
    # selects it, so the ambient value is cleared for both modes.
    config_file = tmp_path / "pitwall.toml"
    config_file.write_text(f'[personal]\nbackend = "{mode}"\n', encoding="utf-8")
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(config_file))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return mode


def _rendered(app: PitwallApp) -> str:
    return "\n".join(str(widget.render()) for widget in app.screen.query(Static))


async def _open(pilot: Any, app: PitwallApp, view: str, mode: str) -> None:
    await pilot.pause()
    await pilot.press("escape")  # leave the start screen's focused input, if any
    await pilot.pause()
    if app.screen.name == view:  # enter through the key, never by starting there
        await pilot.press("p" if mode == "registry" else "d")
        await pilot.pause()
    await pilot.press(KEYS[view])
    await pilot.pause()
    await pilot.pause()


@pytest.mark.parametrize(("backend", "view", "state", "size"), _cases(), indirect=["backend"])
async def test_view_in_state(backend: str, view: str, state: str, size: tuple[int, int]) -> None:
    app = PitwallApp(**console_sources(view, state))
    async with app.run_test(size=size) as pilot:
        await _open(pilot, app, view, backend)
        assert app.screen.name == view
        if view == "serve":
            _fill_spec(app.screen)
            app.screen.query_one("#serve-preview", Button).press()
            await pilot.pause()
            await pilot.pause()
        tables, required, forbidden = EXPECT[(view, state)]
        for table_id, rows in tables.items():
            assert app.screen.query_one(f"#{table_id}", DataTable).row_count == rows
        text = _rendered(app)
        for marker in required:
            assert marker in text, (view, state, marker)
        for marker in (*forbidden, "Traceback"):
            assert marker not in text, (view, state, marker)


@pytest.mark.parametrize("backend", ["registry"], indirect=True)
@pytest.mark.parametrize("view", list(KEYS))
async def test_every_view_key_switches_views(backend: str, view: str) -> None:
    app = PitwallApp(**console_sources(view, "loaded"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(pilot, app, view, backend)
        stack = len(app.screen_stack)
        await pilot.press(KEYS[view])  # the current view's own key never stacks a copy
        await pilot.pause()
        assert app.screen.name == view and len(app.screen_stack) == stack


_REFRESH = {
    "overview": ("overview_source", "load_overview"),
    "providers": ("providers_source", "load_providers"),
    "leases": ("leases_source", "load_leases"),
    "models": ("models_source", "refresh"),
    "pods": ("personal_source", "status"),
    "routes": ("personal_source", "status"),
    "cost": ("cost_source", "load_cost"),
    "resources": ("resources_source", "load_resources"),
    "operations": ("operations_source", "load_operations"),
}


@pytest.mark.parametrize("backend", ["registry"], indirect=True)
@pytest.mark.parametrize("view", list(_REFRESH))
async def test_r_reloads_the_view_source(backend: str, view: str) -> None:
    sources = console_sources(view, "loaded")
    source_name, method = _REFRESH[view]
    app = PitwallApp(**sources)
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(pilot, app, view, backend)
        app.set_focus(None)
        before = sources[source_name].calls[method]
        await pilot.press("r")
        await pilot.pause()
        await pilot.pause()
        assert sources[source_name].calls[method] == before + 1


@pytest.mark.parametrize("backend", ["registry"], indirect=True)
@pytest.mark.parametrize(
    ("key", "method"), [("g", "probe_provider"), ("v", "provider_availability")]
)
async def test_provider_actions_reach_the_source(backend: str, key: str, method: str) -> None:
    sources = console_sources("providers", "loaded")
    app = PitwallApp(**sources)
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(pilot, app, "providers", backend)
        app.screen.query_one("#providers-probe-id", Input).value = "runpod"
        app.set_focus(None)
        await pilot.press(key)
        await pilot.pause()
        await pilot.pause()
        assert sources["providers_source"].calls[method] == 1


@pytest.mark.parametrize("backend", ["registry"], indirect=True)
async def test_models_v_toggles_details_in_the_narrow_layout(backend: str) -> None:
    app = PitwallApp(**console_sources("models", "loaded"))
    async with app.run_test(size=(100, 30)) as pilot:
        await _open(pilot, app, "models", backend)
        screen: Any = app.screen
        before = screen._details_visible
        app.set_focus(None)
        await pilot.press("v")
        await pilot.pause()
        assert screen._details_visible is (not before)


@pytest.mark.parametrize("backend", ["registry"], indirect=True)
async def test_help_command_and_search_overlays_open_act_and_dismiss(backend: str) -> None:
    app = PitwallApp(**console_sources("leases", "loaded"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(pilot, app, "leases", backend)
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == "leases"

        await pilot.press("colon")
        await pilot.pause()
        assert isinstance(app.screen, CommandScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == "leases"
        await pilot.press("colon")
        await pilot.pause()
        for character in "cost":
            await pilot.press(character)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert app.screen.name == "cost"

        await pilot.press("l")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert isinstance(app.screen, SearchScreen)
        for character in "zzz":
            await pilot.press(character)
        await pilot.press("enter")
        await pilot.pause()
        assert "filter: zzz" in app.sub_title
        assert "No matching pod leases" in _rendered(app)


@pytest.mark.parametrize("backend", ["personal"], indirect=True)
@pytest.mark.parametrize("view", ["pods", "routes"])
async def test_escape_returns_from_pods_and_routes_to_serve(backend: str, view: str) -> None:
    app = PitwallApp(**console_sources(view, "loaded"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(pilot, app, view, backend)
        app.set_focus(None)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == "serve"


@pytest.mark.parametrize("backend", ["registry"], indirect=True)
@pytest.mark.parametrize("key", ["q", "ctrl+c"])
async def test_quit_bindings_end_the_console(backend: str, key: str) -> None:
    app = PitwallApp(**console_sources("overview", "loaded"))
    async with app.run_test(size=(160, 45)) as pilot:
        await _open(pilot, app, "overview", backend)
        await pilot.press(key)
        await pilot.pause()
        assert not app.is_running
