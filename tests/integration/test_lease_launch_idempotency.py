"""A repeated lease-launch idempotency key never launches a second pod (real Postgres).

The budget gate, workload and lease rows, and the admission transaction are real; only the
RunPod template and pod-create calls are fakes that count how often they run.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from pitwall.api.exceptions import IdempotencyMismatch, LeaseLaunchInProgress
from pitwall.api.leases import launch
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_gate import BudgetGate
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_READINESS = {
    "runtime_seen_at": "2026-10-06T12:00:18Z",
    "port_mappings_seen_at": "2026-10-06T12:00:19Z",
    "probe_passed_at": "2026-10-06T12:00:34Z",
    "probe_method": "runpod_proxy",
}


def _capability() -> Capability:
    return Capability(
        id="cap_idem_lease",
        name="llm.idem-lease",
        version="1",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider() -> Provider:
    return Provider(
        id="prov_idem_lease",
        capability_id="cap_idem_lease",
        name="idem-lease-pod",
        provider_type=ProviderType.POD_LEASE,
        cloud_type="SECURE",
        config={
            "image_ref": "ghcr.io/acme/pitwall-worker:idem",
            "template_name": "pitwall-idem",
            "gpu_type_priority": ["NVIDIA L4"],
            "ports": {"http": [8000]},
            "cost": {"per_second_active": "0.001"},
        },
        priority=1,
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


class _FakeRunPod:
    """Counts pod creates; persists the initial lease through the real callback."""

    def __init__(self, *, hold: asyncio.Event | None = None) -> None:
        self.creates = 0
        self.hold = hold
        self.persisted = asyncio.Event()

    async def ensure_template(self, *_args: Any, **_kwargs: Any) -> str:
        return "template-idem"

    async def create_pod(self, **kwargs: Any) -> dict[str, Any]:
        self.creates += 1
        pod = {"id": f"pod-idem-{self.creates}", "name": kwargs["name"]}
        await asyncio.to_thread(kwargs["pre_readiness_callback"], pod)
        self.persisted.set()
        if self.hold is not None:
            await self.hold.wait()
        return {**pod, "readiness": dict(_READINESS)}


def _gate(pg_pool: Any) -> BudgetGate:
    counter = itertools.count(1)
    return BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("100.00"),
        per_request_max_usd=Decimal("50.00"),
        workload_id_factory=lambda: f"wkl-idem-lease-{next(counter):03d}",
    )


def _install(monkeypatch: pytest.MonkeyPatch, runpod: _FakeRunPod) -> None:
    monkeypatch.setattr(launch, "ensure_template", runpod.ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", runpod.create_pod)


async def _launch(pg_pool: Any, gate: BudgetGate, *, fingerprint: str) -> dict[str, Any]:
    return await launch.run_launch(
        pool=pg_pool,
        capability=_capability(),
        provider=_provider(),
        budget_gate=gate,
        idempotency_key="idem-lease-key",
        request_fingerprint=fingerprint,
    )


async def _counts(pg_pool: Any) -> tuple[int, int, Decimal]:
    async with pg_pool.acquire() as conn:
        workloads = await conn.fetchval(
            "SELECT count(*) FROM pitwall.workloads WHERE idempotency_key = 'idem-lease-key'"
        )
        leases = await conn.fetchval("SELECT count(*) FROM pitwall.leases")
        reserved = await conn.fetchval(
            "SELECT COALESCE(SUM(cost_estimate_usd), 0) FROM pitwall.workloads"
        )
    return int(workloads), int(leases), Decimal(reserved)


async def test_a_repeated_key_returns_the_same_lease_with_one_pod_and_one_admission(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    runpod = _FakeRunPod()
    _install(monkeypatch, runpod)
    gate = _gate(pg_pool)

    first = await _launch(pg_pool, gate, fingerprint="fp-idem")
    second = await _launch(pg_pool, gate, fingerprint="fp-idem")

    assert runpod.creates == 1
    assert second["replayed"] is True and first.get("replayed") is not True
    assert second["lease_id"] == first["lease_id"]
    assert second["pod_id"] == first["pod_id"] == "pod-idem-1"
    assert second["workload_id"] == first["workload_id"]
    workloads, leases, reserved = await _counts(pg_pool)
    assert (workloads, leases) == (1, 1)
    assert reserved == Decimal("7.200000")
    async with pg_pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT input FROM pitwall.workloads WHERE id = $1", first["workload_id"]
        )
    assert stored == {launch.LAUNCH_REQUEST_DIGEST_KEY: "fp-idem"}


async def test_a_reused_key_with_a_different_request_is_refused_without_a_pod(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    runpod = _FakeRunPod()
    _install(monkeypatch, runpod)
    gate = _gate(pg_pool)
    await _launch(pg_pool, gate, fingerprint="fp-idem")

    with pytest.raises(IdempotencyMismatch):
        await _launch(pg_pool, gate, fingerprint="fp-different")

    assert runpod.creates == 1
    assert (await _counts(pg_pool))[:2] == (1, 1)


async def test_concurrent_same_key_launches_create_exactly_one_pod(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    hold = asyncio.Event()
    runpod = _FakeRunPod(hold=hold)
    _install(monkeypatch, runpod)
    gate = _gate(pg_pool)

    winner = asyncio.create_task(_launch(pg_pool, gate, fingerprint="fp-idem"))
    await asyncio.wait_for(runpod.persisted.wait(), timeout=10)
    # The winner's pod exists and its lease row is CREATING while readiness is pending.
    racers = await asyncio.gather(
        *(_launch(pg_pool, gate, fingerprint="fp-idem") for _ in range(5)),
        return_exceptions=True,
    )
    hold.set()
    first = await winner

    assert runpod.creates == 1
    for racer in racers:
        assert isinstance(racer, dict), racer
        assert racer["replayed"] is True and racer["lease_id"] == first["lease_id"]
    assert (await _counts(pg_pool))[:2] == (1, 1)


async def test_a_same_key_retry_before_the_lease_is_recorded_is_in_progress(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = _gate(pg_pool)
    admitted = await launch.admit_lease_launch(
        pg_pool,
        _capability(),
        _provider(),
        budget_gate=gate,
        idempotency_key="idem-lease-key",
        request_fingerprint="fp-idem",
    )
    assert admitted.is_new is True
    runpod = _FakeRunPod()
    _install(monkeypatch, runpod)

    with pytest.raises(LeaseLaunchInProgress):
        await _launch(pg_pool, gate, fingerprint="fp-idem")

    assert runpod.creates == 0
    assert (await _counts(pg_pool))[:2] == (1, 0)


# --- Fix round 1 ------------------------------------------------------------------------------


async def test_simultaneous_first_admissions_create_exactly_one_pod(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every racer is released before any admission commits; the budget lock picks one."""
    racers = 5
    hold = asyncio.Event()
    runpod = _FakeRunPod(hold=hold)
    _install(monkeypatch, runpod)
    gate = _gate(pg_pool)
    barrier = asyncio.Barrier(racers)
    admit = gate.try_launch_admission

    async def admit_together(**kwargs: Any) -> Any:
        await barrier.wait()
        return await admit(**kwargs)

    monkeypatch.setattr(gate, "try_launch_admission", admit_together)

    tasks = [
        asyncio.create_task(_launch(pg_pool, gate, fingerprint="fp-idem")) for _ in range(racers)
    ]
    while sum(task.done() for task in tasks) < racers - 1:
        await asyncio.sleep(0.01)
    hold.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert runpod.creates == 1
    launched = [r for r in results if isinstance(r, dict) and r.get("replayed") is not True]
    assert len(launched) == 1
    for result in results:
        if isinstance(result, dict):
            assert result["lease_id"] == launched[0]["lease_id"]
        else:
            assert isinstance(result, LeaseLaunchInProgress), result
    workloads, leases, reserved = await _counts(pg_pool)
    assert (workloads, leases, reserved) == (1, 1, Decimal("7.200000"))


