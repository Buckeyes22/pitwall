"""Thin REST contract tests for ROUTE-01 leaf registration."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from pitwall.api.routes.routing import routing_router
from pitwall.core.enums import ProviderAdapterId, WorkloadState
from pitwall.core.models import Workload
from pitwall.cost import BudgetRejected, BudgetSnapshot
from tests.routing.test_production_routing import _plan, _provider

_NOW = dt.datetime(2026, 9, 1, 16, 0, tzinfo=dt.UTC)


class _Quote:
    def estimate(self) -> Decimal:
        return Decimal("0.010000")

    def upper_bound(self) -> Decimal:
        return Decimal("0.010000")

    def to_serializable_dict(self) -> dict[str, object]:
        return {"model": "per_request", "estimate": "0.010000", "ceiling": "0.010000"}


class _Candidate:
    rank = 1
    provider_id = "prov_route"
    quote = _Quote()


class _Plan:
    plan_id = "plan_0123456789abcdef0123456789abcdef"
    selected_provider_id = "prov_route"
    selected = _Candidate()
    attempts = (_Candidate(),)

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "observed_at": _NOW.isoformat(),
            "operation": "async_inference",
            "capability_id": "cap_route",
            "capability_name": "llm.route",
            "payload_sha256": "a" * 64,
            "provider_constraint": None,
            "mode": "weighted",
            "weights": {"cost": "1", "latency": "0.001"},
            "selected_provider_id": self.selected_provider_id,
            "fallback_chain": [self.selected_provider_id],
            "attempts": [],
            "ranked_candidates": [],
            "eliminated": [],
            "signal_policy": {
                "missing_cost": "fail_closed",
                "explicit_capacity_unavailable": "fail_closed",
                "missing_capacity": "neutral",
                "missing_latency": "worst_known_or_zero",
            },
        }


class _Events:
    def to_dict(self) -> dict[str, object]:
        return {
            "workload_id": "wkl_route",
            "plan_id": _Plan.plan_id,
            "events": [{"event": "submitted", "state": "queued", "at": _NOW.isoformat()}],
            "streaming_supported": False,
            "unavailable_reason": "provider_event_stream_unavailable",
        }


class _Service:
    def __init__(self) -> None:
        self.preview_calls: list[dict[str, Any]] = []
        self.submit_calls: list[dict[str, Any]] = []

    async def preview(self, **kwargs: Any) -> _Plan:
        self.preview_calls.append(kwargs)
        return _Plan()

    async def submit_job(self, **kwargs: Any) -> Workload:
        self.submit_calls.append(kwargs)
        plan = _Plan().to_dict()
        return Workload(
            id="wkl_route",
            capability_id="cap_route",
            provider_id="prov_route",
            type="async_job",
            state=WorkloadState.QUEUED,
            external_job_id="external_route",
            submitted_at=_NOW,
            cost_estimate_usd=Decimal("0.010000"),
            cost_ceiling_usd=Decimal("0.010000"),
            cost_quote={
                "model": "route_plan",
                "components": [
                    {
                        "name": "provider_attempt_1",
                        "unit": "attempt",
                        "rate": "0.010000",
                        "ceiling_rate": "0.010000",
                        "count": "1",
                        "ceiling_count": "1",
                        "estimate": "0.010000",
                        "ceiling": "0.010000",
                    }
                ],
                "estimate": "0.010000",
                "ceiling": "0.010000",
                "confidence": "bounded",
                "provenance": "production_route_plan",
                "currency": "USD",
                "assumptions": [
                    "asynchronous submission stops after the first ambiguous provider response",
                    f"route plan {_Plan.plan_id}",
                ],
            },
            route_plan_id=_Plan.plan_id,
            route_plan=plan,
        )

    async def job_events(self, workload_id: str, *, limit: int) -> _Events:
        assert workload_id == "wkl_route"
        assert limit == 3
        return _Events()


def _budget_rejection() -> BudgetRejected:
    return BudgetRejected(
        "monthly_budget",
        BudgetSnapshot(
            monthly_budget_usd=Decimal("1"),
            per_request_max_usd=Decimal("1"),
            mtd_spend_usd=Decimal("1"),
            estimate_usd=Decimal("0.1"),
            budget_remaining_usd=Decimal("0"),
        ),
    )


class _BudgetRejectingService(_Service):
    async def preview(self, **kwargs: Any) -> _Plan:
        del kwargs
        raise _budget_rejection()

    async def submit_job(self, **kwargs: Any) -> Workload:
        del kwargs
        raise _budget_rejection()


def _client(service: _Service) -> TestClient:
    app = FastAPI()

    @app.exception_handler(BudgetRejected)
    async def budget_error(_request: Request, exc: BudgetRejected) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())

    app.state.production_routing_service = service
    app.include_router(routing_router)
    return TestClient(app)


def test_preview_route_returns_payload_free_plan() -> None:
    service = _Service()
    with _client(service) as client:
        response = client.post(
            "/v1/routing/preview",
            json={
                "capability_id": "llm.route",
                "payload": {"messages": []},
                "operation": "async_inference",
            },
        )

    assert response.status_code == 200
    assert response.json()["plan_id"] == _Plan.plan_id
    assert service.preview_calls[0]["payload"] == {"messages": []}


def test_job_submit_and_dry_run_share_plan_shape() -> None:
    service = _Service()
    with _client(service) as client:
        dry = client.post(
            "/v1/jobs",
            json={"capability_id": "llm.route", "input": {"max_output_tokens": 2}, "dry_run": True},
        )
        live = client.post(
            "/v1/jobs",
            headers={"Idempotency-Key": "route-key"},
            json={"capability_id": "llm.route", "input": {"max_output_tokens": 2}},
        )

    assert dry.status_code == 200
    assert live.status_code == 200
    assert dry.json()["plan"]["plan_id"] == live.json()["plan"]["plan_id"]
    assert dry.json()["cost"]["model"] == "route_plan"
    assert dry.json()["cost"]["assumptions"] == live.json()["cost"]["assumptions"]
    assert f"route plan {_Plan.plan_id}" in dry.json()["cost"]["assumptions"]
    assert "plan_id" not in dry.json()["cost"]
    assert service.submit_calls[0]["payload"] == {"max_output_tokens": 2}
    assert service.submit_calls[0]["idempotency_key"] == "route-key"


def test_job_events_are_bounded_and_explicitly_non_streaming() -> None:
    with _client(_Service()) as client:
        response = client.get("/v1/jobs/wkl_route/events?limit=3")

    assert response.status_code == 200
    assert response.json()["streaming_supported"] is False
    assert response.json()["unavailable_reason"] == "provider_event_stream_unavailable"


def test_route_preview_rejects_unknown_fields() -> None:
    with _client(_Service()) as client:
        response = client.post(
            "/v1/routing/preview",
            json={"capability_id": "llm.route", "payload": {}, "surprise": True},
        )

    assert response.status_code == 422


def test_job_submit_preserves_canonical_budget_error() -> None:
    with _client(_BudgetRejectingService()) as client:
        response = client.post(
            "/v1/jobs",
            json={"capability_id": "llm.route", "input": {}},
        )

    assert response.status_code == 402
    assert response.json()["error"] == "budget_rejected"


def test_route_preview_preserves_canonical_budget_error() -> None:
    with _client(_BudgetRejectingService()) as client:
        response = client.post(
            "/v1/routing/preview",
            json={"capability_id": "llm.route", "payload": {}, "operation": "sync_inference"},
        )

    assert response.status_code == 402
    assert response.json()["error"] == "budget_rejected"


def test_preview_route_serves_a_real_planner_plan() -> None:
    """The response model must accept every key the real planner emits, not a hand fake."""
    real = _plan([_provider("prov_real", ProviderAdapterId.RUNPOD, priority=10, latency_ms=100)])
    service = _Service()

    async def preview(**kwargs: Any) -> Any:
        service.preview_calls.append(kwargs)
        return real

    service.preview = preview  # type: ignore[method-assign]  # reason: test replaces the service method with a stub
    with _client(service) as client:
        response = client.post(
            "/v1/routing/preview",
            json={"capability_id": "llm.route", "payload": {"messages": []}},
        )

    assert response.status_code == 200, response.text
    assert response.json() == real.to_dict()
