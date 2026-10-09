from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from pitwall.models import catalogue
from pitwall.models import lookup as lookup_models
from pitwall.models.catalogue import load_catalogue, reload, write_evidence
from pitwall.models.errors import CatalogueError, UnknownVariant
from pitwall.models.lookup import CatalogueLookup, VariantInfo

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED = sorted((_REPO_ROOT / "docs" / "models").glob("*.md"))
_SHIPPED_DOSSIERS = [path for path in _SHIPPED if path.name != "README.md"]
# Tripwire: total variants across the shipped catalogue; adding a variant changes this one number.
_EXPECTED_VARIANT_COUNT = 36


def write_dossier(
    path: Path,
    *,
    model_id: str = "org/model",
    min_vram_gb: int | str = 24,
    startup_min: int | str = 15,
) -> None:
    payload = {
        "model_id": model_id,
        "vendor": "org",
        "family": "Model",
        "release_date": "2026-08-27",
        "license": {
            "name": "Apache-2.0",
            "url": "https://example.test/license",
            "gated": True,
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
        "openai_chat": False,
        "pitwall": {"capability_name": "llm.model", "served_model_name": "model"},
        "variants": [
            {
                "id": "sglang-fp8",
                "default": True,
                "engine": "sglang",
                "image": "lmsysorg/sglang:v0.5.18-runtime",
                "repo": model_id,
                "file": None,
                "format": "bf16",
                "min_vram_gb": min_vram_gb,
                "context": 32768,
                "container_disk_gb": 40,
                "startup_min": startup_min,
                "flags": ["--trust-remote-code"],
                "companions": [
                    {
                        "kind": "draft",
                        "repo": "acme/Draft",
                        "file": "model.safetensors",
                        "flags": [],
                    }
                ],
                "evidence": {
                    "kind": "research",
                    "gpu_class": "NVIDIA A100 80GB PCIe",
                    "observed_vram_gb": "unverified",
                    "observed_startup_s": 91.5,
                    "date": "2026-08-27",
                },
                "env": {},
                "recommended_gpu_classes": ["NVIDIA GeForce RTX 4090"],
                "tool_call_parser": None,
                "reasoning_parser": None,
                "confidence": "medium",
                "sources": ["https://example.test/model"],
            }
        ],
        "confidence": {"overall": "medium", "notes": "source checked"},
        "accessed": "2026-08-27",
    }
    front_matter = yaml.safe_dump(payload, sort_keys=False)
    path.write_text(
        f"---\n{front_matter}---\n\n# Model\n\nResearch body.\n",
        encoding="utf-8",
    )


def test_catalogue_is_a_serve_lookup_and_normalizes_unverified(tmp_path: Path) -> None:
    write_dossier(
        tmp_path / "org--model.md",
        min_vram_gb="unverified",
        startup_min="unverified",
    )
    catalogue = load_catalogue(tmp_path)
    lookup: CatalogueLookup = catalogue
    info = lookup.variant("org/model", "sglang-fp8")
    assert info == VariantInfo(
        variant_id="sglang-fp8",
        engine="sglang",
        image="lmsysorg/sglang:v0.5.18-runtime",
        repo="org/model",
        file=None,
        flags=("--trust-remote-code",),
        companions=(
            lookup_models.CompanionInfo(
                kind="draft",
                repo="acme/Draft",
                file="model.safetensors",
                flags=(),
            ),
        ),
        evidence=lookup_models.EvidenceInfo(
            kind="research",
            gpu_class="NVIDIA A100 80GB PCIe",
            observed_vram_gb=None,
            observed_startup_s=91.5,
            date="2026-08-27",
        ),
        openai_chat=False,
        env={},
        container_disk_gb=40,
        startup_min=None,
        gated=True,
        served_model_name="model",
    )
    dossier = catalogue.get("org/model")
    assert dossier is not None
    assert dossier.body == "# Model\n\nResearch body.\n"
    assert catalogue.variant("missing/model", None) is None


def test_write_evidence_preserves_dossier_body_and_validates_result(tmp_path: Path) -> None:
    path = tmp_path / "org--model.md"
    write_dossier(path)
    original_body = "# Model\n\nResearch body with **exact** formatting.\n"
    original = path.read_text(encoding="utf-8")
    front_matter, _, _ = original.rpartition("---\n")
    path.write_text(front_matter + "---\n" + original_body, encoding="utf-8")

    written = write_evidence(
        tmp_path,
        model_id="org/model",
        variant_id="sglang-fp8",
        gpu_class="NVIDIA L4",
        observed_vram_gb=21.5,
        observed_startup_s=87.0,
        date="2026-08-28",
    )

    assert written.evidence is not None
    assert written.evidence.model_dump(mode="json") == {
        "kind": "measured",
        "gpu_class": "NVIDIA L4",
        "observed_vram_gb": 21.5,
        "observed_startup_s": 87.0,
        "date": "2026-08-28",
    }
    updated_text = path.read_text(encoding="utf-8")
    assert updated_text.endswith(original_body)
    original_front_matter = yaml.safe_load(original.split("---\n", 2)[1])
    updated_front_matter = yaml.safe_load(updated_text.split("---\n", 2)[1])
    assert isinstance(original_front_matter, dict)
    assert isinstance(updated_front_matter, dict)
    for payload in (original_front_matter, updated_front_matter):
        variants = payload.pop("variants")
        assert isinstance(variants, list)
        for variant in variants:
            assert isinstance(variant, dict)
            variant.pop("evidence")
        payload["variants"] = variants
    assert updated_front_matter == original_front_matter
    assert (
        load_catalogue(tmp_path).dossier_variant("org/model", "sglang-fp8").evidence
        == written.evidence
    )


def test_write_evidence_rejects_noncanonical_gpu_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "org--model.md"
    write_dossier(path)
    original = path.read_text(encoding="utf-8")

    with pytest.raises(CatalogueError, match="evidence GPU name must be canonical"):
        write_evidence(
            tmp_path,
            model_id="org/model",
            variant_id="sglang-fp8",
            gpu_class="RTX 4090",
            observed_vram_gb=21.5,
            observed_startup_s=87.0,
            date="2026-08-28",
        )

    assert path.read_text(encoding="utf-8") == original


def test_write_evidence_wraps_path_resolution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_resolve(self: Path, strict: bool = False) -> Path:
        raise OSError("resolution failed")

    monkeypatch.setattr(Path, "resolve", fail_resolve)
    with pytest.raises(CatalogueError, match="cannot resolve dossier path"):
        write_evidence(
            tmp_path,
            model_id="org/missing",
            variant_id="sglang-fp8",
            gpu_class="NVIDIA L4",
            observed_vram_gb=21.5,
            observed_startup_s=87.0,
            date="2026-08-28",
        )


def test_write_evidence_wraps_missing_dossier_path(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError, match="org--missing.md"):
        write_evidence(
            tmp_path,
            model_id="org/missing",
            variant_id="sglang-fp8",
            gpu_class="NVIDIA L4",
            observed_vram_gb=21.5,
            observed_startup_s=87.0,
            date="2026-08-28",
        )


def test_write_evidence_keeps_original_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "org--model.md"
    write_dossier(path)
    original = path.read_bytes()

    def fail_replace(
        source: str | bytes | os.PathLike[str], destination: str | bytes | os.PathLike[str]
    ) -> None:
        raise OSError("replace interrupted")

    monkeypatch.setattr(catalogue.os, "replace", fail_replace)

    with pytest.raises(CatalogueError, match="replace interrupted"):
        write_evidence(
            tmp_path,
            model_id="org/model",
            variant_id="sglang-fp8",
            gpu_class="NVIDIA L4",
            observed_vram_gb=21.5,
            observed_startup_s=87.0,
            date="2026-08-28",
        )

    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("model_id", "variant_id", "companions", "source"),
    [
        (
            "google/gemma-4-31B-it",
            "gguf:UD-Q5_K_XL",
            (("mmproj", "unsloth/gemma-4-31B-it-GGUF", "mmproj-F16.gguf"),),
            "https://huggingface.co/api/models/unsloth/gemma-4-31B-it-GGUF?blobs=true",
        ),
        (
            "Qwen/Qwen3.8-27B",
            "gguf:UD-Q4_K_XL",
            (("mmproj", "unsloth/Qwen3.8-27B-GGUF", "mmproj-F16.gguf"),),
            "https://huggingface.co/api/models/unsloth/Qwen3.8-27B-GGUF?blobs=true",
        ),
        (
            "meta-models/Muse-Glimmer-30B",
            "gguf:UD-Q5_K_XL",
            (),
            "https://huggingface.co/api/models/unsloth/Muse-Glimmer-30B-GGUF?blobs=true",
        ),
    ],
)
def test_shipped_gguf_companions_are_sourced(
    model_id: str, variant_id: str, companions: tuple[tuple[str, str, str], ...], source: str
) -> None:
    variant = load_catalogue(_REPO_ROOT / "docs" / "models").dossier_variant(model_id, variant_id)
    assert tuple((item.kind, item.repo, item.file) for item in variant.companions) == companions
    assert source in variant.sources


