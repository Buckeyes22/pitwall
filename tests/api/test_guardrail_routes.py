"""Hermetic REST contracts for guardrail status and non-persisting preview."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.routes.guardrails import router
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = pytest.mark.anyio


async def test_guardrail_status_and_preview_share_service_without_mutation() -> None:
    service = PreSpendInspectionService()
    app = FastAPI()
    app.state.pre_spend_inspection_service = service
    app.include_router(router)
    secret = "sk-abcdefghijklmnop12345678"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        before = await client.get("/v1/guardrails")
        preview = await client.post(
            "/v1/guardrails/preview",
            json={"payload": {"token": secret}},
        )
        after = await client.get("/v1/guardrails")

    assert before.status_code == 200
    assert before.json()["counters"]["total"] == 0
    assert preview.status_code == 200
    assert preview.json()["decision"] == "block"
    assert preview.json()["findings"][0]["rule"] == "secret_field"
    assert secret not in preview.text
    assert after.json()["counters"]["total"] == 0
    assert after.json()["last_decision"] is None


async def test_guardrail_preview_rejects_missing_payload() -> None:
    app = FastAPI()
    app.state.pre_spend_inspection_service = PreSpendInspectionService()
    app.include_router(router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/v1/guardrails/preview", json={})

    assert response.status_code == 422
