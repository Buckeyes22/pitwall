"""Route- and surface-specific zero-egress evidence for GOV-04."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitwall.cli import routing as cli_routing
from pitwall.cli.routing import cmd_routing
from pitwall.core.enums import ProviderAdapterId
from pitwall.providers.registry import ProviderRegistry
from pitwall.tui.routing_jobs import (
    ProductionRoutingJobsSource,
    RoutingJobAction,
    RoutingJobCommand,
    RoutingJobsError,
)
from tests.api._contract_helpers import build_app, client_for, override
from tests.routing.test_production_routing import _FakeAdapter, _provider, _service

_SECRET_PAYLOAD = {"api_key": "".join(("sk-", "abcdefghijklmnop", "12345678"))}


def _guarded_service():
    adapter = _FakeAdapter("runpod")
    registry = ProviderRegistry()
    registry.register(adapter)
    service, budget, _ = _service(
        [_provider("provider_runpod", ProviderAdapterId.RUNPOD, priority=1, latency_ms=10)],
        registry,
    )
    return service, budget, adapter


@pytest.mark.anyio
async def test_rest_inference_and_job_denial_have_zero_budget_and_provider_calls(
    clear_app_module: None,
) -> None:
    service, budget, adapter = _guarded_service()
    mod = build_app(pool=object())
    from pitwall.api.routes.inference import _routing_service
    from pitwall.api.routes.routing import _service as routing_service

    override(mod, _routing_service, service)
    override(mod, routing_service, service)

    async with client_for(mod) as client:
        inference = await client.post(
            "/v1/inference",
            json={"capability_id": "llm.chat", **_SECRET_PAYLOAD},
        )
        job = await client.post(
            "/v1/jobs",
            json={"capability_id": "llm.chat", "input": _SECRET_PAYLOAD},
        )

    assert inference.status_code == 422
    assert inference.json()["error"] == "pre_spend_payload_rejected"
    assert job.status_code == 422
    assert job.json()["error"] == "pre_spend_payload_rejected"
    assert budget.admissions == 0
    assert adapter.sync_calls == 0
    assert adapter.submit_calls == 0


def test_cli_submission_denial_has_zero_budget_and_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, budget, adapter = _guarded_service()

    async def pool() -> object:
        return object()

    monkeypatch.setattr(cli_routing, "get_pool", pool)
    result = cmd_routing(
        [
            "submit",
            "llm.chat",
            "--input-json",
            json.dumps(_SECRET_PAYLOAD),
            "--confirm",
            "llm.chat",
            "--json",
        ],
        service_factory=lambda _pool: service,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "sk-" not in captured.out + captured.err
    assert budget.admissions == 0
    assert adapter.submit_calls == 0


@pytest.mark.anyio
async def test_tui_submission_denial_has_zero_budget_and_provider_calls() -> None:
    service, budget, adapter = _guarded_service()

    async def factory() -> Any:
        return service

    source = ProductionRoutingJobsSource(factory)
    with pytest.raises(RoutingJobsError, match="pre spend payload rejected"):
        await source.execute_routing_job(
            RoutingJobCommand(
                action=RoutingJobAction.SUBMIT,
                capability_id="llm.chat",
                payload=_SECRET_PAYLOAD,
                confirmation="llm.chat",
            )
        )

    assert budget.admissions == 0
    assert adapter.submit_calls == 0
