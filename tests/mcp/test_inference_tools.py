"""MCP inference tools use the shared production-routing lifecycle."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from pitwall.api.exceptions import JobNotCancellable, PreSpendPayloadRejected, WorkloadNotFound
from pitwall.core.enums import WorkloadState
from pitwall.core.models import Workload
from pitwall.mcp.tools import inference as inference_tools
from pitwall.mcp.tools.inference import _canonical_json
from pitwall.routing.production import JobResultPage, RouteExecutionResult
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 1, 16, 0, tzinfo=dt.UTC)
_PLAN_ID = "plan_0123456789abcdef0123456789abcdef"


class _Quote:
    def to_serializable_dict(self) -> dict[str, object]:
        return {"model": "per_request", "estimate": "0.01", "ceiling": "0.01"}


@dataclass(frozen=True)
class _Candidate:
    provider_id: str = "prov_mcp"
    quote: _Quote = _Quote()


class _Plan:
    plan_id = _PLAN_ID
    selected_provider_id = "prov_mcp"
    selected = _Candidate()

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "capability_name": "embedding.test",
            "selected_provider_id": self.selected_provider_id,
            "fallback_chain": [self.selected_provider_id],
        }


def _workload(
    *,
    state: WorkloadState = WorkloadState.QUEUED,
    result: dict[str, Any] | None = None,
) -> Workload:
    return Workload(
        id="wkl_mcp",
        capability_id="cap_mcp",
        provider_id="prov_mcp",
        type="async_job",
        state=state,
        external_job_id="external_mcp",
        submitted_at=_NOW,
        completed_at=_NOW if state in {WorkloadState.COMPLETED, WorkloadState.CANCELLED} else None,
        cost_estimate_usd=Decimal("0.01"),
        result=result,
        route_plan_id=_PLAN_ID,
        route_plan=_Plan().to_dict(),
    )


class _Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def preview_prepared(self, **kwargs: Any) -> _Plan:
        self.calls.append(("preview_prepared", kwargs))
        return _Plan()

    async def execute_sync_prepared(self, **kwargs: Any) -> RouteExecutionResult:
        self.calls.append(("execute_sync_prepared", kwargs))
        workload = _workload(state=WorkloadState.COMPLETED, result={"dense": [[0.1]]})
        return RouteExecutionResult(workload=workload, plan=_Plan(), output=workload.result)

    async def submit_job_prepared(self, **kwargs: Any) -> Workload:
        self.calls.append(("submit_job_prepared", kwargs))
        return _workload()

    async def get_job(self, workload_id: str) -> Workload:
        self.calls.append(("get_job", {"workload_id": workload_id}))
        if workload_id == "missing":
            raise LookupError(workload_id)
        return _workload(state=WorkloadState.COMPLETED, result={"ok": True})

    async def job_result(self, workload_id: str) -> JobResultPage:
        self.calls.append(("job_result", {"workload_id": workload_id}))
        if workload_id == "missing":
            raise LookupError(workload_id)
        return JobResultPage(
            workload_id=workload_id,
            plan_id=_PLAN_ID,
            state="completed",
            provider_id="prov_mcp",
            external_job_id="external_mcp",
            plan=_Plan().to_dict(),
            cost_estimate_usd=Decimal("0.01"),
            result={"ok": True},
            available=True,
        )

    async def cancel_job(self, workload_id: str) -> Workload:
        self.calls.append(("cancel_job", {"workload_id": workload_id}))
        if workload_id == "missing":
            raise LookupError(workload_id)
        return _workload(state=WorkloadState.CANCELLED)


def _install_service(monkeypatch: pytest.MonkeyPatch, service: _Service) -> None:
    async def factory() -> _Service:
        return service

    monkeypatch.setattr(inference_tools, "get_production_routing_service", factory)


async def test_submit_inference_guardrail_blocks_before_service_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    factory_called = False

    async def factory() -> _Service:
        nonlocal factory_called
        factory_called = True
        return _Service()

    monkeypatch.setattr(inference_tools, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(inference_tools, "get_production_routing_service", factory)

    with pytest.raises(PreSpendPayloadRejected):
        await inference_tools.pitwall_submit_inference(
            "embedding.test",
            {"texts": ["use sk-abcdefghijklmnopqrstuvwxyz123456"]},
        )

    assert factory_called is False
    assert guardrail.status().counters.total == 1


async def test_submit_inference_redacts_then_delegates_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Service()
    guardrail = PreSpendInspectionService()
    _install_service(monkeypatch, service)
    monkeypatch.setattr(inference_tools, "get_pre_spend_inspection_service", lambda: guardrail)

    result = await inference_tools.pitwall_submit_inference(
        "embedding.test",
        {"texts": ["contact ada.lovelace@example.com"]},
        idempotency_key="mcp-sync-1",
    )

    assert result["plan_id"] == _PLAN_ID
    assert result["result"] == {"dense": [[0.1]]}
    assert service.calls == [
        (
            "execute_sync_prepared",
            {
                "capability_id": "embedding.test",
                "prepared_payload": service.calls[0][1]["prepared_payload"],
                "provider_id": None,
                "idempotency_key": "mcp-sync-1",
            },
        )
    ]
    prepared = service.calls[0][1]["prepared_payload"]
    assert prepared.payload == {"texts": ["contact [REDACTED:email]"]}
    assert guardrail.status().counters.total == 1


async def test_sync_dry_run_uses_same_plan_shape_without_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Service()
    _install_service(monkeypatch, service)

    result = await inference_tools.pitwall_submit_inference(
        "embedding.test", {"texts": ["hello"]}, dry_run=True
    )

    assert result["plan_id"] == _PLAN_ID
    assert result["provider_id"] == "prov_mcp"
    assert result["result"] == {"dry_run": True}
    assert service.calls[0][0] == "preview_prepared"


async def test_submit_job_guardrail_blocks_webhook_before_service_or_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    factory_called = False
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    async def factory() -> _Service:
        nonlocal factory_called
        factory_called = True
        return _Service()

    monkeypatch.setattr(inference_tools, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(inference_tools, "get_production_routing_service", factory)

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await inference_tools.pitwall_submit_job(
            "embedding.test",
            {"texts": ["hello"]},
            webhook_url=f"https://hooks.example.test/result?token={secret}",
        )

    assert factory_called is False
    assert secret not in repr(exc_info.value.to_response_body())
    assert guardrail.status().counters.total == 1


async def test_submit_job_preserves_shared_service_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Service()
    _install_service(monkeypatch, service)

    result = await inference_tools.pitwall_submit_job(
        "embedding.test",
        {"texts": ["hello"]},
        provider_id="prov_mcp",
        idempotency_key="mcp-async-1",
        webhook_url="https://hooks.example.test/result",
    )

    assert result["workload_id"] == "wkl_mcp"
    assert result["plan_id"] == _PLAN_ID
    assert service.calls == [
        (
            "submit_job_prepared",
            {
                "capability_id": "embedding.test",
                "prepared_payload": service.calls[0][1]["prepared_payload"],
                "provider_id": "prov_mcp",
                "idempotency_key": "mcp-async-1",
                "webhook_url": "https://hooks.example.test/result",
            },
        )
    ]
    assert service.calls[0][1]["prepared_payload"].payload == {"texts": ["hello"]}


async def test_status_result_and_cancel_share_service_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _Service()
    _install_service(monkeypatch, service)

    status = await inference_tools.pitwall_get_job_status("wkl_mcp")
    result = await inference_tools.pitwall_get_job_result("wkl_mcp")
    cancelled = await inference_tools.pitwall_cancel_job("wkl_mcp")

    assert status["state"] == "completed"
    assert result["result"] == {"ok": True}
    assert set(result) == {
        "workload_id",
        "cost",
        "provider_id",
        "external_job_id",
        "plan_id",
        "plan",
        "state",
        "result",
        "trace_id",
        "available",
        "unavailable_reason",
    }
    assert result["cost"] == {"estimate_usd": "0.01", "actual_usd": None}
    assert result["provider_id"] == "prov_mcp"
    assert result["external_job_id"] == "external_mcp"
    assert cancelled["cancelled"] is True
    assert [name for name, _ in service.calls] == ["get_job", "job_result", "cancel_job"]


@pytest.mark.parametrize(
    "handler",
    [
        inference_tools.pitwall_get_job_status,
        inference_tools.pitwall_get_job_result,
        inference_tools.pitwall_cancel_job,
    ],
)
async def test_lifecycle_missing_workload_is_transport_neutral(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    _install_service(monkeypatch, _Service())

    with pytest.raises(WorkloadNotFound):
        await handler("missing")


def test_canonical_json_is_byte_stable() -> None:
    assert _canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


@pytest.mark.anyio
async def test_cancel_job_failures_keep_the_rest_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.routing.production import JobNotCancellableError

    class _Service:
        async def cancel_job(self, workload_id: str) -> None:
            raise JobNotCancellableError("workload is not an asynchronous job")

    async def factory() -> _Service:
        return _Service()

    monkeypatch.setattr(inference_tools, "get_production_routing_service", factory)

    with pytest.raises(JobNotCancellable) as raised:
        await inference_tools.pitwall_cancel_job("wkl_sync")
    assert raised.value.error_code == "job_not_cancellable"
