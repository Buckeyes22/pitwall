"""Isolated REST cost router contracts before global route registration."""

from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal
from types import ModuleType
from unittest.mock import ANY, AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from pitwall.cost.read_models import (
    CostSummaryEntry,
    CostSummaryRead,
    RecentWorkloadsRead,
    WorkloadCostRead,
    WorkloadCostRecord,
)

pytestmark = pytest.mark.anyio


def _app() -> tuple[FastAPI, ModuleType]:
    """Bind the router and patch target to the same current module instance."""

    route_module = importlib.import_module("pitwall.api.routes.cost")
    app = FastAPI()
    app.state.pool = object()
    app.include_router(route_module.router)
    return app, route_module


async def test_cost_summary_route_delegates_filters_and_preserves_numeric_compatibility() -> None:
    read = CostSummaryRead(
        total_usd=Decimal("1.250000"),
        entries=(
            CostSummaryEntry(
                day=dt.date(2026, 9, 1),
                capability_class="llm",
                provider_type="public_endpoint",
                workload_count=2,
                cost_usd=Decimal("1.250000"),
            ),
        ),
    )
    app, route_module = _app()
    with patch.object(
        route_module, "cost_summary_read", new=AsyncMock(return_value=read)
    ) as service:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/v1/cost/summary",
                params={
                    "capability_class": "llm",
                    "since": "2026-09-01",
                    "until": "2026-09-02",
                },
            )

    assert response.status_code == 200
    assert response.json() == {
        "total_usd": "1.250000",
        "entries": [
            {
                "day": "2026-09-01",
                "capability_class": "llm",
                "provider_type": "public_endpoint",
                "workload_count": 2,
                "cost_usd": "1.250000",
            }
        ],
    }
    service.assert_awaited_once_with(
        ANY,
        capability_class="llm",
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 2),
    )


async def test_workload_route_exposes_one_shared_exact_cost_read() -> None:
    read = RecentWorkloadsRead(
        workloads=(
            WorkloadCostRecord(
                fields={"id": "wkl-cost", "state": "completed"},
                cost=WorkloadCostRead.from_persisted(
                    cost_estimate_usd=Decimal("0.005000"),
                    cost_actual_usd=Decimal("0.004500"),
                ),
            ),
        )
    )
    app, route_module = _app()
    with patch.object(
        route_module,
        "recent_workloads_read",
        new=AsyncMock(return_value=read),
    ) as service:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/v1/cost/workloads",
                params={"state": "completed", "limit": 5},
            )

    assert response.status_code == 200
    workload = response.json()["workloads"][0]
    assert workload["cost_estimate_usd"] == "0.005000"
    assert workload["cost_actual_usd"] == "0.004500"
    assert workload["cost"] == {
        "model": "persisted_workload_cost",
        "components": [],
        "estimate": "0.005000",
        "ceiling": "0.005000",
        "confidence": "unknown",
        "provenance": "pitwall.workloads",
        "currency": "USD",
        "assumptions": [
            "persisted cost_estimate_usd is the available pre-spend amount",
            "quote components and original confidence are not persisted",
        ],
        "actual": "0.004500",
        "variance": "-0.000500",
        "effective": "0.004500",
        "reconciliation_status": "actual_recorded",
        "actual_kind": "recorded",
        "actual_provenance": "recorded_actual_source_unspecified",
        "reconciled_at": None,
        "provider_invoice": False,
    }
    service.assert_awaited_once_with(
        ANY,
        capability_id=None,
        provider_id=None,
        provider_type=None,
        state="completed",
        since=None,
        until=None,
        limit=5,
    )


async def test_workload_route_rejects_unbounded_result_count() -> None:
    app, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        low = await client.get("/v1/cost/workloads", params={"limit": 0})
        high = await client.get("/v1/cost/workloads", params={"limit": 101})

    assert low.status_code == 422
    assert high.status_code == 422


@pytest.mark.parametrize(
    ("path", "param"),
    [
        ("/v1/cost/summary", "capability_class"),
        ("/v1/cost/workloads", "state"),
        ("/v1/cost/workloads", "capability_id"),
        ("/v1/cost/workloads", "provider_id"),
        ("/v1/cost/workloads", "provider_type"),
    ],
)
async def test_cost_filters_reject_nul_before_the_read_model(path: str, param: str) -> None:
    # Regression (fuzz): a NUL filter reached the SQL read, which Postgres refuses (500).
    app, route_module = _app()
    with (
        patch.object(route_module, "cost_summary_read", new=AsyncMock()) as summary,
        patch.object(route_module, "recent_workloads_read", new=AsyncMock()) as workloads,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(path, params={param: "x\x00"})

    assert response.status_code == 422
    summary.assert_not_called()
    workloads.assert_not_called()
