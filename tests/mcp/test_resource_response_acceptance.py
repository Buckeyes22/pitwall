"""MCP mutation result/argument contracts; provider execution remains a separate lane."""

import pytest

from pitwall.runpod_control_plane import MutationResult
from tests.mcp.test_runpod_resource_gating import (
    _APPLY_HANDLERS,
    _apply_requests,
    _patch_hermetic,
)
from tests.runpod_control_plane.journal_fakes import recording_insert_audit

pytestmark = pytest.mark.anyio

_METHODS = [
    "update_pod",
    "action_pod",
    "terminate_pod",
    "create_endpoint",
    "update_endpoint",
    "delete_endpoint",
    "create_template",
    "update_template",
    "delete_template",
    "create_volume",
    "grow_volume",
    "delete_volume",
    "create_registry_auth",
    "replace_registry_auth",
    "delete_registry_auth",
]


@pytest.mark.parametrize(
    ("handler", "method", "payload"),
    list(zip(_APPLY_HANDLERS, _METHODS, _apply_requests(), strict=True)),
    ids=_METHODS,
)
@pytest.mark.parametrize("intent", ["preview", "apply"])
async def test_mutation_handler_preserves_request_and_result_fields(
    monkeypatch, handler, method, payload, intent
):
    payload = payload.model_copy(update={"intent": intent})
    dry_run = intent == "preview"
    already_absent = not dry_run and method.startswith(("delete_", "terminate_"))
    calls = []

    class Service:
        def __getattr__(self, name):
            async def execute(received):
                calls.append((name, received))
                return MutationResult(
                    operation="fixture.operation",
                    resource_type="fixture",
                    resource_id="selected_resource",
                    dry_run=dry_run,
                    changed=not dry_run and not already_absent,
                    already_absent=already_absent,
                    effect="fixture result",
                    idempotency_key=received.idempotency_key,
                    resource={"id": "selected_resource", "status": "fixture-status"},
                )

            return execute

    service_kinds = _patch_hermetic(monkeypatch, Service(), admit_calls=[])
    result = await handler(payload)
    assert len(calls) == 1 and calls[0][0] == method and calls[0][1] is payload
    assert service_kinds == [not dry_run]
    assert result["operation"] == "fixture.operation"
    assert result["resource_type"] == "fixture"
    assert result["resource_id"] == "selected_resource"
    assert result["dry_run"] is dry_run
    assert result["changed"] is (not dry_run and not already_absent)
    assert result["already_absent"] is already_absent
    assert result["effect"] == "fixture result"
    assert result["idempotency_key"] == payload.idempotency_key
    assert result["resource"] == {"id": "selected_resource", "status": "fixture-status"}


@pytest.mark.parametrize(
    ("plural", "singular", "model_name", "fields"),
    [
        ("pods", "pod", "PodResource", {"status": "RUNNING"}),
        (
            "endpoints",
            "endpoint",
            "EndpointResource",
            {
                "workers": {"minimum": 1, "maximum": 5, "idle_timeout_seconds": 90},
                "scaling": {"type": "REQUEST_COUNT", "value": 8.0},
                "flashboot": False,
            },
        ),
        (
            "templates",
            "template",
            "TemplateResource",
            {
                "image": "example/image:1",
                "disk_gb": 20,
                "volume_gb": 0,
                "serverless": True,
                "public": False,
            },
        ),
        ("volumes", "volume", "VolumeResource", {"size_gb": 80, "data_center_id": "fixture-dc"}),
        ("registry_auths", "registry_auth", "RegistryAuthResource", {}),
        (
            "hub_templates",
            "hub_template",
            "HubTemplateResource",
            {"image": "example/image:1", "serverless": True},
        ),
    ],
)
@pytest.mark.parametrize("detail", [False, True])
async def test_resource_read_handlers_preserve_typed_result_and_exact_id(
    monkeypatch, plural, singular, model_name, fields, detail
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pitwall import runpod_control_plane as models
    from pitwall.mcp.tools import runpod_resources

    model = getattr(models, model_name).model_validate(
        {"id": "selected_resource", "name": "selected fixture", **fields}
    )
    method = "get_" + singular if detail else "list_" + plural
    call = AsyncMock(return_value=model if detail else [model])
    service_factory = AsyncMock(return_value=SimpleNamespace(**{method: call}))
    monkeypatch.setattr(runpod_resources, "_service", service_factory)
    handler = getattr(runpod_resources, "pitwall_runpod_" + method)
    result = await handler("selected_resource") if detail else await handler()
    row = result if detail else result[plural][0]
    assert row["id"] == "selected_resource" and row["name"] == "selected fixture"
    for key, value in fields.items():
        assert row[key] == value
    service_factory.assert_awaited_once_with(mutation=False)
    if detail:
        call.assert_awaited_once_with("selected_resource")
    elif singular == "hub_template":
        call.assert_awaited_once_with(limit=50, offset=0)
    else:
        call.assert_awaited_once_with()


@pytest.mark.parametrize("size_gb", [10, 20, 40])
async def test_volume_grow_handler_uses_real_service_and_rejects_non_growth(monkeypatch, size_gb):

    from mcp.shared.exceptions import MCPError

    from pitwall.mcp.tools import runpod_resources
    from pitwall.runpod_control_plane import VolumeGrowRequest
    from tests.runpod_control_plane.backend_test_support import RecordingBackend
    from tests.runpod_control_plane.backend_test_support import control_plane_service as _service

    backend = RecordingBackend()
    service = _service(backend)
    audit: list[dict[str, object]] = []
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit(audit))
    _patch_hermetic(monkeypatch, service, admit_calls=[])
    payload = VolumeGrowRequest(
        intent="apply",
        idempotency_key=f"mcp-grow-{size_gb}-001",
        resource_id="vol_one",
        size_gb=size_gb,
    )
    if size_gb <= 20:
        with pytest.raises(MCPError) as raised:
            await runpod_resources.pitwall_runpod_grow_volume(payload)
        assert raised.value.error.data["error"] == "volume_grow_only"
        assert "volumes.grow" not in backend.calls
        assert audit == []
    else:
        result = await runpod_resources.pitwall_runpod_grow_volume(payload)
        assert result["changed"] is True and result["dry_run"] is False
        assert result["resource_id"] == "vol_one"
        assert result["resource"]["size_gb"] == 40
        assert backend.calls == ["volumes.get", "volumes.grow"]
        assert len(audit) == 1


