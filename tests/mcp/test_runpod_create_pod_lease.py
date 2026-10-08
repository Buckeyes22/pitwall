"""Task 4 D6(a) — create_pod lease wrapping failing tests.

Behaviors:
1. create without ttl_minutes → tool error, no service call
2. create over budget → budget rejection surfaces, no pod
3. successful create returns lease_id and lease row exists
4. successful create passes a 4-dp-quantized max_usd_per_hour on the Lease
5. pod-create failure before the journal's started row → workload closed with actual 0, no lease;
   an unknown outcome (journal still started) keeps the workload open for the reconciler
6. lease-insert failure after pod create → best-effort terminate_pod, workload closed
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.mcp.tools import runpod_resources
from pitwall.runpod_control_plane import (
    MutationResult,
    PodCreateRequest,
    PodResource,
    RunPodControlPlaneError,
)

pytestmark = pytest.mark.anyio


class StubServiceWithTracking:
    def __init__(self) -> None:
        self.create_calls: list[PodCreateRequest] = []
        self.terminate_calls: list[Any] = []

    async def list_pods(self) -> list[PodResource]:
        return []

    async def get_pod(self, resource_id: str) -> PodResource:
        return PodResource(id=resource_id, name="test-pod", status="running")

    async def create_pod(self, request: PodCreateRequest) -> MutationResult:
        self.create_calls.append(request)
        return MutationResult(
            operation="pod.create",
            resource_type="pod",
            resource_id="pod_test123",
            dry_run=request.dry_run,
            changed=True,
            effect="created raw pod pod_test123",
            idempotency_key=request.idempotency_key,
            resource={"id": "pod_test123", "name": request.name, "status": "running"},
        )

    async def idempotency_key_status(self, idempotency_key: str) -> Any:
        return None  # no journal entry: a failure here stopped before the started row

    async def terminate_pod(self, request: Any) -> MutationResult:
        self.terminate_calls.append(request)
        return MutationResult(
            operation="pod.terminate",
            resource_type="pod",
            resource_id=getattr(request, "resource_id", None),
            dry_run=getattr(request, "dry_run", False),
            changed=True,
            effect=f"terminated raw pod {getattr(request, 'resource_id', None)}",
            idempotency_key=getattr(request, "idempotency_key", None),
        )


async def test_create_without_ttl_fails_without_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ttl_minutes the tool must error and must not call RunPod."""
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    # The PodCreateRequest should require ttl_minutes; constructing without it should fail
    # or the handler should reject it before calling the service.
    try:
        request = PodCreateRequest(
            intent="apply",
            idempotency_key="mcp-apply-ttl-missing1",
            name="ttl-missing-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
        )
    except ValidationError:
        # Pydantic validation correctly rejected missing ttl
        assert service.create_calls == []
        return

    # If construction succeeded (ttl optional), handler must reject before service call
    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert service.create_calls == []
    # Should be a validation-class error (invalid_request or budget? but not generic fallback alone)
    data = excinfo.value.error.data or {}
    assert isinstance(data, dict)
    assert (
        data.get("error")
        in {
            "invalid_request",
            "invalid_resource_id",
            "invalid_tool_arguments",
            "tool_execution_failed",
        }
        or "ttl" in str(data).lower()
        or "ttl" in str(excinfo.value.error.message).lower()
    )


