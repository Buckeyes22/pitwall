"""Hermetic tests for the Textual model catalogue screens."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from textual.widgets import DataTable, MarkdownViewer, Static

from pitwall.models.schema import Companion, Evidence, ModelDossier
from pitwall.tui import PitwallApp
from pitwall.tui.models import (
    ModelDetailScreen,
    ModelDisplayRow,
    ModelsSnapshot,
    StaticModelCatalogueSource,
)
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource

pytestmark = pytest.mark.anyio

_SECRET = "-".join(("sk", "live", "detail", "token"))


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
            refreshed_at=dt.datetime(2026, 8, 27, tzinfo=dt.UTC),
        )
    )


def models_snapshot() -> ModelsSnapshot:
    return ModelsSnapshot(
        rows=(
            ModelDisplayRow(
                model_id="org/model",
                model="org/model",
                vendor="org",
                family="Model",
                variants="2",
                default="bf16",
                chat="yes",
            ),
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
            "body": "Research body",
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


class FailingModelsSource:
    async def load_models(self) -> ModelsSnapshot:
        raise RuntimeError("source failed")

    async def load_model(self, model_id: str) -> ModelDossier:
        raise RuntimeError("source failed")

    async def refresh(self) -> ModelsSnapshot:
        raise RuntimeError("source failed")


class DetailFailingSource:
    """List succeeds; one catalogue entry's dossier detail becomes unavailable."""

    def __init__(self) -> None:
        self.detail_attempts = 0

    async def load_models(self) -> ModelsSnapshot:
        return models_snapshot()

    async def load_model(self, model_id: str) -> ModelDossier:
        self.detail_attempts += 1
        raise RuntimeError(f"dossier withdrawn from catalogue; bearer {_SECRET}")

    async def refresh(self) -> ModelsSnapshot:
        return models_snapshot()


async def test_models_screen_renders_and_refreshes() -> None:
    source = StaticModelCatalogueSource(models_snapshot())
    app = PitwallApp(overview_source=overview_source(), models_source=source)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        table = app.screen.query_one("#models-table", DataTable)
        assert app.screen.name == "models"
        assert table.get_row_at(0) == ["org/model", "org", "bf16", ""]
        await pilot.press("r")
        await pilot.pause()
        assert source.refresh_count == 1


async def test_enter_opens_markdown_detail_with_body_and_variants() -> None:
    source = StaticModelCatalogueSource(models_snapshot(), dossiers={"org/model": dossier()})
    app = PitwallApp(overview_source=overview_source(), models_source=source)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.press("m")
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ModelDetailScreen)
        markdown = app.screen.query_one(MarkdownViewer)
        assert "| bf16 | vllm |" in str(markdown.document)
        assert "Research body" in str(markdown.document)


async def test_detail_shows_companion_and_non_chat_badge() -> None:
    selected = companion_dossier()
    source = StaticModelCatalogueSource(
        models_snapshot(),
        dossiers={"org/model": selected},
    )
    app = PitwallApp(overview_source=overview_source(), models_source=source)

    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.press("m")
        await pilot.press("enter")
        await pilot.pause()
        document = str(app.screen.query_one(MarkdownViewer).document)
        assert "mtp: acme/MTP/mtp.safetensors" in document
        assert "not servable via the OpenAI proxy" in document


async def test_models_source_failure_is_inline() -> None:
    app = PitwallApp(overview_source=overview_source(), models_source=FailingModelsSource())
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        assert str(app.screen.query_one("#models-error", Static).content) == (
            "Model catalogue unavailable: source failed"
        )


async def test_models_empty_state_is_inline() -> None:
    app = PitwallApp(
        overview_source=overview_source(),
        models_source=StaticModelCatalogueSource(ModelsSnapshot(rows=())),
    )
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        assert app.screen.query_one("#models-table", DataTable).row_count == 0
        assert str(app.screen.query_one("#models-empty", Static).content) == ("No models available")


