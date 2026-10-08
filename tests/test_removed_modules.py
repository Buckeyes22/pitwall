"""Modules removed in the single-project consolidation stay removed."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REMOVED_MODULES = [
    "pitwall.routing.canary",
    "pitwall.routing.prewarm",
    "pitwall.routing.failover",
    "pitwall.routing.semantic_cache",
    "pitwall.routing.carbon",
    "pitwall.routing.cascade",
    "pitwall.routing.hedging",
    "pitwall.routing.quality_routing",
    "pitwall.routing.arbitrage",
    "pitwall.cost.slo_governor",
    "pitwall.finops.bidding",
    "pitwall.rate_limits.store",
    "pitwall.gitops.reconcile",
    "pitwall.workers",
    "pitwall.workers.vllm",
    "pitwall.workers.header_policy",
    "pitwall.live",
    "pitwall.cost.threshold_alerts",
]


@pytest.mark.parametrize("module", REMOVED_MODULES)
def test_removed_modules_not_importable(module: str) -> None:
    # A developer checkout can keep an untracked ``__pycache__`` directory after a
    # package is deleted; Python then imports it as an empty namespace package. That
    # stale directory is not the module, so only a module with source counts.
    try:
        found = importlib.import_module(module)
    except ModuleNotFoundError:
        return
    assert getattr(found, "__file__", None) is None, f"{module} still has source"
    locations = list(getattr(found, "__path__", []))
    assert not any(any(Path(entry).glob("*.py")) for entry in locations), (
        f"{module} still has source"
    )


REMOVED_SYMBOLS = [
    ("pitwall.routing.types", "RoutePlan"),
    ("pitwall.routing.types", "RouteAttempt"),
    ("pitwall.routing.types", "RouteCandidate"),
    ("pitwall.routing.types", "CapacityDecision"),
    ("pitwall.routing.types", "CapacityProbeKey"),
    ("pitwall.routing.types", "parse_stream_from_bytes"),
    ("pitwall.routing.constraints", "apply_hard_constraints"),
    ("pitwall.routing.constraints", "check_hard_constraints"),
    ("pitwall.routing.constraints", "evaluate_hard_constraint"),
    ("pitwall.routing.constraints", "hard_constraint_filter"),
    ("pitwall.routing.constraints", "hard_constraint_reasons"),
    ("pitwall.routing.constraints", "stage1_hard_constraint_filter"),
    ("pitwall.workload_lifecycle", "enqueue_submit_runpod_job"),
    ("pitwall.workload_lifecycle", "insert_passthrough_workload"),
    ("pitwall.gitops", "apply_plan"),
    ("pitwall.rate_limits", "RateBucketStore"),
    ("pitwall.cost.notifications", "send_threshold_email"),
    ("pitwall.core.models", "RateBucket"),
    ("pitwall.rate_limits", "TokenBucketRateLimiter"),
    ("pitwall.rate_limits", "RateLimiter"),
    ("pitwall.rate_limits", "RateBucketStoreProtocol"),
    ("pitwall.rate_limits.algorithm", "TokenBucketRateLimiter"),
    ("pitwall.routing.types", "RouteElimination"),
    ("pitwall.routing.types", "ProviderEliminated"),
    ("pitwall.routing.constraints", "filter_hard_constraints"),
    ("pitwall.routing.constraints", "HardConstraintFilterResult"),
    ("pitwall.routing", "RouteElimination"),
    ("pitwall.routing", "ProviderEliminated"),
    ("pitwall.routing", "filter_hard_constraints"),
    ("pitwall.routing", "HardConstraintFilterResult"),
    ("pitwall.routing", "RoutePlan"),
    ("pitwall.routing", "hard_constraint_filter"),
    ("pitwall.rate_limits", "RateLimitConfig"),
    ("pitwall.rate_limits.algorithm", "RateLimitConfig"),
    ("pitwall.rate_limits", "capacity_after_429"),
    ("pitwall.rate_limits.algorithm", "capacity_after_429"),
    ("pitwall.rate_limits", "dynamic_capacity"),
    ("pitwall.rate_limits.algorithm", "dynamic_capacity"),
    ("pitwall.rate_limits", "effective_capacity"),
    ("pitwall.rate_limits.algorithm", "effective_capacity"),
    ("pitwall.rate_limits", "halved_capacity"),
    ("pitwall.rate_limits.algorithm", "halved_capacity"),
    ("pitwall.rate_limits", "RateLimitExceeded"),
    ("pitwall.rate_limits.algorithm", "RateLimitExceeded"),
    ("pitwall.rate_limits.algorithm", "MonotonicClock"),
    ("pitwall.rate_limits.algorithm", "WallClock"),
    ("pitwall.rate_limits", "LOCAL_WAIT_LIMIT_S"),
    ("pitwall.rate_limits.algorithm", "LOCAL_WAIT_LIMIT_S"),
    ("pitwall.rate_limits", "CAPACITY_REFRESH_INTERVAL_S"),
    ("pitwall.rate_limits.algorithm", "CAPACITY_REFRESH_INTERVAL_S"),
    ("pitwall.rate_limits", "CAPACITY_REBUILD_WINDOW_S"),
    ("pitwall.rate_limits.algorithm", "CAPACITY_REBUILD_WINDOW_S"),
]


@pytest.mark.parametrize(("module", "name"), REMOVED_SYMBOLS)
def test_removed_symbols_are_gone(module: str, name: str) -> None:
    assert not hasattr(importlib.import_module(module), name)
