"""Hermetic contract tests for the shared RunPod control-plane service."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from pitwall.runpod_control_plane import (
    CREATE_ATTEMPT_ENV,
    EndpointCreateRequest,
    EndpointUpdateRequest,
    IdentifiedMutationRequest,
    PodActionRequest,
    PodCreateRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    StrictRunPodBackend,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
    create_attempt_marker,
    journal_lock_name,
)
from pitwall.security.pre_spend import PreSpendInspectionService
from tests.runpod_control_plane.backend_test_support import (
    RecordingBackend,
)
from tests.runpod_control_plane.backend_test_support import (
    control_plane_service as _service,
)
from tests.runpod_control_plane.backend_test_support import (
    idempotency_key_for as _key,
)
from tests.runpod_control_plane.journal_fakes import (
    JOURNAL_KIND,
    FakeJournalPool,
    recording_insert_audit,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit(calls))
    return calls


async def test_every_supported_resource_operation_reaches_the_explicit_backend_method(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    inspection_service = PreSpendInspectionService()
    service = _service(backend, inspection_service=inspection_service)

    await service.list_pods()
    await service.get_pod("pod_one")
    await service.create_pod(
        PodCreateRequest(
            intent="apply",
            idempotency_key=_key("pod-create"),
            name="new-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    )
    await service.update_pod(
        PodUpdateRequest(
            intent="apply",
            idempotency_key=_key("pod-update"),
            resource_id="pod_one",
            ports=["8000/http"],
        )
    )
    await service.action_pod(
        PodActionRequest(
            intent="apply",
            idempotency_key=_key("pod-action"),
            resource_id="pod_one",
            action="restart",
        )
    )
    await service.terminate_pod(
        IdentifiedMutationRequest(
            intent="apply",
            idempotency_key=_key("pod-terminate"),
            resource_id="pod_one",
        )
    )

    await service.list_endpoints()
    await service.get_endpoint("ep_one")
    resolved_gpu = await service.resolve_endpoint_gpu_types(["NVIDIA L4"], gpu_count=2)
    assert resolved_gpu.pools == ["ADA_24"]
    assert resolved_gpu.excluded_type_ids == ["GPU-X"]
    assert resolved_gpu.count == 2
    endpoint_create = EndpointCreateRequest.model_validate(
        {
            "intent": "apply",
            "idempotency_key": _key("endpoint-create"),
            "name": "new-endpoint",
            "template_id": "tmpl_one",
            "gpu": {"pools": ["AMPERE_24"], "excluded_type_ids": ["GPU-X"]},
        }
    )
    created_endpoint = await service.create_endpoint(endpoint_create)
    assert created_endpoint.resource is not None
    assert created_endpoint.resource["gpu_pools"] == ["AMPERE_24"]
    await service.update_endpoint(
        EndpointUpdateRequest.model_validate(
            {
                "intent": "apply",
                "idempotency_key": _key("endpoint-update"),
                "resource_id": "ep_one",
                "workers": {"minimum": 1, "maximum": 4, "idle_timeout_seconds": 120},
                "scaling": {"type": "REQUEST_COUNT", "value": 2},
                "gpu": {"pools": ["ADA_24"], "excluded_type_ids": ["GPU-Y"]},
            }
        )
    )
    await service.delete_endpoint(
        IdentifiedMutationRequest(
            intent="apply",
            idempotency_key=_key("endpoint-delete"),
            resource_id="ep_one",
        )
    )

    listed_templates = await service.list_templates()
    assert listed_templates[0].env_keys == ("SAFE",)
    assert "not-returned" not in repr([item.model_dump(mode="json") for item in listed_templates])
    assert "arg-secret" not in repr([item.model_dump(mode="json") for item in listed_templates])
    await service.get_template("tmpl_one")
    await service.create_template(
        TemplateCreateRequest(
            intent="apply",
            idempotency_key=_key("template-create"),
            name="new-template",
            image="example/image:1",
        )
    )
    await service.update_template(
        TemplateUpdateRequest(
            intent="apply",
            idempotency_key=_key("template-update"),
            resource_id="tmpl_one",
            name="updated-template",
        )
    )
    await service.delete_template(
        IdentifiedMutationRequest(
            intent="apply",
            idempotency_key=_key("template-delete"),
            resource_id="tmpl_one",
        )
    )

    await service.list_volumes()
    await service.get_volume("vol_one")
    await service.create_volume(
        VolumeCreateRequest(
            intent="apply",
            idempotency_key=_key("volume-create"),
            name="new-volume",
            size_gb=20,
            data_center_id="US-KS-1",
        )
    )
    await service.grow_volume(
        VolumeGrowRequest(
            intent="apply",
            idempotency_key=_key("volume-grow"),
            resource_id="vol_one",
            size_gb=40,
        )
    )
    await service.delete_volume(
        IdentifiedMutationRequest(
            intent="apply",
            idempotency_key=_key("volume-delete"),
            resource_id="vol_one",
        )
    )

    await service.list_registry_auths()
    await service.get_registry_auth("auth_one")
    await service.create_registry_auth(
        RegistryAuthCreateRequest(
            intent="apply",
            idempotency_key=_key("registry-create"),
            name="new-auth",
            username="robot",
            password_env="REGISTRY_PASSWORD",
        )
    )
    backend.registry_present = True
    await service.replace_registry_auth(
        RegistryAuthReplaceRequest(
            intent="apply",
            idempotency_key=_key("registry-replace"),
            resource_id="auth_one",
            name="replaced-auth",
            username="robot",
            password_env="REGISTRY_PASSWORD",
        )
    )
    backend.registry_present = True
    await service.delete_registry_auth(
        IdentifiedMutationRequest(
            intent="apply",
            idempotency_key=_key("registry-delete"),
            resource_id="auth_one",
        )
    )
    await service.list_hub_templates()
    await service.get_hub_template("hub_one")
    matches = await service.search_hub_templates("searchable")
    assert [item.id for item in matches] == ["hub_one"]

    expected_writes = {
        "pods.create",
        "pods.update",
        "pods.restart",
        "pods.terminate",
        "endpoints.create",
        "endpoints.update",
        "endpoints.delete",
        "templates.create",
        "templates.update",
        "templates.delete",
        "volumes.create",
        "volumes.grow",
        "volumes.delete",
        "registry.create",
        "registry.delete",
    }
    assert expected_writes <= set(backend.calls)
    assert len(audit_calls) == 17
    assert inspection_service.status().counters.total == 16
    assert inspection_service.status().counters.allow == 16
    serialized_audit = repr(audit_calls)
    assert "super-secret" not in serialized_audit
    assert "not-returned" not in serialized_audit


async def test_dry_run_requires_no_audit_store_and_performs_no_provider_write() -> None:
    backend = RecordingBackend()
    inspection_service = PreSpendInspectionService()
    service = _service(backend, audit=False, inspection_service=inspection_service)

    result = await service.create_pod(
        PodCreateRequest(
            intent="preview",
            idempotency_key=_key("preview"),
            name="preview-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    )

    assert result.dry_run
    assert not result.changed
    assert backend.calls == []
    assert inspection_service.status().counters.total == 0


@pytest.mark.parametrize(
    "mutation_request,invoke",
    [
        (
            PodCreateRequest(
                intent="apply",
                idempotency_key=_key("guard-pod"),
                name="guarded-pod",
                image="example/image:1",
                gpu_type_ids=["NVIDIA L4"],
                ttl_minutes=60,
                env={"NORMAL": "sk-1234567890abcdef1234567890abcdef"},
            ),
            lambda service, request: service.create_pod(request),
        ),
        (
            TemplateUpdateRequest(
                intent="apply",
                idempotency_key=_key("guard-template"),
                resource_id="tmpl_one",
                args="notify jane.roe@example.com",
            ),
            lambda service, request: service.update_template(request),
        ),
        (
            RegistryAuthCreateRequest(
                intent="apply",
                idempotency_key=_key("guard-registry"),
                name="guarded-auth",
                username="sk-1234567890abcdef1234567890abcdef",
                password_env="REGISTRY_PASSWORD",
            ),
            lambda service, request: service.create_registry_auth(request),
        ),
        (
            IdentifiedMutationRequest(
                intent="apply",
                idempotency_key=_key("guard-identified"),
                resource_id="sk-1234567890abcdef1234567890abcdef",
            ),
            lambda service, request: service.terminate_pod(request),
        ),
    ],
)
async def test_pre_spend_rejection_precedes_provider_audit_and_credential_resolution(
    mutation_request: object,
    invoke: Any,
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    inspection_service = PreSpendInspectionService()
    service = _service(backend, inspection_service=inspection_service)

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await invoke(service, mutation_request)

    serialized = repr(exc_info.value.to_dict())
    assert exc_info.value.code == "pre_spend_payload_rejected"
    assert exc_info.value.resource_id is None
    assert "sk-1234567890abcdef1234567890abcdef" not in serialized
    assert "jane.roe@example.com" not in serialized
    assert backend.calls == []
    assert audit_calls == []
    counters = inspection_service.status().counters
    assert counters.total == 1
    assert counters.allow == 0


async def test_rejected_preview_records_no_guardrail_counter_or_provider_write() -> None:
    backend = RecordingBackend()
    inspection_service = PreSpendInspectionService()
    service = _service(
        backend,
        audit=False,
        inspection_service=inspection_service,
    )

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await service.create_pod(
            PodCreateRequest(
                intent="preview",
                idempotency_key=_key("guard-preview"),
                name="guarded-pod",
                image="example/image:1",
                gpu_type_ids=["NVIDIA L4"],
                ttl_minutes=60,
                env={"NORMAL": "sk-1234567890abcdef1234567890abcdef"},
            )
        )

    assert exc_info.value.code == "pre_spend_payload_rejected"
    assert inspection_service.status().counters.total == 0
    assert backend.calls == []


async def test_volume_shrink_and_equal_resize_fail_before_provider_write(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    service = _service(backend)

    for requested in (20, 10):
        with pytest.raises(RunPodControlPlaneError, match="must grow") as exc_info:
            await service.grow_volume(
                VolumeGrowRequest(
                    intent="apply",
                    idempotency_key=_key(f"shrink-{requested}"),
                    resource_id="vol_one",
                    size_gb=requested,
                )
            )
        assert exc_info.value.code == "volume_grow_only"
    assert "volumes.grow" not in backend.calls


def test_unsafe_ids_and_deprecated_v1_names_are_rejected_locally() -> None:
    with pytest.raises(ValidationError):
        IdentifiedMutationRequest(
            intent="apply",
            idempotency_key=_key("unsafe"),
            resource_id="../../pod",
        )
    with pytest.raises(ValidationError):
        PodCreateRequest.model_validate(
            {
                "intent": "preview",
                "idempotency_key": _key("v1-pod"),
                "name": "pod",
                "image": "example/image:1",
                "gpuTypeIds": ["NVIDIA L4"],
                "ttl_minutes": 60,
            }
        )
    with pytest.raises(ValidationError):
        PodCreateRequest.model_validate(
            {
                "intent": "preview",
                "idempotency_key": _key("gpu-shorthand"),
                "name": "pod",
                "image": "example/image:1",
                "gpu_type_ids": ["H100"],
                "ttl_minutes": 60,
            }
        )
    with pytest.raises(ValidationError):
        EndpointCreateRequest.model_validate(
            {
                "intent": "preview",
                "idempotency_key": _key("v1-endpoint"),
                "name": "endpoint",
                "template_id": "tmpl_one",
                "gpu": {"pools": ["AMPERE_24"]},
                "workersMin": 1,
            }
        )
    with pytest.raises(ValidationError):
        TemplateUpdateRequest.model_validate(
            {
                "intent": "preview",
                "idempotency_key": _key("hub-publish"),
                "resource_id": "tmpl_one",
                "public": True,
            }
        )
    with pytest.raises(ValidationError):
        TemplateCreateRequest.model_validate(
            {
                "intent": "preview",
                "idempotency_key": _key("unused-mount"),
                "name": "template",
                "image": "example/image:1",
                "volume_mount_path": "/workspace",
            }
        )


def test_mutation_validation_string_hides_request_values() -> None:
    secret = "request-secret-that-must-not-leak"

    with pytest.raises(ValidationError) as exc_info:
        PodCreateRequest.model_validate(
            {
                "intent": "preview",
                "idempotency_key": _key("hidden-input"),
                "name": "pod",
                "image": "example/image:1",
                "gpuTypeIds": ["NVIDIA L4"],
                "ttl_minutes": 60,
                "env": {"API_KEY": secret},
            }
        )

    assert secret not in str(exc_info.value)


async def test_repeat_terminate_is_idempotent_and_skips_second_write(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    service = _service(backend)
    request = IdentifiedMutationRequest(
        intent="apply",
        idempotency_key=_key("terminate-repeat"),
        resource_id="pod_one",
    )

    first = await service.terminate_pod(request)
    replayed = await service.terminate_pod(request)
    again = await service.terminate_pod(
        request.model_copy(update={"idempotency_key": _key("terminate-again")})
    )

    assert first.changed
    assert replayed.replayed and replayed.changed
    assert again.already_absent and not again.changed
    assert backend.calls.count("pods.terminate") == 1
    assert len(audit_calls) == 1


async def test_registry_replace_partial_failure_is_stable_and_redacted(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    backend.fail_registry_create = True
    service = _service(backend)

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await service.replace_registry_auth(
            RegistryAuthReplaceRequest(
                intent="apply",
                idempotency_key=_key("replace-partial"),
                resource_id="auth_one",
                name="replacement",
                username="robot",
                password_env="REGISTRY_PASSWORD",
            )
        )

    error = exc_info.value
    assert error.code == "registry_replace_partial_failure"
    assert error.changed
    assert "super-secret" not in repr(error.to_dict())
    assert len(audit_calls) == 1


async def test_registry_replace_name_conflict_fails_before_delete(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await _service(backend).replace_registry_auth(
            RegistryAuthReplaceRequest(
                intent="apply",
                idempotency_key=_key("replace-conflict"),
                resource_id="auth_one",
                name="existing-auth",
                username="robot",
                password_env="REGISTRY_PASSWORD",
            )
        )

    assert exc_info.value.code == "resource_name_conflict"
    assert "registry.delete" not in backend.calls
    assert audit_calls == []


async def test_template_post_create_read_failure_marks_provider_state_changed(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    backend.template_present = False

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await _service(backend).create_template(
            TemplateCreateRequest(
                intent="apply",
                idempotency_key=_key("template-partial"),
                name="new-template",
                image="example/image:1",
            )
        )

    error = exc_info.value
    assert error.code == "template_create_partial_failure"
    assert error.changed
    assert error.resource_id == "tmpl_new"
    assert "templates.create" in backend.calls
    assert len(audit_calls) == 1
    assert audit_calls[0]["new_value"]["resource_id"] == "tmpl_new"


async def test_timeout_and_cancellation_have_distinct_safe_behavior() -> None:
    class SlowBackend(RecordingBackend):
        async def list_pods(self) -> list[dict[str, Any]]:
            await asyncio.sleep(1)
            return []

    service = _service(SlowBackend(), timeout_s=0.01)
    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await service.list_pods()
    assert exc_info.value.code == "provider_timeout"
    assert exc_info.value.retryable

    class CancelBackend(RecordingBackend):
        async def list_pods(self) -> list[dict[str, Any]]:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _service(CancelBackend()).list_pods()


async def test_malformed_pod_response_has_stable_non_secret_error() -> None:
    class MalformedBackend(RecordingBackend):
        async def list_pods(self) -> list[dict[str, Any]]:
            return [{"env": {"API_KEY": "do-not-leak"}}]

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await _service(MalformedBackend()).list_pods()

    assert exc_info.value.code == "malformed_provider_response"
    assert "do-not-leak" not in repr(exc_info.value.to_dict())


async def test_post_write_audit_failure_is_stable_and_marks_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()

    journal_rows = recording_insert_audit([])

    async def failing_audit(pool: object, **kwargs: Any) -> None:
        if (kwargs.get("new_value") or {}).get("kind") == JOURNAL_KIND:
            await journal_rows(pool, **kwargs)
            return
        raise RuntimeError("postgres://audit-secret@database.invalid")

    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", failing_audit)

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await _service(backend).create_pod(
            PodCreateRequest(
                intent="apply",
                idempotency_key=_key("audit-failure"),
                name="new-pod",
                image="example/image:1",
                gpu_type_ids=["NVIDIA L4"],
                ttl_minutes=60,
            )
        )

    assert "pods.create" in backend.calls
    assert exc_info.value.code == "audit_write_failed"
    assert exc_info.value.changed
    assert "audit-secret" not in repr(exc_info.value.to_dict())


@pytest.mark.parametrize(
    "call",
    [
        lambda service: service.list_hub_templates(limit=0),
        lambda service: service.list_hub_templates(offset=-1),
        lambda service: service.search_hub_templates(""),
        lambda service: service.search_hub_templates("worker", limit=101),
    ],
)
async def test_hub_read_bounds_have_stable_pre_provider_errors(call: Any) -> None:
    backend = RecordingBackend()

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await call(_service(backend))

    assert exc_info.value.code == "invalid_request"
    assert backend.calls == []


async def test_unreachable_runpod_is_a_provider_error_not_an_absent_pod(
    monkeypatch: pytest.MonkeyPatch, audit_calls: list[dict[str, Any]]
) -> None:
    def refused(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("connection refused")

    terminated: list[str] = []

    async def terminate(self: StrictRunPodBackend, resource_id: str) -> None:
        terminated.append(resource_id)

    monkeypatch.setattr("pitwall.runpod_client.pods._rest_request", refused)
    monkeypatch.setattr(StrictRunPodBackend, "terminate_pod", terminate)
    service = RunPodControlPlaneService(
        backend=StrictRunPodBackend(), audit_pool=FakeJournalPool(), environ={}, timeout_s=5
    )

    with pytest.raises(RunPodControlPlaneError) as read:
        await service.get_pod("pod_live")
    assert read.value.code == "provider_error"

    request = IdentifiedMutationRequest(
        intent="apply", idempotency_key=_key("terminate-unreachable"), resource_id="pod_live"
    )
    with pytest.raises(RunPodControlPlaneError) as stop:
        await service.terminate_pod(request)
    assert stop.value.code == "provider_error"
    assert terminated == [] and audit_calls == []


_GET_BY_ID_ROUTES = {
    "template": (r"/templates/res_live$", lambda s: s.get_template("res_live")),
    "endpoint": (r"/serverless/res_live$", lambda s: s.get_endpoint("res_live")),
    "volume": (r"/network-volumes/res_live$", lambda s: s.get_volume("res_live")),
    "registry_auth": (r"/registries/res_live$", lambda s: s.get_registry_auth("res_live")),
    "pod": (r"/pods/res_live$", lambda s: s.get_pod("res_live")),
}


@pytest.mark.parametrize("body", [b"", b"null", b"[]", b"{}", b'"ok"'])
@pytest.mark.parametrize("resource_type", sorted(_GET_BY_ID_ROUTES))
@respx.mock
async def test_an_unusable_200_from_a_get_by_id_is_a_provider_error_never_not_found(
    monkeypatch: pytest.MonkeyPatch, resource_type: str, body: bytes
) -> None:
    # Onboarding resume and the MCP pod replay release a key only on resource_not_found, so a
    # provider glitch that answers 200 with nothing usable must never read as a gone resource.
    monkeypatch.setenv("RUNPOD_API_KEY", "service-test-runpod-key")
    monkeypatch.setattr("pitwall.runpod_client.pods._sdk_get_pod_sync", lambda *a, **k: None)
    pattern, call = _GET_BY_ID_ROUTES[resource_type]
    respx.get(url__regex=pattern).mock(return_value=httpx.Response(200, content=body))
    service = RunPodControlPlaneService(
        backend=StrictRunPodBackend(), audit_pool=FakeJournalPool(), environ={}, timeout_s=5
    )

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await call(service)

    assert exc_info.value.code in {"provider_error", "malformed_provider_response"}


@pytest.mark.parametrize("resource_type", sorted(_GET_BY_ID_ROUTES))
@respx.mock
async def test_only_a_404_from_a_get_by_id_is_not_found(
    monkeypatch: pytest.MonkeyPatch, resource_type: str
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "service-test-runpod-key")
    pattern, call = _GET_BY_ID_ROUTES[resource_type]
    respx.get(url__regex=pattern).mock(return_value=httpx.Response(404, text="not found"))
    service = RunPodControlPlaneService(
        backend=StrictRunPodBackend(), audit_pool=FakeJournalPool(), environ={}, timeout_s=5
    )

    with pytest.raises(RunPodControlPlaneError) as exc_info:
        await call(service)

    assert exc_info.value.code == "resource_not_found"


async def test_a_pod_create_journals_the_terms_a_reconciler_needs_and_no_secret(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()

    async def times_out(request: PodCreateRequest) -> dict[str, Any]:
        raise TimeoutError("create timed out")

    backend.create_pod = times_out  # type: ignore[method-assign]  # reason: per-test failure
    service = _service(backend)
    request = PodCreateRequest(
        intent="apply",
        idempotency_key=_key("pod-recovery"),
        name="recover-me",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=90,
        max_cost_per_hour="0.49",
        env={"MODE": "value-that-must-not-appear"},
    )
    with pytest.raises(RunPodControlPlaneError) as failed:
        await service.create_pod(request)
    assert failed.value.code == "provider_timeout"
    await service.create_volume(
        VolumeCreateRequest(
            intent="apply",
            idempotency_key=_key("volume-recovery"),
            name="new-volume",
            size_gb=10,
            data_center_id="US-KS-1",
        )
    )

    journal = {
        row["new_value"]["idempotency_key"]: row["new_value"]
        for row in service._audit_pool.rows
        if row["new_value"].get("kind") == JOURNAL_KIND
    }
    assert journal[request.idempotency_key]["state"] == "started"
    assert journal[request.idempotency_key]["recovery"] == {
        "attempt_marker": create_attempt_marker(request.idempotency_key),
        "name": "recover-me",
        "ttl_minutes": 90,
        "max_cost_per_hour": "0.49",
    }
    assert journal[_key("volume-recovery")]["recovery"] is None
    assert "value-that-must-not-appear" not in repr(service._audit_pool.rows)


async def test_every_pod_create_carries_its_attempt_marker_on_the_create_payload(
    monkeypatch: pytest.MonkeyPatch, audit_calls: list[dict[str, Any]]
) -> None:
    """The real backend and client: the REST create body's env carries the marker."""
    posted: list[dict[str, Any]] = []

    def rest_request(method: str, path: str, **kwargs: Any) -> Any:
        if method == "GET" and path == "pods":
            return {"pods": []}
        if method == "POST" and path == "pods":
            posted.append(kwargs["json_body"])
            return {"id": "pod_marked", "name": "marked-pod", "desiredStatus": "RUNNING"}
        if method == "GET" and path.startswith("pods/"):
            return {"id": "pod_marked", "name": "marked-pod", "desiredStatus": "RUNNING"}
        raise AssertionError(f"unexpected RunPod call {method} {path}")

    monkeypatch.setattr("pitwall.runpod_client.pods._rest_request", rest_request)
    monkeypatch.setattr("pitwall.runpod_client.pods._legacy_rest_request", rest_request)
    monkeypatch.setattr("pitwall.runpod_client.pods._sdk_get_pod_sync", lambda *a, **k: None)
    service = RunPodControlPlaneService(
        backend=StrictRunPodBackend(),
        audit_pool=FakeJournalPool(),
        environ={"RUNPOD_API_KEY": "service-test-runpod-key"},
        timeout_s=5,
    )
    request = PodCreateRequest(
        intent="apply",
        idempotency_key=_key("pod-marker"),
        name="marked-pod",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
        env={"MODE": "fast", CREATE_ATTEMPT_ENV: "caller-chosen"},
    )

    result = await service.create_pod(request)

    assert result.resource_id == "pod_marked"
    assert len(posted) == 1
    assert posted[0]["env"] == {
        "MODE": "fast",
        CREATE_ATTEMPT_ENV: create_attempt_marker(request.idempotency_key),
    }


