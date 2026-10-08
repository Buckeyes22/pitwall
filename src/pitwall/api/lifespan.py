"""Startup wiring for the Pitwall API app.

``db_lifespan`` attaches only the Postgres pool. The request path also needs a Redis
client on ``app.state.redis``: the OpenAI proxy stamps lease traffic through it, and
``stamp_lease_traffic`` returns silently when it is ``None``, so a missing client
disables activity renewal and the idle-timeout stop with no error anywhere.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from pitwall.config import get_settings
from pitwall.db import db_lifespan
from pitwall.onboarding import create_runpod_onboarding_service
from pitwall.routing.lockout import configure_lockout_persistence
from pitwall.routing.production import ProductionRoutingService
from pitwall.runpod_market import build_configured_runpod_market_service

log = logging.getLogger(__name__)


@asynccontextmanager
async def api_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Attach the Postgres pool and a Redis client, honouring pre-injected fakes."""
    injected_market = getattr(app.state, "runpod_market_service", None)
    owns_market = injected_market is None
    if owns_market:
        app.state.runpod_market_service = build_configured_runpod_market_service()
    injected_redis = getattr(app.state, "redis", None)
    owns_redis = injected_redis is None
    if owns_redis:
        redis_url = os.environ.get("REDIS_URL")
        app.state.redis = (
            redis.from_url(redis_url, decode_responses=True)  # type: ignore[no-untyped-call]  # reason: redis.from_url is untyped in redis-py
            if redis_url
            else None
        )
        if app.state.redis is None:
            log.warning("REDIS_URL is unset: lease traffic will not be recorded")
    injected_onboarding = getattr(app.state, "runpod_onboarding_service", None)
    owns_onboarding = injected_onboarding is None
    injected_routing = getattr(app.state, "production_routing_service", None)
    owns_routing = injected_routing is None
    try:
        async with db_lifespan(app):
            pool = app.state.pool
            await configure_lockout_persistence(pool)
            if owns_onboarding:
                app.state.runpod_onboarding_service = create_runpod_onboarding_service(
                    pool,
                    actor="rest:admin",
                )
            if owns_routing:
                app.state.production_routing_service = ProductionRoutingService(
                    pool,
                    settings=get_settings(),
                )
            yield
    finally:
        if owns_onboarding:
            onboarding = getattr(app.state, "runpod_onboarding_service", None)
            if onboarding is not None:
                await onboarding.aclose()
            app.state.runpod_onboarding_service = None
        if owns_routing:
            app.state.production_routing_service = None
        if owns_market:
            market = getattr(app.state, "runpod_market_service", None)
            if market is not None:
                await market.aclose()
            app.state.runpod_market_service = None
        if owns_redis:
            client = getattr(app.state, "redis", None)
            if client is not None:
                await client.aclose()
            app.state.redis = None
