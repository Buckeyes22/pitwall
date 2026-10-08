"""A disarmed serve provider is not routable, exactly like an unhealthy one (review finding #2)."""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.routing import RoutingRequest
from pitwall.routing import openai as openai_routing
from pitwall.routing.production import stage12_elimination
from tests.conftest import make_llm_capability, make_provider

NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize("status", ["unhealthy", "disarmed"])
def test_openai_chain_excludes(status: str) -> None:
    assert openai_routing._provider_unhealthy(make_provider(health_status=status))


@pytest.mark.parametrize("status", ["unhealthy", "disarmed"])
def test_planner_eliminates(status: str) -> None:
    provider = make_provider(health_status=status)
    capability = make_llm_capability()
    gate = stage12_elimination(
        RoutingRequest(capability_name=capability.name, capability_id=capability.id),
        provider,
        capability=capability,
        now=NOW,
    )
    assert gate is not None
    assert gate.reason == "health_unavailable"


def test_healthy_provider_is_still_routable() -> None:
    assert not openai_routing._provider_unhealthy(make_provider(health_status="healthy"))
