"""Hermetic tests for the guarded model-serve console flow."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from textual.pilot import Pilot
from textual.widgets import Static

from pitwall.models.fit import FitOption
from pitwall.models.schema import ModelDossier
from pitwall.serve import ServeResult
from pitwall.tui import PitwallApp
from pitwall.tui.hardware_fit import HardwareFitSnapshot, StaticHardwareFitSource
from pitwall.tui.models import ModelDisplayRow, ModelsSnapshot, StaticModelCatalogueSource
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.serve import ScriptedServeActionSource, redacted_preview

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 8, 27, 12, tzinfo=dt.UTC)


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


def models_source() -> StaticModelCatalogueSource:
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
                    chat="yes",
                ),
            )
        ),
        dossiers={"org/model": dossier()},
    )


def fit_source() -> StaticHardwareFitSource:
    return StaticHardwareFitSource(
        HardwareFitSnapshot(
            options=(
                FitOption(
                    gpu_class="NVIDIA GeForce RTX 4090",
                    gpu_count=2,
                    vram_gb=24,
                    headroom_gb=24,
                    fit="no",
                    price_per_hour=Decimal("2.00"),
                    cost_for_ttl=Decimal("4.00"),
                    cloud="secure",
                    max_count=1,
                ),
            ),
            checked_at=_NOW,
            price_source="live",
        )
    )


def dry_result() -> ServeResult:
    return ServeResult(
        capability="llm.model",
        lease_id=None,
        expires_at=None,
        model_id="model",
        proxy_base_url="http://127.0.0.1:8080/v1/openai/llm.model/v1",
        engine="vllm",
        variant="bf16",
        gpu_count=2,
        workload_id="workload-preview",
        template_id=None,
        provider_id="serve-llm-model",
        dry_run=True,
        created=True,
        cost_estimate_usd="4.00",
    )


def live_result() -> ServeResult:
    return ServeResult(
        capability="llm.model",
        lease_id="lease-serve-1",
        expires_at="2026-08-27T18:00:00+00:00",
        model_id="model",
        proxy_base_url="http://127.0.0.1:8080/v1/openai/llm.model/v1",
        engine="vllm",
        variant="bf16",
        gpu_count=2,
        workload_id="workload-live",
        template_id=None,
        provider_id="serve-llm-model",
        dry_run=False,
        created=True,
        cost_estimate_usd="4.00",
    )


def serve_app(source: ScriptedServeActionSource) -> PitwallApp:
    return PitwallApp(
        overview_source=overview_source(),
        models_source=models_source(),
        hardware_fit_source=fit_source(),
        serve_action_source=source,
    )


async def open_fit(pilot: Pilot) -> None:
    await pilot.press("m")
    await pilot.press("enter")
    await pilot.press("g")
    await pilot.pause()


async def open_preview(pilot: Pilot) -> None:
    await open_fit(pilot)
    await pilot.press("enter")
    await pilot.pause()


async def confirm_exactly(pilot: Pilot, text: str) -> None:
    await open_preview(pilot)
    await pilot.press("enter")
    await pilot.pause()
    await pilot.click("#confirm-text")
    await pilot.press(*list(text))
    await pilot.click("#confirm-launch")
    await pilot.pause()


def test_preview_redacts_secret_key_names_recursively() -> None:
    assert redacted_preview({"env": {"HF_TOKEN": "sample-secret", "SAFE": "1"}}) == {
        "env": {"HF_TOKEN": "***REDACTED***", "SAFE": "1"}
    }


async def test_preview_is_mandatory_before_confirm() -> None:
    source = ScriptedServeActionSource(preview_result=dry_result(), launch_result=live_result())
    app = serve_app(source)
    async with app.run_test(size=(140, 38)) as pilot:
        await open_fit(pilot)
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen.name == "serve-preview"
        assert source.calls == [("preview", True)]
        assert "Fit verdict: no" in str(app.screen.query_one("#serve-fit", Static).content)
        assert "TTL estimate: $4.00" in str(app.screen.query_one("#serve-cost", Static).content)


async def test_wrong_confirmation_text_never_launches() -> None:
    source = ScriptedServeActionSource(preview_result=dry_result(), launch_result=live_result())
    app = serve_app(source)
    async with app.run_test(size=(140, 38)) as pilot:
        await open_preview(pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click("#confirm-text")
        await pilot.press("w", "r", "o", "n", "g")
        await pilot.click("#confirm-launch")
        await pilot.pause()
        assert source.calls == [("preview", True)]


async def test_exact_confirmation_launches_and_renders_success() -> None:
    source = ScriptedServeActionSource(preview_result=dry_result(), launch_result=live_result())
    app = serve_app(source)
    async with app.run_test(size=(140, 38)) as pilot:
        await open_preview(pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.click("#confirm-text")
        await pilot.press(*list("llm.model"))
        await pilot.click("#confirm-launch")
        await pilot.pause()
        assert source.calls == [("preview", True), ("launch", False)]
        status = str(app.screen.query_one("#serve-status", Static).content)
        assert "http://127.0.0.1:8080/v1/openai/llm.model/v1" in status
        assert "2026-08-27T18:00:00+00:00" in status


async def test_launch_failure_stays_inline() -> None:
    source = ScriptedServeActionSource(
        preview_result=dry_result(), launch_error=RuntimeError("launch failed")
    )
    app = serve_app(source)
    async with app.run_test(size=(140, 38)) as pilot:
        await confirm_exactly(pilot, "llm.model")
        assert "Launch failed: launch failed" in str(
            app.screen.query_one("#serve-error", Static).content
        )


@pytest.mark.parametrize("dismiss", ["button", "escape"])
async def test_cancelled_serve_confirmation_never_launches(dismiss: str) -> None:
    source = ScriptedServeActionSource(preview_result=dry_result(), launch_result=live_result())
    app = serve_app(source)
    async with app.run_test(size=(140, 38)) as pilot:
        await open_preview(pilot)
        await pilot.press("enter")
        await pilot.pause()
        if dismiss == "button":
            await pilot.click("#confirm-cancel")
        else:
            await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == "serve-preview"
        assert source.calls == [("preview", True)]
        assert str(app.screen.query_one("#serve-status", Static).content) == ""


@pytest.mark.parametrize("phase", ["preview", "launch"])
async def test_serve_failure_redacts_credentials_and_stays_inline(phase: str) -> None:
    canary = "-".join(("synthetic", "serve", "secret"))
    failure = RuntimeError(f"request failed: bearer {canary}")
    source = ScriptedServeActionSource(
        preview_result=dry_result(),
        preview_error=failure if phase == "preview" else None,
        launch_error=failure if phase == "launch" else None,
    )
    app = serve_app(source)
    async with app.run_test(size=(140, 38)) as pilot:
        if phase == "preview":
            await open_preview(pilot)
            await pilot.press("enter")
            await pilot.pause()
            assert source.calls == [("preview", True)]
            assert str(app.screen.query_one("#serve-preview-content", Static).content) == ""
        else:
            await confirm_exactly(pilot, "llm.model")
            assert source.calls == [("preview", True), ("launch", False)]
        assert app.screen.name == "serve-preview"
        error = str(app.screen.query_one("#serve-error", Static).content)
        assert error.startswith(f"{phase.capitalize()} failed: request failed:")
        assert canary not in error
        assert "[REDACTED]" in error
