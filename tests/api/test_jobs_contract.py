"""Task 5a: async job read/cancel route contract.

Routes: GET /v1/jobs/{id}, /{id}/status, /{id}/result, POST /{id}/cancel.
Verified vs source 2026-09-01:
  - jobs.py handlers call the shared ``ProductionRoutingService`` facade.
  - Workload requires id, capability_id, provider_id, type, state, submitted_at
    (NOT created_at/updated_at; extras forbidden).
  - get/status/result/cancel -> WorkloadNotFound (404); result -> JobNotReady
    (409) when state is not terminal.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.enums import WorkloadState
from pitwall.core.models import Workload
from pitwall.routing.production import JobResultPage
from tests.api._contract_helpers import build_app, client_for

pytestmark = pytest.mark.anyio
_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)


def _workload(state: WorkloadState) -> Workload:
    return Workload(
        id="wkl_x",
        capability_id="cap_bge_m3",
        provider_id="prov_bge_m3",
        type="async",
        state=state,
        result={"ok": True} if state == WorkloadState.COMPLETED else None,
        submitted_at=_NOW,
    )


def _setup(clear_app_module, *, get=None):
    service = MagicMock()
    if get is None:
        service.get_job = AsyncMock(side_effect=LookupError("missing workload"))
        service.job_result = AsyncMock(side_effect=LookupError("missing workload"))
        service.cancel_job = AsyncMock(side_effect=LookupError("missing workload"))
    else:
        service.get_job = AsyncMock(return_value=get)
        state = str(getattr(get.state, "value", get.state))
        terminal = state in {"cancelled", "completed", "failed", "timed_out"}
        service.job_result = AsyncMock(
            return_value=JobResultPage(
                workload_id=get.id,
                plan_id=get.route_plan_id,
                state=state,
                provider_id=get.provider_id,
                result=get.result if terminal else None,
                available=terminal and get.result is not None,
                unavailable_reason=None if terminal else "job_not_terminal",
            )
        )
        service.cancel_job = AsyncMock(return_value=get)
    mod = build_app(pool=MagicMock(), production_routing_service=service)
    return mod, service


async def test_get_job_unknown_404(clear_app_module) -> None:
    mod, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.get("/v1/jobs/wkl_missing")
    assert resp.status_code == 404
    assert resp.json()["error"] == "workload_not_found"


async def test_get_status_unknown_404(clear_app_module) -> None:
    mod, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.get("/v1/jobs/wkl_missing/status")
    assert resp.status_code == 404


async def test_get_result_unknown_404(clear_app_module) -> None:
    mod, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.get("/v1/jobs/wkl_missing/result")
    assert resp.status_code == 404


async def test_get_result_not_ready_409(clear_app_module) -> None:
    mod, _ = _setup(clear_app_module, get=_workload(WorkloadState.RUNNING))
    async with client_for(mod) as client:
        resp = await client.get("/v1/jobs/wkl_x/result")
    assert resp.status_code == 409
    assert resp.json() == {"error": "job_not_ready", "id": "wkl_x", "state": "running"}


async def test_get_result_completed_200(clear_app_module) -> None:
    mod, _ = _setup(clear_app_module, get=_workload(WorkloadState.COMPLETED))
    async with client_for(mod) as client:
        resp = await client.get("/v1/jobs/wkl_x/result")
    assert resp.status_code == 200
    assert resp.json() == {
        "id": "wkl_x",
        "route_plan_id": None,
        "selected_provider_id": "prov_bge_m3",
        "result": {"ok": True},
        "available": True,
        "unavailable_reason": None,
    }


async def test_get_job_completed_200(clear_app_module) -> None:
    workload = _workload(WorkloadState.COMPLETED).model_copy(
        update={
            "cost_estimate_usd": Decimal("0.100000"),
            "cost_ceiling_usd": Decimal("0.600000"),
            "cost_quote": {
                "model": "per_unit",
                "components": [],
                "estimate": "0.100000",
                "ceiling": "0.600000",
                "confidence": "bounded",
                "provenance": "provider_config",
                "currency": "USD",
                "assumptions": [],
            },
            "cost_actual_usd": Decimal("0.250000"),
            "cost_actual_provenance": "provider_report",
            "cost_reconciled_at": _NOW,
        }
    )
    mod, _ = _setup(clear_app_module, get=workload)
    async with client_for(mod) as client:
        resp = await client.get("/v1/jobs/wkl_x")
    assert resp.status_code == 200
    assert resp.json()["cost"] == {
        "model": "per_unit",
        "components": [],
        "estimate": "0.100000",
        "ceiling": "0.600000",
        "confidence": "bounded",
        "provenance": "provider_config",
        "currency": "USD",
        "assumptions": [],
        "actual": "0.250000",
        "variance": "0.150000",
        "effective": "0.250000",
        "reconciliation_status": "reconciled",
        "actual_provenance": "provider_report",
        "actual_kind": "provider_reported",
        "reconciled_at": _NOW.isoformat(),
        "provider_invoice": False,
    }


async def test_cancel_unknown_404(clear_app_module) -> None:
    mod, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.post("/v1/jobs/wkl_missing/cancel")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("not_cancellable", 409, "job_not_cancellable"),
        ("provider_failed", 502, "job_cancel_failed"),
        ("provider_missing", 404, "provider_not_found"),
    ],
)
async def test_cancel_failures_keep_typed_codes_naming_the_right_resource(
    clear_app_module, failure: str, status: int, code: str
) -> None:
    from pitwall.resolver.exceptions import ProviderNotFoundError
    from pitwall.routing.production import JobNotCancellableError, RouteProviderInvocationError

    error = {
        "not_cancellable": JobNotCancellableError("workload is not an asynchronous job"),
        "provider_failed": RouteProviderInvocationError(),
        "provider_missing": ProviderNotFoundError("prov_gone"),
    }[failure]
    mod, service = _setup(clear_app_module, get=_workload(WorkloadState.RUNNING))
    service.cancel_job = AsyncMock(side_effect=error)
    async with client_for(mod) as client:
        resp = await client.post("/v1/jobs/wkl_x/cancel")
    assert resp.status_code == status
    body = resp.json()
    assert body["error"] == code
    assert "capability" not in body, "a workload id is never reported as a capability"
