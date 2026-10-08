"""Hermetic Pilot coverage for the personal Serve wizard."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from rich.text import Text
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Static

from pitwall.personal.service import ServePreview, ServeRefused
from pitwall.personal.state import PersonalLease
from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE
from pitwall.serve import ServePlanResult
from pitwall.tui import PitwallApp
from pitwall.tui.hardware_fit import HardwareFitSnapshot, StaticHardwareFitSource
from pitwall.tui.models import ModelDisplayRow, ModelsSnapshot, StaticModelCatalogueSource
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.personal import StaticPersonalSource

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 2, 12, tzinfo=dt.UTC)
_MODEL = "ornith-ai/Ornith-1.5-35B-A3B-GGUF"
_GPU = "NVIDIA GeForce RTX 3090"


def _models() -> ModelsSnapshot:
    return ModelsSnapshot(
        rows=(
            ModelDisplayRow(
                model_id=_MODEL,
                model=_MODEL,
                vendor="ornith-ai",
                family="Ornith",
                variants="1",
                default="Q4_K_M",
                chat="yes",
            ),
        )
    )


def _preview() -> ServePreview:
    return ServePreview(
        plan=ServePlanResult(
            model_id="ornith",
            engine="llama.cpp",
            variant="Q4_K_M",
            gpu_class=_GPU,
            gpu_count=1,
            image="ghcr.io/ggml-org/llama.cpp:server-cuda",
            argv=["--model", "/models/ornith.gguf"],
            volume_cache_env={},
            fit="fits",
            startup_timeout_s=900,
            cost_estimate_usd="0.22",
            price_source="live",
        ),
        price_per_hour_usd=Decimal("0.22"),
        max_spend_usd=Decimal("0.22"),
        deadline_at=_NOW + dt.timedelta(hours=1),
        request_preview={
            "image_name": "ghcr.io/ggml-org/llama.cpp:server-cuda",
            "docker_start_cmd": "llama-server --api-key <redacted>",
            "ports": "8000/http",
            "gpu_class": _GPU,
            "cloud": "community",
        },
    )


def _lease(*, state: str = "ready") -> PersonalLease:
    return PersonalLease(
        route="ornith",
        pod_id="pod-1",
        model=_MODEL,
        served_model_id="ornith",
        engine="llama.cpp",
        variant="Q4_K_M",
        image="ghcr.io/ggml-org/llama.cpp:server-cuda",
        gpu_class=_GPU,
        gpu_count=1,
        cloud="community",
        price_per_hour_usd="0.22",
        endpoint_url="https://pod-1-8000.proxy.runpod.net/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=_NOW,
        deadline_at=_NOW + dt.timedelta(hours=1),
        state=state,
    )


def _app(source: StaticPersonalSource) -> PitwallApp:
    return PitwallApp(
        personal_source=source,
        models_source=StaticModelCatalogueSource(_models()),
        overview_source=StaticOverviewSource(
            OverviewSnapshot(0, 0, {}, {}, 0, Decimal("0"), 0, 0, _NOW)
        ),
        hardware_fit_source=StaticHardwareFitSource(HardwareFitSnapshot((), _NOW, "live")),
    )


def _fill_spec(screen: Screen[object]) -> None:
    screen.query_one("#serve-model", Input).value = _MODEL
    screen.query_one("#serve-gpu-class", Input).value = _GPU
    screen.query_one("#serve-cap", Input).value = "1.00"
    screen.query_one("#serve-route", Input).value = "ornith"


async def test_wizard_previews_before_confirm_and_requires_typed_route() -> None:
    source = StaticPersonalSource(preview=_preview(), lease=_lease())
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        _fill_spec(screen)
        screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        rendered = str(screen.query_one("#serve-preview-result", Static).content)
        assert "max spend" in rendered and "<redacted>" in rendered
        assert source.serve_calls == 0
        screen.query_one("#serve-confirm-text", Input).value = "wrong"
        screen.query_one("#serve-launch", Button).press()
        await pilot.pause()
        assert source.serve_calls == 0
        screen.query_one("#serve-confirm-text", Input).value = "ornith"
        screen.query_one("#serve-launch", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert source.serve_calls == 1
        assert "ready" in str(screen.query_one("#serve-status", Static).content)


def _no_key_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state = tmp_path / "state"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    return state


async def test_registry_dashboard_mounts_without_runpod_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _no_key_environment(monkeypatch, tmp_path)
    app = PitwallApp(
        models_source=StaticModelCatalogueSource(_models()),
        overview_source=StaticOverviewSource(
            OverviewSnapshot(0, 0, {}, {}, 0, Decimal("0"), 0, 0, _NOW)
        ),
    )
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        assert app.screen.name == "overview"
        assert not app.is_screen_installed("serve")
    assert not state.exists()


async def test_serve_screen_without_a_key_shows_the_credential_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "personal-backend.toml"
    config.write_text('[personal]\nbackend = "personal"\n', encoding="utf-8")
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(config))
    state = _no_key_environment(monkeypatch, tmp_path)
    app = PitwallApp(models_source=StaticModelCatalogueSource(_models()))
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.name == "serve"
        _fill_spec(screen)
        screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        await pilot.pause()
        rendered = str(screen.query_one("#serve-preview-result", Static).content)
        assert MISSING_CREDENTIAL_MESSAGE in rendered
    assert not state.exists()


async def test_wizard_shows_refusal_without_launch() -> None:
    source = StaticPersonalSource(
        preview_error=ServeRefused("price_over_cap", "0.22 > 0.10"),
        lease=_lease(),
    )
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        _fill_spec(screen)
        screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        rendered = str(screen.query_one("#serve-preview-result", Static).content)
        assert "refused: price_over_cap" in rendered
        assert source.serve_calls == 0


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_wizard_cloud_and_ttl_inputs_stay_in_the_viewport(size: tuple[int, int]) -> None:
    app = _app(StaticPersonalSource(leases=[]))
    async with app.run_test(size=size) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        panel = screen.query_one("#personal-serve-panel").region
        for field_id in ("#serve-cloud", "#serve-ttl"):
            field = screen.query_one(field_id, Input)
            region = field.region
            assert field.content_region.width > 0
            assert field.content_region.height > 0
            assert 0 <= region.x < size[0]
            assert region.x + region.width > 0
            assert region.x + region.width <= size[0]
            assert 0 <= region.y < size[1]
            assert region.y + region.height > 0
            assert region.y + region.height <= size[1]
            assert panel.x <= region.x
            assert region.x + region.width <= panel.x + panel.width
            assert panel.y <= region.y
            assert region.y + region.height <= panel.y + panel.height


async def test_pods_view_lists_leases_with_remaining_time_and_stops_with_confirmation() -> None:
    source = StaticPersonalSource(
        leases=[_lease(state="ready")],
        logs="loading model\nready",
    )
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        table = app.screen.query_one("#pods-table", DataTable)
        assert table.row_count == 1
        assert "ready" in str(app.screen.query_one("#pods-logs", Static).content)
        app.screen.query_one("#pods-stop", Button).press()
        await pilot.pause()
        app.screen.query_one("#pods-confirm-text", Input).value = "ornith"
        app.screen.query_one("#pods-confirm-stop", Button).press()
        await pilot.pause()
        assert source.stopped == ["ornith"]


async def test_routes_view_shows_probe_and_shim_line_and_key_warning() -> None:
    source = StaticPersonalSource(leases=[_lease(state="ready")], key_present=False)
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        table = app.screen.query_one("#routes-table", DataTable)
        assert table.row_count == 1
        rendered = str(app.screen.query_one("#routes-detail", Static).content)
        assert "route-shim.sh ornith prompt.md" in rendered
        assert "PITWALL_ENDPOINT_KEY is not set" in rendered


async def test_view_set_follows_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # DATABASE_URL stays set: only `[personal] backend` selects the registry views.
    monkeypatch.delenv("PITWALL_CONFIG_FILE", raising=False)
    app = PitwallApp(
        personal_source=StaticPersonalSource(leases=[]),
        models_source=StaticModelCatalogueSource(_models()),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        assert app.screen.name == "serve"
        await pilot.press("o")
        await pilot.pause()
        assert app.screen.name == "serve"


_CANARY = "-".join(("synthetic", "personal", "bearer"))


class _CountingPersonalSource(StaticPersonalSource):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.status_calls = 0

    async def status(self) -> list[PersonalLease]:
        self.status_calls += 1
        return await super().status()


class _FailingPersonalSource(StaticPersonalSource):
    def __init__(
        self, *, fail_status: bool = False, fail_logs: bool = False, **kwargs: object
    ) -> None:
        super().__init__(**kwargs)
        self._fail_status = fail_status
        self._fail_logs = fail_logs

    async def status(self) -> list[PersonalLease]:
        if self._fail_status:
            raise RuntimeError(f"request failed: Bearer {_CANARY}")
        return await super().status()

    async def logs(self, route: str) -> str:
        if self._fail_logs:
            raise RuntimeError(f"request failed: Bearer {_CANARY}")
        return await super().logs(route)


async def test_pods_empty_state_and_r_refresh_reload_status() -> None:
    source = _CountingPersonalSource(leases=[])
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        assert source.status_calls == 1
        table = app.screen.query_one("#pods-table", DataTable)
        assert table.row_count == 0
        assert "No personal pods" in str(app.screen.query_one("#pods-logs", Static).content)
        assert app.screen.query_one("#pods-stop", Button).disabled is True
        await pilot.press("r")
        await pilot.pause()
        assert source.status_calls == 2
        assert table.row_count == 0
        assert "No personal pods" in str(app.screen.query_one("#pods-logs", Static).content)


async def test_routes_empty_state_and_r_refresh_reload_status() -> None:
    source = _CountingPersonalSource(leases=[])
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        assert source.status_calls == 1
        table = app.screen.query_one("#routes-table", DataTable)
        assert table.row_count == 0
        assert "No personal routes" in str(app.screen.query_one("#routes-detail", Static).content)
        await pilot.press("r")
        await pilot.pause()
        assert source.status_calls == 2
        assert table.row_count == 0
        assert "No personal routes" in str(app.screen.query_one("#routes-detail", Static).content)


async def test_pods_wrong_confirmation_never_calls_stop() -> None:
    source = StaticPersonalSource(leases=[_lease(state="ready")], logs="ready")
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#pods-stop", Button).press()
        await pilot.pause()
        app.screen.query_one("#pods-confirm-text", Input).value = "wrong"
        app.screen.query_one("#pods-confirm-stop", Button).press()
        await pilot.pause()
        assert source.stopped == []
        assert "type the route name exactly to stop" in str(
            app.screen.query_one("#pods-status", Static).content
        )


async def test_pods_status_and_logs_failures_stay_inline_and_redacted() -> None:
    source = _FailingPersonalSource(fail_status=True)
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        rendered = str(app.screen.query_one("#pods-status", Static).content)
        assert rendered.startswith("pods unavailable: request failed:")
        assert _CANARY not in rendered
        assert "[REDACTED]" in rendered
        assert app.screen.query_one("#pods-stop", Button).disabled is True

    source = _FailingPersonalSource(leases=[_lease(state="ready")], fail_logs=True)
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        rendered = str(app.screen.query_one("#pods-logs", Static).content)
        assert rendered.startswith("logs unavailable: request failed:")
        assert _CANARY not in rendered
        assert "[REDACTED]" in rendered


async def test_routes_status_failure_stays_inline_and_redacted() -> None:
    source = _FailingPersonalSource(fail_status=True)
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("t")
        await pilot.pause()
        rendered = str(app.screen.query_one("#routes-status", Static).content)
        assert rendered.startswith("routes unavailable: request failed:")
        assert _CANARY not in rendered
        assert "[REDACTED]" in rendered


async def test_wizard_launch_failure_stays_inline_and_redacted() -> None:
    source = StaticPersonalSource(
        preview=_preview(),
        serve_error=RuntimeError(f"request failed: Bearer {_CANARY}"),
    )
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        _fill_spec(screen)
        screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        screen.query_one("#serve-confirm-text", Input).value = "ornith"
        screen.query_one("#serve-launch", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert source.serve_calls == 1
        rendered = str(screen.query_one("#serve-status", Static).content)
        assert rendered.startswith("launch failed: request failed:")
        assert _CANARY not in rendered
        assert "[REDACTED]" in rendered


async def test_wizard_failure_after_launch_shows_the_pods_log_tail() -> None:
    from pitwall.personal.service import ServeFailed

    source = StaticPersonalSource(
        preview=_preview(),
        serve_error=ServeFailed(
            "container_restarting",
            "pod-9",
            log_tail="[/] ggml_cuda_init: failed to initialize CUDA [bold]",
        ),
    )
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        _fill_spec(screen)
        screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        screen.query_one("#serve-confirm-text", Input).value = "ornith"
        screen.query_one("#serve-launch", Button).press()
        await pilot.pause()
        await pilot.pause()
        rendered = str(screen.query_one("#serve-status", Static).content)
        assert rendered.startswith("failed after launch: container_restarting")
        # Log text is shown literally; bracketed log tags are not console markup.
        shown = Text.from_markup(rendered).plain
        assert "[/] ggml_cuda_init: failed to initialize CUDA [bold]" in shown


async def test_wizard_preview_failure_stays_inline_and_redacted() -> None:
    source = StaticPersonalSource(preview_error=RuntimeError(f"Bearer {_CANARY}"))
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("s")
        await pilot.pause()
        _fill_spec(app.screen)
        app.screen.query_one("#serve-preview", Button).press()
        await pilot.pause()
        rendered = str(app.screen.query_one("#serve-preview-result", Static).content)
        assert "preview failed:" in rendered
        assert _CANARY not in rendered and "[REDACTED]" in rendered
        assert source.serve_calls == 0


async def test_pods_stop_failure_stays_inline_and_redacted() -> None:
    class StopFailure(StaticPersonalSource):
        async def stop(self, route: str) -> PersonalLease:
            self.stopped.append(route)
            raise RuntimeError(f"Bearer {_CANARY}")

    source = StopFailure(leases=[_lease(state="ready")])
    app = _app(source)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#pods-stop", Button).press()
        await pilot.pause()
        app.screen.query_one("#pods-confirm-text", Input).value = "ornith"
        app.screen.query_one("#pods-confirm-stop", Button).press()
        await pilot.pause()
        rendered = str(app.screen.query_one("#pods-status", Static).content)
        assert "stop failed:" in rendered
        assert _CANARY not in rendered and "[REDACTED]" in rendered
        assert source.stopped == ["ornith"]
