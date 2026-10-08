"""Contract tests for ``POST /v1/inference`` production routing."""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.core.enums import CapabilityClass, WorkloadState
from pitwall.core.models import Workload
from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.resolver import CapabilityDisabledError, NoHealthyProviderError
from pitwall.routing.production import (
    RouteExecutionResult,
    RouteGuardrailRejected,
    RoutePlanningError,
)
from tests.api._contract_helpers import build_app, client_for, override

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 1, 16, 0, tzinfo=dt.UTC)
_CAPABILITY_ID = "cap_bge_m3"
_CAPABILITY_NAME = "embedding.bge-m3"
_PLAN_ID = "plan_0123456789abcdef0123456789abcdef"


class _Plan:
    plan_id = _PLAN_ID
    capability_name = _CAPABILITY_NAME
    selected_provider_id = "prov_bge_m3"

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "capability_name": self.capability_name,
            "selected_provider_id": self.selected_provider_id,
            "fallback_chain": [self.selected_provider_id],
        }


def _workload() -> Workload:
    return Workload(
        id="wkl_routed",
        capability_id=_CAPABILITY_ID,
        provider_id="prov_bge_m3",
        type="inference",
        state=WorkloadState.COMPLETED,
        submitted_at=_NOW,
        completed_at=_NOW,
        cost_estimate_usd=Decimal("0.01"),
        result={"ok": True},
        langfuse_trace_id="trace-routed",
        route_plan_id=_PLAN_ID,
        route_plan=_Plan().to_dict(),
    )


class _Service:
    def __init__(self, capability_class: CapabilityClass = CapabilityClass.EMBEDDING) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._capability_class = capability_class

    async def capability_class(self, capability_id: str) -> CapabilityClass:
        return self._capability_class

    async def preview(self, **kwargs: Any) -> _Plan:
        self.calls.append(("preview", kwargs))
        return _Plan()

    async def execute_sync(self, **kwargs: Any) -> RouteExecutionResult:
        self.calls.append(("execute_sync", kwargs))
        workload = _workload()
        return RouteExecutionResult(workload=workload, plan=_Plan(), output=workload.result)


def _snapshot() -> BudgetSnapshot:
    return BudgetSnapshot(
        monthly_budget_usd=Decimal("50.0"),
        per_request_max_usd=Decimal("10.0"),
        mtd_spend_usd=Decimal("49.5"),
        estimate_usd=Decimal("1.0"),
        budget_remaining_usd=Decimal("0.5"),
    )


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"capability_id": _CAPABILITY_ID, "text": "hello"}
    body.update(overrides)
    return body


def _setup(service: _Service):
    mod = build_app(pool=object())
    from pitwall.api.routes.inference import _routing_service

    override(mod, _routing_service, service)
    return mod


async def test_success_returns_workload_and_exact_route_headers(clear_app_module) -> None:
    service = _Service()
    mod = _setup(service)

    async with client_for(mod) as client:
        response = await client.post(
            "/v1/inference",
            headers={"Idempotency-Key": "idem-rest-1"},
            json=_body(),
        )

    assert response.status_code == 200
    assert response.json() == {"workload_id": "wkl_routed", "result": {"ok": True}}
    assert response.headers["X-Pitwall-Route-Plan-ID"] == _PLAN_ID
    assert response.headers["X-Pitwall-Trace"] == "trace-routed"
    assert service.calls == [
        (
            "execute_sync",
            {
                "capability_id": _CAPABILITY_ID,
                "payload": {"text": "hello"},
                "provider_id": None,
                "idempotency_key": "idem-rest-1",
            },
        )
    ]


async def test_dry_run_uses_preview_without_execution(clear_app_module) -> None:
    service = _Service()
    mod = _setup(service)

    async with client_for(mod) as client:
        response = await client.post("/v1/inference", json=_body(dry_run=True))

    assert response.status_code == 200
    assert response.json()["result"]["plan"]["plan_id"] == _PLAN_ID
    assert response.headers["X-Pitwall-Route-Plan-ID"] == _PLAN_ID
    assert service.calls[0][0] == "preview"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (CapabilityDisabledError(_CAPABILITY_NAME), 409, "capability_disabled"),
        (NoHealthyProviderError(_CAPABILITY_NAME), 503, "no_providers_available"),
        (RoutePlanningError("idempotency key conflicts"), 422, "idempotency_mismatch"),
        (RouteGuardrailRejected(("secret",)), 422, "pre_spend_payload_rejected"),
    ],
)
async def test_shared_service_errors_keep_rest_contract(
    clear_app_module,
    error: Exception,
    status: int,
    code: str,
) -> None:
    class _FailingService(_Service):
        async def execute_sync(self, **kwargs: Any) -> RouteExecutionResult:
            self.calls.append(("execute_sync", kwargs))
            raise error

    mod = _setup(_FailingService())
    async with client_for(mod) as client:
        response = await client.post("/v1/inference", json=_body())

    assert response.status_code == status
    assert response.json()["error"] == code


