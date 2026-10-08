from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pitwall.models.catalogue import load_catalogue
from tools.models.convert_research_dossier import convert_dossiers


def _write(path: Path, payload: dict[str, object], body: str = "# Research\n") -> None:
    path.write_text(
        "---\n" + yaml.safe_dump(payload, sort_keys=False) + "---\n\n" + body,
        encoding="utf-8",
    )


def _upstream(model_id: str, *, engine: str = "vllm") -> dict[str, object]:
    return {
        "model_id": model_id,
        "vendor": "org",
        "family": "Model",
        "release_date": "2026-08-27",
        "license": {"name": "Apache-2.0", "url": "https://example.test/license", "gated": False},
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
        "serving": {
            "recommended_engine": engine,
            "fits_openai_proxy": True,
            "image": "old:image",
            "docker_start_cmd": [model_id, "--host", "0.0.0.0", "--port", "8000", "--flag", "x"],
            "env": {"HF_TOKEN": "not needed", "KEEP": "yes"},
            "tool_call_parser": None,
            "reasoning_parser": None,
        },
        "hardware": {
            "recommended_gpu_classes": ["NVIDIA RTX 4090"],
            "min_vram_gb_native": 24,
            "container_disk_gb": 40,
            "startup_time_estimate_min": 15,
        },
        "weights": {"format": "safetensors"},
        "pitwall": {"capability_name": "llm.model", "served_model_name": "model"},
        "confidence": {"overall": "medium", "notes": "source checked"},
        "accessed": "2026-08-27",
    }


def _matrix(model_id: str, entries: list[dict[str, object]]) -> dict[str, object]:
    return {"entries": [{"model_id": model_id, **entry} for entry in entries]}


def research_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    normalized, gguf, output = (tmp_path / name for name in ("normalized", "gguf", "output"))
    normalized.mkdir()
    gguf.mkdir()
    _write(normalized / "org--model.md", _upstream("org/model"))
    _write(gguf / "unsloth--model-GGUF.md", _upstream("unsloth/model-GGUF"), "# GGUF research\n")
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(
        yaml.safe_dump(
            _matrix(
                "org/model",
                [
                    {
                        "variant": "fp8",
                        "engine": "vllm",
                        "image": "vllm:test",
                        "openai_chat": True,
                        "min_vram_gb": 24,
                        "context_assumed": 32768,
                        "container_disk_gb": 40,
                        "startup_timeout_s_suggested": 900,
                        "gpu_options": [{"gpu_class": "NVIDIA GeForce RTX 4090"}],
                        "confidence": "medium",
                    },
                    {
                        "variant": "gguf:UD-Q4_K_XL",
                        "engine": "llama.cpp",
                        "image": "llama:test",
                        "openai_chat": True,
                        "min_vram_gb": 24,
                        "context_assumed": 32768,
                        "container_disk_gb": 40,
                        "startup_timeout_s_suggested": 900,
                        "gpu_options": [{"gpu_class": "NVIDIA GeForce RTX 4090"}],
                        "confidence": "medium",
                    },
                ],
            ),
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return normalized, gguf, output, matrix


def music_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    normalized, gguf, output = (tmp_path / name for name in ("normalized", "gguf", "output"))
    normalized.mkdir()
    gguf.mkdir()
    _write(
        normalized / "MiniMaxAI--MiniMax-Music3.md",
        _upstream("MiniMaxAI/MiniMax-Music3", engine="sglang"),
    )
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(
        yaml.safe_dump(
            _matrix("MiniMaxAI/MiniMax-Music3", [{"variant": "bf16", "engine": "sglang"}])
        ),
        encoding="utf-8",
    )
    return normalized, gguf, output, matrix


def orphan_matrix_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    normalized, gguf, output = (tmp_path / name for name in ("normalized", "gguf", "output"))
    normalized.mkdir()
    gguf.mkdir()
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text(yaml.safe_dump(_matrix("missing/model", [{"variant": "bf16"}])))
    return normalized, gguf, output, matrix


def test_converter_folds_gguf_and_strips_old_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "tools.models.convert_research_dossier.UPSTREAM_FOR_REPUBLICATION",
        {"unsloth/model-GGUF": "org/model"},
    )
    monkeypatch.setattr(
        "tools.models.convert_research_dossier.VARIANT_REPOS",
        {("org/model", "gguf:UD-Q4_K_XL"): "unsloth/model-GGUF"},
    )
    monkeypatch.setattr(
        "tools.models.convert_research_dossier.GGUF_FILES",
        {("org/model", "gguf:UD-Q4_K_XL"): "model-UD-Q4_K_XL.gguf"},
    )
    normalized, gguf, output, matrix = research_fixture(tmp_path)
    report = convert_dossiers(normalized, gguf, matrix, output)
    dossier = load_catalogue(output).get("org/model")
    assert dossier is not None
    assert [variant.id for variant in dossier.variants] == ["fp8", "gguf:UD-Q4_K_XL"]
    assert dossier.variants[0].default is True
    assert dossier.variants[1].default is False
    assert dossier.variants[1].repo == "unsloth/model-GGUF"
    assert dossier.variants[1].file == "model-UD-Q4_K_XL.gguf"
    assert dossier.variants[1].recommended_gpu_classes == ("NVIDIA GeForce RTX 4090",)
    text = (output / "org--model.md").read_text(encoding="utf-8")
    assert "weights:" not in text.split("---", 2)[1]
    assert "serving:" not in text.split("---", 2)[1]
    assert "hardware:" not in text.split("---", 2)[1]
    assert "Folded Unsloth GGUF research" in text
    assert report.folded == {"unsloth/model-GGUF": "org/model"}


def test_converter_reports_sglang_without_emitting_a_dossier(tmp_path: Path) -> None:
    normalized, gguf, output, matrix = music_fixture(tmp_path)
    report = convert_dossiers(normalized, gguf, matrix, output)
    assert report.excluded == {
        "MiniMaxAI/MiniMax-Music3": "unsupported engine sglang; catalogue engines are vllm and llama.cpp"
    }
    assert not (output / "MiniMaxAI--MiniMax-Music3.md").exists()


def test_converter_rejects_matrix_rows_without_source_dossiers(tmp_path: Path) -> None:
    normalized, gguf, output, matrix = orphan_matrix_fixture(tmp_path)
    with pytest.raises(ValueError, match="matrix model has no upstream dossier"):
        convert_dossiers(normalized, gguf, matrix, output)


def test_converter_is_byte_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tools.models.convert_research_dossier.UPSTREAM_FOR_REPUBLICATION",
        {"unsloth/model-GGUF": "org/model"},
    )
    monkeypatch.setattr(
        "tools.models.convert_research_dossier.VARIANT_REPOS",
        {("org/model", "gguf:UD-Q4_K_XL"): "unsloth/model-GGUF"},
    )
    monkeypatch.setattr(
        "tools.models.convert_research_dossier.GGUF_FILES",
        {("org/model", "gguf:UD-Q4_K_XL"): "model-UD-Q4_K_XL.gguf"},
    )
    normalized, gguf, first, matrix = research_fixture(tmp_path)
    second = tmp_path / "second"
    convert_dossiers(normalized, gguf, matrix, first)
    convert_dossiers(normalized, gguf, matrix, second)
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
