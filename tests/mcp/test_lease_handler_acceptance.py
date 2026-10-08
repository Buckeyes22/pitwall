"""Direct MCP lease handler contracts; all launch/persistence effects are fake."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall.core.enums import LeaseState
from pitwall.mcp.tools import leases
from tests.leases.test_teardown import _lease

pytestmark = pytest.mark.anyio


def _fingerprint(capability_id, provider_id):
    launch = importlib.import_module("pitwall.api.leases.launch")
    return launch.routed_lease_fingerprint(capability_id, provider_id)


class _Plan:
    plan_id = "plan_mcp_lease"
    selected_provider_id = "provider_selected"

    def to_dict(self):
        return {"plan_id": self.plan_id, "selected_provider_id": self.selected_provider_id}


def _setup(monkeypatch):
    pool = object()
    cap = SimpleNamespace(id="cap_internal")
    provider = SimpleNamespace(id="provider_selected", provider_type="pod_lease")
    cap_repo = SimpleNamespace(
        get_by_name=AsyncMock(return_value=cap), get=AsyncMock(return_value=cap)
    )
    provider_repo = SimpleNamespace(get=AsyncMock(return_value=provider))
    stored = _lease(LeaseState.ACTIVE).model_copy(
        update={"provider_id": provider.id, "workload_id": "wkl_mcp_lease"}
    )
    lease_repo = SimpleNamespace(
        get=AsyncMock(return_value=stored), get_by_workload=AsyncMock(return_value=stored)
    )
    workload_repo = SimpleNamespace(
        attach_route_plan=AsyncMock(),
        get_by_idempotency_key=AsyncMock(return_value=None),
        get=AsyncMock(return_value=None),
    )
    routing = SimpleNamespace(
        preview=AsyncMock(return_value=_Plan()),
        plan_execution=AsyncMock(return_value=(_Plan(), {})),
    )
    launch = AsyncMock(
        return_value={"template_id": "template_selected", "template_name": "selected"}
    )
    monkeypatch.setattr(leases, "get_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(leases, "CapabilityRepository", lambda p: cap_repo)
    monkeypatch.setattr(leases, "ProviderRepository", lambda p: provider_repo)
    monkeypatch.setattr(leases, "LeaseRepository", lambda p: lease_repo)
    monkeypatch.setattr(leases, "WorkloadRepository", lambda p: workload_repo)
    # Patch the module objects sys.modules holds: the handler imports these at call time,
    # and other suites evict and re-import pitwall.api modules.
    monkeypatch.setattr(importlib.import_module("pitwall.api.leases.launch"), "run_launch", launch)
    # Patch the handler's factory, not the class: a module imported while a class patch is
    # live (pitwall.api.routes.openai, via the launch path) would keep the stub for good.
    monkeypatch.setattr(leases, "get_production_routing_service", AsyncMock(return_value=routing))
    return SimpleNamespace(
        pool=pool,
        cap=cap,
        provider=provider,
        caps=cap_repo,
        providers=provider_repo,
        stored=stored,
        lease_repo=lease_repo,
        workloads=workload_repo,
        routing=routing,
        launch=launch,
    )


@pytest.mark.parametrize("name_exists", [True, False])
async def test_dry_run_resolves_the_capability_and_previews_through_the_planner(
    monkeypatch, name_exists
):
    f = _setup(monkeypatch)
    if not name_exists:
        f.caps.get_by_name.return_value = None
    result = await leases.pitwall_lease_pod(
        "cap.example",
        provider_id="provider_selected",
        dry_run=True,
        idempotency_key="mcp-lease-plan-001",
    )
    f.caps.get_by_name.assert_awaited_once_with("cap.example")
    if name_exists:
        f.caps.get.assert_not_awaited()
    else:
        f.caps.get.assert_awaited_once_with("cap.example")
    preview = f.routing.preview.await_args.kwargs
    assert preview["operation"].value == "compute"
    assert preview["provider_id"] == "provider_selected"
    f.routing.plan_execution.assert_not_awaited()
    f.providers.get.assert_awaited_once_with("provider_selected")
    f.launch.assert_awaited_once_with(
        pool=f.pool,
        capability=f.cap,
        provider=f.provider,
        idempotency_key="mcp-lease-plan-001",
        dry_run=True,
        request_fingerprint=_fingerprint("cap_internal", "provider_selected"),
    )
    f.workloads.get_by_idempotency_key.assert_not_awaited()
    f.lease_repo.get.assert_not_awaited()
    assert result == {
        "id": None,
        "state": "dry_run",
        "dry_run": True,
        "capability_id": "cap_internal",
        "provider_id": "provider_selected",
        "template_id": "template_selected",
        "template_name": "selected",
        "route_plan_id": "plan_mcp_lease",
        "route_plan": _Plan().to_dict(),
    }


async def test_implicit_selection_skips_higher_priority_non_pod_provider(monkeypatch):
    """The tool asks the planner for a compute route and launches exactly its selection.

    For compute, the planner eliminates a higher-priority provider without compute support
    and any RunPod provider that is not a pod lease (tests/routing/test_production_routing.py
    ::test_compute_routes_only_leasable_runpod_providers), so a non-pod provider is never
    launched here.
    """
    f = _setup(monkeypatch)
    await leases.pitwall_lease_pod("cap.example", dry_run=True)
    assert f.routing.preview.await_args.kwargs["operation"].value == "compute"
    assert f.routing.preview.await_args.kwargs["provider_id"] is None
    f.providers.get.assert_awaited_once_with(_Plan.selected_provider_id)
    assert f.launch.await_args.kwargs["provider"] is f.provider


@pytest.mark.parametrize("failure", ["capability", "provider", "no_route"])
async def test_missing_selection_never_reaches_launch(monkeypatch, failure):
    f = _setup(monkeypatch)
    # Resolve the classes when the test runs: other suites evict and re-import
    # pitwall.api modules, and the handler imports these lazily at call time.
    exceptions = importlib.import_module("pitwall.api.exceptions")
    production = importlib.import_module("pitwall.routing.production")
    if failure == "capability":
        f.caps.get_by_name.return_value = None
        f.caps.get.return_value = None
        error, code = exceptions.CapabilityNotFound, "capability_not_found"
    elif failure == "provider":
        f.providers.get.return_value = None
        error, code = exceptions.ProviderNotFound, "provider_not_found"
    else:
        f.routing.preview.side_effect = production.NoExecutableRouteError("cap.example", [])
        error, code = exceptions.ProviderUnavailable, "no_providers_available"
    with pytest.raises(error) as raised:
        await leases.pitwall_lease_pod("cap.example", dry_run=True)
    assert raised.value.error_code == code
    f.launch.assert_not_awaited()
    f.lease_repo.get.assert_not_awaited()


async def test_created_lease_is_refetched_and_serialized(monkeypatch):
    f = _setup(monkeypatch)
    f.launch.return_value = {"lease_id": f.stored.id}
    result = await leases.pitwall_lease_pod(
        "cap.example", provider_id="provider_selected", idempotency_key="mcp-lease-apply-001"
    )
    f.routing.plan_execution.assert_awaited_once()
    f.launch.assert_awaited_once_with(
        pool=f.pool,
        capability=f.cap,
        provider=f.provider,
        idempotency_key="mcp-lease-apply-001",
        dry_run=False,
        request_fingerprint=_fingerprint("cap_internal", "provider_selected"),
    )
    f.lease_repo.get.assert_awaited_once_with(f.stored.id)
    f.workloads.attach_route_plan.assert_awaited_once_with(
        "wkl_mcp_lease", route_plan_id="plan_mcp_lease", route_plan=_Plan().to_dict()
    )
    assert result["id"] == f.stored.id and result["state"] == "active"
    assert result["provider_id"] == "provider_selected"
    assert result["expires_at"] == f.stored.expires_at.isoformat()
    assert result["endpoints"] == f.stored.endpoints.model_dump(mode="json")
    assert result["route_plan_id"] == "plan_mcp_lease"
    assert result["replayed"] is False


_RECORDED_PLAN = "plan_" + "0" * 32


async def test_repeated_key_returns_the_same_lease_and_launches_once(monkeypatch):
    """A retried pitwall_lease_pod with the same key never launches a second pod."""
    from pitwall.core.models import Workload

    f = _setup(monkeypatch)
    f.launch.return_value = {"lease_id": f.stored.id}
    first = await leases.pitwall_lease_pod("cap.example", idempotency_key="mcp-lease-retry")
    f.workloads.get_by_idempotency_key.return_value = Workload(
        id="wkl_mcp_lease",
        capability_id="cap_internal",
        provider_id="provider_selected",
        type="inference",
        state="running",
        idempotency_key="mcp-lease-retry",
        input={
            importlib.import_module(
                "pitwall.api.leases.launch"
            ).LAUNCH_REQUEST_DIGEST_KEY: _fingerprint("cap_internal", None)
        },
        route_plan_id=_RECORDED_PLAN,
        route_plan={"plan_id": _RECORDED_PLAN, "selected_provider_id": "provider_selected"},
        submitted_at=f.stored.created_at,
    )

    second = await leases.pitwall_lease_pod("cap.example", idempotency_key="mcp-lease-retry")

    f.launch.assert_awaited_once()
    f.routing.plan_execution.assert_awaited_once()
    assert second["id"] == first["id"] == f.stored.id
    assert second["replayed"] is True and first["replayed"] is False
    assert second["route_plan_id"] == _RECORDED_PLAN
    lease_fields = {k for k in first if k not in {"replayed", "route_plan_id", "route_plan"}}
    assert {k: second[k] for k in lease_fields} == {k: first[k] for k in lease_fields}
    assert set(second) == set(first)


async def test_repeated_key_with_a_different_request_is_idempotency_mismatch(monkeypatch):
    from pitwall.core.models import Workload

    f = _setup(monkeypatch)
    f.workloads.get_by_idempotency_key.return_value = Workload(
        id="wkl_mcp_lease",
        capability_id="cap_internal",
        provider_id="provider_selected",
        type="inference",
        state="running",
        idempotency_key="mcp-lease-retry",
        input={
            importlib.import_module(
                "pitwall.api.leases.launch"
            ).LAUNCH_REQUEST_DIGEST_KEY: _fingerprint("cap_internal", None)
        },
        submitted_at=f.stored.created_at,
    )
    exceptions = importlib.import_module("pitwall.api.exceptions")

    with pytest.raises(exceptions.IdempotencyMismatch) as raised:
        await leases.pitwall_lease_pod(
            "cap.example", provider_id="provider_other", idempotency_key="mcp-lease-retry"
        )
    assert raised.value.error_code == "idempotency_mismatch"
    f.launch.assert_not_awaited()
