"""The what-if simulator plans with the live production planner."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pitwall.config import RoutingWeights
from pitwall.core.enums import ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.simulator import WhatIfSimulator
from pitwall.providers import runpod as runpod_adapter
from pitwall.providers.registry import get_default_registry
from pitwall.routing import PlanningContext, RoutingRequest
from pitwall.routing.lockout import LockoutKey, LockoutSnapshot, LockoutState
from pitwall.routing.production import (
    RoutingOperation,
    build_production_plan,
)

_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
_CAP_ID = "cap_parity"
_CAP_NAME = "embedding.parity"
_LOCKED_MODEL = "model-b"


@pytest.fixture(autouse=True)
def _runpod_rows_lock_on_the_gateway_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture rows carry their model id under ``gateway``; the adapter declares that."""
    declaration = dataclasses.replace(
        runpod_adapter.RUNPOD_DECLARATION, lockout_model_paths=(("gateway", "model_id"),)
    )
    monkeypatch.setattr(runpod_adapter.RunPodProvider, "declaration", declaration)


def _capability() -> Capability:
    return Capability(
        id=_CAP_ID,
        name=_CAP_NAME,
        version="1.0.0",
        **{"class": "embedding"},
        cost_mode="per_second",
        defaults={"execution_timeout_ms": 60_000},
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(
    provider_id: str,
    *,
    priority: int,
    rate: str,
    health_status: str = "healthy",
    extra_config: dict[str, object] | None = None,
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id=_CAP_ID,
        name=provider_id,
        provider_type=ProviderType.SERVERLESS_QUEUE,
        region="US-KS-2",
        config={"cost": {"per_second_active": rate}, **(extra_config or {})},
        priority=priority,
        health_status=health_status,
        updated_at=_NOW,
    )


def _providers() -> list[Provider]:
    return [
        _provider("prov_a", priority=3, rate="0.001"),
        _provider(
            "prov_b",
            priority=1,
            rate="0.004",
            extra_config={"gateway": {"model_id": _LOCKED_MODEL}},
        ),
        _provider("prov_c", priority=2, rate="0.002"),
        _provider("prov_sick", priority=0, rate="0.001", health_status="unhealthy"),
    ]


def _context(providers: list[Provider]) -> PlanningContext:
    return PlanningContext.replay(now=_NOW, providers=providers, capability=_capability())


def _request() -> RoutingRequest:
    return RoutingRequest(capability_name=_CAP_NAME, capability_id=_CAP_ID)


def _live_order(
    providers: list[Provider],
    *,
    mode: str,
    lockouts: LockoutSnapshot | None = None,
) -> tuple[str, ...]:
    plan = build_production_plan(
        capability=_capability(),
        providers=providers,
        payload={},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode=mode,
        weights=RoutingWeights(),
        max_attempts=3,
        context=_context(providers),
        lockouts=lockouts,
    )
    return plan.fallback_chain


def test_simulation_matches_live_plan() -> None:
    providers = _providers()

    for mode, expected in (
        ("priority", ("prov_b", "prov_c", "prov_a")),
        ("weighted", ("prov_a", "prov_c", "prov_b")),
    ):
        simulated = WhatIfSimulator(_context(providers), mode=mode).simulate(_request())

        assert simulated.plan is not None
        assert simulated.plan.fallback_chain == expected
        assert simulated.plan.fallback_chain == _live_order(providers, mode=mode)


def test_simulation_takes_lockouts_as_an_explicit_input() -> None:
    providers = _providers()
    lockouts = LockoutSnapshot(
        states={
            LockoutKey("prov_b", _LOCKED_MODEL): LockoutState(
                failures=1, locked_until=_NOW + timedelta(hours=1), reason="quota"
            )
        }
    )

    simulated = WhatIfSimulator(_context(providers), lockouts=lockouts).simulate(_request())

    assert simulated.plan is not None
    assert simulated.plan.fallback_chain == ("prov_c", "prov_a")
    assert simulated.plan.fallback_chain == _live_order(
        providers, mode="priority", lockouts=lockouts
    )
    assert simulated.reserved_usd == Decimal("0.120000")
