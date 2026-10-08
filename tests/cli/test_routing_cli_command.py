"""Stable human/JSON and confirmation contracts for the ROUTE-01 CLI leaf."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.cli import routing as cli_routing
from pitwall.core.enums import WorkloadState
from pitwall.core.models import Workload
from pitwall.routing.production import JobResultPage, RouteGuardrailRejected

_NOW = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
_PLAN_ID = "plan_cccccccccccccccccccccccccccccccc"


class _Quote:
    def estimate(self) -> Decimal:
        return Decimal("0.01")

    def upper_bound(self) -> Decimal:
        return Decimal("0.01")


class _Candidate:
    rank = 1
    provider_id = "prov_cli"
    quote = _Quote()


class _Plan:
    plan_id = _PLAN_ID
    selected_provider_id = "prov_cli"
    selected = _Candidate()
    attempts = (_Candidate(),)

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "capability_name": "llm.cli",
            "selected_provider_id": self.selected_provider_id,
            "mode": "priority",
            "fallback_chain": [self.selected_provider_id],
        }


class _Service:
    def __init__(self, pool: object) -> None:
        self.pool = pool
        self.preview_calls = 0
        self.preview_requests: list[dict[str, Any]] = []
        self.submit_calls = 0

    async def preview(self, **request: Any) -> _Plan:
        self.preview_calls += 1
        self.preview_requests.append(request)
        return _Plan()

    async def submit_job(self, **_: Any) -> Workload:
        self.submit_calls += 1
        return Workload(
            id="wkl_cli",
            capability_id="cap_cli",
            provider_id="prov_cli",
            type="async_job",
            state=WorkloadState.QUEUED,
            submitted_at=_NOW,
            route_plan_id=_PLAN_ID,
            route_plan={"plan_id": _PLAN_ID},
        )

    async def cancel_job(self, workload_id: str) -> Workload:
        assert workload_id == "wkl_cli"
        return Workload(
            id=workload_id,
            capability_id="cap_cli",
            provider_id="prov_cli",
            type="async_job",
            state=WorkloadState.CANCELLED,
            submitted_at=_NOW,
            completed_at=_NOW,
            route_plan_id=_PLAN_ID,
            route_plan={"plan_id": _PLAN_ID},
        )

    async def get_job(self, workload_id: str) -> Workload:
        assert workload_id == "wkl_cli"
        return Workload(
            id=workload_id,
            capability_id="cap_cli",
            provider_id="prov_cli",
            type="async_job",
            state=WorkloadState.COMPLETED,
            submitted_at=_NOW,
            completed_at=_NOW,
            result={"ok": True},
            route_plan_id=_PLAN_ID,
            route_plan={"plan_id": _PLAN_ID},
        )

    async def job_result(self, workload_id: str) -> JobResultPage:
        assert workload_id == "wkl_cli"
        return JobResultPage(
            workload_id=workload_id,
            plan_id=_PLAN_ID,
            state="completed",
            result={"ok": True},
            available=True,
        )


class _FailingService(_Service):
    async def submit_job(self, **kwargs: Any) -> Workload:
        del kwargs
        raise RuntimeError("provider secret detail")


class _GuardrailService(_Service):
    async def preview(self, **kwargs: Any) -> _Plan:
        del kwargs
        raise RouteGuardrailRejected(("secret-canary-must-not-render",))


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> _Service:
    pool = object()

    async def get_pool() -> object:
        return pool

    monkeypatch.setattr(cli_routing, "get_pool", get_pool)
    created = _Service(pool)
    return created


def test_plan_json_is_stable_and_payload_free(
    service: _Service,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_routing.cmd_routing(
        ["plan", "llm.cli", "--input-json", '{"messages":[]}', "--json"],
        service_factory=lambda _: service,  # type: ignore[arg-type]  # reason: hermetic fake
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert f'"plan_id": "{_PLAN_ID}"' in output
    assert "messages" not in output
    assert service.preview_calls == 1
    assert service.preview_requests[0]["capability_id"] == "llm.cli"
    assert service.preview_requests[0]["payload"] == {"messages": []}
    assert service.preview_requests[0]["provider_id"] is None


def test_plan_forwards_explicit_provider_selector(service: _Service) -> None:
    rc = cli_routing.cmd_routing(
        ["plan", "llm.cli", "--provider-id", "prov_requested", "--json"],
        service_factory=lambda _: service,  # type: ignore[arg-type]  # reason: hermetic fake
    )
    assert rc == 0
    assert service.preview_calls == 1
    assert service.preview_requests[0]["provider_id"] == "prov_requested"


def test_submit_requires_exact_confirmation_before_service_call(
    service: _Service,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_routing.cmd_routing(
        ["submit", "llm.cli", "--input-json", "{}"],
        service_factory=lambda _: service,  # type: ignore[arg-type]  # reason: hermetic fake
    )

    assert rc == 2
    assert service.submit_calls == 0
    assert "--confirm must exactly match" in capsys.readouterr().err


def test_submit_and_cancel_return_plan_identity(
    service: _Service,
    capsys: pytest.CaptureFixture[str],
) -> None:
    submit_rc = cli_routing.cmd_routing(
        ["submit", "llm.cli", "--confirm", "llm.cli", "--json"],
        service_factory=lambda _: service,  # type: ignore[arg-type]  # reason: hermetic fake
    )
    submit_output = capsys.readouterr().out
    cancel_rc = cli_routing.cmd_routing(
        ["cancel", "wkl_cli", "--confirm", "wkl_cli", "--json"],
        service_factory=lambda _: service,  # type: ignore[arg-type]  # reason: hermetic fake
    )
    cancel_output = capsys.readouterr().out

    assert submit_rc == cancel_rc == 0
    assert f'"plan_id": "{_PLAN_ID}"' in submit_output
    assert '"state": "cancelled"' in cancel_output


@pytest.mark.parametrize("command", ["status", "result", "follow"])
def test_job_reads_use_shared_service_and_keep_plan_identity(
    command: str,
    service: _Service,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_routing.cmd_routing(
        [command, "wkl_cli", "--json"],
        service_factory=lambda _: service,  # type: ignore[arg-type]  # reason: hermetic fake
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert f'"plan_id": "{_PLAN_ID}"' in output


def test_provider_failure_detail_is_not_rendered(
    service: _Service,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing = _FailingService(service.pool)

    rc = cli_routing.cmd_routing(
        ["submit", "llm.cli", "--confirm", "llm.cli"],
        service_factory=lambda _: failing,  # type: ignore[arg-type]  # reason: hermetic fake
    )

    assert rc == 1
    rendered = capsys.readouterr().err
    assert "provider secret detail" not in rendered
    assert "routing operation failed (RuntimeError)" in rendered


def test_guardrail_denial_has_exact_stable_json_envelope(
    service: _Service,
    capsys: pytest.CaptureFixture[str],
) -> None:
    guarded = _GuardrailService(service.pool)

    rc = cli_routing.cmd_routing(
        ["plan", "llm.cli", "--input-json", "{}", "--json"],
        service_factory=lambda _: guarded,  # type: ignore[arg-type]  # reason: hermetic fake
    )

    assert rc == 2
    assert capsys.readouterr().out == '{\n  "error": "pre_spend_payload_rejected"\n}\n'