async def test_budget_rejected_402(clear_app_module) -> None:
    class _BudgetService(_Service):
        async def execute_sync(self, **kwargs: Any) -> RouteExecutionResult:
            del kwargs
            raise BudgetRejected("monthly_budget", _snapshot())

    mod = _setup(_BudgetService())
    async with client_for(mod) as client:
        response = await client.post("/v1/inference", json=_body())

    assert response.status_code == 402
    assert response.json()["error"] == "budget_rejected"


async def test_inference_rejects_null_byte_capability_id_before_service(
    clear_app_module,
) -> None:
    service = _Service()
    mod = _setup(service)

    async with client_for(mod) as client:
        response = await client.post("/v1/inference", json={"capability_id": "\x00"})

    assert response.status_code == 422
    assert service.calls == []


async def test_concurrent_idempotent_requests_coalesce_one_execution(
    clear_app_module,
) -> None:
    request_count = 6
    all_arrived = asyncio.Event()

    class _CoalescingService(_Service):
        async def execute_sync(self, **kwargs: Any) -> RouteExecutionResult:
            self.calls.append(("execute_sync", kwargs))
            all_arrived.set()
            await asyncio.sleep(0.05)
            workload = _workload()
            return RouteExecutionResult(workload=workload, plan=_Plan(), output=workload.result)

    service = _CoalescingService()
    mod = _setup(service)
    async with client_for(mod) as client:
        responses = await asyncio.gather(
            *[
                client.post(
                    "/v1/inference",
                    headers={"Idempotency-Key": "idem-coalesced"},
                    json=_body(text="same prompt"),
                )
                for _ in range(request_count)
            ]
        )
    await all_arrived.wait()

    assert [response.status_code for response in responses] == [200] * request_count
    assert [name for name, _ in service.calls] == ["execute_sync"]


@pytest.mark.parametrize(
    ("capability_class", "payload", "executions"),
    [
        # Sampled chat: each anonymous caller must get its own sample (finding #12).
        (CapabilityClass.LLM, {"messages": [{"role": "user", "content": "hi"}]}, 3),
        (
            CapabilityClass.LLM,
            {"messages": [{"role": "user", "content": "hi"}], "temperature": 0},
            1,
        ),
        (CapabilityClass.EMBEDDING, {"text": "same prompt"}, 1),
    ],
)
async def test_anonymous_requests_coalesce_only_when_the_answer_cannot_differ(
    clear_app_module,
    capability_class: CapabilityClass,
    payload: dict[str, object],
    executions: int,
) -> None:
    class _SlowService(_Service):
        async def execute_sync(self, **kwargs: Any) -> RouteExecutionResult:
            self.calls.append(("execute_sync", kwargs))
            await asyncio.sleep(0.05)
            workload = _workload()
            return RouteExecutionResult(workload=workload, plan=_Plan(), output=workload.result)

    service = _SlowService(capability_class)
    mod = _setup(service)
    async with client_for(mod) as client:
        responses = await asyncio.gather(
            *[client.post("/v1/inference", json=_body(**payload)) for _ in range(3)]
        )

    assert [response.status_code for response in responses] == [200] * 3
    assert len(service.calls) == executions


@pytest.mark.parametrize(
    "lookalike",
    [{"dryrun": True}, {"dryRun": True}, {"dry-run": True}, {"idempotencyKey": "k-1"}],
)
async def test_a_misspelled_control_field_is_refused_before_any_execution(
    clear_app_module, lookalike: dict[str, object]
) -> None:
    """Extra fields are the provider payload, but a misspelled dry_run must never run live."""
    service = _Service()
    mod = _setup(service)

    async with client_for(mod) as client:
        response = await client.post(
            "/v1/inference",
            json={"capability_id": "embedding.demo", "texts": ["hi"], **lookalike},
        )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert service.calls == []
