from __future__ import annotations

import json
from pathlib import Path
from typing import get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.shared.exceptions import MCPError

from pitwall.api.exceptions import install_api_error_handler
from pitwall.api.routes.onboarding import router
from pitwall.cli.onboarding import cmd_runpod_onboard
from pitwall.mcp import onboarding_specs
from pitwall.mcp.tools import onboarding as mcp_onboarding
from pitwall.onboarding import (
    OnboardingAction,
    OnboardingCommand,
    OnboardingStatus,
    RunPodOnboardingRequest,
    RunPodOnboardingService,
)
from tests.onboarding.test_service import _endpoint_request, _service


def _payload() -> dict[str, object]:
    return _endpoint_request().model_dump(mode="json")


@pytest.mark.asyncio
async def test_mcp_five_tools_share_one_service_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _, _ = _service()

    async def factory() -> RunPodOnboardingService:
        return service

    monkeypatch.setattr(mcp_onboarding, "get_runpod_onboarding_service", factory)
    plan = await mcp_onboarding.pitwall_runpod_onboarding_plan(_payload())
    status = await mcp_onboarding.pitwall_runpod_onboarding_status(_payload())
    rollback = await mcp_onboarding.pitwall_runpod_onboarding_rollback(_payload())
    applied = await mcp_onboarding.pitwall_runpod_onboarding_apply(_payload(), str(plan["plan_id"]))
    resumed = await mcp_onboarding.pitwall_runpod_onboarding_resume(
        _payload(), str(plan["plan_id"])
    )

    assert plan["action"] == "plan"
    assert plan["zero_write"] is True
    assert status["action"] == "status"
    assert rollback["action"] == "rollback"
    assert applied["status"] == "complete"
    assert resumed["status"] == "complete"
    assert {item.name for item in onboarding_specs.ONBOARDING_TOOL_SPECS} == {
        "pitwall_runpod_onboarding_plan",
        "pitwall_runpod_onboarding_apply",
        "pitwall_runpod_onboarding_status",
        "pitwall_runpod_onboarding_resume",
        "pitwall_runpod_onboarding_rollback",
    }
    assert (
        get_type_hints(mcp_onboarding.pitwall_runpod_onboarding_plan)["request"]
        is RunPodOnboardingRequest
    )


@pytest.mark.asyncio
async def test_mcp_errors_are_bounded_and_non_disclosing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, _, _ = _service()
    state.fail_at = "provider"

    async def factory() -> RunPodOnboardingService:
        return service

    monkeypatch.setattr(mcp_onboarding, "get_runpod_onboarding_service", factory)
    plan = await mcp_onboarding.pitwall_runpod_onboarding_plan(_payload())
    with pytest.raises(MCPError) as caught:
        await mcp_onboarding.pitwall_runpod_onboarding_apply(_payload(), str(plan["plan_id"]))
    assert "secret-provider-detail" not in str(caught.value.error.data)

    with pytest.raises(MCPError) as invalid:
        await mcp_onboarding.pitwall_runpod_onboarding_plan({"credential_ref": "literal"})
    assert invalid.value.error.data == {"error": "runpod_onboarding_invalid_request"}


@pytest.mark.asyncio
async def test_mcp_cleanup_failure_cannot_override_safe_result_or_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, state, _, _ = _service()

    async def factory() -> RunPodOnboardingService:
        return service

    async def broken_close() -> None:
        raise RuntimeError("Authorization: Bearer cleanup-canary")

    monkeypatch.setattr(service, "aclose", broken_close)
    monkeypatch.setattr(mcp_onboarding, "get_runpod_onboarding_service", factory)
    plan = await mcp_onboarding.pitwall_runpod_onboarding_plan(_payload())
    assert plan["action"] == "plan"

    state.fail_at = "provider"
    with pytest.raises(MCPError) as caught:
        await mcp_onboarding.pitwall_runpod_onboarding_apply(_payload(), str(plan["plan_id"]))
    assert "cleanup-canary" not in str(caught.value)
    assert caught.value.error.message == "RunPod onboarding failed safely"


