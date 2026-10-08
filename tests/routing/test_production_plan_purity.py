"""build_production_plan is I/O-free: lockouts arrive as an explicit snapshot."""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.config import RoutingWeights
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Provider
from pitwall.providers.registry import get_default_registry
from pitwall.routing import lockout
from pitwall.routing.lockout import LockoutKey, LockoutTable
from pitwall.routing.production import RoutingOperation, build_production_plan

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_chat",
        name="llm.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_TOKEN,
        source=CapabilitySource.API,
        enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _gateway(provider_id: str, priority: int) -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_chat",
        name=provider_id,
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=priority,
        updated_at=NOW,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.API,
        config={
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            },
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "beta/b1",
                "catalog": {
                    "pool_key": "alpha-pool",
                    "free_type": "recurring-monthly",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                },
            },
        },
    )


def _plan(**extra):
    return build_production_plan(
        capability=_capability(),
        providers=[_gateway("prov_gw", 1), _gateway("prov_other", 2)],
        payload={"messages": [], "max_output_tokens": 64},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=NOW,
        mode="priority",
        weights=RoutingWeights(cost="1", latency="0.001"),
        max_attempts=3,
        **extra,
    )


def _locked_table() -> LockoutTable:
    table = LockoutTable()
    table.record_failure(
        LockoutKey("prov_gw", "beta/b1"),
        now=NOW - dt.timedelta(minutes=1),
        reason="rate_limit_exceeded",
    )
    return table


def test_plan_reads_only_injected_lockout(monkeypatch: pytest.MonkeyPatch) -> None:
    table = _locked_table()
    # The process-global table is locked, but the plan must not read it.
    monkeypatch.setattr(lockout, "get_lockout_table", lambda: table)
    plan = _plan()
    assert plan.selected_provider_id == "prov_gw"
    assert not any(item.reason == "model_locked_out" for item in plan.eliminated)

    # An empty global table is irrelevant when the caller injects a locked snapshot.
    monkeypatch.setattr(lockout, "get_lockout_table", lambda: LockoutTable())
    plan = _plan(lockouts=table.frozen())
    assert plan.selected_provider_id == "prov_other"
    reasons = {item.provider_id: item.reason for item in plan.eliminated}
    assert reasons["prov_gw"] == "model_locked_out"
