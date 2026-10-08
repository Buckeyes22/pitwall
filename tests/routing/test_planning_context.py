"""Replay-context coverage for deterministic route planning."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal

from pitwall.config import RoutingWeights
from pitwall.core.enums import ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.providers.registry import get_default_registry
from pitwall.routing import PlanningContext
from pitwall.routing.production import RoutingOperation, build_production_plan
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_vision",
        name="vision.yolov11",
        version="1.0.0",
        **{"class": "vision"},
        cost_mode="per_second",
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_planning_context_public_import_succeeds_in_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pitwall.routing import PlanningContext; "
            "assert PlanningContext.__name__ == 'PlanningContext'",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def _public_provider_for_quota(provider_id: str = "prov_public_quota") -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_vision",
        name=provider_id,
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        region="US-KS-2",
        config={
            "cost": {"kind": "gpu_hour", "per_second_active": "0.001"},
        },
        priority=1,
        health_status="healthy",
        enabled=True,
        updated_at=_NOW,
    )


def _quota_record(used_units: str = "1") -> QuotaRecord:
    return QuotaRecord(
        provider_id="prov_public_quota",
        pool_key="alpha-pool",
        free_type="recurring-monthly",
        window_start=_NOW,
        reset_at=_NOW,
        budget_units=Decimal("5000000"),
        used_units=Decimal(used_units),
        tos_verdict="ok",
        evidence={},
        updated_at=_NOW,
    )


def _plan_kwargs(
    capability: Capability,
    providers: list[Provider],
    *,
    context: PlanningContext,
) -> dict[str, object]:
    return {
        "capability": capability,
        "providers": providers,
        "payload": {},
        "operation": RoutingOperation.SYNC_INFERENCE,
        "registry": _registry(),
        "now": _NOW,
        "mode": "weighted",
        "weights": _weights(),
        "max_attempts": 1,
        "context": context,
    }


def _registry():
    return get_default_registry()


def _weights() -> RoutingWeights:
    return RoutingWeights(cost=Decimal("1"), latency=Decimal("0.001"))


def test_replay_with_different_quota_snapshot_changes_plan_id() -> None:
    capability = _capability()
    providers = [_public_provider_for_quota()]
    context_a = PlanningContext.replay(
        now=_NOW,
        providers=providers,
        capability=capability,
        quota_snapshot=QuotaSnapshot(records=(_quota_record("1"),)),
    )
    context_b = PlanningContext.replay(
        now=_NOW,
        providers=providers,
        capability=capability,
        quota_snapshot=QuotaSnapshot(records=(_quota_record("2"),)),
    )

    plan_a = build_production_plan(**_plan_kwargs(capability, providers, context=context_a))
    plan_b = build_production_plan(**_plan_kwargs(capability, providers, context=context_b))

    assert plan_a.plan_id != plan_b.plan_id
    assert (
        build_production_plan(**_plan_kwargs(capability, providers, context=context_a)).plan_id
        == plan_a.plan_id
    )
