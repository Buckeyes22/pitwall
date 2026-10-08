"""Property-based tests for the production route planner (routing/production.py).

``build_production_plan`` is the only route planner. It is pure: providers, quota, lockouts, and
the clock are arguments, so every property below is reproducible.

Invariants:
    1. Determinism: identical inputs -> byte-identical plan (or the same elimination set)
    2. Partition: every input provider id is ranked XOR eliminated (never both, never lost)
    3. Bounded attempts: attempt ids are a subset of ranked ids; len <= max_attempts
    4. In priority mode the ranking is non-decreasing by priority
    5. Disabled / unhealthy providers never appear in the attempt chain
    6. max_attempts outside 1..10 raises ValueError
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitwall.config import RoutingWeights
from pitwall.core.enums import ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.providers.registry import get_default_registry
from pitwall.routing.production import (
    NoExecutableRouteError,
    ProductionRoutePlan,
    RouteElimination,
    RoutingOperation,
    build_production_plan,
)

pytestmark = pytest.mark.property

_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)
_CAP_ID = "cap_bge_m3"
_CAP_NAME = "embedding.bge-m3"
_NON_POD_TYPES = [ProviderType.SERVERLESS_QUEUE, ProviderType.PUBLIC_ENDPOINT]


def _capability() -> Capability:
    return Capability(
        id=_CAP_ID,
        name=_CAP_NAME,
        version="1.0.0",
        **{"class": "embedding"},
        cost_mode="per_second",
        created_at=_NOW,
        updated_at=_NOW,
    )


@st.composite
def provider_lists(draw: st.DrawFn) -> list[Provider]:
    n = draw(st.integers(min_value=1, max_value=6))
    providers: list[Provider] = []
    for i in range(n):
        providers.append(
            Provider(
                id=f"prov_{i}",
                capability_id=_CAP_ID,
                name=f"prov_{i}",
                provider_type=draw(st.sampled_from(_NON_POD_TYPES)),
                config={"cost": {"per_second_active": "0.001"}},
                priority=draw(st.integers(min_value=1, max_value=10)),
                enabled=draw(st.booleans()),
                health_status=draw(st.sampled_from(["healthy", "degraded", "unhealthy"])),
                cold_start_p50_ms=draw(st.integers(min_value=100, max_value=30_000)),
                recent_error_rate=draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False)),
                updated_at=_NOW,
            )
        )
    return providers


def _plan(
    providers: list[Provider], *, max_attempts: int = 3
) -> tuple[ProductionRoutePlan | None, tuple[RouteElimination, ...]]:
    try:
        plan = build_production_plan(
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
    except NoExecutableRouteError as exc:
        return None, exc.eliminations
    return plan, plan.eliminated


@given(providers=provider_lists())
def test_determinism(providers: list[Provider]) -> None:
    plan_a, eliminated_a = _plan(providers)
    plan_b, eliminated_b = _plan(providers)
    assert eliminated_a == eliminated_b
    assert (plan_a is None) == (plan_b is None)
    if plan_a is not None and plan_b is not None:
        assert plan_a.canonical_bytes() == plan_b.canonical_bytes()


@given(providers=provider_lists())
def test_partition_every_provider_ranked_xor_eliminated(
    providers: list[Provider],
) -> None:
    plan, eliminated = _plan(providers)
    input_ids = {p.id for p in providers}
    ranked_ids = (
        set() if plan is None else {candidate.provider_id for candidate in plan.ranked_candidates}
    )
    eliminated_ids = {item.provider_id for item in eliminated}
    assert ranked_ids.isdisjoint(eliminated_ids)
    assert ranked_ids | eliminated_ids == input_ids


@given(providers=provider_lists(), max_attempts=st.integers(min_value=1, max_value=5))
def test_attempts_bounded_and_subset_of_ranked(
    providers: list[Provider], max_attempts: int
) -> None:
    plan, _eliminated = _plan(providers, max_attempts=max_attempts)
    if plan is None:
        return
    ranked_ids = {candidate.provider_id for candidate in plan.ranked_candidates}
    attempt_ids = [candidate.provider_id for candidate in plan.attempts]
    assert 1 <= len(attempt_ids) <= max_attempts
    assert set(attempt_ids) <= ranked_ids


@given(providers=provider_lists())
def test_priority_ranking_is_non_decreasing(providers: list[Provider]) -> None:
    plan, _eliminated = _plan(providers)
    if plan is None:
        return
    priorities = [candidate.priority for candidate in plan.ranked_candidates]
    assert priorities == sorted(priorities)
    assert [candidate.rank for candidate in plan.ranked_candidates] == list(
        range(1, len(priorities) + 1)
    )


@given(providers=provider_lists())
def test_disabled_or_unhealthy_never_attempted(providers: list[Provider]) -> None:
    plan, _eliminated = _plan(providers)
    if plan is None:
        return
    by_id = {p.id: p for p in providers}
    for attempt in plan.attempts:
        prov = by_id[attempt.provider_id]
        assert prov.enabled is True
        assert prov.health_status in {"healthy", "warming"}


@given(bad=st.one_of(st.integers(max_value=0), st.integers(min_value=11)))
def test_max_attempts_outside_bounds_raises(bad: int) -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _plan([], max_attempts=bad)
