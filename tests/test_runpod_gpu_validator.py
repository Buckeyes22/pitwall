from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitwall.audit.checks import CANONICAL_GPU_NAMES as AUDIT_CANONICAL_GPU_NAMES
from pitwall.runpod_client import (
    CANONICAL_GPU_NAMES,
    GPU_VRAM_GB,
    LEGACY_GPU_NAME_ALIASES,
    NonCanonicalGPUNameError,
    WorkloadConfig,
    canonical_gpu_name_suggestions,
    validate_canonical_gpu_name,
    validate_canonical_gpu_names,
)

_SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "runpod_gputypes_2026-08-27.json"


def _live_gpu_types() -> list[dict[str, object]]:
    return json.loads(_SNAPSHOT_PATH.read_text())


def _workload_config(*, gpu_types: list[str]) -> WorkloadConfig:
    return WorkloadConfig(
        name="vision",
        capability="vision",
        gpu_types=gpu_types,
    )


def test_validator_accepts_exact_runpod_gpu_names() -> None:
    assert validate_canonical_gpu_name("NVIDIA L4") == "NVIDIA L4"
    assert validate_canonical_gpu_names(["NVIDIA H100 80GB HBM3", "NVIDIA GeForce RTX 4090"]) == [
        "NVIDIA H100 80GB HBM3",
        "NVIDIA GeForce RTX 4090",
    ]


def test_validator_accepts_every_live_runpod_gpu_id() -> None:
    live_ids = {gpu_type["id"] for gpu_type in _live_gpu_types()}

    assert {validate_canonical_gpu_name(gpu_id) for gpu_id in live_ids} == live_ids


@pytest.mark.parametrize(
    ("legacy_name", "live_id"),
    [
        ("NVIDIA A100 80GB", "NVIDIA A100-SXM4-80GB"),
        ("NVIDIA A100 40GB", "NVIDIA A100-SXM4-40GB"),
        ("NVIDIA A6000", "NVIDIA RTX A6000"),
        ("NVIDIA RTX 6000 Ada", "NVIDIA RTX 6000 Ada Generation"),
        ("NVIDIA RTX 4090", "NVIDIA GeForce RTX 4090"),
    ],
)
def test_validator_normalizes_legacy_gpu_name_aliases(legacy_name: str, live_id: str) -> None:
    assert LEGACY_GPU_NAME_ALIASES[legacy_name] == live_id
    assert validate_canonical_gpu_name(legacy_name) == live_id


def test_canonical_gpu_names_equal_live_snapshot_ids() -> None:
    assert {gpu_type["id"] for gpu_type in _live_gpu_types()} == CANONICAL_GPU_NAMES


def test_gpu_vram_covers_live_catalog_and_matches_known_values() -> None:
    snapshot_vram = {gpu_type["id"]: gpu_type["memoryInGb"] for gpu_type in _live_gpu_types()}

    assert snapshot_vram == GPU_VRAM_GB
    assert GPU_VRAM_GB["NVIDIA GeForce RTX 4090"] == 24
    assert GPU_VRAM_GB["NVIDIA H100 80GB HBM3"] == 80
    assert GPU_VRAM_GB["NVIDIA B300 SXM6 AC"] == 288


@pytest.mark.parametrize("gpu_name", ["H100", "L4", "RTX4090"])
def test_validator_rejects_shorthand_gpu_names(gpu_name: str) -> None:
    with pytest.raises(NonCanonicalGPUNameError, match="not canonical"):
        validate_canonical_gpu_name(gpu_name)


def test_validator_suggests_canonical_name_without_accepting_alias() -> None:
    assert canonical_gpu_name_suggestions("L4") == ("NVIDIA L4",)
    with pytest.raises(NonCanonicalGPUNameError):
        validate_canonical_gpu_name("nvidia l4")


@pytest.mark.parametrize(
    ("shorthand", "suggestions"),
    [
        ("H100", ("NVIDIA H100 80GB HBM3", "NVIDIA H100 NVL", "NVIDIA H100 PCIe")),
        ("4090", ("NVIDIA GeForce RTX 4090",)),
        ("RTX5090", ("NVIDIA GeForce RTX 5090",)),
        (
            "RTXPRO6000",
            (
                "NVIDIA RTX PRO 6000 Blackwell Server Edition",
                "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
            ),
        ),
        ("B300", ("NVIDIA B300 SXM6 AC",)),
        ("H100PCIE", ("NVIDIA H100 PCIe",)),
        ("3090", ("NVIDIA GeForce RTX 3090", "NVIDIA GeForce RTX 3090 Ti")),
        ("MI300X", ("AMD Instinct MI300X OAM",)),
    ],
)
def test_shorthand_suggestions_use_live_gpu_ids(
    shorthand: str, suggestions: tuple[str, ...]
) -> None:
    assert canonical_gpu_name_suggestions(shorthand) == suggestions
    with pytest.raises(NonCanonicalGPUNameError) as exc_info:
        validate_canonical_gpu_name(shorthand)
    assert exc_info.value.suggestions == suggestions


def test_workload_config_rejects_shorthand_gpu_names() -> None:
    with pytest.raises(ValidationError, match="RunPod GPU name 'H100' is not canonical"):
        _workload_config(gpu_types=["H100"])


def test_workload_config_accepts_canonical_gpu_names() -> None:
    config = _workload_config(gpu_types=["NVIDIA L4", "NVIDIA B200"])

    assert config.gpu_types == ["NVIDIA L4", "NVIDIA B200"]


def test_audit_uses_runpod_client_canonical_gpu_set() -> None:
    assert AUDIT_CANONICAL_GPU_NAMES is CANONICAL_GPU_NAMES
