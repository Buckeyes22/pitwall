from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.models.fit import fit_options, fit_options_local
from pitwall.models.inventory import LocalInventory
from pitwall.models.schema import Companion, Variant
from pitwall.runpod_client.graphql import RunpodGpuType


@pytest.fixture
def variant() -> Variant:
    return Variant.model_validate(
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
    )


def gpu(
    name: str,
    memory: int,
    *,
    secure: str | None = "2.00",
    community: str | None = "1.00",
    secure_max: int = 8,
    community_max: int = 4,
) -> RunpodGpuType:
    return RunpodGpuType.model_validate(
        {
            "id": name,
            "memoryInGb": memory,
            "secureCloud": True,
            "communityCloud": True,
            "securePrice": secure,
            "communityPrice": community,
            "maxGpuCount": max(secure_max, community_max),
            "maxGpuCountSecureCloud": secure_max,
            "maxGpuCountCommunityCloud": community_max,
        }
    )


def local_inventory(*, count: int = 1, arch: str = "sm_90", nvlink: bool = True):
    return LocalInventory.model_validate(
        {
            "gpus": [
                {
                    "name": "<gpu-name>",
                    "count": count,
                    "vram_gb": 24,
                    "arch": arch,
                    "nvlink": nvlink,
                }
            ],
            "gpu_memory_utilization": "0.90",
        }
    )


def test_weights_plus_kv_need_two_cards_when_inventory_has_two(variant: Variant) -> None:
    row = fit_options_local(
        variant.model_copy(update={"min_vram_gb": 21}),
        inventory=local_inventory(count=2, nvlink=False),
        context_length=32768,
    )[0]
    assert (row.fit, row.gpu_count, row.warnings) == ("tp", 2, ("communication-bound",))


def test_weights_plus_kv_exceed_one_card_inventory(variant: Variant) -> None:
    row = fit_options_local(
        variant.model_copy(update={"min_vram_gb": 21}),
        inventory=local_inventory(count=1),
        context_length=32768,
    )[0]
    assert (row.fit, row.reason) == ("no", "kv_cache")


def test_non_nvlink_tp_is_allowed_but_warned(variant: Variant) -> None:
    row = fit_options_local(
        variant.model_copy(update={"min_vram_gb": 30}),
        inventory=local_inventory(count=2, nvlink=False),
        context_length=1024,
    )[0]
    assert row.fit == "tp"
    assert row.warnings == ("communication-bound",)


def test_pre_sm89_rejects_fp8_and_accepts_int4(variant: Variant) -> None:
    fp8 = fit_options_local(
        variant.model_copy(update={"format": "fp8", "min_vram_gb": 12}),
        inventory=local_inventory(arch="sm_86"),
        context_length=1024,
    )[0]
    int4 = fit_options_local(
        variant.model_copy(update={"format": "awq", "min_vram_gb": 12}),
        inventory=local_inventory(arch="sm_86"),
        context_length=1024,
    )[0]
    assert (fp8.fit, fp8.reason) == ("no", "arch")
    assert int4.fit in {"fits", "tight"}


def test_local_tight_band_matches_cloud_rule(variant: Variant) -> None:
    row = fit_options_local(
        variant.model_copy(update={"min_vram_gb": 20}),
        inventory=local_inventory(),
        context_length=1024,
    )[0]
    assert row.fit == "tight"


def test_unverified_local_size_fails_closed(variant: Variant) -> None:
    row = fit_options_local(
        variant.model_copy(update={"min_vram_gb": "unverified"}),
        inventory=local_inventory(),
        context_length=1024,
    )[0]
    assert (row.fit, row.reason, row.headroom_gb) == ("no", "unverified", None)


def test_single_gpu_boundary_is_fits_and_decimal_cost(variant: Variant) -> None:
    row = fit_options(
        variant.model_copy(update={"min_vram_gb": 80}),
        gpu_types=[gpu("NVIDIA H100 80GB HBM3", 80)],
        ttl_minutes=120,
        cloud="secure",
    )[0]
    assert row.fit == "fits"
    assert row.gpu_count == 1
    assert row.headroom_gb == 0
    assert row.price_per_hour == Decimal("2.00")
    assert row.cost_for_ttl == Decimal("4.00")


def test_fit_options_exposes_configured_warm_cache_state(variant: Variant) -> None:
    row = fit_options(
        variant,
        gpu_types=[gpu("NVIDIA H100 80GB HBM3", 80)],
        ttl_minutes=120,
        cloud="secure",
        warm_cache=True,
    )[0]

    assert row.warm_cache is True