@pytest.mark.parametrize(
    ("method", "backend_write", "resource_id"),
    [
        ("terminate_pod", "pods.terminate", "pod_one"),
        ("delete_endpoint", "endpoints.delete", "ep_one"),
        ("delete_template", "templates.delete", "tpl_one"),
        ("delete_volume", "volumes.delete", "vol_one"),
        ("delete_registry_auth", "registry.delete", "auth_one"),
    ],
)
async def test_delete_replay_crosses_tool_and_real_service_once(
    monkeypatch, method, backend_write, resource_id
):

    from pitwall.mcp.tools import runpod_resources
    from pitwall.runpod_control_plane import IdentifiedMutationRequest
    from tests.runpod_control_plane.backend_test_support import RecordingBackend
    from tests.runpod_control_plane.backend_test_support import control_plane_service as _service

    backend = RecordingBackend()
    service = _service(backend)
    audit: list[dict[str, object]] = []
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit(audit))
    _patch_hermetic(monkeypatch, service, admit_calls=[])
    payload = IdentifiedMutationRequest(
        intent="apply", idempotency_key="mcp-delete-repeat-001", resource_id=resource_id
    )
    handler = getattr(runpod_resources, "pitwall_runpod_" + method)
    first = await handler(payload)
    replayed = await handler(payload)
    again = await handler(payload.model_copy(update={"idempotency_key": "mcp-delete-again-001"}))
    assert first["changed"] is True and first["resource_id"] == resource_id
    assert replayed == {**first, "replayed": True}
    assert again["already_absent"] is True and again["changed"] is False
    assert backend.calls.count(backend_write) == 1
    assert len(audit) == 1


@pytest.mark.parametrize(
    ("resource", "present_field"),
    [
        ("pod", "pod_present"),
        ("endpoint", "endpoint_present"),
        ("template", "template_present"),
        ("volume", "volume_present"),
        ("registry_auth", "registry_present"),
    ],
)
async def test_missing_resource_crosses_tool_boundary(monkeypatch, resource, present_field):
    from mcp.shared.exceptions import MCPError

    from pitwall.mcp.tools import runpod_resources
    from tests.runpod_control_plane.backend_test_support import RecordingBackend
    from tests.runpod_control_plane.backend_test_support import control_plane_service as _service

    backend = RecordingBackend()
    setattr(backend, present_field, False)
    _patch_hermetic(monkeypatch, _service(backend), admit_calls=[])
    handler = getattr(runpod_resources, "pitwall_runpod_get_" + resource)
    with pytest.raises(MCPError) as raised:
        await handler("missing_resource")
    assert raised.value.error.data["error"] == "resource_not_found"
    assert not any(call.endswith((".delete", ".terminate", ".create")) for call in backend.calls)


async def test_hub_search_forwards_query_limit_and_empty_result(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from pitwall.mcp.tools import runpod_resources
    from pitwall.runpod_control_plane import HubTemplateResource

    model = HubTemplateResource(
        id="hub_match", name="matched", image="example/worker:1", serverless=True
    )
    call = AsyncMock(side_effect=[[model], []])
    factory = AsyncMock(return_value=SimpleNamespace(search_hub_templates=call))
    monkeypatch.setattr(runpod_resources, "_service", factory)
    result = await runpod_resources.pitwall_runpod_search_hub_templates("exact phrase", limit=7)
    assert result == {"hub_templates": [model.model_dump(mode="json")]}
    call.assert_awaited_once_with("exact phrase", limit=7)
    result = await runpod_resources.pitwall_runpod_search_hub_templates("absent", limit=1)
    assert result == {"hub_templates": []}
    assert call.await_args.args == ("absent",) and call.await_args.kwargs == {"limit": 1}
    assert all(item.kwargs == {"mutation": False} for item in factory.await_args_list)


async def test_registry_replacement_partial_failure_is_reported_without_secret(monkeypatch):

    from mcp.shared.exceptions import MCPError

    from pitwall.mcp.tools import runpod_resources
    from pitwall.runpod_control_plane import RegistryAuthReplaceRequest
    from tests.runpod_control_plane.backend_test_support import RecordingBackend
    from tests.runpod_control_plane.backend_test_support import control_plane_service as _service

    backend = RecordingBackend()
    backend.fail_registry_create = True
    audit: list[dict[str, object]] = []
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit(audit))
    _patch_hermetic(monkeypatch, _service(backend), admit_calls=[])
    payload = RegistryAuthReplaceRequest(
        intent="apply",
        idempotency_key="mcp-registry-replace-001",
        resource_id="auth_one",
        name="replacement",
        username="robot",
        password_env="REGISTRY_PASSWORD",
    )
    with pytest.raises(MCPError) as raised:
        await runpod_resources.pitwall_runpod_replace_registry_auth(payload)
    data = raised.value.error.data
    assert data["error"] == "registry_replace_partial_failure"
    assert data["changed"] is True
    assert "super-secret" not in str(raised.value)
    assert backend.calls.index("registry.delete") < backend.calls.index("registry.create")
    assert backend.registry_present is False
    assert len(audit) == 1
