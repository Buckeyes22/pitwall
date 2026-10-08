from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from pitwall.models import lookup as lookup_models
from pitwall.models.lookup import CatalogueLookup, VariantInfo
from pitwall.models.schema import ModelDossier


def valid_payload() -> dict[str, object]:
    return {
        "model_id": "org/model",
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
                "flags": ["--max-model-len", "32768"],
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


def test_valid_dossier_round_trips_and_forbids_extra_keys() -> None:
    dossier = ModelDossier.model_validate(valid_payload())
    assert dossier.model_id == "org/model"
    assert dossier.variants[0].flags == ("--max-model-len", "32768")
    bad = valid_payload()
    bad["unexpected"] = True
    with pytest.raises(ValidationError, match="unexpected"):
        ModelDossier.model_validate(bad)


@pytest.mark.parametrize("mutation", ["no_default", "two_defaults", "duplicate_ids"])
def test_dossier_requires_unique_ids_and_exactly_one_default(mutation: str) -> None:
    payload = valid_payload()
    variants = cast(list[dict[str, object]], payload["variants"])
    first = dict(variants[0])
    if mutation == "no_default":
        first["default"] = False
        payload["variants"] = [first]
    else:
        second = dict(first)
        second["id"] = "fp8" if mutation == "two_defaults" else "bf16"
        second["default"] = mutation == "two_defaults"
        payload["variants"] = [first, second]
    with pytest.raises(ValidationError, match="default|unique"):
        ModelDossier.model_validate(payload)


def test_llama_file_is_required_and_vllm_file_is_forbidden() -> None:
    payload = valid_payload()
    variant = dict(cast(list[dict[str, object]], payload["variants"])[0])
    variant.update(engine="llama.cpp", file=None, format="gguf")
    payload["variants"] = [variant]
    with pytest.raises(ValidationError, match="file"):
        ModelDossier.model_validate(payload)

    payload = valid_payload()
    variant = dict(cast(list[dict[str, object]], payload["variants"])[0])
    variant["file"] = "weights.gguf"
    payload["variants"] = [variant]
    with pytest.raises(ValidationError, match="file"):
        ModelDossier.model_validate(payload)


def test_unverified_positive_fields_are_accepted_but_zero_is_not() -> None:
    payload = valid_payload()
    variant = dict(cast(list[dict[str, object]], payload["variants"])[0])
    for key in ("min_vram_gb", "context", "container_disk_gb", "startup_min"):
        variant[key] = "unverified"
    payload["variants"] = [variant]
    assert ModelDossier.model_validate(payload).variants[0].min_vram_gb == "unverified"
    variant["context"] = 0
    with pytest.raises(ValidationError, match="context"):
        ModelDossier.model_validate(payload)


def test_noncanonical_recommended_gpu_is_rejected() -> None:
    payload = valid_payload()
    variant = dict(cast(list[dict[str, object]], payload["variants"])[0])
    variant["recommended_gpu_classes"] = ["RTX 4090"]
    payload["variants"] = [variant]
    with pytest.raises(ValidationError, match="canonical"):
        ModelDossier.model_validate(payload)


def test_sglang_companions_and_evidence_are_strict() -> None:
    payload = valid_payload()
    variant = cast(list[dict[str, object]], payload["variants"])[0]
    variant.update(
        {
            "engine": "sglang",
            "file": None,
            "companions": [
                {
                    "kind": "mtp",
                    "repo": "acme/MTP",
                    "file": "mtp.safetensors",
                    "flags": ["--speculative-config", '{"method":"mtp"}'],
                }
            ],
            "evidence": {
                "kind": "research",
                "gpu_class": "NVIDIA A100 80GB PCIe",
                "observed_vram_gb": "unverified",
                "observed_startup_s": 91.5,
                "date": "2026-08-27",
            },
        }
    )

    dossier = ModelDossier.model_validate(payload)

    assert dossier.variants[0].engine == "sglang"
    assert dossier.variants[0].companions[0].flags[1] == '{"method":"mtp"}'
    assert dossier.variants[0].evidence is not None
    assert dossier.variants[0].evidence.observed_vram_gb == "unverified"


@pytest.mark.parametrize("engine", ["vllm", "sglang"])
def test_non_llama_variant_rejects_file(engine: str) -> None:
    payload = valid_payload()
    variant = cast(list[dict[str, object]], payload["variants"])[0]
    variant.update({"engine": engine, "file": "weights.gguf"})

    with pytest.raises(ValidationError, match="file must be null"):
        ModelDossier.model_validate(payload)


def test_companion_rejects_unknown_fields() -> None:
    payload = valid_payload()
    variant = cast(list[dict[str, object]], payload["variants"])[0]
    variant["companions"] = [
        {
            "kind": "draft",
            "repo": "acme/draft",
            "file": "draft.safetensors",
            "flags": [],
            "guessed_flag": "--draft-model",
        }
    ]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelDossier.model_validate(payload)


def accepts_lookup(value: CatalogueLookup) -> CatalogueLookup:
    return value


def test_variant_info_field_contract_is_exact() -> None:
    info = VariantInfo(
        variant_id="fp8",
        engine="vllm",
        image="image:tag",
        repo="org/model",
        file=None,
        flags=("--flag",),
        companions=(
            lookup_models.CompanionInfo(
                kind="draft", repo="org/draft", file="draft.safetensors", flags=()
            ),
        ),
        evidence=lookup_models.EvidenceInfo(
            kind="research",
            gpu_class="NVIDIA A100 80GB PCIe",
            observed_vram_gb=None,
            observed_startup_s=91.5,
            date="2026-08-27",
        ),
        openai_chat=True,
        env={"SAFE": "1"},
        container_disk_gb=40,
        startup_min=15,
        gated=True,
    )
    assert tuple(info.__dataclass_fields__) == (
        "variant_id",
        "engine",
        "image",
        "repo",
        "file",
        "flags",
        "companions",
        "evidence",
        "openai_chat",
        "env",
        "container_disk_gb",
        "startup_min",
        "gated",
        "min_cuda",
        "served_model_name",
    )


def _with_min_cuda(value: object) -> dict[str, object]:
    payload = valid_payload()
    variants = cast(list[dict[str, object]], payload["variants"])
    variants[0]["min_cuda"] = value
    return payload


@pytest.mark.parametrize("value", ['\\"12.8"', '"12.8"', "v12.8", "12.8.1", "12", "12.x", ""])
def test_min_cuda_rejects_malformed_versions(value: str) -> None:
    with pytest.raises(ValidationError, match="min_cuda"):
        ModelDossier.model_validate(_with_min_cuda(value))


@pytest.mark.parametrize("value", ["12.8", "13.0", "12.10"])
def test_min_cuda_accepts_major_minor(value: str) -> None:
    dossier = ModelDossier.model_validate(_with_min_cuda(value))
    assert dossier.variants[0].min_cuda == value
