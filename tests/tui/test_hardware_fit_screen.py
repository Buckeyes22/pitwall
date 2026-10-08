"""Hermetic tests for the Textual hardware-fit screen."""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from textual.pilot import Pilot
from textual.widgets import DataTable, Static

from pitwall.config import PitwallSettings
from pitwall.models.fit import Cloud
from pitwall.models.prices import GpuPriceSnapshot
from pitwall.models.schema import Companion, Evidence, ModelDossier, Variant
from pitwall.runpod_client.gpu import GPU_VRAM_GB
from pitwall.runpod_client.graphql import RunpodGpuType
from pitwall.tui import PitwallApp
from pitwall.tui.hardware_fit import (
    HardwareFitSnapshot,
    HardwareFitSource,
    LiveHardwareFitSource,
    StaticHardwareFitSource,
)
from pitwall.tui.models import ModelDisplayRow, ModelsSnapshot, StaticModelCatalogueSource
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 8, 27, 12, tzinfo=dt.UTC)


def overview_source() -> StaticOverviewSource:
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


def dossier() -> ModelDossier:
    return ModelDossier.model_validate(
        {
            "model_id": "org/model",
            "vendor": "org",
            "family": "Model",
            "release_date": "2026-08-27",
            "license": {
                "name": "Apache-2.0",
                "url": "https://example.test/license",
                "gated": False,
            },
            "architecture": {
                "kind": "dense",
                "params_total_b": 7,
                "params_active_b": 7,
                "context_length_max": 32768,
                "modalities": ["text"],
                "thinking_mode": "optional",
            },
            "capabilities": {
                "tool_calling": "yes",
                "structured_outputs": "yes",
                "vision": False,
                "languages": "English",
            },
            "openai_chat": True,
            "pitwall": {"capability_name": "llm.model", "served_model_name": "model"},
            "variants": [
                {
                    "id": "bf16",
                    "default": True,
                    "engine": "vllm",
                    "image": "vllm/vllm-openai:v0.12.1",
                    "repo": "org/model",
                    "file": None,
                    "format": "bf16",
                    "min_vram_gb": 24,
                    "context": 32768,
                    "container_disk_gb": 40,
                    "startup_min": 15,
                    "flags": [],
                    "env": {},
                    "recommended_gpu_classes": [],
                    "tool_call_parser": None,
                    "reasoning_parser": None,
                    "confidence": "medium",
                    "sources": ["https://example.test/model"],
                }
            ],
            "confidence": {"overall": "medium", "notes": "source checked"},
            "accessed": "2026-08-27",
        }
    )


def companion_dossier() -> ModelDossier:
    base = dossier()
    variant = base.variants[0].model_copy(
        update={
            "container_disk_gb": 46,
            "companions": (
                Companion(
                    kind="mtp",
                    repo="acme/MTP",
                    file="mtp.safetensors",
                    flags=(
                        "--speculative-config",
                        '{"method":"mtp","num_speculative_tokens":5}',
                    ),
                ),
            ),
            "evidence": Evidence(
                kind="measured",
                gpu_class="NVIDIA L4",
                observed_vram_gb=21.5,
                observed_startup_s=87.0,
                date="2026-08-27",
            ),
        }
    )
    return base.model_copy(update={"openai_chat": False, "variants": (variant,)})


def models_source(selected: ModelDossier | None = None) -> StaticModelCatalogueSource:
    selected = selected or dossier()
    return StaticModelCatalogueSource(
        ModelsSnapshot(
            rows=(
                ModelDisplayRow(
                    model_id="org/model",
                    model="org/model",
                    vendor="org",
                    family="Model",
                    variants="1",
                    default="bf16",
                    chat="yes" if selected.openai_chat else "no",
                ),
            )
        ),
        dossiers={"org/model": selected},
    )


