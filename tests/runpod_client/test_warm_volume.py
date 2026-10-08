"""CLI coverage for the serve-backed ``warm-volume`` command."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.cli import output as cli_output
from pitwall.cli import warm_volume as cli_warm_volume
from pitwall.models.fit import FitOption
from pitwall.models.prices import GpuPriceSnapshot
from pitwall.serve import WarmResult


def test_warm_volume_new_flags_and_missing_database_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    rc = cli_warm_volume.cmd_warm_volume(
        ["--model", "org/model", "--volume-id", "volume-cache", "--dry-run", "--json"]
    )

    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"] == "missing_database_url"


def test_warm_volume_json_delegates_to_warm_only_serve(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def fake_warm(args: Any, out: Any) -> int:
        seen["args"] = args
        out.set_json(
            WarmResult(
                capability="llm.model",
                provider_id="prov-serve",
                lease_id=None,
                volume_id=args.volume_id,
                datacenter=args.datacenter,
                model_id=args.model,
                variant=args.variant,
                engine="vllm",
                gpu_class=args.gpu_class,
                gpu_count=1,
                price_source=None,
                seconds_to_ready=None,
                torn_down=False,
                cost_estimate_usd="0.01",
                dry_run=True,
            ).to_dict()
        )
        out.emit()
        return 0

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli_warm_volume, "_warm_volume_async", fake_warm)
    rc = cli_warm_volume.cmd_warm_volume(
        [
            "--model",
            "org/model",
            "--variant",
            "fp8",
            "--volume-id",
            "volume-cache",
            "--datacenter",
            "US-EXAMPLE-1",
            "--gpu-class",
            "NVIDIA GeForce RTX 4090",
            "--gated",
            "--dry-run",
            "--json",
        ]
    )

    assert rc == 0
    assert seen["args"].gated is True
    output = json.loads(capsys.readouterr().out)
    assert output["volume_id"] == "volume-cache"
    assert output["torn_down"] is False
    assert output["gpu_class"] == "NVIDIA GeForce RTX 4090"
    assert output["gpu_count"] == 1
    assert output["price_source"] is None


@pytest.mark.anyio
async def test_warm_volume_resolves_cheapest_fit_for_dry_run_and_explicit_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    variant = SimpleNamespace(id="fp8", startup_min=15, min_vram_gb=24)
    dossier = SimpleNamespace(
        pitwall=SimpleNamespace(capability_name="llm.model"),
        resolve_variant=lambda _: variant,
    )
    seen: list[Any] = []

    monkeypatch.setattr(
        cli_warm_volume, "load_catalogue", lambda: SimpleNamespace(get=lambda _: dossier)
    )
    monkeypatch.setattr(
        cli_warm_volume,
        "load_gpu_price_snapshot",
        AsyncMock(
            return_value=GpuPriceSnapshot.model_construct(
                gpu_types=(), checked_at=None, source="live"
            )
        ),
    )
    monkeypatch.setattr(
        cli_warm_volume,
        "fit_options",
        lambda *_args, **_kwargs: [
            FitOption(
                gpu_class="NVIDIA A40",
                gpu_count=1,
                vram_gb=48,
                headroom_gb=24,
                fit="fits",
                price_per_hour=Decimal("0.50"),
                cost_for_ttl=None,
                cloud="secure",
                max_count=1,
            ),
            FitOption(
                gpu_class="NVIDIA A100 80GB PCIe",
                gpu_count=1,
                vram_gb=80,
                headroom_gb=56,
                fit="fits",
                price_per_hour=Decimal("0.50"),
                cost_for_ttl=None,
                cloud="secure",
                max_count=1,
            ),
        ],
    )
    monkeypatch.setattr("pitwall.config.get_settings", lambda: SimpleNamespace(pitwall_base_url=""))
    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=object()))

    async def fake_serve(_pool: Any, request: Any, **_kwargs: Any) -> WarmResult:
        seen.append(request)
        return WarmResult(
            capability="llm.model",
            provider_id="prov-serve",
            lease_id=None,
            volume_id="volume-cache",
            datacenter=None,
            model_id=request.model,
            variant="fp8",
            engine="vllm",
            gpu_class=request.gpu_class,
            gpu_count=request.gpu_count,
            price_source=request.price_source,
            seconds_to_ready=None,
            torn_down=False,
            cost_estimate_usd="0",
            dry_run=True,
        )

    monkeypatch.setattr("pitwall.serve.serve_model", fake_serve)
    out = cli_output.Output(True)
    await cli_warm_volume._warm_volume_async(
        cli_warm_volume._parse_warm_volume_args(
            ["--model", "org/model", "--volume-id", "volume-cache", "--dry-run"]
        ),
        out,
    )
    assert seen[-1].gpu_class == "NVIDIA A100 80GB PCIe"
    assert seen[-1].price_source == "live"

    await cli_warm_volume._warm_volume_async(
        cli_warm_volume._parse_warm_volume_args(
            [
                "--model",
                "org/model",
                "--volume-id",
                "volume-cache",
                "--gpu-class",
                "NVIDIA A40",
                "--dry-run",
            ]
        ),
        out,
    )
    assert seen[-1].gpu_class == "NVIDIA A40"
    assert seen[-1].price_source is None


@pytest.mark.anyio
async def test_warm_volume_rejects_no_single_gpu_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    variant = SimpleNamespace(id="fp8", startup_min=15, min_vram_gb=160)
    dossier = SimpleNamespace(
        pitwall=SimpleNamespace(capability_name="llm.model"),
        resolve_variant=lambda _: variant,
    )
    monkeypatch.setattr(
        cli_warm_volume, "load_catalogue", lambda: SimpleNamespace(get=lambda _: dossier)
    )
    monkeypatch.setattr(cli_warm_volume, "load_gpu_price_snapshot", AsyncMock())
    monkeypatch.setattr(cli_warm_volume, "fit_options", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("pitwall.config.get_settings", lambda: SimpleNamespace())
    with pytest.raises(Exception, match="no single-GPU fit for fp8; pass --gpu-class"):
        await cli_warm_volume._warm_volume_async(
            cli_warm_volume._parse_warm_volume_args(
                ["--model", "org/model", "--volume-id", "volume-cache"]
            ),
            cli_output.Output(True),
        )
