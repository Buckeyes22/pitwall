"""Lambda Cloud and Vast lease spend reaches the budget (real Postgres).

Admission reserves the adapter's rate for the whole lease TTL, the lease is linked to its
admission workload, and teardown settles that workload at the adapter's rate for the time the
VM ran, releasing the rest of the reservation. Only the provider HTTP calls are fakes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx
import pytest

from pitwall.api.leases import teardown
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Provider
from pitwall.cost.budget_gate import BudgetGate, month_to_date_spend
from pitwall.db.repository import CapabilityRepository, ProviderRepository
from pitwall.providers import ProviderOperationContext, ProvisionRequest
from pitwall.providers.interface import CredentialReference
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_LAMBDA_COST = {"kind": "per_vm_second", "rate_per_second": "0.00016"}
_VAST_COST = {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"}


def _config(adapter: str, cost: dict[str, Any]) -> dict[str, Any]:
    if adapter == "lambda_cloud":
        return {
            "launch": {
                "region_name": "us-west-1",
                "instance_type_name": "gpu_1x_a10",
                "ssh_key_names": ["pitwall-ci"],
                "image": {"family": "lambda-stack"},
            },
            "lease_ttl_ms": 7_200_000,
            "cost": cost,
        }
    return {"ask_id": 12345, "create": {"image": "pytorch/pytorch:2.4.0", "disk": 40}, "cost": cost}


def _adapter(adapter: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/instance-operations/launch"):
            return httpx.Response(200, json={"data": {"instance_ids": ["vm-lambda-1"]}})
        if request.url.path.endswith("/instance-operations/terminate"):
            return httpx.Response(200, json={"data": {"terminated_instances": []}})
        if request.method == "PUT":
            return httpx.Response(200, json={"success": True, "new_contract": 987654})
        return httpx.Response(200, json={"success": True})

    transport = httpx.MockTransport(handler)
    if adapter == "lambda_cloud":
        from pitwall.providers.lambda_cloud import LambdaCloudProvider

        return LambdaCloudProvider(transport=transport, launch_interval_s=0, request_interval_s=0)
    from pitwall.providers.vast import VastProvider

    return VastProvider(transport=transport)


async def _seed(pg_pool: Any, adapter: str, cost: dict[str, Any]) -> tuple[Capability, Provider]:
    now = dt.datetime.now(dt.UTC)
    capability = await CapabilityRepository(pg_pool).create(
        Capability(
            id=f"cap_bill_{adapter}",
            name=f"gpu.bill-{adapter}",
            version="1",
            class_=CapabilityClass.GPU_LEASE,
            cost_mode=CostMode.PER_SECOND,
            source=CapabilitySource.API,
            created_at=now,
            updated_at=now,
        )
    )
    provider = await ProviderRepository(pg_pool).create(
        Provider(
            id=f"prov_bill_{adapter}",
            capability_id=capability.id,
            name=f"bill-{adapter}",
            provider_type=ProviderType.POD_LEASE,
            adapter_id=ProviderAdapterId(adapter),
            config=_config(adapter, cost),
            priority=1,
            source=CapabilitySource.API,
            updated_at=now,
        )
    )
    return capability, provider


async def _lease_for_half_an_hour(
    pg_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
    adapter: str,
    cost: dict[str, Any],
    *,
    settle_with_cost: dict[str, Any] | None = None,
) -> tuple[Decimal, Decimal, dict[str, Any], Decimal]:
    """Provision a 2 h lease, read its reservation, tear it down 30 minutes later."""
    capability, provider = await _seed(pg_pool, adapter, cost)
    monkeypatch.setenv(provider.credential_ref, f"{adapter}-test-key")
    vm = _adapter(adapter)

    class Registry:
        def lookup_compute(self, _adapter_id: str) -> Any:
            return vm

    monkeypatch.setattr(teardown, "get_default_registry", Registry)
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("100"), per_request_max_usd=Decimal("50"))
    started = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
    result = await vm.provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=pg_pool, now=started),
            capability=capability,
            provider_record=provider,
            credentials=CredentialReference(provider.credential_ref),
            budget_gate=gate,
        )
    )
    async with pg_pool.acquire() as conn:
        reserved = await month_to_date_spend(conn)
    if settle_with_cost is not None:
        await ProviderRepository(pg_pool).patch(
            provider.id, config=_config(adapter, settle_with_cost)
        )
    closed = await teardown.run_teardown(
        result.lease_id, pool=pg_pool, now=started + dt.timedelta(minutes=30)
    )
    async with pg_pool.acquire() as conn:
        workload = dict(
            await conn.fetchrow(
                "SELECT id, cost_actual_usd, cost_actual_provenance FROM pitwall.workloads"
            )
        )
        lease_workload = await conn.fetchval(
            "SELECT workload_id FROM pitwall.leases WHERE id = $1", result.lease_id
        )
        settled = await month_to_date_spend(conn)
    assert lease_workload == workload["id"]
    assert closed.lease.cost_accrued_usd is not None
    return reserved, closed.lease.cost_accrued_usd, workload, settled


async def test_a_lambda_lease_reserves_its_ttl_and_settles_at_its_rate(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reserved, accrued, workload, settled = await _lease_for_half_an_hour(
        pg_pool, monkeypatch, "lambda_cloud", _LAMBDA_COST
    )

    assert reserved == Decimal("1.152000")  # 0.00016 USD/s x 2 h
    assert accrued == Decimal("0.288000")  # 0.00016 USD/s x 30 min
    assert workload["cost_actual_usd"] == Decimal("0.288000")
    assert workload["cost_actual_provenance"] == "lease_teardown"
    assert settled == Decimal("0.288000")  # the rest of the reservation is released


async def test_a_vast_lease_reserves_its_ttl_and_settles_at_its_bid_rate(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reserved, accrued, workload, settled = await _lease_for_half_an_hour(
        pg_pool, monkeypatch, "vast", _VAST_COST
    )

    assert reserved == Decimal("1.440000")  # 0.72 USD/h bid x 2 h
    assert accrued == Decimal("0.360000")  # 0.72 USD/h x 30 min
    assert workload["cost_actual_usd"] == Decimal("0.360000")
    assert settled == Decimal("0.360000")


async def test_a_lease_whose_rate_disappeared_settles_at_the_fallback_never_zero(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    reserved, accrued, workload, settled = await _lease_for_half_an_hour(
        pg_pool, monkeypatch, "lambda_cloud", _LAMBDA_COST, settle_with_cost={}
    )

    assert reserved == Decimal("1.152000")
    assert accrued == Decimal("0.250000")  # 0.50 USD/h fallback x 30 min
    assert workload["cost_actual_usd"] == Decimal("0.250000")
    assert settled == Decimal("0.250000")