def test_known_model_unknown_variant_is_loud(tmp_path: Path) -> None:
    write_dossier(tmp_path / "org--model.md")
    with pytest.raises(UnknownVariant, match="org/model.*missing"):
        load_catalogue(tmp_path).variant("org/model", "missing")


def test_invalid_dossier_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "org--broken.md"
    path.write_text("---\nmodel_id: org/broken\n---\n", encoding="utf-8")
    with pytest.raises(CatalogueError, match="org--broken.md"):
        load_catalogue(tmp_path)


@pytest.mark.parametrize("reader", ["load_catalogue", "write_evidence"])
def test_dossier_yaml_error_never_quotes_the_file(tmp_path: Path, reader: str) -> None:
    path = tmp_path / "org--broken.md"
    path.write_text(
        "---\nmodel_id: org/broken\ntoken: hunter2\napi_key: sk-SECRET123: x\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(CatalogueError) as caught:
        if reader == "load_catalogue":
            load_catalogue(tmp_path)
        else:
            write_evidence(
                tmp_path,
                model_id="org/broken",
                variant_id="v",
                gpu_class="a100",
                observed_vram_gb=1.0,
                observed_startup_s=1.0,
                date="2026-10-06",
            )
    message = str(caught.value)
    assert "org--broken.md" in message
    assert "line 4, column 22" in message
    assert "sk-SECRET123" not in message
    assert "hunter2" not in message
    assert caught.value.__cause__ is None


def test_models_dir_env_override_and_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_dossier(tmp_path / "org--one.md", model_id="org/one")
    monkeypatch.setenv("PITWALL_MODELS_DIR", str(tmp_path))
    reload()
    first = load_catalogue()
    assert [model.model_id for model in first.models()] == ["org/one"]
    write_dossier(tmp_path / "org--two.md", model_id="org/two")
    assert len(load_catalogue().models()) == 1
    reload()
    assert [model.model_id for model in load_catalogue().models()] == ["org/one", "org/two"]


@pytest.mark.parametrize("path", _SHIPPED_DOSSIERS, ids=lambda path: path.name)
def test_every_shipped_dossier_validates_individually(path: Path) -> None:
    catalogue = load_catalogue(path.parent)
    expected_model_id = path.stem.replace("--", "/", 1)
    assert catalogue.get(expected_model_id) is not None


def test_shipped_catalogue_inventory_and_variant_count() -> None:
    catalogue = load_catalogue(_REPO_ROOT / "docs" / "models")
    assert [model.model_id for model in catalogue.models()] == [
        "MiniMaxAI/MiniMax-H3",
        "MiniMaxAI/MiniMax-M2.7",
        "MiniMaxAI/MiniMax-M3",
        "Qwen/Qwen3.8-27B",
        "Qwen/Qwen3.8-Flash-Next",
        "XiaomiMiMo/MiMo-V2.5",
        "XiaomiMiMo/MiMo-V2.5-Pro",
        "XiaomiMiMo/MiMo-V2.6-Distill-Qwen-9B",
        "XiaomiMiMo/MiMo-V2.6-Flash-MOPD",
        "XiaomiMiMo/MiMo-V2.6-Flash-RL",
        "XiaomiMiMo/MiMo-V2.6-Pro-MOPD",
        "XiaomiMiMo/MiMo-V2.6-Pro-RL",
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        "deepseek-ai/DeepSeek-V4-Flash-Vision-Exp",
        "deepseek-ai/DeepSeek-V4-Pro-0813",
        "google/gemma-4-31B-it",
        "meituan-longcat/LongCat-2.0",
        "meta-models/Muse-Glimmer-30B",
        "moonshotai/Kimi-K2.6",
        "moonshotai/Kimi-K2.7-Code",
        "moonshotai/Kimi-K3",
        "ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        "tencent/Hy3",
        "tencent/Hy4-preview",
        "zai-org/GLM-5.1",
        "zai-org/GLM-5.2",
        "zai-org/GLM-5.3",
        "zai-org/GLM-5.3-Flash",
    ]
    assert sum(len(model.variants) for model in catalogue.models()) == _EXPECTED_VARIANT_COUNT
    assert catalogue.get("MiniMaxAI/MiniMax-Music3") is None