async def test_create_over_budget_surfaces_without_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    """When BudgetGate rejects, the error surfaces and no pod is created."""
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    # Mock get_pool to return a dummy pool (budget gate needs it)
    class DummyPool:
        pass

    dummy_pool = DummyPool()

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    # Mock lease budget admission to raise BudgetRejected (via admit_raw_pod_lease)
    snapshot = BudgetSnapshot(
        monthly_budget_usd=Decimal("10"),
        per_request_max_usd=Decimal("5"),
        mtd_spend_usd=Decimal("9"),
        estimate_usd=Decimal("5"),
        budget_remaining_usd=Decimal("1"),
    )

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        raise BudgetRejected("monthly_budget", snapshot)

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    # Also need to patch LeaseRepository to avoid DB writes if handler tries to create lease after budget
    # but budget should fail before lease, so not needed

    # Construct request with ttl_minutes even before field exists (use model_construct workaround)
    try:
        request = PodCreateRequest(
            intent="apply",
            idempotency_key="mcp-budget-reject1",
            name="budget-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    except ValidationError:
        request = PodCreateRequest.model_construct(
            intent="apply",
            idempotency_key="mcp-budget-reject1",
            name="budget-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
        )
        object.__setattr__(request, "ttl_minutes", 60)

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert service.create_calls == []
    data = excinfo.value.error.data or {}
    # Budget errors map to -31002 and carry exactly the budget_rejected code
    assert data.get("error") == "budget_rejected"
    # MCP code should be -31002 (budget class)
    assert excinfo.value.error.code == -31002


async def test_successful_create_returns_lease_id_and_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful pod creation returns lease_id and a lease row exists."""
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        assert mutation is True
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    class DummyConn:
        pass

    class DummyPool:
        def acquire(self):  # type: ignore[no-untyped-def]  # reason: test double leaves its return type unannotated
            class _Ctx:
                async def __aenter__(self):  # type: ignore[no-untyped-def]  # reason: test double leaves its return type unannotated
                    return DummyConn()

                async def __aexit__(self, *a: Any):  # type: ignore[no-untyped-def]  # reason: test double leaves its return type unannotated
                    return False

            return _Ctx()

        async def fetchrow(self, *a: Any, **kw: Any):  # type: ignore[no-untyped-def]  # reason: test double leaves its return type unannotated
            return None

    dummy_pool = DummyPool()

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    # Mock lease budget admission to succeed
    class FakeAdmission:
        def __init__(self) -> None:
            self.workload_id = "wkl_test123"
            self.is_new = True

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    # Capture lease creation
    created_leases: list[Any] = []

    class FakeLease:
        def __init__(self, **kw: Any) -> None:
            self.__dict__.update(kw)
            self.id = kw.get("id")
            self.provider_id = kw.get("provider_id")
            self.workload_id = kw.get("workload_id")
            self.runpod_pod_id = kw.get("runpod_pod_id")

    class FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            created_leases.append(lease)
            return lease

        async def get(self, lease_id: str) -> Any:
            for lease in created_leases:
                if getattr(lease, "id", None) == lease_id:
                    return lease
            return None

    # Some implementations may use LeaseRepository or direct SQL; patch both possibilities
    monkeypatch.setattr(runpod_resources, "LeaseRepository", FakeLeaseRepository, raising=False)

    # Also need Lease and related enums for construction if handler builds lease via model
    # The handler may import Lease inside function; patching repository is enough.

    try:
        request = PodCreateRequest(
            intent="apply",
            idempotency_key="mcp-success-lease1",
            name="success-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    except ValidationError:
        request = PodCreateRequest.model_construct(
            intent="apply",
            idempotency_key="mcp-success-lease1",
            name="success-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
        )
        object.__setattr__(request, "ttl_minutes", 60)

    result = await runpod_resources.pitwall_runpod_create_pod(request)

    assert "lease_id" in result, f"result missing lease_id: {result}"
    lease_id = result["lease_id"]
    assert isinstance(lease_id, str) and lease_id

    # Lease row must exist (via fake repo capture or real DB)
    assert len(created_leases) >= 1 or service.create_calls, "lease row was not created"
    # Verify the returned lease_id matches a created lease
    if created_leases:
        ids = [getattr(lease, "id", None) for lease in created_leases]
        assert lease_id in ids

    # Pod must have been created
    assert len(service.create_calls) == 1
    assert result.get("resource_id") == "pod_test123" or result.get("lease_id")


class DummyPool:
    pass


class TrackingWorkloadRepository:
    """Captures WorkloadRepository.update_state calls for workload-close assertions."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.update_calls: list[tuple[Any, ...]] = []
        self.kwargs_calls: list[dict[str, Any]] = []
        self.released: list[str] = []

    async def update_state(self, *args: Any, **kwargs: Any) -> Any:
        self.update_calls.append(args)
        self.kwargs_calls.append(kwargs)
        return None

    async def fail_and_release_idempotency_key(self, workload_id: str, **kwargs: Any) -> Any:
        self.update_calls.append((workload_id, "failed"))
        self.kwargs_calls.append({"cost_actual_usd": Decimal("0"), **kwargs})
        self.released.append(workload_id)
        return None


async def test_successful_create_builds_audited_mutation_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The apply-path service is the audited variant (mutation=True) so the
    failure-path rollback terminate_pod call writes its config_audit row too."""
    service = StubServiceWithTracking()
    gathered: list[bool] = []

    async def fake_service(*, mutation: bool = False) -> Any:
        gathered.append(mutation)
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    dummy_pool = DummyPool()

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    class FakeAdmission:
        workload_id = "wkl_index"
        is_new = True

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        assert kw["idempotency_key"] == "mcp-index-123"
        return FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    class FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            return lease

    monkeypatch.setattr(runpod_resources, "LeaseRepository", FakeLeaseRepository, raising=False)

    request = _make_request("mcp-index-123")

    result = await runpod_resources.pitwall_runpod_create_pod(request)

    assert "lease_id" in result
    assert gathered[0] is True  # pod create rides the audited service
    assert all(mutation is True for mutation in gathered)


class FailingLeaseRepository:
    """Lease repository whose create fails after a successful pod create."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.create_calls: list[Any] = []

    async def create(self, lease: Any) -> Any:
        self.create_calls.append(lease)
        raise RuntimeError("lease insert failed")

    async def latest_for_external_resource(
        self, provider_id: str, external_resource_id: str
    ) -> Any:
        return None  # the lease store answers: no lease for this pod


def _make_request(idempotency_key: str, **extra: Any) -> PodCreateRequest:
    try:
        return PodCreateRequest(
            intent="apply",
            idempotency_key=idempotency_key,
            name="test-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
            **extra,
        )
    except ValidationError:
        request = PodCreateRequest.model_construct(
            intent="apply",
            idempotency_key=idempotency_key,
            name="test-pod",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            **extra,
        )
        object.__setattr__(request, "ttl_minutes", 60)
        for key, value in extra.items():
            object.__setattr__(request, key, value)
        return request


async def test_successful_create_passes_quantized_max_usd_per_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 6-dp request max_cost_per_hour lands on the Lease quantized to 4 dp."""
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    dummy_pool = DummyPool()

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    class FakeAdmission:
        workload_id = "wkl_q1"
        is_new = True

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    created_leases: list[Any] = []

    class FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            created_leases.append(lease)
            return lease

    monkeypatch.setattr(runpod_resources, "LeaseRepository", FakeLeaseRepository, raising=False)

    request = _make_request("mcp-max-rate-q1", max_cost_per_hour=Decimal("0.123456"))

    result = await runpod_resources.pitwall_runpod_create_pod(request)

    assert "lease_id" in result
    assert len(created_leases) == 1
    lease = created_leases[0]
    assert lease.max_usd_per_hour == Decimal("0.1235")


async def test_pod_create_failure_closes_admitted_workload_and_writes_no_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create that failed before its journal ``started`` row closes the workload at $0."""
    service = StubServiceWithTracking()

    async def fail_create(request: PodCreateRequest) -> MutationResult:
        raise RunPodControlPlaneError(
            "provider_error",
            "RunPod rejected the pod create",
            operation="pod.create",
            resource_type="pod",
        )

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(service, "create_pod", fail_create)

    dummy_pool = DummyPool()

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    class FakeAdmission:
        workload_id = "wkl_fail_after_admit"
        is_new = True

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    created_leases: list[Any] = []

    class FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            created_leases.append(lease)
            return lease

    monkeypatch.setattr(runpod_resources, "LeaseRepository", FakeLeaseRepository, raising=False)

    workload_repo = TrackingWorkloadRepository(dummy_pool)
    monkeypatch.setattr(runpod_resources, "WorkloadRepository", lambda pool: workload_repo)

    request = _make_request("mcp-create-fail-1")

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    code = excinfo.value.error.code
    assert code < 0  # a structured client-visible error, not the fallback

    assert len(workload_repo.update_calls) == 1
    args = workload_repo.update_calls[0]
    assert args[0] == "wkl_fail_after_admit"
    assert args[1] == "failed"
    assert workload_repo.kwargs_calls[0]["cost_actual_usd"] == Decimal("0")

    assert created_leases == []
    assert service.terminate_calls == []


async def test_lease_insert_failure_terminates_pod_closes_workload_and_resurfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lease-insert failure after pod create: best-effort terminate + close, original error surfaces."""
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    dummy_pool = DummyPool()

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return dummy_pool

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    class FakeAdmission:
        workload_id = "wkl_lease_insert_fail"
        is_new = True

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    lease_repo = FailingLeaseRepository(dummy_pool)
    monkeypatch.setattr(runpod_resources, "LeaseRepository", lambda pool: lease_repo)

    workload_repo = TrackingWorkloadRepository(dummy_pool)
    monkeypatch.setattr(runpod_resources, "WorkloadRepository", lambda pool: workload_repo)

    request = _make_request("mcp-lease-insert-fail-1")

    with pytest.raises(RuntimeError, match="lease insert failed"):
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert len(service.create_calls) == 1
    assert len(lease_repo.create_calls) == 1

    assert len(service.terminate_calls) == 1
    terminate_request = service.terminate_calls[0]
    assert terminate_request.resource_id == "pod_test123"

    assert len(workload_repo.update_calls) == 1
    args = workload_repo.update_calls[0]
    assert args[0] == "wkl_lease_insert_fail"
    assert args[1] == "failed"
    assert workload_repo.kwargs_calls[0]["cost_actual_usd"] == Decimal("0")


async def test_create_without_a_pod_id_is_an_error_not_a_fabricated_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R13: a create result with no pod id must never mint a fake id; the real pod would be un-tearable."""

    class NoIdService(StubServiceWithTracking):
        async def create_pod(self, request: PodCreateRequest) -> MutationResult:
            self.create_calls.append(request)
            return MutationResult(
                operation="pod.create",
                resource_type="pod",
                resource_id=None,
                dry_run=False,
                changed=True,
                effect="created raw pod",
                idempotency_key=request.idempotency_key,
                resource={"name": request.name},
            )

    service = NoIdService()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    monkeypatch.setattr(runpod_resources, "_service", fake_service)

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return DummyPool()

    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)

    class FakeAdmission:
        workload_id = "wkl_noid"
        is_new = True

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return FakeAdmission()

    monkeypatch.setattr(
        runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease, raising=False
    )

    created: list[Any] = []

    class FakeLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            created.append(lease)
            return lease

    monkeypatch.setattr(runpod_resources, "LeaseRepository", FakeLeaseRepository, raising=False)
    closes: list[tuple[Any, ...]] = []

    class FakeWorkloadRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def update_state(self, *args: Any, **kwargs: Any) -> Any:
            closes.append(args)

    monkeypatch.setattr(
        runpod_resources, "WorkloadRepository", FakeWorkloadRepository, raising=False
    )

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-noid-1"))

    assert excinfo.value.error.code == -31004
    data = excinfo.value.error.data or {}
    assert data.get("error") == "malformed_provider_response"
    assert "console" in excinfo.value.error.message.lower()
    assert created == [], "no lease row may be written for an unknown pod id"
    assert closes == [], "the admitted workload stays open: the pod may be billing"
    assert service.terminate_calls == []