def fit_snapshot(
    variant: Variant | None = None,
    *,
    checked_at: dt.datetime = _NOW,
    price_source: str = "fallback",
) -> HardwareFitSnapshot:
    snapshot = GpuPriceSnapshot(
        gpu_types=tuple(
            RunpodGpuType(id=name, memoryInGb=vram_gb) for name, vram_gb in GPU_VRAM_GB.items()
        ),
        checked_at=checked_at,
        source=price_source,
    )
    return HardwareFitSnapshot.from_price_snapshot(
        variant or dossier().variants[0], snapshot, ttl_minutes=120, cloud="secure"
    )


def fit_app(
    source: HardwareFitSource,
    selected: ModelDossier | None = None,
) -> PitwallApp:
    return PitwallApp(
        overview_source=overview_source(),
        models_source=models_source(selected),
        hardware_fit_source=source,
    )


async def open_fit(pilot: Pilot) -> None:
    await pilot.press("m")
    await pilot.press("enter")
    await pilot.press("g")
    await pilot.pause()


class FailingRefreshHardwareFitSource:
    def __init__(self, snapshot: HardwareFitSnapshot) -> None:
        self._snapshot = snapshot
        self.load_count = 0

    async def load_fit(
        self,
        variant: Variant,
        *,
        capability_name: str,
        ttl_minutes: int,
        cloud: Cloud,
    ) -> HardwareFitSnapshot:
        del capability_name
        self.load_count += 1
        if self.load_count > 1:
            raise RuntimeError("source failed")
        return self._snapshot


def test_price_state_distinguishes_live_stale_and_unpriced() -> None:
    live = fit_snapshot(checked_at=_NOW - dt.timedelta(minutes=4), price_source="live")
    stale = fit_snapshot(checked_at=_NOW - dt.timedelta(hours=1, minutes=5), price_source="live")
    fallback = fit_snapshot(checked_at=_NOW, price_source="fallback")
    assert live.price_state(_NOW) == "live (4m old)"
    assert stale.price_state(_NOW) == "stale (1h 5m old)"
    assert fallback.price_state(_NOW) == "unpriced"


async def test_live_fit_source_does_not_check_cache_without_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_pool = AsyncMock(side_effect=AssertionError("no registry lookup expected"))
    monkeypatch.setattr(
        "pitwall.tui.hardware_fit.load_gpu_price_snapshot",
        AsyncMock(
            return_value=GpuPriceSnapshot(
                gpu_types=(RunpodGpuType(id="NVIDIA L4", memoryInGb=24),),
                checked_at=_NOW,
                source="fallback",
            )
        ),
    )
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)

    result = await LiveHardwareFitSource(PitwallSettings(database_url="")).load_fit(
        dossier().variants[0],
        capability_name="llm.model",
        ttl_minutes=120,
        cloud="secure",
    )

    assert result.options[0].cache_state == "not_checked"
    get_pool.assert_not_awaited()


