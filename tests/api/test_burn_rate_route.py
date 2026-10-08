"""Feature-local REST adapter tests for the burn-rate read model."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.routes import burn_rate as burn_rate_route
from tests.finops._burn_rate import sample_burn_rate_read

pytestmark = pytest.mark.anyio


async def test_burn_rate_route_returns_the_shared_typed_model_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = sample_burn_rate_read()
    pool = object()
    service = AsyncMock(return_value=expected)
    monkeypatch.setattr(burn_rate_route, "read_configured_burn_rate", service)
    monkeypatch.setattr(burn_rate_route, "_utc_now", lambda: expected.now)
    app = FastAPI()
    app.state.pool = pool
    app.include_router(burn_rate_route.burn_rate_router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/cost/burn-rate?window_days=7")

    assert response.status_code == 200
    assert response.json() == expected.to_dict()
    service.assert_awaited_once_with(pool, now=expected.now, window_days=7)


async def test_burn_rate_route_validates_feature_local_window_bounds() -> None:
    app = FastAPI()
    app.include_router(burn_rate_route.burn_rate_router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/cost/burn-rate?window_days=367")

    assert response.status_code == 422