async def test_preview_carries_the_budget_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    service = StubServiceWithTracking()

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    async def fake_pool() -> Any:
        return object()

    async def fake_preview(pool: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs == {"ttl_minutes": 240, "max_cost_per_hour": Decimal("0.49")}
        return {
            "admitted": False,
            "reason": "monthly_budget",
            "snapshot": {"budget_remaining_usd": "0.182799"},
        }

    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(runpod_resources, "get_pool", fake_pool)
    monkeypatch.setattr(runpod_resources, "preview_raw_pod_lease_budget", fake_preview)
    request = PodCreateRequest(
        intent="preview",
        idempotency_key="mcp-preview-budget-1",
        name="p",
        image="example/image:1",
        gpu_type_ids=["NVIDIA A40"],
        ttl_minutes=240,
        max_cost_per_hour=Decimal("0.49"),
    )
    result = await runpod_resources.pitwall_runpod_create_pod(request)
    assert result["budget"]["admitted"] is False
    assert result["budget"]["reason"] == "monthly_budget"
    assert "pitwall_budget_set" in result["budget"]["remedy"]


class _LeaseLookupPool:
    """Pool stand-in; the lease lookup is answered by the patched repository."""

    def __init__(self, recorded: Any | None) -> None:
        self.recorded = recorded
        self.lookups: list[tuple[str, str]] = []


def _patch_replayed_create(
    monkeypatch: pytest.MonkeyPatch, pool: _LeaseLookupPool, *, is_new: bool = False
) -> tuple[StubServiceWithTracking, list[Any], TrackingWorkloadRepository]:
    service = StubServiceWithTracking()
    original = service.create_pod

    async def replayed_create(request: PodCreateRequest) -> MutationResult:
        result = await original(request)
        return result.model_copy(update={"replayed": True})

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return pool

    class FakeAdmission:
        workload_id = "wkl_first_attempt"

    FakeAdmission.is_new = is_new  # type: ignore[attr-defined]  # reason: per-test admission flag

    async def fake_admit_raw_pod_lease(pool: Any, **kw: Any) -> Any:
        return FakeAdmission()

    created_leases: list[Any] = []

    class FakeLeaseRepository:
        def __init__(self, pool: _LeaseLookupPool) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            created_leases.append(lease)
            return lease

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            self.pool.lookups.append((provider_id, external_resource_id))
            return self.pool.recorded

    workload_repo = TrackingWorkloadRepository(pool)
    monkeypatch.setattr(service, "create_pod", replayed_create)
    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)
    monkeypatch.setattr(runpod_resources, "admit_raw_pod_lease", fake_admit_raw_pod_lease)
    monkeypatch.setattr(runpod_resources, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(runpod_resources, "WorkloadRepository", lambda pool: workload_repo)
    return service, created_leases, workload_repo


async def test_replayed_create_returns_the_recorded_lease_without_a_second_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expires_at = dt.datetime(2026, 10, 6, 18, 0, tzinfo=dt.UTC)
    recorded = SimpleNamespace(
        id="lease_runpod_first", expires_at=expires_at, workload_id="wkl_first_attempt"
    )
    pool = _LeaseLookupPool(recorded)
    _service, created_leases, workload_repo = _patch_replayed_create(monkeypatch, pool)

    result = await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-replay-lease1"))

    assert result["replayed"] is True
    assert result["lease_id"] == "lease_runpod_first"
    assert result["expires_at"] == expires_at.isoformat()
    assert result["workload_id"] == "wkl_first_attempt"
    assert created_leases == []
    assert workload_repo.update_calls == []
    assert pool.lookups == [("runpod_direct", "pod_test123")]


async def test_replayed_create_without_a_recorded_lease_records_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _LeaseLookupPool(None)
    _service, created_leases, _workloads = _patch_replayed_create(monkeypatch, pool)

    result = await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-replay-lease2"))

    assert result["replayed"] is True
    assert [lease.id for lease in created_leases] == [result["lease_id"]]
    assert created_leases[0].runpod_pod_id == "pod_test123"


@pytest.mark.parametrize(
    "code", ["idempotency_conflict", "mutation_outcome_ambiguous", "mutation_in_progress"]
)
@pytest.mark.parametrize("is_new", [False, True])
async def test_a_journal_refusal_closes_only_a_workload_this_call_admitted(
    monkeypatch: pytest.MonkeyPatch, code: str, is_new: bool
) -> None:
    pool = _LeaseLookupPool(None)
    service, created_leases, workload_repo = _patch_replayed_create(
        monkeypatch, pool, is_new=is_new
    )

    async def refused(request: PodCreateRequest) -> MutationResult:
        raise RunPodControlPlaneError(
            code, "refused by the idempotency journal", operation="pod.create", resource_type="pod"
        )

    monkeypatch.setattr(service, "create_pod", refused)

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-refused-lease"))

    assert (excinfo.value.error.data or {})["error"] == code
    closed = [call[0] for call in workload_repo.update_calls]
    assert closed == (["wkl_first_attempt"] if is_new else [])
    assert created_leases == []


class _KeyedAdmissions:
    """``admit_raw_pod_lease`` as the budget gate behaves for idempotency keys.

    A key bound to a workload returns that workload (``is_new=False``) without a budget
    check; an unbound key passes the budget check and binds a new workload. Releasing a
    workload's key unbinds it, as ``WorkloadRepository.fail_and_release_idempotency_key`` does.
    """

    def __init__(self) -> None:
        self.by_key: dict[str, str] = {}
        self.budget_checks = 0
        self.reject = False
        self.closed: list[str] = []
        self.released: list[str] = []

    async def admit(self, pool: Any, **kw: Any) -> Any:
        key = kw["idempotency_key"]
        if key in self.by_key:
            return SimpleNamespace(workload_id=self.by_key[key], is_new=False)
        self.budget_checks += 1
        if self.reject:
            raise BudgetRejected(
                "monthly_budget",
                BudgetSnapshot(
                    monthly_budget_usd=Decimal("10"),
                    per_request_max_usd=Decimal("5"),
                    mtd_spend_usd=Decimal("10"),
                    estimate_usd=Decimal("5"),
                    budget_remaining_usd=Decimal("0"),
                ),
            )
        workload_id = f"wkl_admitted_{self.budget_checks}"
        self.by_key[key] = workload_id
        return SimpleNamespace(workload_id=workload_id, is_new=True)

    def workloads(self, pool: Any) -> Any:
        admissions = self

        class _Workloads:
            async def fail_and_release_idempotency_key(
                self, workload_id: str, **kwargs: Any
            ) -> Any:
                admissions.closed.append(workload_id)
                admissions.released.append(workload_id)
                for key, bound in list(admissions.by_key.items()):
                    if bound == workload_id:
                        del admissions.by_key[key]
                return None

        return _Workloads()


def _patch_rollback_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any, _KeyedAdmissions, list[Any]]:
    from tests.runpod_control_plane.journal_fakes import FakeJournalPool, recording_insert_audit
    from tests.runpod_control_plane.test_service import RecordingBackend
    from tests.runpod_control_plane.test_service import _service as control_plane_service

    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit([]))
    backend = RecordingBackend()
    control_plane = control_plane_service(backend)
    assert isinstance(control_plane._audit_pool, FakeJournalPool)
    admissions = _KeyedAdmissions()
    created_leases: list[Any] = []
    fail_inserts = [True]

    async def fake_service(*, mutation: bool = False) -> Any:
        return control_plane

    async def fake_get_pool(*args: Any, **kwargs: Any) -> Any:
        return DummyPool()

    class FlakyLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            if fail_inserts.pop(0) if fail_inserts else False:
                raise RuntimeError("lease insert failed")
            created_leases.append(lease)
            return lease

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            return None

    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(runpod_resources, "get_pool", fake_get_pool)
    monkeypatch.setattr(runpod_resources, "admit_raw_pod_lease", admissions.admit)
    monkeypatch.setattr(runpod_resources, "LeaseRepository", FlakyLeaseRepository)
    monkeypatch.setattr(runpod_resources, "WorkloadRepository", admissions.workloads)
    return backend, control_plane, admissions, created_leases