@pytest.mark.parametrize(("warm_variant", "expected"), [("bf16", "warm"), ("awq", "cold")])
async def test_live_fit_source_checks_provider_cache(
    warm_variant: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = SimpleNamespace(
        config={
            "network_volume_id": "volume-cache",
            "warm_cache": {
                "variant": warm_variant,
                "verified_at": "2026-08-28T12:00:00+00:00",
                "volume_id": "volume-cache",
            },
        }
    )
    monkeypatch.setattr(
        "pitwall.tui.hardware_fit.load_gpu_price_snapshot",
        AsyncMock(
            return_value=GpuPriceSnapshot(
                gpu_types=(RunpodGpuType(id="NVIDIA L4", memoryInGb=24),),
                checked_at=_NOW,
                source="fallback",
            )
        ),
    )
    pool = SimpleNamespace(close=AsyncMock(), terminate=lambda: None)
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    monkeypatch.setattr(
        "asyncpg.create_pool", AsyncMock(side_effect=AssertionError("use the shared pool factory"))
    )
    monkeypatch.setattr(
        "pitwall.db.repository.ProviderRepository.get_by_name",
        AsyncMock(return_value=provider),
    )

    result = await LiveHardwareFitSource(
        PitwallSettings(database_url="postgresql://registry.test/pitwall")
    ).load_fit(
        dossier().variants[0],
        capability_name="llm.model",
        ttl_minutes=120,
        cloud="secure",
    )

    assert result.options[0].cache_state == expected
    get_pool.assert_awaited_once()
    assert get_pool.await_args.args[0] == "postgresql://registry.test/pitwall"
    # The shared pool outlives a lookup; the source never closes it.
    pool.close.assert_not_awaited()


async def test_live_fit_source_reuses_the_shared_pool_across_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = SimpleNamespace(close=AsyncMock(), terminate=lambda: None)
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    monkeypatch.setattr(
        "asyncpg.create_pool", AsyncMock(side_effect=AssertionError("use the shared pool factory"))
    )
    monkeypatch.setattr(
        "pitwall.db.repository.ProviderRepository.get_by_name", AsyncMock(return_value=None)
    )
    source = LiveHardwareFitSource(
        PitwallSettings(database_url="postgresql://registry.test/pitwall")
    )

    first = await source._cache_state(dossier().variants[0], capability_name="llm.model")
    second = await source._cache_state(dossier().variants[0], capability_name="llm.model")

    assert (first, second) == ("cold", "cold")
    pool.close.assert_not_awaited()


async def test_live_fit_source_cache_lookup_times_out_as_not_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hanging_pool(*_args: object, **_kwargs: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr("pitwall.db.get_pool", hanging_pool)
    monkeypatch.setattr("pitwall.tui.hardware_fit._CACHE_LOOKUP_TIMEOUT_S", 0.01)

    state = await LiveHardwareFitSource(
        PitwallSettings(database_url="postgresql://registry.test/pitwall")
    )._cache_state(dossier().variants[0], capability_name="llm.model")

    assert state == "not_checked"


async def test_g_opens_fit_and_renders_all_rows() -> None:
    fit_source = StaticHardwareFitSource(fit_snapshot())
    app = PitwallApp(
        overview_source=overview_source(),
        models_source=models_source(),
        hardware_fit_source=fit_source,
    )
    async with app.run_test(size=(140, 36)) as pilot:
        await pilot.press("m")
        await pilot.press("enter")
        await pilot.press("g")
        await pilot.pause()
        table = app.screen.query_one("#fit-table", DataTable)
        assert app.screen.name == "hardware-fit"
        assert table.row_count == len(GPU_VRAM_GB)
        assert "unpriced" in str(app.screen.query_one("#fit-price-state", Static).content)


async def test_fit_table_renders_tight_verdict() -> None:
    selected = dossier().model_copy(
        update={"variants": (dossier().variants[0].model_copy(update={"min_vram_gb": 22}),)}
    )
    app = fit_app(StaticHardwareFitSource(fit_snapshot(selected.variants[0])), selected)
    async with app.run_test(size=(140, 36)) as pilot:
        await open_fit(pilot)
        row = app.screen.query_one("#fit-table", DataTable).get_row("NVIDIA GeForce RTX 4090")
        assert "tight" in [str(value) for value in row]
        assert "not checked" in [str(value) for value in row]


@pytest.mark.parametrize(("cache_state", "label"), [("warm", "warm"), ("cold", "cold")])
async def test_fit_table_renders_checked_cache_state(cache_state: str, label: str) -> None:
    snapshot = fit_snapshot()
    checked_options = tuple(
        option.model_copy(update={"warm_cache": cache_state == "warm", "cache_state": cache_state})
        for option in snapshot.options
    )
    app = fit_app(
        StaticHardwareFitSource(
            HardwareFitSnapshot(
                options=checked_options,
                checked_at=snapshot.checked_at,
                price_source=snapshot.price_source,
            )
        )
    )
    async with app.run_test(size=(140, 36)) as pilot:
        await open_fit(pilot)
        row = app.screen.query_one("#fit-table", DataTable).get_row("NVIDIA GeForce RTX 4090")
        assert label in [str(value) for value in row]


async def test_fit_summary_shows_companion_evidence_and_non_chat_badge() -> None:
    selected = companion_dossier()
    source = StaticHardwareFitSource(fit_snapshot(selected.variants[0]))
    app = fit_app(source, selected)

    async with app.run_test(size=(140, 36)) as pilot:
        await open_fit(pilot)
        summary = str(app.screen.query_one("#fit-summary", Static).content)
        assert "confidence: medium · evidence: measured" in summary
        assert "companions: mtp: acme/MTP/mtp.safetensors" in summary
        assert "not servable via the OpenAI proxy" in summary


async def test_fit_refresh_and_cloud_toggle_call_source() -> None:
    source = StaticHardwareFitSource(fit_snapshot())
    app = fit_app(source)
    async with app.run_test(size=(140, 36)) as pilot:
        await open_fit(pilot)
        await pilot.press("r")
        await pilot.press("c")
        await pilot.pause()
        assert source.calls[-2:] == [("bf16", 120, "secure"), ("bf16", 120, "community")]


async def test_stale_price_state_renders_in_fit_screen() -> None:
    snapshot = fit_snapshot(
        checked_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=1, minutes=5),
        price_source="live",
    )
    app = fit_app(StaticHardwareFitSource(snapshot))
    async with app.run_test(size=(140, 36)) as pilot:
        await open_fit(pilot)
        assert "stale (1h 5m old)" in str(app.screen.query_one("#fit-price-state", Static).content)


def test_price_state_uses_configured_staleness_policy() -> None:
    snapshot = fit_snapshot(checked_at=_NOW - dt.timedelta(minutes=2), price_source="live")
    assert snapshot.price_state(_NOW, max_age_s=60) == "stale (0h 2m old)"


async def test_fit_refresh_failure_is_inline_and_clears_stale_rows() -> None:
    source = FailingRefreshHardwareFitSource(fit_snapshot())
    app = fit_app(source)
    async with app.run_test(size=(140, 36)) as pilot:
        await open_fit(pilot)
        table = app.screen.query_one("#fit-table", DataTable)
        assert table.row_count == len(GPU_VRAM_GB)
        await pilot.press("r")
        await pilot.pause()
        assert table.row_count == 0
        assert str(app.screen.query_one("#fit-error", Static).content) == (
            "Hardware fit unavailable: source failed"
        )


@pytest.mark.parametrize("size, expects_hint", [((100, 30), True), ((160, 40), False)])
async def test_fit_table_adapts_to_terminal_width(
    size: tuple[int, int], expects_hint: bool
) -> None:
    app = fit_app(StaticHardwareFitSource(fit_snapshot()))
    async with app.run_test(size=size) as pilot:
        await open_fit(pilot)
        table = app.screen.query_one("#fit-table", DataTable)
        hint = str(app.screen.query_one("#fit-price-state", Static).content)
        assert table.virtual_size.width <= size[0]
        assert ("more columns" in hint) is expects_hint
        if expects_hint:
            assert len(table.get_row_at(0)) == 5
            await pilot.press("v")
            assert "VRAM:" in str(app.screen.query_one("#fit-details", Static).content)


async def test_failed_fit_refresh_does_not_restore_stale_rows_on_resize() -> None:
    class RefreshFailure(StaticHardwareFitSource):
        async def load_fit(
            self, variant: Variant, *, capability_name: str, ttl_minutes: int, cloud: Cloud
        ) -> HardwareFitSnapshot:
            if self.calls:
                raise RuntimeError("refresh unavailable")
            return await super().load_fit(
                variant, capability_name=capability_name, ttl_minutes=ttl_minutes, cloud=cloud
            )

    app = fit_app(RefreshFailure(fit_snapshot()))
    async with app.run_test(size=(100, 30)) as pilot:
        await open_fit(pilot)
        table = app.screen.query_one("#fit-table", DataTable)
        assert table.row_count > 0
        await pilot.press("v")
        await pilot.pause()
        assert str(app.screen.query_one("#fit-details", Static).content)
        await pilot.press("r")
        await pilot.pause()
        assert table.row_count == 0
        assert str(app.screen.query_one("#fit-details", Static).content) == ""
        await pilot.resize_terminal(160, 40)
        await pilot.pause()
        assert table.row_count == 0
        assert "refresh unavailable" in str(app.screen.query_one("#fit-error", Static).content)


async def test_fit_variant_shortcut_reloads_selected_variant_and_wraps() -> None:
    selected = dossier()
    second = selected.variants[0].model_copy(update={"id": "alternate", "default": False})
    selected = selected.model_copy(update={"variants": (*selected.variants, second)})
    source = StaticHardwareFitSource(fit_snapshot())
    app = fit_app(source, selected)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_fit(pilot)
        await pilot.press("shift+v")
        await pilot.pause()
        assert source.calls[-1][0] == "alternate"
        await pilot.press("shift+v")
        await pilot.pause()
        assert [call[0] for call in source.calls] == ["bf16", "alternate", "bf16"]


async def test_fit_row_selection_without_launch_source_stays_inline() -> None:
    app = fit_app(StaticHardwareFitSource(fit_snapshot()))
    async with app.run_test(size=(140, 40)) as pilot:
        await open_fit(pilot)
        app._serve_action_source = None
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.name == "hardware-fit"
        assert str(app.screen.query_one("#fit-error", Static).content) == (
            "Model launch is unavailable"
        )


async def test_fit_row_selection_opens_preview_without_launching() -> None:
    from pitwall.tui.serve import ScriptedServeActionSource, ServePreviewScreen

    source = ScriptedServeActionSource(preview_error=RuntimeError("preview fixture"))
    app = PitwallApp(
        overview_source=overview_source(),
        models_source=models_source(),
        hardware_fit_source=StaticHardwareFitSource(fit_snapshot()),
        serve_action_source=source,
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await open_fit(pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ServePreviewScreen)
        assert source.calls == [("preview", True)]
        assert "preview fixture" in str(app.screen.query_one("#serve-error", Static).content)


async def test_fit_empty_options_survive_selection_resize_and_refresh() -> None:
    source = StaticHardwareFitSource(HardwareFitSnapshot((), _NOW, "live"))
    app = fit_app(source)
    async with app.run_test(size=(140, 40)) as pilot:
        await open_fit(pilot)
        await pilot.press("enter")
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert app.screen.name == "hardware-fit"
        assert app.screen.query_one("#fit-table", DataTable).row_count == 0
        assert str(app.screen.query_one("#fit-error", Static).content) == ""
        await pilot.press("r")
        await pilot.pause()
        assert len(source.calls) == 2
        assert app.screen.query_one("#fit-table", DataTable).row_count == 0


async def test_moving_the_highlight_shows_that_rows_details_in_the_narrow_layout() -> None:
    app = fit_app(StaticHardwareFitSource(fit_snapshot()))
    async with app.run_test(size=(100, 30)) as pilot:
        await open_fit(pilot)
        screen: Any = app.screen
        table = screen.query_one("#fit-table", DataTable)
        if not screen._details_visible:
            await pilot.press("v")
        table.focus()
        await pilot.press("down")
        await pilot.pause()
        key = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        option = next(item for item in screen._options if item.gpu_class == key)
        assert table.cursor_row == 1
        assert f"VRAM: {option.vram_gb} GB" in str(screen.query_one("#fit-details", Static).content)
