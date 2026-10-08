"""A lease renewal reserves rate x extension under the budget lock (real Postgres).

Covers the REST/MCP/CLI renewal service and the reconciler's activity renewal on RunPod,
Lambda Cloud, and Vast leases, the audit actors those surfaces write (migration 0042), and
RunPod settlement after a renewal. Only provider HTTP calls are fakes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from pitwall.api.leases import teardown
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, Provider
from pitwall.cost.budget_gate import BudgetGate, BudgetRejected, month_to_date_spend
from pitwall.db.repository import CapabilityRepository, LeaseRepository, ProviderRepository
from pitwall.leases.mutations import renew_lease
from pitwall.providers import ProviderOperationContext, ProvisionRequest
from pitwall.providers.interface import CredentialReference
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


def _budget(monkeypatch: pytest.MonkeyPatch, monthly: str) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", monthly)
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "50")


async def _capability(pg_pool: Any, suffix: str) -> Capability:
    now = dt.datetime.now(dt.UTC)
    return await CapabilityRepository(pg_pool).create(
        Capability(
            id=f"cap_renew_{suffix}",
            name=f"gpu.renew-{suffix}",
            version="1",
            class_=CapabilityClass.GPU_LEASE,
            cost_mode=CostMode.PER_SECOND,
            source=CapabilitySource.API,
            created_at=now,
            updated_at=now,
        )
    )


async def _runpod_lease(
    pg_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
    monthly: str,
    cost: dict[str, str] | None = None,
) -> Lease:
    """A RunPod lease at 0.001 USD/s admitted for 1 h (3.6 USD) that started 30 min ago.

    ``cost`` replaces the provider's legacy ``per_second_active`` cost (tagged pricing).
    """
    _budget(monkeypatch, monthly)
    capability = await _capability(pg_pool, "runpod")
    provider = await ProviderRepository(pg_pool).create(
        Provider(
            id="prov_renew_runpod",
            capability_id=capability.id,
            name="renew-runpod",
            provider_type=ProviderType.POD_LEASE,
            config={
                "cost": cost or {"per_second_active": "0.001"},
                "lease_ttl_ms": 3_600_000,
            },
            priority=1,
            source=CapabilitySource.API,
            updated_at=dt.datetime.now(dt.UTC),
        )
    )
    admission = await BudgetGate(pg_pool).try_launch_admission(
        capability_id=capability.id, provider_id=provider.id, estimate_usd=Decimal("3.600000")
    )
    started = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
    return await LeaseRepository(pg_pool).create(
        Lease(
            id="lease_renew_runpod",
            provider_id=provider.id,
            workload_id=admission.workload_id,
            runpod_pod_id="pod-renew-1",
            state=LeaseState.CREATING,
            created_at=started,
            expires_at=started + dt.timedelta(hours=1),
            renewal_policy=LeaseRenewalPolicy.MANUAL,
        )
    )


async def _ceiling(pg_pool: Any, workload_id: str | None) -> Decimal:
    async with pg_pool.acquire() as conn:
        return Decimal(
            await conn.fetchval(
                "SELECT cost_ceiling_usd FROM pitwall.workloads WHERE id = $1", workload_id
            )
        )


async def _spend(pg_pool: Any) -> Decimal:
    async with pg_pool.acquire() as conn:
        return await month_to_date_spend(conn)


async def _renew(pg_pool: Any, lease_id: str, minutes: int, actor: str = "rest:lease") -> Lease:
    return await renew_lease(
        LeaseRepository(pg_pool), lease_id, extends_minutes=minutes, actor=actor, pool=pg_pool
    )


async def test_a_runpod_renewal_within_budget_reserves_rate_times_extension(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = await _runpod_lease(pg_pool, monkeypatch, "100")

    renewed = await _renew(pg_pool, lease.id, 60)

    assert renewed.expires_at == lease.expires_at + dt.timedelta(minutes=60)
    assert await _ceiling(pg_pool, lease.workload_id) == Decimal("7.200000")
    assert await _spend(pg_pool) == Decimal("7.200000")


@pytest.mark.parametrize("budget", [None, "lots"])
async def test_a_same_key_renewal_retry_replays_after_the_budget_setting_breaks(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, budget: str | None
) -> None:
    """A retry of an applied renewal is answered from its record, before any budget check."""
    from pitwall.cost.budget_gate import BudgetNotConfigured

    lease = await _runpod_lease(pg_pool, monkeypatch, "100")
    repo = LeaseRepository(pg_pool)
    first = await renew_lease(
        repo,
        lease.id,
        extends_minutes=60,
        actor="rest:lease",
        idempotency_key="renew-retry-1",
        pool=pg_pool,
    )
    if budget is None:
        monkeypatch.delenv("PITWALL_MONTHLY_BUDGET_USD")
    else:
        monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", budget)

    replayed = await renew_lease(
        repo,
        lease.id,
        extends_minutes=60,
        actor="rest:lease",
        idempotency_key="renew-retry-1",
        pool=pg_pool,
    )

    assert replayed.expires_at == first.expires_at
    assert await _ceiling(pg_pool, lease.workload_id) == Decimal("7.200000")  # reserved once
    # A new renewal (another key) still needs a valid budget.
    with pytest.raises(BudgetNotConfigured):
        await renew_lease(
            repo,
            lease.id,
            extends_minutes=60,
            actor="rest:lease",
            idempotency_key="renew-retry-2",
            pool=pg_pool,
        )


async def test_a_runpod_renewal_past_budget_is_refused_and_keeps_its_expiry(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = await _runpod_lease(pg_pool, monkeypatch, "5")

    with pytest.raises(BudgetRejected) as refused:
        await _renew(pg_pool, lease.id, 60)

    assert refused.value.reason == "monthly_budget"
    current = await LeaseRepository(pg_pool).get(lease.id)
    assert current is not None and current.expires_at == lease.expires_at
    assert await _ceiling(pg_pool, lease.workload_id) == Decimal("3.600000")
    async with pg_pool.acquire() as conn:
        audits = await conn.fetchval(
            "SELECT count(*) FROM pitwall.config_audit WHERE action = 'renew'"
        )
    assert audits == 0


async def test_runpod_settlement_after_a_renewal_is_rate_times_actual_runtime(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = await _runpod_lease(pg_pool, monkeypatch, "100")
    await _renew(pg_pool, lease.id, 600)
    assert await _spend(pg_pool) == Decimal("39.600000")  # 3.6 + 0.001 x 600 min
    monkeypatch.setattr(teardown, "terminate_pod", _no_op)

    closed = await teardown.run_teardown(
        lease.id, pool=pg_pool, now=lease.created_at + dt.timedelta(minutes=90)
    )

    assert closed.lease.cost_accrued_usd == Decimal("5.400000")  # 0.001 x 90 min
    assert await _spend(pg_pool) == Decimal("5.400000")


async def test_a_tagged_pricing_runpod_lease_renews_and_settles_at_its_rate_never_zero(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tagged ``per_second`` pricing (no ``per_second_active``, no cap) is the rate both ways."""
    lease = await _runpod_lease(
        pg_pool, monkeypatch, "100", cost={"kind": "per_second", "rate_per_second": "0.001"}
    )
    await _renew(pg_pool, lease.id, 60)
    assert await _ceiling(pg_pool, lease.workload_id) == Decimal("7.200000")  # + 3.6 USD/h x 1 h
    monkeypatch.setattr(teardown, "terminate_pod", _no_op)

    closed = await teardown.run_teardown(
        lease.id, pool=pg_pool, now=lease.created_at + dt.timedelta(minutes=90)
    )

    assert closed.lease.cost_accrued_usd == Decimal("5.400000")  # 0.001 x 90 min, not 0
    assert await _spend(pg_pool) == Decimal("5.400000")


