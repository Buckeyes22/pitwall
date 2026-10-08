from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall import cli
from pitwall.cli import models as cli_models
from pitwall.models.catalogue import Catalogue
from pitwall.models.schema import ModelDossier
from pitwall.runpod_client.graphql import RunpodGpuType
from tests.models.test_catalogue import write_dossier


@pytest.fixture(autouse=True)
def _no_ambient_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fit checks the registry cache only when a database is configured: tests that want it
    set DATABASE_URL themselves, and an exported one must not leak in."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


def miniature_catalogue() -> Catalogue:
    return Catalogue(
        (
            ModelDossier.model_validate(
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
                            "id": "awq",
                            "default": False,
                            "engine": "vllm",
                            "image": "vllm/vllm-openai:v0.12.1",
                            "repo": "org/model-awq",
                            "file": None,
                            "format": "awq",
                            "min_vram_gb": 20,
                            "context": 32768,
                            "container_disk_gb": 40,
                            "startup_min": 15,
                            "flags": [],
                            "env": {},
                            "recommended_gpu_classes": [],
                            "tool_call_parser": None,
                            "reasoning_parser": None,
                            "confidence": "medium",
                            "sources": ["https://example.test/model-awq"],
                        },
                        {
                            "id": "bf16",
                            "default": True,
                            "engine": "vllm",
                            "image": "vllm/vllm-openai:v0.12.1",
                            "repo": "org/model",
                            "file": None,
                            "format": "bf16",
                            "min_vram_gb": 40,
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
                        },
                    ],
                    "confidence": {"overall": "medium", "notes": "source checked"},
                    "accessed": "2026-08-27",
                    "body": "Research body",
                }
            ),
        )
    )


async def fake_snapshot(*, cloud: str) -> object:
    return type(
        "Snapshot",
        (),
        {
            "source": "live",
            "checked_at": datetime.now(UTC),
            "gpu_types": (
                RunpodGpuType(
                    id="NVIDIA L4",
                    memoryInGb=24,
                    securePrice=Decimal("0.10"),
                    communityPrice=Decimal("0.08"),
                ),
                RunpodGpuType(
                    id="NVIDIA L40S",
                    memoryInGb=48,
                    securePrice=Decimal("1.00"),
                    communityPrice=Decimal("0.80"),
                ),
            ),
        },
    )()


async def fallback_snapshot(*, cloud: str) -> object:
    return type(
        "Snapshot",
        (),
        {
            "source": "fallback",
            "checked_at": datetime.now(UTC),
            "gpu_types": (RunpodGpuType(id="NVIDIA L4", memoryInGb=24),),
        },
    )()


async def zero_price_snapshot(*, cloud: str) -> object:
    return type(
        "Snapshot",
        (),
        {
            "source": "live",
            "checked_at": datetime.now(UTC),
            "gpu_types": (
                RunpodGpuType(
                    id="NVIDIA L40S",
                    memoryInGb=48,
                    securePrice=Decimal("0.00"),
                    communityPrice=Decimal("0.00"),
                ),
            ),
        },
    )()


def test_models_parser_defaults() -> None:
    args = cli_models._parse_models_args(["fit", "org/model"])
    assert (args.command, args.model, args.variant, args.ttl_minutes, args.cloud, args.json) == (
        "fit",
        "org/model",
        None,
        120,
        "secure",
        False,
    )


