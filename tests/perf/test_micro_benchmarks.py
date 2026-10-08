from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from pitwall.config import RoutingWeights
from pitwall.cost.estimator import get_estimator
from pitwall.providers.registry import get_default_registry
from pitwall.routing.production import RoutingOperation, build_production_plan
from tests.conftest import TEST_NOW, make_llm_capability, make_provider


def _assert_positive_finite_median(benchmark: Any) -> None:
    median_s = benchmark.stats["median"]
    assert median_s > 0
    assert math.isfinite(median_s)


@pytest.mark.benchmark
def test_per_second_estimator_micro_benchmark(benchmark: Any) -> None:
    estimator = get_estimator("per_second")
    capability = make_llm_capability(cost_mode="per_second")
    provider_cost = {"per_second_active": Decimal("0.000123")}

    def estimate_once() -> Decimal:
        return estimator.estimate(capability, provider_cost, {})

    result = benchmark.pedantic(estimate_once, rounds=500)

    assert result == Decimal("0.007380")
    _assert_positive_finite_median(benchmark)


@pytest.mark.benchmark
def test_build_production_plan_micro_benchmark(benchmark: Any) -> None:
    capability = make_llm_capability()
    provider = make_provider(endpoint_id="endpoint-route", cost={"per_second_active": "0.001"})
    registry = get_default_registry()

    def plan_once() -> Any:
        return build_production_plan(
            capability=capability,
            providers=[provider],
            payload={},
            operation=RoutingOperation.SYNC_INFERENCE,
            registry=registry,
            now=TEST_NOW,
            mode="priority",
            weights=RoutingWeights(),
            max_attempts=3,
        )

    result = benchmark.pedantic(plan_once, rounds=500)

    assert result.selected_provider_id == provider.id
    assert result.eliminated == ()
    _assert_positive_finite_median(benchmark)
