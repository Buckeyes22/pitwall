"""Isolated MCP contracts for RunPod account resources."""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from pitwall.mcp.safe_boundary import _stable_error_payload
from pitwall.mcp.tools import runpod_resources
from pitwall.runpod_control_plane import (
    MutationResult,
    PodCreateRequest,
    PodResource,
    RegistryAuthResource,
    RunPodControlPlaneError,
)

pytestmark = pytest.mark.anyio


class StubService:
    def __init__(
        self,
        *,
        get_pod_error: BaseException | None = None,
        create_pod_error: BaseException | None = None,
    ) -> None:
        self.requests: list[PodCreateRequest] = []
        self._get_pod_error = get_pod_error
        self._create_pod_error = create_pod_error

    async def list_pods(self) -> list[PodResource]:
        return [PodResource(id="pod_one", name="one", status="RUNNING")]

    async def get_pod(self, resource_id: str) -> PodResource:
        if self._get_pod_error is not None:
            raise self._get_pod_error
        return PodResource(id=resource_id, name="one", status="RUNNING")

    async def create_pod(self, request: PodCreateRequest) -> MutationResult:
        if self._create_pod_error is not None:
            raise self._create_pod_error
        self.requests.append(request)
        return MutationResult(
            operation="pod.create",
            resource_type="pod",
            resource_id=None if request.dry_run else "pod_stub0001",
            dry_run=request.dry_run,
            changed=not request.dry_run,
            effect="preview raw pod creation" if request.dry_run else "created raw pod",
            idempotency_key=request.idempotency_key,
        )

    async def list_registry_auths(self) -> list[RegistryAuthResource]:
        return [RegistryAuthResource(id="auth_one", name="registry")]


def test_feature_manifest_has_the_exact_complete_inventory() -> None:
    specs = runpod_resources.RUNPOD_RESOURCE_TOOL_SPECS
    names = {spec.name for spec in specs}

    assert len(specs) == len(names) == 29
    assert {"pitwall_runpod_list_pods", "pitwall_runpod_action_pod"} <= names
    assert {
        "pitwall_runpod_create_endpoint",
        "pitwall_runpod_update_endpoint",
        "pitwall_runpod_delete_endpoint",
    } <= names
    assert {
        "pitwall_runpod_list_hub_templates",
        "pitwall_runpod_get_hub_template",
        "pitwall_runpod_search_hub_templates",
    } <= names
    assert not any(
        action in name
        for name in names
        if "hub_template" in name
        for action in ("create", "update", "delete", "publish", "deploy")
    )
    assert all(spec.handler.__name__ == spec.name for spec in specs)