async def _no_op(*_args: Any, **_kwargs: Any) -> None:
    return None


async def test_an_uncapped_raw_pod_renews_and_settles_at_its_recorded_cost_per_hour(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RunPod recorded costPerHr 3.00 for the pod: renewal reserves it and teardown charges it."""
    from pitwall.api.leases.launch import raw_pod_lease
    from pitwall.runpod_control_plane import PodCreateRequest, RunPodControlPlaneService
    from tests.runpod_control_plane.test_service import RecordingBackend

    _budget(monkeypatch, "100")
    backend = RecordingBackend()

    async def create(request: PodCreateRequest) -> dict[str, Any]:
        pod = backend._pod("pod_raw_renew", request.name)
        pod["costPerHr"] = "3.00"
        return pod

    backend.create_pod = create  # type: ignore[method-assign]  # reason: a priced pod
    service = RunPodControlPlaneService(
        backend=backend, audit_pool=pg_pool, environ={"RUNPOD_API_KEY": "renew-key"}, timeout_s=1
    )
    created = await service.create_pod(
        PodCreateRequest(
            intent="apply",
            idempotency_key="raw-pod-renew",
            name="raw-pod-renew",
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
        )
    )
    assert created.resource_id == "pod_raw_renew"
    async with pg_pool.acquire() as conn:  # the uncapped admission: 0.50 USD/h x 60 min
        await conn.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state,"
            " idempotency_key, submitted_at, cost_estimate_usd, cost_ceiling_usd)"
            " VALUES ('wkl_raw_renew', 'runpod_direct', 'runpod_direct', 'inference',"
            " 'running', 'raw-pod-renew', now(), 0.50, 0.50)"
        )
    started = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
    lease = await LeaseRepository(pg_pool).create(
        raw_pod_lease(
            pod_id="pod_raw_renew",
            workload_id="wkl_raw_renew",
            ttl_minutes=60,
            max_cost_per_hour=None,
            created_at=started,
        )
    )

    await _renew(pg_pool, lease.id, 60)

    assert await _ceiling(pg_pool, lease.workload_id) == Decimal("3.500000")  # 0.50 + 3.00 x 1 h
    monkeypatch.setattr(teardown, "terminate_pod", _no_op)
    closed = await teardown.run_teardown(
        lease.id, pool=pg_pool, now=lease.created_at + dt.timedelta(minutes=90)
    )
    assert closed.lease.cost_accrued_usd == Decimal("4.500000")  # 3.00 x 1.5 h
    assert await _spend(pg_pool) == Decimal("4.500000")


# Lambda Cloud and Vast leases, provisioned through their adapters.

_VM_CONFIG: dict[str, dict[str, Any]] = {
    "lambda_cloud": {
        "launch": {
            "region_name": "us-west-1",
            "instance_type_name": "gpu_1x_a10",
            "ssh_key_names": ["pitwall-ci"],
            "image": {"family": "lambda-stack"},
        },
        "lease_ttl_ms": 7_200_000,
        "cost": {"kind": "per_vm_second", "rate_per_second": "0.00016"},
    },
    "vast": {
        "ask_id": 12345,
        "create": {"image": "pytorch/pytorch:2.4.0", "disk": 40},
        "cost": {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"},
    },
}


async def _vm_lease(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, adapter: str, monthly: str
) -> Lease:
    _budget(monkeypatch, monthly)
    capability = await _capability(pg_pool, adapter)
    provider = await ProviderRepository(pg_pool).create(
        Provider(
            id=f"prov_renew_{adapter}",
            capability_id=capability.id,
            name=f"renew-{adapter}",
            provider_type=ProviderType.POD_LEASE,
            adapter_id=ProviderAdapterId(adapter),
            config=_VM_CONFIG[adapter],
            priority=1,
            source=CapabilitySource.API,
            updated_at=dt.datetime.now(dt.UTC),
        )
    )
    monkeypatch.setenv(provider.credential_ref, f"{adapter}-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if adapter == "lambda_cloud":
            return httpx.Response(200, json={"data": {"instance_ids": ["vm-renew-1"]}})
        return httpx.Response(200, json={"success": True, "new_contract": 4242})

    transport = httpx.MockTransport(handler)
    if adapter == "lambda_cloud":
        from pitwall.providers.lambda_cloud import LambdaCloudProvider

        vm: Any = LambdaCloudProvider(
            transport=transport, launch_interval_s=0, request_interval_s=0
        )
    else:
        from pitwall.providers.vast import VastProvider

        vm = VastProvider(transport=transport)
    result = await vm.provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=pg_pool),
            capability=capability,
            provider_record=provider,
            credentials=CredentialReference(provider.credential_ref),
            budget_gate=BudgetGate(pg_pool),
        )
    )
    lease = await LeaseRepository(pg_pool).get(result.lease_id or "")
    assert lease is not None
    return lease


@pytest.mark.parametrize(
    ("adapter", "reserved", "renewed"),
    [
        ("lambda_cloud", Decimal("1.152000"), Decimal("1.728000")),  # + 0.00016 x 60 min
        ("vast", Decimal("1.440000"), Decimal("2.160000")),  # + 0.72 USD/h bid x 60 min
    ],
)
async def test_a_vm_lease_renewal_reserves_its_adapter_rate_times_extension(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, adapter: str, reserved: Decimal, renewed: Decimal
) -> None:
    lease = await _vm_lease(pg_pool, monkeypatch, adapter, "100")
    assert await _ceiling(pg_pool, lease.workload_id) == reserved

    await _renew(pg_pool, lease.id, 60)

    assert await _ceiling(pg_pool, lease.workload_id) == renewed
    assert await _spend(pg_pool) == renewed


@pytest.mark.parametrize("adapter", ["lambda_cloud", "vast"])
async def test_a_vm_lease_renewal_past_budget_is_refused_and_keeps_its_expiry(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, adapter: str
) -> None:
    lease = await _vm_lease(pg_pool, monkeypatch, adapter, "1.5")

    with pytest.raises(BudgetRejected):
        await _renew(pg_pool, lease.id, 600)

    current = await LeaseRepository(pg_pool).get(lease.id)
    assert current is not None and current.expires_at == lease.expires_at


# The CLI and reconciler actors (migration 0042).


async def test_a_cli_renewal_is_audited_as_cli_lease(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.registry_admin import renew_operator_lease

    lease = await _runpod_lease(pg_pool, monkeypatch, "100")

    renewed = await renew_operator_lease(pg_pool, None, lease.id, 30)

    assert renewed.expires_at == lease.expires_at + dt.timedelta(minutes=30)
    async with pg_pool.acquire() as conn:
        actor = await conn.fetchval(
            "SELECT actor FROM pitwall.config_audit WHERE action = 'renew' AND entity_id = $1",
            lease.id,
        )
    assert actor == "cli:lease"


def _sweep(pg_pool: Any) -> Any:
    return SimpleNamespace(
        pool=pg_pool,
        redis=None,
        settings=SimpleNamespace(pitwall_lease_max_lifetime_min=1440),
        lease_repo=LeaseRepository(pg_pool),
    )


async def test_a_reconciler_auto_renewal_is_audited_and_reserved(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.reconciler import _renew_activity_lease

    lease = await _runpod_lease(pg_pool, monkeypatch, "100")
    provider = await ProviderRepository(pg_pool).get(lease.provider_id)
    assert provider is not None

    outcome = await _renew_activity_lease(_sweep(pg_pool), lease, provider, "gpu.renew-runpod")

    assert outcome == "renewed"
    async with pg_pool.acquire() as conn:
        actor = await conn.fetchval(
            "SELECT actor FROM pitwall.config_audit WHERE action = 'renew' AND entity_id = $1",
            lease.id,
        )
    assert actor == "reconciler:activity"
    assert await _ceiling(pg_pool, lease.workload_id) == Decimal("7.200000")


async def test_a_reconciler_auto_renewal_past_budget_stops_renewing(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.reconciler import _renew_activity_lease

    lease = await _runpod_lease(pg_pool, monkeypatch, "5")
    provider = await ProviderRepository(pg_pool).get(lease.provider_id)
    assert provider is not None

    outcome = await _renew_activity_lease(_sweep(pg_pool), lease, provider, "gpu.renew-runpod")

    assert outcome == "budget"
    current = await LeaseRepository(pg_pool).get(lease.id)
    assert current is not None and current.expires_at == lease.expires_at
