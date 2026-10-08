"""Cross-prong what-if projections: own-pod, free burn-down, and metered (plan Task 20)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderType,
)
from pitwall.core.models import Capability
from pitwall.cost.simulator import OwnPodFit, WhatIfSimulator
from pitwall.models.schema import Variant
from pitwall.routing import Hints, PlanningContext, RoutingRequest
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot
from pitwall.runpod_client.graphql import RunpodGpuType

_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
_CAP_ID = "cap_prongs"
_CAP_NAME = "chat.prongs"
_DAILY_TOKENS = 10_000_000


def _capability() -> Capability:
    return Capability(
        id=_CAP_ID,
        name=_CAP_NAME,
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_TOKEN,
        defaults={"execution_timeout_ms": 60_000},
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _metered_provider() -> dict[str, object]:
    return {
        "id": "deepseek",
        "capability_id": _CAP_ID,
        "name": "deepseek",
        "provider_type": ProviderType.SERVERLESS_QUEUE.value,
        "priority": 1,
        "enabled": True,
        "health_status": "healthy",
        "cold_start_p50_ms": 0,
        "recent_error_rate": 0.0,
        "config": {
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            }
        },
    }


def _context() -> PlanningContext:
    return PlanningContext.replay(
        now=_NOW,
        providers=[_metered_provider()],
        capability=_capability(),
    )


def _request() -> RoutingRequest:
    return RoutingRequest(
        capability_name=_CAP_NAME,
        capability_id=_CAP_ID,
        hints=Hints(cost_sensitive=True),
    )


def _payload() -> dict[str, object]:
    return {"max_tokens": 1024}


def _variant(*, min_vram_gb: int = 24) -> Variant:
    return Variant.model_validate(
        {
            "id": "bf16",
            "default": True,
            "engine": "vllm",
            "image": "vllm/vllm-openai:v0.12.1",
            "repo": "org/model",
            "file": None,
            "format": "bf16",
            "min_vram_gb": min_vram_gb,
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


def _gpu(secure_price: str = "0.40") -> RunpodGpuType:
    return RunpodGpuType.model_validate(
        {
            "id": "NVIDIA RTX 4090",
            "memoryInGb": 24,
            "secureCloud": True,
            "communityCloud": True,
            "securePrice": secure_price,
            "communityPrice": "0.30",
            "maxGpuCount": 8,
            "maxGpuCountSecureCloud": 8,
            "maxGpuCountCommunityCloud": 4,
        }
    )


def _own_pod_fit(*, secure_price: str = "0.40", min_vram_gb: int = 24) -> OwnPodFit:
    return OwnPodFit(
        variant=_variant(min_vram_gb=min_vram_gb),
        gpu_types=(_gpu(secure_price),),
        ttl_minutes=60,
        cloud="secure",
    )


def _quota_snapshot(*, budget_units: str = "5000000", used_units: str = "1000000") -> QuotaSnapshot:
    return QuotaSnapshot(
        records=(
            QuotaRecord(
                provider_id="deepseek-free",
                pool_key="free-pool",
                free_type="recurring-monthly",
                window_start=_NOW,
                reset_at=_NOW.replace(hour=14),
                budget_units=Decimal(budget_units),
                used_units=Decimal(used_units),
                tos_verdict="ok",
                evidence={},
            ),
        )
    )


def test_ten_million_token_day_projects_all_three_prongs() -> None:
    projection = WhatIfSimulator(_context()).simulate(
        _request(),
        payload=_payload(),
        daily_tokens=_DAILY_TOKENS,
        quota_snapshot=_quota_snapshot(),
        own_pod_fit=_own_pod_fit(),
    )

    rows = projection.prong_comparison
    assert [row.prong for row in rows] == ["own_serve", "free", "metered"]

    own_serve = rows[0]
    # $0.40/hr x 24h = $9.60/day across a 10M-token day -> $0.96 per million.
    assert own_serve.usd_per_million_tokens == Decimal("0.960000")
    assert own_serve.coverage_pct == 100.0
    assert own_serve.note.startswith("own pod NVIDIA RTX 4090")

    free = rows[1]
    # 5M budget with 1M used leaves 4M of the 10M-token day covered for $0.
    assert free.usd_per_million_tokens == Decimal("0.000000")
    assert free.coverage_pct == 40.0

    metered = rows[2]
    # Blended 50/50 prompt/completion: (1 + 1) / 2 = $1.00 per million.
    assert metered.usd_per_million_tokens == Decimal("1.000000")
    assert metered.coverage_pct == 100.0
    assert metered.note.startswith("deepseek")


def test_prong_comparison_is_in_projection_to_dict() -> None:
    projection = WhatIfSimulator(_context()).simulate(
        _request(),
        payload=_payload(),
        daily_tokens=_DAILY_TOKENS,
        quota_snapshot=_quota_snapshot(),
        own_pod_fit=_own_pod_fit(),
    )

    payload = projection.to_dict()
    rows = payload["prong_comparison"]
    assert isinstance(rows, list) and len(rows) == 3
    assert rows[0] == {
        "prong": "own_serve",
        "usd_per_million_tokens": "0.960000",
        "coverage_pct": 100.0,
        "note": rows[0]["note"],
    }
    assert rows[1]["prong"] == "free"
    assert rows[1]["usd_per_million_tokens"] == "0.000000"
    assert rows[2]["prong"] == "metered"


def test_projection_without_prong_inputs_keeps_empty_comparison() -> None:
    projection = WhatIfSimulator(_context()).simulate(_request(), payload=_payload())

    assert projection.prong_comparison == ()
    assert projection.to_dict()["prong_comparison"] == []


def test_quota_snapshot_alone_does_not_activate_prong_rows() -> None:
    projection = WhatIfSimulator(_context()).simulate(
        _request(),
        payload=_payload(),
        quota_snapshot=_quota_snapshot(),
    )

    assert projection.prong_comparison == ()


def _pool_record(provider_id: str, pool_key: str) -> QuotaRecord:
    return QuotaRecord(
        provider_id=provider_id,
        pool_key=pool_key,
        free_type="recurring-monthly",
        window_start=_NOW,
        reset_at=_NOW.replace(hour=14),
        budget_units=Decimal("4000000"),
        used_units=Decimal("0"),
        tos_verdict="ok",
        evidence={},
    )


def _free_coverage(*records: QuotaRecord) -> float:
    projection = WhatIfSimulator(_context()).simulate(
        _request(),
        payload=_payload(),
        daily_tokens=8_000_000,
        quota_snapshot=QuotaSnapshot(records=records),
        own_pod_fit=_own_pod_fit(),
    )
    (free,) = [row for row in projection.prong_comparison if row.prong == "free"]
    return free.coverage_pct


def test_a_shared_quota_pool_is_counted_once() -> None:
    """Two providers drawing on one free pool do not double its coverage."""
    assert (
        _free_coverage(_pool_record("free-a", "shared"), _pool_record("free-b", "shared")) == 50.0
    )


def test_distinct_pools_and_records_without_a_pool_key_each_count() -> None:
    assert (
        _free_coverage(_pool_record("free-a", "pool-1"), _pool_record("free-b", "pool-2")) == 100.0
    )
    assert _free_coverage(_pool_record("free-a", ""), _pool_record("free-b", "")) == 100.0


def test_no_fitting_own_pod_gpu_is_an_error() -> None:
    unfit = _own_pod_fit(min_vram_gb=200)
    with pytest.raises(ValueError, match="no fitting"):
        WhatIfSimulator(_context()).simulate(
            _request(),
            payload=_payload(),
            daily_tokens=_DAILY_TOKENS,
            own_pod_fit=unfit,
        )


def test_daily_tokens_must_be_positive() -> None:
    with pytest.raises(ValueError, match="daily_tokens"):
        WhatIfSimulator(_context()).simulate(
            _request(),
            payload=_payload(),
            daily_tokens=0,
            quota_snapshot=_quota_snapshot(),
            own_pod_fit=_own_pod_fit(),
        )


def test_cheapest_fitting_gpu_wins_the_own_serve_row() -> None:
    affordable = RunpodGpuType.model_validate(
        {
            "id": "NVIDIA L4",
            "memoryInGb": 24,
            "secureCloud": True,
            "communityCloud": False,
            "securePrice": "0.50",
            "communityPrice": None,
            "maxGpuCount": 8,
            "maxGpuCountSecureCloud": 8,
            "maxGpuCountCommunityCloud": 0,
        }
    )
    fit = OwnPodFit(
        variant=_variant(),
        gpu_types=(_gpu("2.00"), affordable),
        ttl_minutes=60,
        cloud="secure",
    )

    projection = WhatIfSimulator(_context()).simulate(
        _request(),
        payload=_payload(),
        daily_tokens=_DAILY_TOKENS,
        own_pod_fit=fit,
    )

    own_serve = projection.prong_comparison[0]
    # $0.50/hr x 24h = $12/day -> $1.20 per million, beating the $2.00 card.
    assert own_serve.usd_per_million_tokens == Decimal("1.200000")
    assert own_serve.note.startswith("own pod NVIDIA L4")