def test_models_list_json_uses_best_priced_single_gpu(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", fake_snapshot)
    assert cli.main(["models", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["models"][0]["model"] == "org/model"
    assert payload["models"][0]["best_single_gpu"] == "NVIDIA L40S"
    assert payload["models"][0]["price_per_hour"] == "1.00"


def test_models_show_keeps_markdown_body(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    assert cli.main(["models", "show", "org/model"]) == 0
    assert "Research body" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["show", "fit"])
def test_models_accept_filename_style_model_id_alias(
    command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", fake_snapshot)

    assert cli.main(["models", command, "org--model"]) == 0
    assert "unknown model" not in capsys.readouterr().err.lower()


def test_models_fit_renders_every_gpu_and_unpriced(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalogue = miniature_catalogue()
    dossier = catalogue.models()[0]
    adjusted = dossier.variants[1].model_copy(update={"min_vram_gb": 22})
    monkeypatch.setattr(
        cli_models,
        "load_catalogue",
        lambda: Catalogue((dossier.model_copy(update={"variants": (adjusted,)}),)),
    )
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", fallback_snapshot)
    assert cli.main(["models", "fit", "org/model", "--ttl-minutes", "60"]) == 0
    output = capsys.readouterr().out
    assert "NVIDIA L4" in output
    assert "unpriced" in output
    assert "medium" in output
    assert "RunPod pricing unavailable; showing fallback GPU data." in output
    assert "tight" in output


def _write_inventory(tmp_path: Path) -> Path:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "inventory:\n"
        "  gpus: [{name: RTX4090, count: 1, vram_gb: 24, arch: sm_89, nvlink: false}]\n",
        encoding="utf-8",
    )
    return path


def test_models_fit_inventory_prints_a_human_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    path = _write_inventory(tmp_path)
    assert (
        cli.main(["models", "fit", "org/model", "--inventory", str(path), "--variant", "awq"]) == 0
    )
    output = capsys.readouterr().out
    assert "Hardware fit" in output
    assert "RTX4090" in output
    assert "medium" in output
    assert f"Local inventory: {path}, context 32768" in output


def test_models_fit_inventory_json_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    path = _write_inventory(tmp_path)
    argv = ["models", "fit", "org/model", "--inventory", str(path), "--json"]
    assert cli.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"model", "variant", "inventory", "context_length", "options", "source"}
    assert result["source"] == "local"
    assert result["options"][0]["gpu_class"] == "RTX4090"


def test_models_fit_does_not_render_zero_price_as_unpriced(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", zero_price_snapshot)
    assert cli.main(["models", "fit", "org/model"]) == 0
    output = capsys.readouterr().out
    assert "0.00" in output
    assert "unpriced" not in output


def test_models_fit_json_includes_snapshot_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    get_pool = AsyncMock(side_effect=AssertionError("no registry lookup expected"))
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", fallback_snapshot)
    assert cli.main(["models", "fit", "org/model", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "fallback"
    assert payload["age_seconds"] >= 0
    assert payload["stale"] is False
    assert payload["options"][0]["cache_state"] == "not_checked"
    get_pool.assert_not_awaited()


def test_models_cache_state_closes_dedicated_pool_after_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingConnection:
        async def fetchrow(self, query: str, name: str) -> None:
            raise RuntimeError("registry query failed")

    class Acquire:
        async def __aenter__(self) -> RaisingConnection:
            return RaisingConnection()

        async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
            return False

    class ClosingPool:
        def __init__(self) -> None:
            self.close = AsyncMock()

        def acquire(self) -> Acquire:
            return Acquire()

    pool = ClosingPool()
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)

    cache_state = asyncio.run(
        cli_models._models_cache_state(
            database_url="postgresql://registry.test/pitwall",
            capability_name="llm.model",
            variant_id="bf16",
        )
    )

    assert cache_state == "not_checked"
    get_pool.assert_awaited_once_with("postgresql://registry.test/pitwall", min_size=1, max_size=1)
    pool.close.assert_awaited_once_with()


def test_models_cache_state_times_out_query_and_terminates_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HangingConnection:
        async def fetchrow(self, query: str, name: str) -> None:
            await asyncio.Event().wait()

    class Acquire:
        async def __aenter__(self) -> HangingConnection:
            return HangingConnection()

        async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
            return False

    class HangingClosePool:
        def __init__(self) -> None:
            self.terminated = False

        def acquire(self) -> Acquire:
            return Acquire()

        async def close(self) -> None:
            await asyncio.Event().wait()

        def terminate(self) -> None:
            self.terminated = True

    pool = HangingClosePool()
    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(cli_models, "_MODELS_CACHE_LOOKUP_TIMEOUT_S", 0.01)
    monkeypatch.setattr(cli_models, "_MODELS_CACHE_CLOSE_TIMEOUT_S", 0.01)

    assert (
        asyncio.run(
            cli_models._models_cache_state(
                database_url="postgresql://registry.test/pitwall",
                capability_name="llm.model",
                variant_id="bf16",
            )
        )
        == "not_checked"
    )
    assert pool.terminated is True


def test_models_cache_state_returns_not_checked_when_pool_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_pool = AsyncMock(side_effect=RuntimeError("registry unavailable"))
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)

    assert (
        asyncio.run(
            cli_models._models_cache_state(
                database_url="postgresql://registry.test/pitwall",
                capability_name="llm.model",
                variant_id="bf16",
            )
        )
        == "not_checked"
    )
    get_pool.assert_awaited_once()


@pytest.mark.parametrize(
    ("warm_variant", "expected"),
    [("bf16", "warm"), ("awq", "cold")],
)
def test_models_fit_checks_registry_cache_state(
    warm_variant: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
    get_by_name = AsyncMock(return_value=provider)
    pool = SimpleNamespace(close=AsyncMock())
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setenv("DATABASE_URL", "postgresql://registry.test/pitwall")
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    monkeypatch.setattr(cli_models, "load_gpu_price_snapshot", fallback_snapshot)
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    monkeypatch.setattr("pitwall.db.repository.ProviderRepository.get_by_name", get_by_name)

    assert cli.main(["models", "fit", "org/model"]) == 0

    assert expected in capsys.readouterr().out
    get_by_name.assert_awaited_once_with("serve-llm.model")
    get_pool.assert_awaited_once_with("postgresql://registry.test/pitwall", min_size=1, max_size=1)
    pool.close.assert_awaited_once_with()


def test_models_unknown_variant_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_models, "load_catalogue", lambda: miniature_catalogue())
    assert cli.main(["models", "fit", "org/model", "--variant", "missing"]) == 1
    assert "unknown variant" in capsys.readouterr().err.lower()


def test_models_evidence_records_measured_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "org--model.md"
    write_dossier(path)
    monkeypatch.setenv("PITWALL_MODELS_DIR", str(tmp_path))
    cli_models.reload()

    assert (
        cli.main(
            [
                "models",
                "evidence",
                "org--model",
                "--variant",
                "sglang-fp8",
                "--gpu-class",
                "NVIDIA L4",
                "--observed-vram-gb",
                "21.5",
                "--observed-startup-s",
                "87",
            ]
        )
        == 0
    )

    assert "Recorded measured evidence" in capsys.readouterr().out
    assert "gpu_class: NVIDIA L4" in path.read_text(encoding="utf-8")
    cli_models.reload()


def test_models_evidence_rejects_noncanonical_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "org--model.md"
    write_dossier(path)
    original = path.read_text(encoding="utf-8")
    monkeypatch.setenv("PITWALL_MODELS_DIR", str(tmp_path))
    cli_models.reload()

    assert (
        cli.main(
            [
                "models",
                "evidence",
                "org/model",
                "--variant",
                "sglang-fp8",
                "--gpu-class",
                "RTX 4090",
                "--observed-vram-gb",
                "21.5",
                "--observed-startup-s",
                "87",
            ]
        )
        == 1
    )

    # The error panel wraps long lines; compare the text with box borders and wrapping removed.
    err = " ".join(capsys.readouterr().err.replace("│", " ").split()).lower()
    assert "evidence gpu name must be canonical" in err
    assert path.read_text(encoding="utf-8") == original
    cli_models.reload()


def test_models_evidence_missing_dossier_returns_normal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_MODELS_DIR", str(tmp_path))
    cli_models.reload()

    assert (
        cli.main(
            [
                "models",
                "evidence",
                "org/missing",
                "--variant",
                "sglang-fp8",
                "--gpu-class",
                "NVIDIA L4",
                "--observed-vram-gb",
                "21.5",
                "--observed-startup-s",
                "87",
            ]
        )
        == 1
    )

    assert "error:" in capsys.readouterr().err.lower()
    cli_models.reload()