def test_rest_five_routes_have_semantic_parity_and_stable_schema() -> None:
    service, _, _, _ = _service()
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.state.runpod_onboarding_service = service
    client = TestClient(app)

    plan = client.post("/v1/admin/runpod/onboarding/plan", json=_payload())
    assert plan.status_code == 200
    plan_body = plan.json()
    assert plan_body["action"] == "plan"
    assert plan_body["zero_write"] is True

    status = client.post("/v1/admin/runpod/onboarding/status", json=_payload())
    rollback = client.post("/v1/admin/runpod/onboarding/rollback", json=_payload())
    apply = client.post(
        "/v1/admin/runpod/onboarding/apply",
        json={
            "action": "apply",
            "request": _payload(),
            "confirmed_plan_id": plan_body["plan_id"],
        },
    )
    resume = client.post(
        "/v1/admin/runpod/onboarding/resume",
        json={
            "action": "resume",
            "request": _payload(),
            "confirmed_plan_id": plan_body["plan_id"],
        },
    )

    assert status.json()["action"] == "status"
    assert rollback.json()["action"] == "rollback"
    assert apply.json()["status"] == "complete"
    assert resume.json()["status"] == "complete"
    schema = app.openapi()
    assert set(schema["paths"]) == {
        "/v1/admin/runpod/onboarding/plan",
        "/v1/admin/runpod/onboarding/apply",
        "/v1/admin/runpod/onboarding/status",
        "/v1/admin/runpod/onboarding/resume",
        "/v1/admin/runpod/onboarding/rollback",
    }


def test_rest_rejects_wrong_route_action_without_writing() -> None:
    service, state, resources, _ = _service()
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.state.runpod_onboarding_service = service
    client = TestClient(app)
    plan = client.post("/v1/admin/runpod/onboarding/plan", json=_payload()).json()

    response = client.post(
        "/v1/admin/runpod/onboarding/apply",
        json={
            "action": "resume",
            "request": _payload(),
            "confirmed_plan_id": plan["plan_id"],
        },
    )

    assert response.status_code == 422
    assert not [item for item in resources.ledger if item.startswith("write:")]
    assert not state.ledger


def test_rest_validation_does_not_reflect_request_values() -> None:
    service, _, _, _ = _service()
    app = FastAPI()
    app.include_router(router)
    install_api_error_handler(app)
    app.state.runpod_onboarding_service = service
    response = TestClient(app).post(
        "/v1/admin/runpod/onboarding/plan",
        json={"api_key": "rest-secret-canary"},
    )

    assert response.status_code == 422
    assert "rest-secret-canary" not in response.text
    assert response.json()["error"] == "invalid_request"


def _write_request(path: Path) -> None:
    path.write_text(json.dumps(_payload()), encoding="utf-8")


def test_cli_defaults_to_plan_then_applies_confirmed_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _, _, _ = _service()

    async def factory() -> RunPodOnboardingService:
        return service

    path = tmp_path / "onboarding.json"
    _write_request(path)
    assert cmd_runpod_onboard([str(path), "--json"], service_factory=factory) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["action"] == "plan"
    assert plan["zero_write"] is True

    assert (
        cmd_runpod_onboard(
            [
                str(path),
                "--action",
                "apply",
                "--confirmed-plan-id",
                plan["plan_id"],
                "--json",
            ],
            service_factory=factory,
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "complete"


def test_cli_human_errors_and_exit_codes_are_stable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")
    assert cmd_runpod_onboard([str(invalid)]) == 2
    assert "invalid" in capsys.readouterr().err.lower()

    service, _, _, _ = _service()

    async def factory() -> RunPodOnboardingService:
        return service

    valid = tmp_path / "valid.json"
    _write_request(valid)
    code = cmd_runpod_onboard(
        [
            str(valid),
            "--action",
            "apply",
            "--confirmed-plan-id",
            "runpod_onboard_" + "0" * 24,
            "--json",
        ],
        service_factory=factory,
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["error"] == "plan_confirmation_mismatch"
    assert "never-render" not in json.dumps(payload)


def test_cli_human_plan_renders_steps_cost_and_rollback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _, _, _ = _service()

    async def factory() -> RunPodOnboardingService:
        return service

    path = tmp_path / "human.json"
    _write_request(path)
    assert cmd_runpod_onboard([str(path)], service_factory=factory) == 0
    rendered = capsys.readouterr().out
    assert "RunPod onboarding" in rendered
    assert "Steps" in rendered
    assert "Endpoint hourly range" in rendered
    assert "Provider resources" in rendered
    assert "planned_template_" in rendered
    assert "Database mutations" in rendered
    assert "pitwall.config_audit" in rendered
    assert "Existing resource reuse: none" in rendered
    assert "Writes" in rendered
    assert "Reused" in rendered
    assert "Rollback guidance" in rendered


def test_surface_model_is_the_service_model_not_a_transport_copy() -> None:
    request = _endpoint_request()
    assert (
        OnboardingCommand(
            action=OnboardingAction.PLAN,
            request=request,
        ).request
        is request
    )
    assert OnboardingStatus.COMPLETE.value == "complete"