@pytest.mark.parametrize("legacy", [False, True])
def test_both_pod_create_payload_builders_forward_the_marker(legacy: bool) -> None:
    from pitwall.runpod_client import pods
    from pitwall.runpod_client.workloads import WorkloadConfig

    marker_env = {CREATE_ATTEMPT_ENV: create_attempt_marker("resource-test-builders")}
    workload = WorkloadConfig(
        name="marked-pod",
        capability="raw.runpod.resource",
        gpu_types=["NVIDIA L4"],
        gpu_type_priority="custom",
        data_center_priority="custom",
    )
    common: dict[str, Any] = {
        "name": "marked-pod",
        "template_id": None,
        "image_name": "example/image:1",
        "workload": workload,
        "env": marker_env,
        "cloud_type": "SECURE",
        "network_volume_id": None,
        "data_center_id": None,
        "docker_start_cmd": None,
        "container_registry_auth_id": None,
    }
    if legacy:
        payload = pods._legacy_v1_create_payload(
            **common,
            gpu_type_ids=["NVIDIA L4"],
            docker_entrypoint=["/bin/sh"],
            support_public_ip=False,
        )
    else:
        payload = pods._v2_create_payload(**common, gpu_type_id="NVIDIA L4")
    assert payload["env"] == marker_env


