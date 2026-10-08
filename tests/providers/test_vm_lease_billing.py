"""Lambda Cloud and Vast leases reserve their TTL, link their workload, and settle at their rate."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
from pitwall.core.models import Capability
from pitwall.cost.estimator import (
    CostQuote,
    PerRequestPricing,
    PerSecondPricing,
    PerVmSecondPricing,
)
from pitwall.providers.provisioning import (
    FALLBACK_LEASE_USD_PER_HOUR,
    fallback_lease_rate_per_second,
    lease_rate_per_second,
    lease_reservation,
)

_NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.UTC)
_TWO_HOURS_MS = 7_200_000


def _capability() -> Capability:
    return Capability(
        id="cap_gpu_lease",
        name="gpu.lease",
        version="1",
        class_=CapabilityClass.GPU_LEASE,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_a_lambda_lease_reserves_its_vm_rate_for_the_whole_ttl() -> None:
    reservation = lease_reservation(
        PerVmSecondPricing(rate_per_second=Decimal("0.00016")),
        _capability(),
        {},
        ttl_ms=_TWO_HOURS_MS,
    )

    assert isinstance(reservation, CostQuote)
    assert reservation.upper_bound() == Decimal("1.152000")


def test_a_vast_lease_reserves_the_higher_of_price_and_bid_for_the_ttl() -> None:
    pricing = PerSecondPricing(
        rate_per_second=Decimal("0.0001"), bid_rate_per_second=Decimal("0.0002")
    )

    reservation = lease_reservation(pricing, _capability(), {}, ttl_ms=_TWO_HOURS_MS)

    assert isinstance(reservation, CostQuote)
    assert reservation.estimate() == Decimal("0.720000")
    assert reservation.upper_bound() == Decimal("1.440000")
    assert lease_rate_per_second(pricing) == Decimal("0.0002")


def test_a_pricing_without_a_per_second_rate_reserves_the_fallback_rate_never_zero() -> None:
    reservation = lease_reservation(
        PerRequestPricing(per_request=Decimal("0")), _capability(), {}, ttl_ms=_TWO_HOURS_MS
    )

    assert reservation == FALLBACK_LEASE_USD_PER_HOUR * 2
    assert reservation > 0
    assert fallback_lease_rate_per_second() * 3600 == FALLBACK_LEASE_USD_PER_HOUR


@pytest.mark.parametrize(
    ("adapter", "provider_module"),
    [
        ("lambda_cloud", "tests.providers.test_lambda_cloud_provider"),
        ("vast", "tests.providers.test_vast_provider"),
    ],
)
@pytest.mark.anyio
async def test_a_provisioned_vm_lease_is_linked_to_its_admission_workload(
    adapter: str, provider_module: str
) -> None:
    import importlib

    from pitwall.cost.budget_gate import BudgetAdmission
    from pitwall.providers import ProviderOperationContext, ProvisionRequest

    module = importlib.import_module(provider_module)

    class Gate:
        async def try_launch_admission(self, **_kwargs: Any) -> BudgetAdmission:
            return BudgetAdmission(workload_id="wkl_vm_link", is_new=True)

    if adapter == "lambda_cloud":
        import httpx

        fake = module.LambdaHttpFake(
            [httpx.Response(200, json={"data": {"instance_ids": ["instance-1"]}})]
        )
        provider = module.LambdaCloudProvider(transport=fake.transport())
    else:
        import httpx

        fake = module.VastHttpFake(
            [httpx.Response(200, json={"success": True, "new_contract": 987654})]
        )
        provider = module.VastProvider(transport=fake.transport())
    pool = module.FakePool()

    await provider.provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=pool),
            capability=module._capability(),
            provider_record=module._provider_record(),
            credentials=module._credentials(),
            budget_gate=Gate(),
        )
    )

    _query, insert_args = next(
        command for command in pool.commands if "INSERT INTO pitwall.leases" in command[0]
    )
    assert insert_args[2] == "wkl_vm_link"


_VAST_COST = {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"}


@pytest.mark.parametrize(
    ("override", "hourly"),
    [
        ({"create": {"image": "img", "price": "2.0"}}, Decimal("2.0")),
        ({"create": {"image": "img"}, "price": "1.5"}, Decimal("1.5")),
        ({"create": {"image": "img", "price": "0.5"}}, Decimal("0.5")),
    ],
    ids=["create-price-above-bid", "top-level-price", "create-price-below-bid"],
)
def test_a_vast_config_price_is_the_bid_the_lease_reserves_and_settles_at(
    override: dict[str, Any], hourly: Decimal
) -> None:
    from pitwall.providers.vast import VastProvider
    from tests.providers.test_vast_provider import _provider_record

    record = _provider_record({"ask_id": 12345, "cost": _VAST_COST, **override})
    pricing = VastProvider().pricing_model(_capability(), record)

    assert lease_rate_per_second(pricing) == max(Decimal("0.36"), hourly) / 3600
    reservation = lease_reservation(pricing, _capability(), {}, ttl_ms=_TWO_HOURS_MS)
    assert isinstance(reservation, CostQuote)
    assert reservation.upper_bound() == (max(Decimal("0.36"), hourly) * 2).quantize(
        Decimal("0.000001")
    )


@pytest.mark.anyio
async def test_a_vast_create_sends_the_config_price_it_counts() -> None:
    import httpx

    from pitwall.providers import ProviderOperationContext, ProvisionRequest
    from tests.providers import test_vast_provider as vast_tests

    fake = vast_tests.VastHttpFake(
        [httpx.Response(200, json={"success": True, "new_contract": 987654})]
    )
    record = vast_tests._provider_record(
        {"ask_id": 12345, "cost": _VAST_COST, "create": {"image": "img", "price": "2.0"}}
    )

    await vast_tests.VastProvider(transport=fake.transport()).provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=object()),
            capability=vast_tests._capability(),
            provider_record=record,
            credentials=vast_tests._credentials(),
        )
    )

    assert Decimal(str(vast_tests._json(fake.requests[0])["price"])) == Decimal("2.0")


@pytest.mark.anyio
async def test_a_request_price_above_the_counted_rate_is_refused_before_any_call() -> None:
    from pitwall.providers import ProviderOperationContext, ProvisionRequest
    from pitwall.providers.vast import VastProviderError
    from tests.providers import test_vast_provider as vast_tests

    fake = vast_tests.VastHttpFake([])
    budget = SimpleNamespaceGate()

    with pytest.raises(VastProviderError, match="configured rate"):
        await vast_tests.VastProvider(transport=fake.transport()).provision(
            ProvisionRequest(
                context=ProviderOperationContext(pool=object()),
                capability=vast_tests._capability(),
                provider_record=vast_tests._provider_record(),
                credentials=vast_tests._credentials(),
                payload={"price": "5.0"},
                budget_gate=budget,
            )
        )
    assert fake.requests == [] and budget.calls == 0


class SimpleNamespaceGate:
    calls = 0

    async def try_launch_admission(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("no admission for a refused price")
