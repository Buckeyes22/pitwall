"""Application-wide REST validation errors never reflect invalid input."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.anyio


async def test_request_validation_error_is_stable_and_non_reflecting(clear_app_module) -> None:
    from tests import conftest

    mod = conftest._import_app(conftest._env_for_app())
    canary = "sk-rest-validation-canary-1234567890abcdef"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mod.app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/v1/cost/burn-rate",
            params={"window_days": canary},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_request",
        "detail": [{"type": "int_parsing"}],
    }
    assert canary not in response.text
