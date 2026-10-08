"""The TUI routing summary plans with the same lockout snapshot the production service uses."""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.config import PitwallSettings
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Provider
from pitwall.routing import lockout
from pitwall.routing.lockout import LockoutKey, LockoutTable
from pitwall.tui import operations

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_chat",
        name="llm.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
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
            "cost": {"kind": "zero"},
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


def test_routing_summary_respects_active_lockouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The summary hands the planner the process lockout table's snapshot, as production does.

    The empty payload the summary plans with cannot price a per-token gateway and a zero-cost
    pool has no quota row, so no provider survives to be ranked; the snapshot the planner
    receives is what is asserted.
    """
    seen: list[lockout.LockoutSnapshot | None] = []
    real = operations.build_production_plan

    def spy(**kwargs):
        seen.append(kwargs.get("lockouts"))
        return real(**kwargs)

    table = LockoutTable()
    table.record_failure(
        LockoutKey("prov_gw", "beta/b1"),
        now=NOW - dt.timedelta(minutes=1),
        reason="rate_limit_exceeded",
    )
    monkeypatch.setattr(lockout, "get_lockout_table", lambda: table)
    monkeypatch.setattr(operations, "build_production_plan", spy)

    operations._routing_summary(
        [_capability()],
        [_gateway("prov_gw", 1), _gateway("prov_other", 2)],
        now=NOW,
        settings=PitwallSettings(pitwall_routing_mode="priority"),
    )

    assert len(seen) == 1 and seen[0] is not None
    assert seen[0].is_locked(LockoutKey("prov_gw", "beta/b1"), now=NOW)
    assert not seen[0].is_locked(LockoutKey("prov_other", "beta/b1"), now=NOW)
