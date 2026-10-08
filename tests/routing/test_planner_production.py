"""Routing rules that used to be pinned on the removed ``plan_route``, on the one planner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pitwall.config import RoutingWeights
from pitwall.core.enums import ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.providers.registry import get_default_registry
from pitwall.routing.production import (
    NoExecutableRouteError,
    ProductionRoutePlan,
    RoutingOperation,
    build_production_plan,
)

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


def _provider(
    provider_id: str,
    *,
    priority: int = 1,
    health_status: str = "healthy",
    cooldown_until: datetime | None = None,
    enabled: bool = True,
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_embed",
        name=provider_id,
        provider_type=ProviderType.SERVERLESS_QUEUE,
        region="US-KS-2",
        config={"cost": {"per_second_active": "0.001"}},
        priority=priority,
        enabled=enabled,
        health_status=health_status,
        cooldown_until=cooldown_until,
        updated_at=_NOW,
    )


def _capability() -> Capability:
    return Capability(
        id="cap_embed",
        name="embedding.bge-m3",
        version="1.0.0",
        **{"class": "embedding"},
        cost_mode="per_second",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _plan(providers: list[Provider], *, max_attempts: int = 3) -> ProductionRoutePlan:
    return build_production_plan(
        capability=_capability(),
        providers=providers,
        payload={},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode="priority",
        weights=RoutingWeights(),
        max_attempts=max_attempts,
    )


def test_attempt_chain_follows_priority_and_is_capped() -> None:
    providers = [_provider(f"prov_{n}", priority=n) for n in (4, 2, 1, 3)]

    assert _plan(providers).fallback_chain == ("prov_1", "prov_2", "prov_3")
    assert _plan(providers, max_attempts=1).fallback_chain == ("prov_1",)
    assert _plan(providers, max_attempts=10).fallback_chain == (
        "prov_1",
        "prov_2",
        "prov_3",
        "prov_4",
    )


def test_health_disabled_and_cooldown_eliminations_are_reported() -> None:
    plan = _plan(
        [
            _provider("prov_ok"),
            _provider("prov_unhealthy", health_status="unhealthy"),
            _provider("prov_disarmed", health_status="disarmed"),
            _provider("prov_off", enabled=False),
            _provider("prov_cooling", cooldown_until=_NOW + timedelta(minutes=3)),
        ]
    )

    assert plan.fallback_chain == ("prov_ok",)
    assert plan.dropped_provider_reasons == {
        "prov_cooling": ["health:health_cooldown"],
        "prov_disarmed": ["health:health_unavailable"],
        "prov_off": ["health:disabled"],
        "prov_unhealthy": ["health:health_unavailable"],
    }


def test_expired_cooldown_and_warming_providers_stay_routable() -> None:
    plan = _plan(
        [
            _provider("prov_expired", cooldown_until=_NOW - timedelta(minutes=1)),
            _provider("prov_warming", priority=2, health_status="warming"),
        ]
    )

    assert plan.fallback_chain == ("prov_expired", "prov_warming")


def test_no_survivor_raises_with_every_elimination() -> None:
    with pytest.raises(NoExecutableRouteError) as caught:
        _plan([_provider("prov_a", health_status="unhealthy"), _provider("prov_b", enabled=False)])

    assert {item.provider_id for item in caught.value.eliminations} == {"prov_a", "prov_b"}