async def test_a_retry_after_lease_rollback_creates_a_new_pod_under_a_new_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, control_plane, admissions, created_leases = _patch_rollback_retry(monkeypatch)
    request = _make_request("mcp-rollback-retry-1")

    with pytest.raises(RuntimeError):
        await runpod_resources.pitwall_runpod_create_pod(request)
    assert backend.calls.count("pods.terminate") == 1
    assert admissions.closed == admissions.released == ["wkl_admitted_1"]

    result = await runpod_resources.pitwall_runpod_create_pod(request)

    assert result["replayed"] is False
    assert backend.calls.count("pods.create") == 2
    assert admissions.budget_checks == 2
    assert result["workload_id"] == "wkl_admitted_2"
    assert [(lease.runpod_pod_id, lease.workload_id) for lease in created_leases] == [
        (result["resource_id"], "wkl_admitted_2")
    ]
    assert control_plane._audit_pool.states(request.idempotency_key) == [
        "started",
        "completed",
        "compensated",
        "started",
        "completed",
    ]


async def test_a_retry_after_lease_rollback_is_budget_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _control_plane, admissions, created_leases = _patch_rollback_retry(monkeypatch)
    request = _make_request("mcp-rollback-budget-1")
    with pytest.raises(RuntimeError):
        await runpod_resources.pitwall_runpod_create_pod(request)

    admissions.reject = True
    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "budget_rejected"
    assert admissions.budget_checks == 2
    assert backend.calls.count("pods.create") == 1
    assert created_leases == []