async def test_handlers_are_thin_typed_shared_service_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = StubService()

    service_kinds: list[bool] = []

    async def fake_service(*, mutation: bool = False) -> Any:
        service_kinds.append(mutation)
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    # Lease wrapping requires budget admission and a lease row for apply; mock hermetically.
    class _DummyPool:
        pass

    dummy_pool = _DummyPool()

    async def _fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", _fake_get_pool, raising=False)

    class _FakeAdmission:
        workload_id = "wkl_test_stub"
        is_new = True

    async def _fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return _FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", _fake_admit_raw_pod_lease, raising=False
    )

    async def _fake_preview_budget(pool: Any, **kw: Any) -> Any:
        return {
            "admitted": True,
            "reason": None,
            "snapshot": {},
            "estimate_basis": "ttl_minutes x max_cost_per_hour",
        }

    monkeypatch.setattr(
        runpod_resources, "preview_raw_pod_lease_budget", _fake_preview_budget, raising=False
    )

    class _FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            return lease

    monkeypatch.setattr(runpod_resources, "LeaseRepository", _FakeLeaseRepository, raising=False)

    listing = await runpod_resources.pitwall_runpod_list_pods()
    preview = await runpod_resources.pitwall_runpod_create_pod(
        PodCreateRequest(
            intent="preview",
            idempotency_key="mcp-preview-0001",
            name="preview-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    )
    await runpod_resources.pitwall_runpod_create_pod(
        PodCreateRequest(
            intent="apply",
            idempotency_key="mcp-apply-000001",
            name="apply-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    )

    assert listing == {
        "pods": [
            {
                "id": "pod_one",
                "name": "one",
                "status": "RUNNING",
                "image": None,
                "gpu_type_id": None,
                "gpu_count": None,
                "cost_per_hour": None,
                "uptime_seconds": None,
                "public_ip": None,
                "port_mappings": {},
            }
        ]
    }
    assert preview["dry_run"] is True
    assert preview["changed"] is False
    assert preview["budget"]["admitted"] is True
    assert [request.intent for request in service.requests] == ["preview", "apply"]
    assert service_kinds == [False, False, True]


async def test_registry_mcp_serialization_never_exposes_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = StubService()

    async def fake_service(*, mutation: bool = False) -> Any:
        assert not mutation
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    result = await runpod_resources.pitwall_runpod_list_registry_auths()

    assert result == {"registry_auths": [{"id": "auth_one", "name": "registry"}]}
    assert "password" not in repr(result).casefold()
    assert "credential" not in repr(result).casefold()


def test_every_manifest_handler_routes_errors_through_the_shared_call_helper() -> None:
    """Every one of the 29 tools must adapt known errors, not just a sample."""
    for spec in runpod_resources.RUNPOD_RESOURCE_TOOL_SPECS:
        source = inspect.getsource(spec.handler)
        assert "_call(" in source, f"{spec.name} does not route through the shared _call() helper"


async def test_control_plane_error_on_a_read_tool_surfaces_its_real_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_plane_error = RunPodControlPlaneError(
        "resource_not_found",
        "RunPod pod was not found",
        operation="pod.get",
        resource_type="pod",
        resource_id="pod_missing",
    )
    service = StubService(get_pod_error=control_plane_error)

    async def fake_service(*, mutation: bool = False) -> Any:
        assert not mutation
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_get_pod("pod_missing")

    assert excinfo.value.error.data == control_plane_error.to_dict()
    assert excinfo.value.error.data["error"] == "resource_not_found"


async def test_control_plane_error_on_a_mutation_tool_surfaces_its_real_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix must cover mutation tools too, not only read-only ones."""
    control_plane_error = RunPodControlPlaneError(
        "resource_name_conflict",
        "pod name already exists",
        operation="pod.create",
        resource_type="pod",
        retryable=False,
    )
    service = StubService(create_pod_error=control_plane_error)

    async def fake_service(*, mutation: bool = False) -> Any:
        assert mutation
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    # Budget/lease path must be mocked so the control-plane error still surfaces.
    class _DummyPool:
        pass

    dummy_pool = _DummyPool()

    async def _fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", _fake_get_pool, raising=False)

    class _FakeAdmission:
        workload_id = "wkl_conflict"
        is_new = True

    async def _fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return _FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", _fake_admit_raw_pod_lease, raising=False
    )

    class _FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            return lease

    monkeypatch.setattr(runpod_resources, "LeaseRepository", _FakeLeaseRepository, raising=False)

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(
            PodCreateRequest(
                intent="apply",
                idempotency_key="mcp-apply-conflict1",
                name="dup-pod",
                image="example/image:1",
                gpu_type_ids=["NVIDIA L4"],
                ttl_minutes=60,
            )
        )

    assert excinfo.value.error.data == control_plane_error.to_dict()
    assert excinfo.value.error.data["error"] == "resource_name_conflict"
    # The structured payload must never carry credential/request-body detail.
    assert "password" not in repr(excinfo.value.error.data).casefold()


async def test_unexpected_exception_is_not_swallowed_by_the_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuinely unexpected failure must propagate unadapted to the boundary."""
    boom = RuntimeError("unexpected local failure")
    service = StubService(get_pod_error=boom)

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    with pytest.raises(RuntimeError) as excinfo:
        await runpod_resources.pitwall_runpod_get_pod("pod_x")

    assert excinfo.value is boom


async def test_unexpected_exception_still_degrades_safely_via_the_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global safe-call boundary remains the backstop for unknown errors.

    MCPServer wraps an escaped handler exception in a ``ToolError`` chained via
    ``from`` before ``install_safe_call_boundary`` ever sees it; reproduce
    that shape here and assert the boundary still reduces it to the generic,
    non-reflecting code rather than leaking exception text.
    """
    secret_canary = "super-secret-connection-string"
    boom = RuntimeError(f"connection failed: {secret_canary}")
    service = StubService(get_pod_error=boom)

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    with pytest.raises(RuntimeError):
        await runpod_resources.pitwall_runpod_get_pod("pod_x")

    wrapped = ToolError(str(boom))
    wrapped.__cause__ = boom
    payload = _stable_error_payload(wrapped)

    assert payload == {"error": "tool_execution_failed"}
    assert secret_canary not in str(payload)


async def test_boundary_recovers_the_real_code_from_an_adapted_control_plane_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: known errors reach the boundary with their code intact."""
    control_plane_error = RunPodControlPlaneError(
        "invalid_gpu_selection",
        "endpoint GPU type selection is invalid",
        operation="resolve_gpu_types",
        resource_type="endpoint",
    )
    service = StubService(get_pod_error=control_plane_error)

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    try:
        await runpod_resources.pitwall_runpod_get_pod("pod_x")
    except MCPError as exc:
        mcp_error: MCPError = exc
    else:
        pytest.fail("expected an MCPError carrying the structured RunPod error code")

    # MCPServer passes a raised MCPError through unwrapped; a ToolError-wrapped one keeps its code too.
    assert _stable_error_payload(mcp_error) == {"error": "invalid_gpu_selection"}
    wrapped = ToolError(str(mcp_error))
    wrapped.__cause__ = mcp_error
    payload = _stable_error_payload(wrapped)

    assert payload == {"error": "invalid_gpu_selection"}