async def test_a_second_cancel_during_create_cleanup_keeps_the_key_lock_until_the_thread_ends(
    monkeypatch: pytest.MonkeyPatch, audit_calls: list[dict[str, Any]]
) -> None:
    """The journal key lock outlives the create thread, however often the call is cancelled.

    While the lock is held, the orphaned-workload reaper skips the key, so it cannot close
    the workload at $0 before the pod the thread is still creating appears.
    """
    import threading

    from pitwall.runpod_client import pods
    from tests.hang_guard import HANG_GUARD_SECS

    started = threading.Event()
    release = threading.Event()
    terminated: list[str] = []
    cleanup_entered = asyncio.Event()

    def slow_create(**kwargs: Any) -> dict[str, Any]:
        started.set()
        release.wait(timeout=HANG_GUARD_SECS)
        return {"id": "pod-late", "name": "cancelled-pod", "desiredStatus": "RUNNING"}

    async def fake_terminate(pod_id: str) -> None:
        terminated.append(pod_id)

    original_cleanup = pods._terminate_orphaned_create

    async def cleanup(worker: Any) -> None:
        cleanup_entered.set()
        await original_cleanup(worker)

    monkeypatch.setattr(pods, "_rest_request", lambda *a, **k: {"pods": []})
    monkeypatch.setattr(pods, "create_pod_with_fallback_sync", slow_create)
    monkeypatch.setattr(pods, "terminate_pod", fake_terminate)
    monkeypatch.setattr(pods, "_terminate_orphaned_create", cleanup)
    pool = FakeJournalPool()
    service = RunPodControlPlaneService(
        backend=StrictRunPodBackend(),
        audit_pool=pool,
        environ={"RUNPOD_API_KEY": "service-test-runpod-key"},
        timeout_s=5,
    )
    key = _key("cancel-twice")
    lock = pool.lock_for(journal_lock_name(key))
    task = asyncio.ensure_future(
        service.create_pod(
            PodCreateRequest(
                intent="apply",
                idempotency_key=key,
                name="cancelled-pod",
                image="example/image:1",
                gpu_type_ids=["NVIDIA L4"],
                ttl_minutes=60,
            )
        )
    )
    try:
        await asyncio.to_thread(started.wait, HANG_GUARD_SECS)
        assert lock.locked()

        task.cancel()
        await asyncio.wait_for(cleanup_entered.wait(), HANG_GUARD_SECS)
        task.cancel()  # a second cancellation arrives while the cleanup waits for the thread
        for _ in range(20):
            await asyncio.sleep(0)
        assert not task.done(), "the call must not unwind while the create thread runs"
        assert lock.locked(), "the key lock must stay held until the create thread ends"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()
    assert terminated == ["pod-late"]
    assert pool.states(key) == ["started"]