async def test_detail_load_failure_stays_on_models_with_inline_error() -> None:
    source = DetailFailingSource()
    app = PitwallApp(overview_source=overview_source(), models_source=source)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        table = app.screen.query_one("#models-table", DataTable)
        assert table.row_count == 1

        await pilot.press("enter")
        await pilot.pause()

        assert app.screen.name == "models"
        assert not isinstance(app.screen, ModelDetailScreen)
        error = str(app.screen.query_one("#models-error", Static).content)
        assert error == (
            "Model detail unavailable: dossier withdrawn from catalogue; Bearer [REDACTED]"
        )
        assert _SECRET not in error
        assert source.detail_attempts == 1

        assert app.screen.query_one("#models-table", DataTable).row_count == 1
        assert app.screen.query_one("#models-table", DataTable).get_row_at(0) == [
            "org/model",
            "org",
            "bf16",
            "",
        ]

        source.detail_attempts = 0
        await pilot.press("r")
        await pilot.pause()
        assert source.detail_attempts == 0
        assert str(app.screen.query_one("#models-error", Static).content) == ""
        assert app.screen.query_one("#models-table", DataTable).row_count == 1

        await pilot.press("enter")
        await pilot.pause()
        assert source.detail_attempts == 1
        assert app.screen.name == "models"

        await pilot.press("o")
        await pilot.pause()
        assert app.screen.name == "overview"


@pytest.mark.parametrize("size, expects_hint", [((100, 30), True), ((160, 40), False)])
async def test_models_table_adapts_to_terminal_width(
    size: tuple[int, int], expects_hint: bool
) -> None:
    app = PitwallApp(
        overview_source=overview_source(),
        models_source=StaticModelCatalogueSource(models_snapshot()),
    )
    async with app.run_test(size=size) as pilot:
        await pilot.press("m")
        await pilot.pause()
        table = app.screen.query_one("#models-table", DataTable)
        hint = str(app.screen.query_one("#models-refreshed", Static).content)
        assert table.virtual_size.width <= size[0]
        assert ("more columns" in hint) is expects_hint
        if expects_hint:
            assert table.get_row_at(0) == ["org/model", "org", "bf16", ""]
            await pilot.press("v")
            assert "Variants: 2" in str(app.screen.query_one("#models-details", Static).content)


async def test_models_resize_rebuilds_columns_and_preserves_loaded_row() -> None:
    source = StaticModelCatalogueSource(models_snapshot())
    app = PitwallApp(overview_source=overview_source(), models_source=source)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        table = app.screen.query_one("#models-table", DataTable)
        assert table.get_row_at(0) == ["org/model", "org", "bf16", ""]
        await pilot.resize_terminal(160, 40)
        await pilot.pause()
        assert table.get_row_at(0) == ["org/model", "org", "bf16", "", "2", "Model", "yes"]
        assert len(table.columns) == 7
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert len(table.columns) == 4
        assert table.get_row_at(0) == ["org/model", "org", "bf16", ""]
        assert table.virtual_size.width <= 80


async def test_failed_model_refresh_does_not_restore_stale_rows_on_resize() -> None:
    class RefreshFailure(StaticModelCatalogueSource):
        async def refresh(self) -> ModelsSnapshot:
            raise RuntimeError("refresh unavailable")

    app = PitwallApp(
        overview_source=overview_source(), models_source=RefreshFailure(models_snapshot())
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        table = app.screen.query_one("#models-table", DataTable)
        assert table.row_count == 1
        await pilot.press("v")
        await pilot.pause()
        assert str(app.screen.query_one("#models-details", Static).content)
        await pilot.press("r")
        await pilot.pause()
        assert table.row_count == 0
        assert str(app.screen.query_one("#models-details", Static).content) == ""
        await pilot.resize_terminal(160, 40)
        await pilot.pause()
        assert table.row_count == 0
        assert "refresh unavailable" in str(app.screen.query_one("#models-error", Static).content)


async def test_moving_the_highlight_shows_that_rows_details_in_the_narrow_layout() -> None:
    rows = (
        ModelDisplayRow(
            model_id="org/first",
            model="org/first",
            vendor="org",
            family="First",
            variants="1",
            default="bf16",
            chat="yes",
        ),
        ModelDisplayRow(
            model_id="org/second",
            model="org/second",
            vendor="org",
            family="Second",
            variants="3",
            default="fp8",
            chat="no",
        ),
    )
    source = StaticModelCatalogueSource(ModelsSnapshot(rows=rows))
    app = PitwallApp(overview_source=overview_source(), models_source=source)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("m")
        await pilot.pause()
        screen: Any = app.screen
        if not screen._details_visible:
            await pilot.press("v")
        screen.query_one("#models-table", DataTable).focus()
        await pilot.press("down")
        await pilot.pause()
        details = str(screen.query_one("#models-details", Static).content)
        assert details == "Variants: 3 · Family: Second · Chat: no"