@pytest.mark.parametrize(
    ("required", "expected"),
    [(80, "fits"), (79, "tight"), (72, "fits")],
)
def test_single_gpu_fit_band_boundaries(variant: Variant, required: int, expected: str) -> None:
    row = fit_options(
        variant.model_copy(update={"min_vram_gb": required}),
        gpu_types=[gpu("NVIDIA H100 80GB HBM3", 80)],
        ttl_minutes=60,
        cloud="secure",
    )[0]
    assert row.fit == expected


def test_tp_fit_remains_tp(variant: Variant) -> None:
    row = fit_options(
        variant.model_copy(update={"min_vram_gb": 141}),
        gpu_types=[gpu("NVIDIA H100 80GB HBM3", 80, secure_max=2)],
        ttl_minutes=60,
        cloud="secure",
    )[0]
    assert row.fit == "tp"


def test_unknown_headroom_remains_no(variant: Variant) -> None:
    row = fit_options(
        variant.model_copy(update={"min_vram_gb": "unverified"}),
        gpu_types=[gpu("NVIDIA H100 80GB HBM3", 80)],
        ttl_minutes=60,
        cloud="secure",
    )[0]
    assert row.fit == "no"


def test_companion_allocation_changes_disk_estimate_only(variant: Variant) -> None:
    gpu_type = gpu("NVIDIA H100 80GB HBM3", 80)
    without_companion = fit_options(
        variant,
        gpu_types=[gpu_type],
        ttl_minutes=60,
        cloud="secure",
    )[0]
    with_companion = fit_options(
        variant.model_copy(
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
            }
        ),
        gpu_types=[gpu_type],
        ttl_minutes=60,
        cloud="secure",
    )[0]

    assert without_companion.container_disk_gb == 40
    assert with_companion.container_disk_gb == 46
    assert (with_companion.gpu_count, with_companion.headroom_gb) == (
        without_companion.gpu_count,
        without_companion.headroom_gb,
    )


def test_tp_count_uses_ceiling_and_cloud_cap(variant: Variant) -> None:
    options = fit_options(
        variant.model_copy(update={"min_vram_gb": 141}),
        gpu_types=[
            gpu("NVIDIA H100 80GB HBM3", 80, secure_max=2),
            gpu("NVIDIA L4", 24, secure_max=4),
        ],
        ttl_minutes=60,
        cloud="secure",
    )
    assert options[0].fit == "tp"
    assert options[0].gpu_count == 2
    assert options[0].headroom_gb == 19
    assert options[1].fit == "no"
    assert options[1].gpu_count == 6
    assert options[1].max_count == 4


def test_llama_cpp_tp_rows_are_advisory_not_suppressed(variant: Variant) -> None:
    llama = variant.model_copy(
        update={
            "engine": "llama.cpp",
            "file": "weights.gguf",
            "format": "gguf",
            "min_vram_gb": 96,
            "confidence": "low",
        }
    )
    row = fit_options(
        llama,
        gpu_types=[gpu("NVIDIA L40S", 48)],
        ttl_minutes=120,
        cloud="community",
    )[0]
    assert (row.fit, row.gpu_count) == ("tp", 2)


def test_unverified_floor_is_no_everywhere_and_unpriced_stays_none(variant: Variant) -> None:
    row = fit_options(
        variant.model_copy(update={"min_vram_gb": "unverified"}),
        gpu_types=[gpu("NVIDIA H200", 141, secure=None)],
        ttl_minutes=120,
        cloud="secure",
    )[0]
    assert row.fit == "no"
    assert row.headroom_gb is None
    assert row.price_per_hour is None
    assert row.cost_for_ttl is None


def test_sort_is_fits_then_price_then_headroom(variant: Variant) -> None:
    rows = fit_options(
        variant.model_copy(update={"min_vram_gb": 40}),
        gpu_types=[
            gpu("NVIDIA H100 80GB HBM3", 80, secure="2.00"),
            gpu("NVIDIA L40S", 48, secure="1.00"),
            gpu("NVIDIA L4", 24, secure=None, secure_max=1),
        ],
        ttl_minutes=60,
        cloud="secure",
    )
    assert [row.gpu_class for row in rows] == [
        "NVIDIA L40S",
        "NVIDIA H100 80GB HBM3",
        "NVIDIA L4",
    ]


def test_sort_orders_fits_tight_tp_and_no(variant: Variant) -> None:
    rows = fit_options(
        variant.model_copy(update={"min_vram_gb": 79}),
        gpu_types=[
            gpu("NVIDIA H100 80GB HBM3", 80, secure_max=1),
            gpu("NVIDIA L40S", 48, secure_max=2),
            gpu("NVIDIA L4", 24, secure_max=1),
        ],
        ttl_minutes=60,
        cloud="secure",
    )
    assert [(row.gpu_class, row.fit) for row in rows] == [
        ("NVIDIA H100 80GB HBM3", "tight"),
        ("NVIDIA L40S", "tp"),
        ("NVIDIA L4", "no"),
    ]
