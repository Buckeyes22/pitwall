"""An applied control-plane mutation honours its idempotency key.

Every test drives the real service and its Postgres journal over an in-memory ``config_audit``
stand-in, with a recording RunPod backend. A repeated key and request replays the stored result
without any provider call; a reused key with a different request, or a prior attempt whose outcome
is unknown, is refused before any provider call.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from pitwall.runpod_client.pods import RunPodRestError
from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointGpuRequest,
    EndpointScalingRequest,
    EndpointUpdateRequest,
    EndpointWorkersRequest,
    IdentifiedMutationRequest,
    MutationRequest,
    MutationResult,
    PodActionRequest,
    PodCreateRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
)
from tests.runpod_control_plane.journal_fakes import (
    JOURNAL_KIND,
    FakeJournalPool,
    recording_insert_audit,
)
from tests.runpod_control_plane.test_service import RecordingBackend

pytestmark = pytest.mark.anyio

Mutation = Callable[[RunPodControlPlaneService, Any], Awaitable[MutationResult]]


@pytest.fixture
def audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit(calls))
    return calls


def _service(backend: RecordingBackend, pool: FakeJournalPool) -> RunPodControlPlaneService:
    return RunPodControlPlaneService(
        backend=backend,
        audit_pool=pool,
        environ={
            "REGISTRY_PASSWORD": "registry-password-value",
            "RUNPOD_API_KEY": "journal-test-runpod-key",
        },
        timeout_s=1,
    )


def _apply(model: type[MutationRequest], key: str, **fields: Any) -> Any:
    return model(intent="apply", idempotency_key=f"journal-{key}", **fields)


# (case id, request, service call, a field change that makes a different request)
_CASES: list[tuple[str, Any, Mutation, dict[str, Any]]] = [
    (
        "pod.create",
        _apply(
            PodCreateRequest,
            "pod-create",
            name="journal-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            env={"MODEL_REVISION": "pod-env-value"},
            args=["--revision", "pod-arg-value"],
            ttl_minutes=60,
        ),
        lambda service, request: service.create_pod(request),
        {"name": "other-pod"},
    ),
    (
        "pod.update",
        _apply(PodUpdateRequest, "pod-update", resource_id="pod_one", ports=["8000/http"]),
        lambda service, request: service.update_pod(request),
        {"ports": ["9000/http"]},
    ),
    (
        "pod.restart",
        _apply(PodActionRequest, "pod-restart", resource_id="pod_one", action="restart"),
        lambda service, request: service.action_pod(request),
        {"action": "reset"},
    ),
    (
        "pod.reset",
        _apply(PodActionRequest, "pod-reset", resource_id="pod_one", action="reset"),
        lambda service, request: service.action_pod(request),
        {"resource_id": "pod_two"},
    ),
    (
        "pod.terminate",
        _apply(IdentifiedMutationRequest, "pod-terminate", resource_id="pod_one"),
        lambda service, request: service.terminate_pod(request),
        {"resource_id": "pod_two"},
    ),
    (
        "endpoint.create",
        _apply(
            EndpointCreateRequest,
            "endpoint-create",
            name="journal-endpoint",
            template_id="tmpl_existing",
            gpu=EndpointGpuRequest(pools=["ADA_24"]),
        ),
        lambda service, request: service.create_endpoint(request),
        {"flashboot": True},
    ),
    (
        "endpoint.update",
        _apply(
            EndpointUpdateRequest,
            "endpoint-update",
            resource_id="ep_one",
            workers=EndpointWorkersRequest(minimum=0, maximum=2),
            scaling=EndpointScalingRequest(),
        ),
        lambda service, request: service.update_endpoint(request),
        {"flashboot": True},
    ),
    (
        "endpoint.delete",
        _apply(IdentifiedMutationRequest, "endpoint-delete", resource_id="ep_one"),
        lambda service, request: service.delete_endpoint(request),
        {"resource_id": "ep_two"},
    ),
    (
        "template.create",
        _apply(
            TemplateCreateRequest,
            "template-create",
            name="journal-template",
            image="example/image:1",
            env={"MODEL_REVISION": "template-env-value"},
            args=["--revision", "template-arg-value"],
        ),
        lambda service, request: service.create_template(request),
        {"disk_gb": 60},
    ),
    (
        "template.update",
        _apply(
            TemplateUpdateRequest,
            "template-update",
            resource_id="tmpl_one",
            env={"MODEL_REVISION": "template-update-value"},
        ),
        lambda service, request: service.update_template(request),
        {"image": "example/image:2"},
    ),
    (
        "template.delete",
        _apply(IdentifiedMutationRequest, "template-delete", resource_id="tmpl_one"),
        lambda service, request: service.delete_template(request),
        {"resource_id": "tmpl_two"},
    ),
    (
        "volume.create",
        _apply(
            VolumeCreateRequest,
            "volume-create",
            name="journal-volume",
            size_gb=20,
            data_center_id="US-KS-1",
        ),
        lambda service, request: service.create_volume(request),
        {"size_gb": 30},
    ),
    (
        "volume.grow",
        _apply(VolumeGrowRequest, "volume-grow", resource_id="vol_one", size_gb=30),
        lambda service, request: service.grow_volume(request),
        {"size_gb": 40},
    ),
    (
        "volume.delete",
        _apply(IdentifiedMutationRequest, "volume-delete", resource_id="vol_one"),
        lambda service, request: service.delete_volume(request),
        {"resource_id": "vol_two"},
    ),
    (
        "registry_auth.create",
        _apply(
            RegistryAuthCreateRequest,
            "registry-create",
            name="journal-auth",
            username="registry-user",
            password_env="REGISTRY_PASSWORD",
        ),
        lambda service, request: service.create_registry_auth(request),
        {"name": "other-auth"},
    ),
    (
        "registry_auth.replace",
        _apply(
            RegistryAuthReplaceRequest,
            "registry-replace",
            resource_id="auth_one",
            name="journal-auth",
            username="registry-user",
            password_env="REGISTRY_PASSWORD",
        ),
        lambda service, request: service.replace_registry_auth(request),
        {"name": "other-auth"},
    ),
    (
        "registry_auth.delete",
        _apply(IdentifiedMutationRequest, "registry-delete", resource_id="auth_one"),
        lambda service, request: service.delete_registry_auth(request),
        {"resource_id": "auth_two"},
    ),
]
_IDS = [case[0] for case in _CASES]


@pytest.mark.parametrize(("case", "request_model", "call", "change"), _CASES, ids=_IDS)
async def test_repeated_apply_replays_the_stored_result_without_any_provider_call(
    audit_calls: list[dict[str, Any]],
    case: str,
    request_model: Any,
    call: Mutation,
    change: dict[str, Any],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)

    first = await call(service, request_model)
    provider_calls = list(backend.calls)
    audit_rows = len(audit_calls)
    second = await call(service, request_model)

    assert backend.calls == provider_calls, f"{case} replay reached the provider"
    assert len(audit_calls) == audit_rows, f"{case} replay wrote another audit row"
    assert first.model_dump().get("replayed") is False
    assert second.model_dump().get("replayed") is True
    assert second.model_dump(exclude={"replayed"}) == first.model_dump(exclude={"replayed"})
    assert pool.states(request_model.idempotency_key) == ["started", "completed"]


@pytest.mark.parametrize(("case", "request_model", "call", "change"), _CASES, ids=_IDS)
async def test_same_key_with_a_different_request_is_an_idempotency_conflict(
    audit_calls: list[dict[str, Any]],
    case: str,
    request_model: Any,
    call: Mutation,
    change: dict[str, Any],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    await call(service, request_model)
    provider_calls = list(backend.calls)

    different = request_model.model_copy(update=change)
    with pytest.raises(RunPodControlPlaneError) as excinfo:
        await call(service, different)

    assert excinfo.value.code == "idempotency_conflict"
    assert excinfo.value.changed is False
    assert backend.calls == provider_calls, f"{case} conflict reached the provider"


@pytest.mark.parametrize(("case", "request_model", "call", "change"), _CASES, ids=_IDS)
async def test_a_started_entry_without_completion_is_ambiguous_and_never_reapplied(
    audit_calls: list[dict[str, Any]],
    case: str,
    request_model: Any,
    call: Mutation,
    change: dict[str, Any],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    # A crashed attempt: the started row committed, the completion never did.
    pool.fail_states = {"completed"}
    with pytest.raises(RunPodControlPlaneError) as first:
        await call(service, request_model)
    assert first.value.code == "audit_write_failed"
    pool.fail_states = set()
    provider_calls = list(backend.calls)

    with pytest.raises(RunPodControlPlaneError) as excinfo:
        await call(service, request_model)

    assert excinfo.value.code == "mutation_outcome_ambiguous"
    assert excinfo.value.retryable is False
    assert backend.calls == provider_calls, f"{case} ambiguous retry reached the provider"
    assert pool.states(request_model.idempotency_key) == ["started"]


@pytest.mark.parametrize(("case", "request_model", "call", "change"), _CASES, ids=_IDS)
async def test_preview_never_touches_the_journal(
    audit_calls: list[dict[str, Any]],
    case: str,
    request_model: Any,
    call: Mutation,
    change: dict[str, Any],
) -> None:
    pool = FakeJournalPool()
    service = _service(RecordingBackend(), pool)

    preview = await call(service, request_model.model_copy(update={"intent": "preview"}))

    assert preview.dry_run is True
    assert pool.acquired == 0 and pool.rows == [] and audit_calls == []


async def test_a_provider_timeout_leaves_the_outcome_ambiguous(
    audit_calls: list[dict[str, Any]],
) -> None:
    class TimingOut(RecordingBackend):
        async def action_pod(self, request: PodActionRequest) -> dict[str, Any]:
            self.calls.append(f"pods.{request.action}")
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    backend = TimingOut()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _apply(PodActionRequest, "reset-timeout", resource_id="pod_one", action="reset")

    with pytest.raises(RunPodControlPlaneError) as first:
        await service.action_pod(request)
    assert first.value.code == "provider_timeout"

    with pytest.raises(RunPodControlPlaneError) as retry:
        await service.action_pod(request)

    assert retry.value.code == "mutation_outcome_ambiguous"
    assert backend.calls == ["pods.reset"]


async def test_a_definite_provider_rejection_lets_the_same_key_retry(
    audit_calls: list[dict[str, Any]],
) -> None:
    class RejectsOnce(RecordingBackend):
        rejected = False

        async def grow_volume(self, request: VolumeGrowRequest) -> Any:
            if not self.rejected:
                self.rejected = True
                self.calls.append("volumes.grow.rejected")
                raise RunPodRestError("PATCH", "networkvolumes/vol_one", 400, "bad size")
            return await super().grow_volume(request)

    backend = RejectsOnce()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _apply(VolumeGrowRequest, "grow-retry", resource_id="vol_one", size_gb=30)

    with pytest.raises(RunPodControlPlaneError) as first:
        await service.grow_volume(request)
    assert first.value.code == "provider_error" and first.value.changed is False

    result = await service.grow_volume(request)

    assert result.changed is True and result.model_dump().get("replayed") is False
    assert backend.calls.count("volumes.grow") == 1
    assert pool.states(request.idempotency_key) == ["started", "failed", "started", "completed"]


async def test_a_precondition_failure_lets_the_same_key_retry(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _apply(
        VolumeCreateRequest,
        "name-taken",
        name="existing-volume",
        size_gb=20,
        data_center_id="US-KS-1",
    )

    for _ in range(2):
        with pytest.raises(RunPodControlPlaneError) as excinfo:
            await service.create_volume(request)
        assert excinfo.value.code == "resource_name_conflict"

    assert backend.calls == ["volumes.list", "volumes.list"]


async def test_concurrent_callers_with_one_key_make_one_provider_call(
    audit_calls: list[dict[str, Any]],
) -> None:
    class SlowRestart(RecordingBackend):
        async def action_pod(self, request: PodActionRequest) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return await super().action_pod(request)

    backend = SlowRestart()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _apply(PodActionRequest, "concurrent", resource_id="pod_one", action="restart")

    results = await asyncio.gather(service.action_pod(request), service.action_pod(request))

    assert backend.calls == ["pods.restart"]
    assert sorted(result.model_dump().get("replayed") for result in results) == [False, True]


async def test_the_journal_never_stores_credentials(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    for case, request_model, call, _change in _CASES:
        if case.startswith(("pod.create", "template.", "registry_auth.")):
            await call(service, request_model)

    stored = json.dumps(pool.rows)
    for secret in (
        "pod-env-value",
        "pod-arg-value",
        "template-env-value",
        "template-arg-value",
        "template-update-value",
        "registry-password-value",
        "registry-user",
    ):
        assert secret not in stored
    assert all(row["new_value"]["kind"] == JOURNAL_KIND for row in pool.rows)
    assert all(len(row["new_value"]["request_hash"]) == 64 for row in pool.rows)


async def test_an_unreachable_journal_refuses_before_any_provider_call(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    pool.fail_load = True
    service = _service(backend, pool)

    with pytest.raises(RunPodControlPlaneError) as excinfo:
        await service.action_pod(
            _apply(PodActionRequest, "journal-down", resource_id="pod_one", action="reset")
        )

    assert excinfo.value.code == "audit_unavailable"
    assert backend.calls == []
    assert pool.statements[-1].startswith("SELECT pg_advisory_unlock")


async def test_a_precondition_read_timeout_leaves_the_key_free(
    audit_calls: list[dict[str, Any]],
) -> None:
    class SlowFirstRead(RecordingBackend):
        slow = True

        async def get_volume(self, resource_id: str) -> Any:
            if self.slow:
                self.slow = False
                await asyncio.sleep(5)
            return await super().get_volume(resource_id)

    backend = SlowFirstRead()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _apply(VolumeGrowRequest, "read-timeout", resource_id="vol_one", size_gb=30)

    with pytest.raises(RunPodControlPlaneError) as first:
        await service.grow_volume(request)
    assert first.value.code == "provider_timeout"
    assert pool.states(request.idempotency_key) == []

    result = await service.grow_volume(request)

    assert result.changed is True and result.replayed is False
    assert backend.calls.count("volumes.grow") == 1
    assert pool.states(request.idempotency_key) == ["started", "completed"]


async def test_a_precondition_transport_error_leaves_the_key_free(
    audit_calls: list[dict[str, Any]],
) -> None:
    class FlakyList(RecordingBackend):
        flaky = True

        async def list_volumes(self) -> Any:
            if self.flaky:
                self.flaky = False
                raise httpx.ConnectError("connection refused")
            return await super().list_volumes()

    backend = FlakyList()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _apply(
        VolumeCreateRequest, "list-flaky", name="flaky-volume", size_gb=20, data_center_id="US-KS-1"
    )

    with pytest.raises(RunPodControlPlaneError) as first:
        await service.create_volume(request)
    assert first.value.code == "provider_error"

    result = await service.create_volume(request)
    assert result.changed is True
    assert pool.states(request.idempotency_key) == ["started", "completed"]


@pytest.mark.parametrize(
    ("case", "change"),
    [
        ("pod.create", {"env": {"MODEL_REVISION": "other-env-value"}}),
        ("pod.create", {"args": ["--revision", "other-arg-value"]}),
        ("template.update", {"env": {"MODEL_REVISION": "other-update-value"}}),
        ("registry_auth.create", {"username": "other-registry-user"}),
    ],
)
async def test_a_change_to_a_credential_bearing_value_is_a_conflict(
    audit_calls: list[dict[str, Any]], case: str, change: dict[str, Any]
) -> None:
    _case, request_model, call, _change = next(item for item in _CASES if item[0] == case)
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    first = await call(service, request_model)
    replayed = await call(service, request_model.model_copy())

    with pytest.raises(RunPodControlPlaneError) as conflict:
        await call(service, request_model.model_copy(update=change))

    assert replayed.replayed is True and replayed.resource_id == first.resource_id
    assert conflict.value.code == "idempotency_conflict"


async def test_the_credential_hmac_is_keyed_by_the_runpod_api_key(
    audit_calls: list[dict[str, Any]],
) -> None:
    request = _CASES[0][1]
    pool = FakeJournalPool()
    first = RunPodControlPlaneService(
        backend=RecordingBackend(),
        audit_pool=pool,
        environ={"RUNPOD_API_KEY": "first-runpod-key"},
        timeout_s=1,
    )
    rotated = RunPodControlPlaneService(
        backend=RecordingBackend(),
        audit_pool=pool,
        environ={"RUNPOD_API_KEY": "rotated-runpod-key"},
        timeout_s=1,
    )
    await first.create_pod(request)

    with pytest.raises(RunPodControlPlaneError) as conflict:
        await rotated.create_pod(request)
    assert conflict.value.code == "idempotency_conflict"


async def test_credential_bearing_values_need_a_runpod_key_before_any_io(
    audit_calls: list[dict[str, Any]], tmp_path: Any
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = RunPodControlPlaneService(
        backend=backend, audit_pool=pool, environ={"HOME": str(tmp_path)}, timeout_s=1
    )

    with pytest.raises(RunPodControlPlaneError) as excinfo:
        await service.create_pod(_CASES[0][1])

    assert excinfo.value.code == "credential_reference_unset"
    assert backend.calls == [] and pool.acquired == 0


async def test_a_pod_create_4xx_after_an_ambiguous_attempt_keeps_the_key(
    audit_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    runpod_rest_fake: Any,
) -> None:
    from pitwall.runpod_client import pods
    from pitwall.runpod_client.workloads import WorkloadConfig
    from pitwall.runpod_control_plane import StrictRunPodBackend

    class FallbackCreate(StrictRunPodBackend):
        """The real fallback client on its retrying (REST v1) path."""

        async def list_pods(self) -> list[dict[str, Any]]:
            return []

        async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
            workload = WorkloadConfig(
                name=request.name,
                capability="raw.runpod.resource",
                template_name=None,
                gpu_types=request.gpu_type_ids,
                gpu_count=request.gpu_count,
                container_disk_gb=request.disk_gb,
                min_vcpu=4,
                min_memory_gb=None,
                cloud_type=request.cloud,
                gpu_type_priority="custom",
                data_center_priority="custom",
            )
            return await pods.create_pod_with_fallback(
                name=request.name,
                template_id=None,
                image_name=request.image,
                workload=workload,
                env={},
                wait_for_readiness=False,
            )

    # The first POST times out at the gateway: the request may have landed, so the client
    # marks the create ambiguous and retries. The retry answers a permanent 400, which the
    # client re-raises bare, without the ambiguity flag.
    runpod_rest_fake.add(
        "POST",
        "pods",
        pods.RunPodRestError("POST", "pods", 504, "gateway timeout"),
        pods.RunPodRestError("POST", "pods", 400, "invalid gpu type for this cloud"),
    )
    monkeypatch.setattr(pods, "_rest_request", runpod_rest_fake)
    monkeypatch.setattr(pods, "_legacy_rest_request", runpod_rest_fake)
    monkeypatch.setattr(pods, "TRANSIENT_CREATE_BACKOFF_S", 0.0)
    pool = FakeJournalPool()
    service = RunPodControlPlaneService(
        backend=FallbackCreate(),
        audit_pool=pool,
        environ={"RUNPOD_API_KEY": "fallback-test-key"},
        timeout_s=1,
    )
    request = _apply(
        PodCreateRequest,
        "fallback-4xx",
        name="fallback-pod",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
    )

    with pytest.raises(RunPodControlPlaneError) as first:
        await service.create_pod(request)
    assert first.value.code == "provider_error" and first.value.provider_status == 400
    assert [call.method for call in runpod_rest_fake.calls] == ["POST", "POST"]

    with pytest.raises(RunPodControlPlaneError) as retry:
        await service.create_pod(request)
    assert retry.value.code == "mutation_outcome_ambiguous"
    assert pool.states(request.idempotency_key) == ["started"]


async def test_release_lets_the_same_key_apply_again(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    request = _CASES[11][1]  # volume.create
    await service.create_volume(request)

    assert await service.release_idempotency_key(request.idempotency_key, reason="undone")
    assert not await service.release_idempotency_key(request.idempotency_key, reason="again")
    assert not await service.release_idempotency_key("journal-never-used", reason="unused")
    again = await service.create_volume(request)

    assert again.replayed is False
    assert backend.calls.count("volumes.create") == 2
    assert pool.states(request.idempotency_key) == [
        "started",
        "completed",
        "compensated",
        "started",
        "completed",
    ]


async def test_a_waiting_caller_gives_up_with_a_retryable_error(
    audit_calls: list[dict[str, Any]],
) -> None:
    class SlowCreate(RecordingBackend):
        async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return await super().create_pod(request)

    pool = FakeJournalPool()
    service = RunPodControlPlaneService(
        backend=SlowCreate(),
        audit_pool=pool,
        environ={"RUNPOD_API_KEY": "lock-test-key"},
        timeout_s=0.1,
    )
    request = _apply(
        PodCreateRequest,
        "lock-wait",
        name="slow-pod",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
    )

    first, second = await asyncio.gather(
        service.create_pod(request), service.create_pod(request), return_exceptions=True
    )

    assert isinstance(first, MutationResult) and first.changed
    assert isinstance(second, RunPodControlPlaneError)
    assert second.code == "mutation_in_progress" and second.retryable is True


async def test_the_mutation_audit_row_shares_the_journal_connection(
    audit_calls: list[dict[str, Any]],
) -> None:
    from tests.runpod_control_plane.journal_fakes import FakeJournalConnection

    pool = FakeJournalPool()
    service = _service(RecordingBackend(), pool)
    await service.action_pod(
        _apply(PodActionRequest, "one-connection", resource_id="pod_one", action="restart")
    )

    assert pool.acquired == 1
    assert len(audit_calls) == 1
    assert isinstance(audit_calls[0]["conn"], FakeJournalConnection)


async def test_a_released_key_accepts_a_new_request_only_for_the_same_target(
    audit_calls: list[dict[str, Any]],
) -> None:
    backend = RecordingBackend()
    pool = FakeJournalPool()
    service = _service(backend, pool)
    volume = _apply(
        VolumeCreateRequest,
        "released",
        name="released-volume",
        size_gb=20,
        data_center_id="US-KS-1",
    )
    await service.create_volume(volume)
    await service.release_idempotency_key(volume.idempotency_key, reason="undone")

    template = _apply(TemplateCreateRequest, "released", name="other-template", image="img:1")
    with pytest.raises(RunPodControlPlaneError) as other_operation:
        await service.create_template(template)
    resized = await service.create_volume(volume.model_copy(update={"size_gb": 30}))

    assert other_operation.value.code == "idempotency_conflict"
    assert resized.changed and not resized.replayed

    restart = _apply(PodActionRequest, "released-restart", resource_id="pod_one", action="restart")
    await service.action_pod(restart)
    await service.release_idempotency_key(restart.idempotency_key, reason="undone")
    with pytest.raises(RunPodControlPlaneError) as other_resource:
        await service.action_pod(restart.model_copy(update={"resource_id": "pod_two"}))
    assert other_resource.value.code == "idempotency_conflict"
    assert backend.calls.count("templates.create") == 0