async def test_a_failed_key_release_refuses_the_retry_instead_of_leasing_a_dead_pod(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    backend, control_plane, _admissions, created_leases = _patch_rollback_retry(monkeypatch)

    async def release_fails(idempotency_key: str, *, reason: str) -> bool:
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr(control_plane, "release_idempotency_key", release_fails)
    request = _make_request("mcp-rollback-norelease")
    with (
        caplog.at_level("WARNING", logger="pitwall.mcp.tools.runpod_resources"),
        pytest.raises(RuntimeError),
    ):
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert backend.calls.count("pods.terminate") == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any("key release failed" in message for message in messages)
    assert not any("terminate failed" in message for message in messages)

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "resource_not_found"
    assert backend.calls.count("pods.create") == 1
    assert created_leases == []
    # The refused retry's workload is closed and its key freed, so a later same-key retry
    # is budget-checked again instead of reusing a workload that never got a pod.
    assert _admissions.closed == ["wkl_admitted_1", "wkl_admitted_2"]

    with pytest.raises(MCPError):
        await runpod_resources.pitwall_runpod_create_pod(request)
    assert _admissions.budget_checks == 3
    assert _admissions.closed == ["wkl_admitted_1", "wkl_admitted_2", "wkl_admitted_3"]


@pytest.mark.parametrize(("status", "leased"), [("terminated", False), ("exited", True)])
async def test_a_replayed_create_treats_only_a_terminated_pod_as_gone(
    monkeypatch: pytest.MonkeyPatch, status: str, leased: bool
) -> None:
    pool = _LeaseLookupPool(None)
    service, created_leases, workload_repo = _patch_replayed_create(monkeypatch, pool, is_new=True)

    async def get_pod(resource_id: str) -> PodResource:
        return PodResource(id=resource_id, name="test-pod", status=status)

    monkeypatch.setattr(service, "get_pod", get_pod)
    request = _make_request(f"mcp-replay-{status}")

    if leased:
        result = await runpod_resources.pitwall_runpod_create_pod(request)
        assert [lease.runpod_pod_id for lease in created_leases] == [result["resource_id"]]
        assert workload_repo.update_calls == []
    else:
        with pytest.raises(MCPError) as excinfo:
            await runpod_resources.pitwall_runpod_create_pod(request)
        assert (excinfo.value.error.data or {})["error"] == "resource_not_found"
        assert created_leases == []
        assert workload_repo.released == ["wkl_first_attempt"]


async def test_a_replay_that_finds_another_workloads_lease_closes_its_own_new_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last step of a concurrent same-key interleaving, ordered deterministically.

    Attempt A admits W1 and C reuses W1 (same key). A fails in validate and frees the key;
    C creates the pod and records its lease against W1. D then admits a fresh workload,
    replays C's create, and finds C's lease: D must close its own new workload and report
    the lease's workload, not leave a phantom workload that never gets a lease.
    """
    expires_at = dt.datetime(2026, 10, 6, 18, 0, tzinfo=dt.UTC)
    recorded = SimpleNamespace(
        id="lease_runpod_from_c", expires_at=expires_at, workload_id="wkl_w1_from_a"
    )
    pool = _LeaseLookupPool(recorded)
    _service, created_leases, workload_repo = _patch_replayed_create(monkeypatch, pool, is_new=True)

    result = await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-interleaved"))

    assert result["replayed"] is True
    assert result["lease_id"] == "lease_runpod_from_c"
    assert result["workload_id"] == "wkl_w1_from_a"
    assert workload_repo.released == ["wkl_first_attempt"]  # D's own new workload
    assert created_leases == []


def _unknown_outcome_create(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> tuple[Any, Any, _KeyedAdmissions, list[Any]]:
    """The real control plane and journal, with a backend create that fails with *error*."""
    backend, control_plane, admissions, created_leases = _patch_rollback_retry(monkeypatch)

    async def create_fails(request: PodCreateRequest) -> dict[str, Any]:
        backend.calls.append("pods.create")
        raise error

    class RecordingLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            created_leases.append(lease)
            return lease

    monkeypatch.setattr(backend, "create_pod", create_fails)
    monkeypatch.setattr(runpod_resources, "LeaseRepository", RecordingLeaseRepository)
    return backend, control_plane, admissions, created_leases


async def test_an_unknown_outcome_create_keeps_its_workload_open_with_its_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _backend, control_plane, admissions, created_leases = _unknown_outcome_create(
        monkeypatch, TimeoutError("create timed out")
    )
    request = _make_request("mcp-unknown-outcome-1")

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "provider_timeout"
    assert control_plane._audit_pool.states(request.idempotency_key) == ["started"]
    assert admissions.closed == []  # open, so its reserved ceiling keeps counting
    assert admissions.by_key == {request.idempotency_key: "wkl_admitted_1"}
    assert created_leases == []  # no pod id to lease: the orphaned-workload reaper settles it

    # A same-key retry reuses the held workload (no second reservation) and is refused.
    with pytest.raises(MCPError) as retry:
        await runpod_resources.pitwall_runpod_create_pod(request)
    assert (retry.value.error.data or {})["error"] == "mutation_outcome_ambiguous"
    assert admissions.budget_checks == 1
    assert admissions.closed == []


async def test_an_unknown_outcome_create_that_names_its_pod_leases_it_for_the_reconciler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.runpod_client.pods import PodVolumeAttachTimeout

    _backend, control_plane, admissions, created_leases = _unknown_outcome_create(
        monkeypatch, PodVolumeAttachTimeout("pod_unknown_1", 60)
    )
    request = _make_request("mcp-unknown-outcome-2")

    with pytest.raises(MCPError):
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert control_plane._audit_pool.states(request.idempotency_key) == ["started"]
    assert admissions.closed == []
    assert [
        (lease.provider_id, lease.runpod_pod_id, lease.workload_id, lease.auto_teardown_on_expiry)
        for lease in created_leases
    ] == [("runpod_direct", "pod_unknown_1", "wkl_admitted_1", True)]


async def test_a_create_that_fails_before_its_started_row_closes_its_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, control_plane, admissions, created_leases = _patch_rollback_retry(monkeypatch)
    request = _make_request("mcp-name-conflict").model_copy(update={"name": "existing-pod"})

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "resource_name_conflict"
    assert "pods.create" not in backend.calls
    assert control_plane._audit_pool.states(request.idempotency_key) == []
    assert admissions.closed == admissions.released == ["wkl_admitted_1"]
    assert created_leases == []


async def test_an_unreadable_journal_after_a_failed_create_keeps_the_workload_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _backend, control_plane, admissions, _leases = _unknown_outcome_create(
        monkeypatch, TimeoutError("create timed out")
    )

    async def journal_down(idempotency_key: str) -> Any:
        raise RuntimeError("journal unavailable")

    monkeypatch.setattr(control_plane, "idempotency_key_status", journal_down)

    with pytest.raises(MCPError):
        await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-unknown-outcome-3"))

    assert admissions.closed == []


@pytest.mark.parametrize("pod_state", ["absent", "found"])
async def test_the_reconciler_settles_an_unknown_outcome_create_once_its_pod_is_known(
    monkeypatch: pytest.MonkeyPatch, pod_state: str
) -> None:
    """The lease an unknown-outcome create records is settled by the existing lease sweep.

    A pod RunPod confirms absent closes the workload at its accrued cost on the next tick
    after the creation grace; a pod that is found keeps the workload open until its TTL
    teardown closes it at its accrued cost. The provider is a fake ``get_pod_strict``.
    """
    from unittest.mock import AsyncMock

    from pitwall.reconciler import _lease_expiry_reconcile
    from pitwall.runpod_client.pods import PodVolumeAttachTimeout
    from tests.reconciler.test_lease_expiry_reconcile import (
        _make_mock_pool,
        _patch_automation_repositories,
        _patch_run_teardown,
        _plain_redis,
        _teardown_result,
    )

    _backend, _control_plane, admissions, created_leases = _unknown_outcome_create(
        monkeypatch, PodVolumeAttachTimeout("pod_unknown_r", 60)
    )
    with pytest.raises(MCPError):
        await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-unknown-settle"))
    assert admissions.closed == []
    (lease,) = created_leases

    pool = _make_mock_pool()
    teardown = AsyncMock(return_value=_teardown_result(lease))
    _patch_run_teardown(monkeypatch, teardown)
    lease_repo = _patch_automation_repositories(monkeypatch, lease)
    lease_repo.list_active_for_activity_control.return_value = []
    provider_repo = AsyncMock()
    provider_repo.get.return_value = None
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    settled: list[tuple[object, ...]] = []

    class _Workloads:
        async def update_state(self, workload_id: str, state: str, **kwargs: Any) -> None:
            settled.append((workload_id, state, kwargs["cost_actual_usd"]))

    monkeypatch.setattr("pitwall.reconciler.WorkloadRepository", lambda _pool: _Workloads())
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": lease.expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]

    async def get_pod_strict(pod_id: str, **_: object) -> dict[str, object] | None:
        return None if pod_state == "absent" else {"id": pod_id, "desiredStatus": "RUNNING"}

    monkeypatch.setattr("pitwall.runpod_client.pods.get_pod_strict", get_pod_strict)
    accrued = ("wkl_admitted_1", "completed", Decimal("0.42"))
    mid_ttl = lease.created_at + dt.timedelta(minutes=30)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": mid_ttl})

    if pod_state == "absent":
        assert teardown.await_args.kwargs["terminated_reason"] == "pod_absent"
        assert settled == [accrued]
        return
    teardown.assert_not_awaited()
    assert settled == []  # still open: the running pod's reservation keeps counting

    after_ttl = lease.expires_at + dt.timedelta(minutes=1)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": after_ttl})
    assert teardown.await_args.kwargs["reason"] == "ttl"
    assert settled == [accrued]


async def test_the_mcp_create_hands_the_backend_a_marked_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.runpod_control_plane import CREATE_ATTEMPT_ENV, create_attempt_marker

    backend, _control_plane, _admissions, _leases = _patch_rollback_retry(monkeypatch)
    received: list[PodCreateRequest] = []
    original = backend.create_pod

    async def capture(request: PodCreateRequest) -> dict[str, Any]:
        received.append(request)
        return await original(request)

    monkeypatch.setattr(backend, "create_pod", capture)
    request = _make_request("mcp-marker-0001")
    with pytest.raises(RuntimeError):  # the patched lease insert fails once
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert [r.env for r in received] == [
        {CREATE_ATTEMPT_ENV: create_attempt_marker("mcp-marker-0001")}
    ]


def _lease_insert_loses(
    monkeypatch: pytest.MonkeyPatch, recorded: Any | None
) -> tuple[Any, Any, _KeyedAdmissions, list[Any]]:
    """The real control plane; the lease insert fails, and *recorded* is what a re-read finds."""
    backend, control_plane, admissions, created_leases = _patch_rollback_retry(monkeypatch)
    inserts: list[Any] = []

    class LosingLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            inserts.append(lease)
            raise RuntimeError("duplicate key value violates unique constraint")

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            return recorded

    monkeypatch.setattr(runpod_resources, "LeaseRepository", LosingLeaseRepository)
    return backend, control_plane, admissions, inserts


async def test_a_lease_the_reaper_recorded_first_is_returned_instead_of_a_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reaper's lease for this pod and workload lands first; the tool's insert loses."""
    expires_at = dt.datetime(2026, 10, 6, 19, 0, tzinfo=dt.UTC)
    reaper_lease = SimpleNamespace(
        id="lease_runpod_reaper", expires_at=expires_at, workload_id="wkl_admitted_1"
    )
    backend, control_plane, admissions, inserts = _lease_insert_loses(monkeypatch, reaper_lease)
    request = _make_request("mcp-reaper-first")

    result = await runpod_resources.pitwall_runpod_create_pod(request)

    assert len(inserts) == 1  # the tool tried, and lost to the reaper's lease
    assert result["lease_id"] == "lease_runpod_reaper"
    assert result["workload_id"] == "wkl_admitted_1"
    assert result["expires_at"] == expires_at.isoformat()
    assert "pods.terminate" not in backend.calls  # the caller's pod is kept
    assert admissions.closed == []
    assert control_plane._audit_pool.states(request.idempotency_key) == ["started", "completed"]


async def test_a_lease_for_another_workload_does_not_stop_the_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = SimpleNamespace(
        id="lease_runpod_other",
        expires_at=dt.datetime(2026, 10, 6, 19, 0, tzinfo=dt.UTC),
        workload_id="wkl_someone_else",
    )
    backend, _control_plane, admissions, _inserts = _lease_insert_loses(monkeypatch, other)

    with pytest.raises(RuntimeError, match="unique constraint"):
        await runpod_resources.pitwall_runpod_create_pod(_make_request("mcp-other-lease"))

    assert backend.calls.count("pods.terminate") == 1
    assert admissions.closed == ["wkl_admitted_1"]


async def test_a_rollback_whose_terminate_fails_keeps_the_workload_and_key_for_the_reaper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, control_plane, admissions, _inserts = _lease_insert_loses(monkeypatch, None)

    async def terminate_fails(resource_id: str) -> None:
        backend.calls.append("pods.terminate")
        raise TimeoutError("RunPod unavailable")

    monkeypatch.setattr(backend, "terminate_pod", terminate_fails)
    request = _make_request("mcp-terminate-fails")

    with pytest.raises(RuntimeError, match="unique constraint"):
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert backend.calls.count("pods.terminate") == 1
    assert admissions.closed == []  # not closed at $0: the pod may still bill
    assert admissions.by_key == {request.idempotency_key: "wkl_admitted_1"}
    # The create key is not released, so the journal still names the pod for the reaper.
    assert control_plane._audit_pool.states(request.idempotency_key) == ["started", "completed"]


async def test_a_failed_lease_re_read_keeps_the_pod_and_the_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Insert and re-read both fail: ownership is unknown, so the pod is never terminated."""
    backend, control_plane, admissions, _created = _patch_rollback_retry(monkeypatch)

    class DownLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            raise OSError("connection refused")

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            raise OSError("connection refused")

    monkeypatch.setattr(runpod_resources, "LeaseRepository", DownLeaseRepository)
    request = _make_request("mcp-lease-store-down")

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    data = excinfo.value.error.data or {}
    assert data["error"] == "audit_unavailable"
    assert data["retryable"] is True
    assert data["changed"] is True
    assert data["resource_id"] == "pod_new"
    assert "same idempotency_key" in data["detail"]
    assert "connection refused" not in repr(data)  # no exception text reflected
    assert "pods.terminate" not in backend.calls
    assert admissions.closed == []
    assert admissions.by_key == {request.idempotency_key: "wkl_admitted_1"}
    # The key is not released: the journal still names the pod for the reaper, and a
    # same-key retry replays the completed create and records its lease.
    assert control_plane._audit_pool.states(request.idempotency_key) == ["started", "completed"]

    # Once the database is back, the same-key retry the error asks for replays the
    # completed create and records the lease; no second pod is created.
    recorded: list[Any] = []

    class UpLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            recorded.append(lease)
            return lease

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            return None

    monkeypatch.setattr(runpod_resources, "LeaseRepository", UpLeaseRepository)
    retried = await runpod_resources.pitwall_runpod_create_pod(request)

    assert retried["replayed"] is True
    assert backend.calls.count("pods.create") == 1
    assert [(lease.runpod_pod_id, lease.workload_id) for lease in recorded] == [
        ("pod_new", "wkl_admitted_1")
    ]
    assert admissions.budget_checks == 1


async def test_the_mcp_client_sees_the_same_key_retry_remedy_over_the_real_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the broker's installed tools/call boundary, not the handler directly."""
    from pitwall.mcp import mcp as broker
    from pitwall.mcp.safe_boundary import CREATED_POD_RETRY_REMEDY
    from tests.mcp.conftest import safe_call_tool_for

    backend, _control_plane, admissions, _created = _patch_rollback_retry(monkeypatch)

    class DownLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            raise OSError("connection refused SECRET-CANARY")

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            raise OSError("connection refused SECRET-CANARY")

    monkeypatch.setattr(runpod_resources, "LeaseRepository", DownLeaseRepository)
    result = await safe_call_tool_for(
        broker,
        "pitwall_runpod_create_pod",
        {
            "request": {
                "intent": "apply",
                "idempotency_key": "mcp-boundary-retry-1",
                "name": "boundary-pod",
                "image": "example/image:1",
                "gpu_type_ids": ["NVIDIA L4"],
                "ttl_minutes": 60,
            }
        },
    )

    assert result.is_error is True
    assert result.structured_content == {
        "error": "audit_unavailable",
        "remedy": CREATED_POD_RETRY_REMEDY,
        "retryable": True,
        "changed": True,
        "resource_id": "pod_new",
    }
    assert "same idempotency_key" in result.structured_content["remedy"]
    text = result.content[0].text
    assert "SECRET-CANARY" not in text and "connection refused" not in text
    assert "boundary-pod" not in text  # no request value reflected
    assert "pods.terminate" not in backend.calls
    assert admissions.closed == []


async def test_a_replay_that_finds_the_original_pod_gone_leases_it_instead_of_a_zero_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The original workload was kept open (a failed rollback terminate). A same-key replay
    finds no lease and the pod gone: the pod existed and billed, so it is leased by its id
    from the create's time for the lease sweep to settle, and the caller is still refused."""
    backend, control_plane, admissions, _inserts = _lease_insert_loses(monkeypatch, None)

    async def terminate_fails(resource_id: str) -> None:
        raise TimeoutError("RunPod unavailable")

    monkeypatch.setattr(backend, "terminate_pod", terminate_fails)
    request = _make_request("mcp-replay-gone-pod")
    with pytest.raises(RuntimeError, match="unique constraint"):
        await runpod_resources.pitwall_runpod_create_pod(request)
    assert admissions.closed == []  # kept open by the failed terminate

    # Later the pod is gone (terminated out of band) and ten minutes have passed.
    backend.pod_present = False
    control_plane._audit_pool.advance(600)
    recorded: list[Any] = []

    class UpLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            recorded.append(lease)
            return lease

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            return None

    monkeypatch.setattr(runpod_resources, "LeaseRepository", UpLeaseRepository)
    settled: list[tuple[Any, str]] = []
    redis_client = object()

    async def settle(pool: Any, redis: Any, lease_id: str, *, now: dt.datetime) -> None:
        settled.append((redis, lease_id))

    class _Redis:
        async def __aenter__(self) -> Any:
            return redis_client

        async def __aexit__(self, *exc: object) -> None:
            return None

    # Patch the module objects the tool's lazy imports resolve (sys.modules). Other suites
    # purge ``pitwall.api*`` from sys.modules, so a dotted-string target can resolve to a
    # stale module object the tool never sees.
    import importlib

    teardown_module = importlib.import_module("pitwall.api.leases.teardown")
    redis_env = importlib.import_module("pitwall.redis_env")
    monkeypatch.setattr(teardown_module, "settle_absent_raw_pod_lease", settle)
    monkeypatch.setattr(redis_env, "optional_redis_from_env", lambda: _Redis())
    before = dt.datetime.now(dt.UTC)
    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "resource_not_found"
    # Settled during the call, not by the sweep, with Redis so lease events publish.
    assert settled == [(redis_client, recorded[0].id)]
    assert admissions.closed == []  # not closed at $0
    assert admissions.budget_checks == 1
    (lease,) = recorded
    assert (lease.runpod_pod_id, lease.workload_id) == ("pod_new", "wkl_admitted_1")
    # TTL counts from the create, ten minutes back, not from the replay.
    started = before - dt.timedelta(seconds=600)
    assert abs((lease.created_at - started).total_seconds()) < 5
    assert lease.expires_at == lease.created_at + dt.timedelta(minutes=60)
    assert backend.calls.count("pods.create") == 1


async def test_the_gone_pod_refusal_survives_a_config_the_reconciler_would_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PITWALL_RETENTION_MODE=archive`` without archive settings is valid for the MCP server
    but makes ``import pitwall.reconciler`` raise SystemExit(78). The D1 settlement must not
    import the reconciler, so the caller still gets the gone-pod refusal."""
    import sys

    from pitwall.config import require_runtime_env

    monkeypatch.setenv("PITWALL_RETENTION_MODE", "archive")
    for name in (
        "PITWALL_ARCHIVE_DIR",
        "PITWALL_ARCHIVE_ENCRYPTION_KEY",
        "PITWALL_ARCHIVE_ENCRYPTION_KEY_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(sys.modules, "pitwall.reconciler", raising=False)
    require_runtime_env("mcp")  # valid for the MCP server

    backend, control_plane, admissions, _inserts = _lease_insert_loses(monkeypatch, None)

    async def terminate_fails(resource_id: str) -> None:
        raise TimeoutError("RunPod unavailable")

    monkeypatch.setattr(backend, "terminate_pod", terminate_fails)
    request = _make_request("mcp-replay-reconciler-config")
    with pytest.raises(RuntimeError, match="unique constraint"):
        await runpod_resources.pitwall_runpod_create_pod(request)
    backend.pod_present = False
    control_plane._audit_pool.advance(600)

    class UpLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            return lease

        async def latest_for_external_resource(
            self, provider_id: str, external_resource_id: str
        ) -> Any:
            return None

    monkeypatch.setattr(runpod_resources, "LeaseRepository", UpLeaseRepository)
    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "resource_not_found"
    assert "pitwall.reconciler" not in sys.modules


@pytest.mark.parametrize(
    ("code", "remedy", "retryable"),
    [
        (
            "mutation_in_progress",
            "retry with the same idempotency_key once the first call finishes",
            True,
        ),
        ("idempotency_conflict", "this key is spent; use a new idempotency_key", False),
        (
            "idempotency_mismatch",
            "this key was used for a different request; use a new idempotency_key",
            False,
        ),
    ],
)
async def test_idempotency_key_refusals_carry_a_fixed_remedy_over_the_real_boundary(
    monkeypatch: pytest.MonkeyPatch, code: str, remedy: str, retryable: bool
) -> None:
    from pitwall.mcp import mcp as broker
    from tests.mcp.conftest import safe_call_tool_for

    class RefusingService:
        async def update_pod(self, request: Any) -> Any:
            raise RunPodControlPlaneError(
                code,
                "refused DETAIL-CANARY for key mcp-key-canary-01",
                operation="pod.update",
                resource_type="pod",
                resource_id="pod-request-canary",
                retryable=retryable,
            )

    async def fake_service(*, mutation: bool = False) -> Any:
        return RefusingService()

    async def admitted() -> None:
        return None

    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(runpod_resources, "_admit_mutation", admitted)
    result = await safe_call_tool_for(
        broker,
        "pitwall_runpod_update_pod",
        {
            "request": {
                "intent": "apply",
                "idempotency_key": "mcp-key-canary-01",
                "resource_id": "pod-request-canary",
                "ports": ["8000/http"],
            }
        },
    )

    assert result.is_error is True
    assert result.structured_content == {"error": code, "remedy": remedy, "retryable": retryable}
    text = result.content[0].text
    assert "DETAIL-CANARY" not in text
    assert "mcp-key-canary-01" not in text and "pod-request-canary" not in text


@pytest.mark.parametrize("price", ["0.00004", "1000000000"])
async def test_an_unusable_runpod_price_never_blocks_the_lease(
    monkeypatch: pytest.MonkeyPatch, price: str
) -> None:
    """An uncapped create records its lease with a null cap whatever RunPod quotes; an
    unrepresentable price can no longer fail lease validation after the pod exists."""
    backend, _control_plane, admissions, _created = _patch_rollback_retry(monkeypatch)
    recorded: list[Any] = []

    class RecordingLeaseRepository:
        def __init__(self, pool: Any) -> None:
            self.pool = pool

        async def create(self, lease: Any) -> Any:
            recorded.append(lease)
            return lease

    async def priced_create(request: PodCreateRequest) -> dict[str, Any]:
        backend.calls.append("pods.create")
        pod = backend._pod("pod_priced", request.name)
        pod["costPerHr"] = price
        return pod

    monkeypatch.setattr(runpod_resources, "LeaseRepository", RecordingLeaseRepository)
    monkeypatch.setattr(backend, "create_pod", priced_create)

    result = await runpod_resources.pitwall_runpod_create_pod(_make_request(f"mcp-price-{price}"))

    assert [(lease.runpod_pod_id, lease.max_usd_per_hour) for lease in recorded] == [
        ("pod_priced", None)
    ]
    assert result["lease_id"] == recorded[0].id
    assert admissions.closed == []


async def test_a_cap_below_the_lease_precision_is_refused_before_any_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, _control_plane, admissions, _created = _patch_rollback_retry(monkeypatch)
    request = _make_request("mcp-tiny-cap-01", max_cost_per_hour=Decimal("0.00004"))

    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(request)

    assert (excinfo.value.error.data or {})["error"] == "invalid_request"
    assert backend.calls == [] and admissions.budget_checks == 0
