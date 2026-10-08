"""Ownership contracts for API-scoped onboarding and routing services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from pitwall.api import lifespan

pytestmark = pytest.mark.anyio


async def test_lifespan_builds_and_retires_owned_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.state.pool = object()
    market = MagicMock(aclose=AsyncMock())
    redis = MagicMock(aclose=AsyncMock())
    onboarding = MagicMock(aclose=AsyncMock())
    routing = object()

    monkeypatch.setenv("REDIS_URL", "redis://example.test/0")
    monkeypatch.setattr(lifespan, "build_configured_runpod_market_service", lambda: market)
    monkeypatch.setattr(lifespan.redis, "from_url", lambda *_args, **_kwargs: redis)
    monkeypatch.setattr(
        lifespan,
        "create_runpod_onboarding_service",
        lambda pool, *, actor: onboarding,
    )
    monkeypatch.setattr(
        lifespan,
        "ProductionRoutingService",
        lambda pool, *, settings: routing,
    )

    async with lifespan.api_lifespan(app):
        assert app.state.runpod_market_service is market
        assert app.state.redis is redis
        assert app.state.runpod_onboarding_service is onboarding
        assert app.state.production_routing_service is routing

    market.aclose.assert_awaited_once_with()
    redis.aclose.assert_awaited_once_with()
    onboarding.aclose.assert_awaited_once_with()
    assert app.state.runpod_market_service is None
    assert app.state.redis is None
    assert app.state.runpod_onboarding_service is None
    assert app.state.production_routing_service is None


async def test_lifespan_preserves_injected_service_ownership() -> None:
    app = FastAPI()
    app.state.pool = object()
    market = MagicMock(aclose=AsyncMock())
    redis = MagicMock(aclose=AsyncMock())
    onboarding = MagicMock(aclose=AsyncMock())
    routing = object()
    app.state.runpod_market_service = market
    app.state.redis = redis
    app.state.runpod_onboarding_service = onboarding
    app.state.production_routing_service = routing

    async with lifespan.api_lifespan(app):
        assert app.state.runpod_market_service is market
        assert app.state.redis is redis
        assert app.state.runpod_onboarding_service is onboarding
        assert app.state.production_routing_service is routing

    market.aclose.assert_not_awaited()
    redis.aclose.assert_not_awaited()
    onboarding.aclose.assert_not_awaited()
    assert app.state.runpod_market_service is market
    assert app.state.redis is redis
    assert app.state.runpod_onboarding_service is onboarding
    assert app.state.production_routing_service is routing