async def test_a_no_capacity_failure_before_any_pod_frees_the_key_for_a_retry(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.runpod_client.pods import NoCapacityError

    runpod = _FakeRunPod()
    attempts = 0

    async def create_pod(**kwargs: Any) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise NoCapacityError("no capacity", pod_attempts=0)
        return await runpod.create_pod(**kwargs)

    monkeypatch.setattr(launch, "ensure_template", runpod.ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", create_pod)
    gate = _gate(pg_pool)

    with pytest.raises(NoCapacityError):
        await _launch(pg_pool, gate, fingerprint="fp-idem")
    retried = await _launch(pg_pool, gate, fingerprint="fp-idem")

    assert retried.get("replayed") is not True and retried["pod_id"] == "pod-idem-1"
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT idempotency_key, state, cost_actual_usd, cost_actual_provenance"
            " FROM pitwall.workloads ORDER BY submitted_at"
        )
    assert [tuple(row) for row in rows] == [
        (None, "failed", Decimal("0.000000"), "lease_launch_precreation_failure"),
        ("idem-lease-key", "running", None, None),
    ]


async def test_an_unknown_outcome_create_keeps_the_key_spent(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.api.exceptions import IdempotencyConflict
    from pitwall.runpod_client.pods import NoCapacityError

    runpod = _FakeRunPod()

    async def create_pod(**_kwargs: Any) -> dict[str, Any]:
        raise NoCapacityError("no capacity", ambiguous_create=True)

    monkeypatch.setattr(launch, "ensure_template", runpod.ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", create_pod)
    gate = _gate(pg_pool)

    with pytest.raises(NoCapacityError):
        await _launch(pg_pool, gate, fingerprint="fp-idem")
    with pytest.raises(IdempotencyConflict):
        await _launch(pg_pool, gate, fingerprint="fp-idem")


# Keyed lease retries on the Lambda Cloud and Vast adapters, through the shared routed path.

_LAMBDA_CONFIG: dict[str, Any] = {
    "launch": {
        "region_name": "us-west-1",
        "instance_type_name": "gpu_1x_a10",
        "ssh_key_names": ["pitwall-ci"],
        "image": {"family": "lambda-stack"},
    },
    "lease_ttl_ms": 7_200_000,
    "cost": {"kind": "per_vm_second", "rate_per_second": "0.00016"},
}
_VAST_CONFIG: dict[str, Any] = {
    "ask_id": 12345,
    "create": {"image": "pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime", "disk": 40},
    "cost": {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"},
}


class _RoutedPlan:
    def __init__(self, provider_id: str) -> None:
        self.plan_id = "plan_" + "1" * 32
        self.selected_provider_id = provider_id

    def to_dict(self) -> dict[str, object]:
        return {"plan_id": self.plan_id, "selected_provider_id": self.selected_provider_id}


class _Routing:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        self.plans = 0

    async def plan_execution(self, **_kwargs: Any) -> tuple[_RoutedPlan, dict[str, Any]]:
        self.plans += 1
        return _RoutedPlan(self.provider_id), {}


async def _seed_vm_provider(pg_pool: Any, adapter: str) -> tuple[Capability, Provider]:
    from pitwall.core.enums import ProviderAdapterId
    from pitwall.db.repository import CapabilityRepository, ProviderRepository

    capability = await CapabilityRepository(pg_pool).create(
        Capability(
            id=f"cap_vm_{adapter}",
            name=f"gpu.vm-{adapter}",
            version="1",
            class_=CapabilityClass.GPU_LEASE,
            cost_mode=CostMode.PER_SECOND,
            source=CapabilitySource.API,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    provider = await ProviderRepository(pg_pool).create(
        Provider(
            id=f"prov_vm_{adapter}",
            capability_id=capability.id,
            name=f"vm-{adapter}",
            provider_type=ProviderType.POD_LEASE,
            adapter_id=ProviderAdapterId(adapter),
            config=_LAMBDA_CONFIG if adapter == "lambda_cloud" else _VAST_CONFIG,
            priority=1,
            source=CapabilitySource.API,
            updated_at=_NOW,
        )
    )
    return capability, provider


def _vm_adapter(adapter: str, requests: list[Any]) -> Any:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if adapter == "lambda_cloud":
            return httpx.Response(200, json={"data": {"instance_ids": [f"i-{len(requests)}"]}})
        return httpx.Response(200, json={"success": True, "new_contract": 900 + len(requests)})

    transport = httpx.MockTransport(handler)
    if adapter == "lambda_cloud":
        from pitwall.providers.lambda_cloud import LambdaCloudProvider

        return LambdaCloudProvider(transport=transport, launch_interval_s=0, request_interval_s=0)
    from pitwall.providers.vast import VastProvider

    return VastProvider(transport=transport)


async def _routed(pg_pool: Any, routing: _Routing, capability: Capability, **kwargs: Any) -> Any:
    from pitwall.db.repository import (
        CapabilityRepository,
        LeaseRepository,
        ProviderRepository,
        WorkloadRepository,
    )

    return await launch.create_routed_lease(
        pool=pg_pool,
        capability_repo=CapabilityRepository(pg_pool),
        provider_repo=ProviderRepository(pg_pool),
        lease_repo=LeaseRepository(pg_pool),
        workload_repo=WorkloadRepository(pg_pool),
        routing_service=routing,
        capability_ref=capability.name,
        idempotency_key="idem-vm-key",
        dry_run=False,
        **kwargs,
    )


@pytest.mark.parametrize("adapter", ["lambda_cloud", "vast"])
async def test_a_keyed_vm_lease_retry_returns_the_original_lease_and_provisions_once(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, adapter: str
) -> None:
    capability, provider = await _seed_vm_provider(pg_pool, adapter)
    monkeypatch.setenv(provider.credential_ref, f"{adapter}-test-key")
    requests: list[Any] = []
    vm_adapter = _vm_adapter(adapter, requests)

    class Registry:
        def lookup_compute(self, _adapter_id: str) -> Any:
            return vm_adapter

    monkeypatch.setattr(launch, "get_default_registry", Registry)
    monkeypatch.setattr(launch, "BudgetGate", lambda pool: _gate(pool))
    routing = _Routing(provider.id)

    first = await _routed(pg_pool, routing, capability, provider_id=None)
    second = await _routed(pg_pool, routing, capability, provider_id=None)

    assert len(requests) == 1
    assert first.replayed is False and second.replayed is True
    assert first.lease is not None and second.lease is not None
    assert second.lease.id == first.lease.id
    assert second.route_plan_fields()["route_plan_id"] == "plan_" + "1" * 32
    assert routing.plans == 1

    with pytest.raises(IdempotencyMismatch):
        await _routed(pg_pool, routing, capability, provider_id=provider.id)
    assert len(requests) == 1
    async with pg_pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM pitwall.leases")
    assert count == 1


@pytest.mark.parametrize("adapter", ["lambda_cloud", "vast"])
async def test_a_digestless_vm_lease_workload_falls_through_to_the_adapter_replay(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, adapter: str
) -> None:
    """A keyed workload admitted before digests still replays through the adapter."""
    capability, provider = await _seed_vm_provider(pg_pool, adapter)
    monkeypatch.setenv(provider.credential_ref, f"{adapter}-test-key")
    requests: list[Any] = []
    vm_adapter = _vm_adapter(adapter, requests)

    class Registry:
        def lookup_compute(self, _adapter_id: str) -> Any:
            return vm_adapter

    monkeypatch.setattr(launch, "get_default_registry", Registry)
    monkeypatch.setattr(launch, "BudgetGate", lambda pool: _gate(pool))
    routing = _Routing(provider.id)
    first = await _routed(pg_pool, routing, capability, provider_id=None)
    async with pg_pool.acquire() as conn:
        await conn.execute("UPDATE pitwall.workloads SET input = NULL")

    second = await _routed(pg_pool, routing, capability, provider_id=None)

    assert len(requests) == 1
    assert second.replayed is True and second.lease.id == first.lease.id
