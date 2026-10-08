"""Lease teardown settles a Lambda Cloud or Vast lease at its adapter's rate; RunPod is unchanged."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.api.leases import teardown
from pitwall.core.enums import ProviderAdapterId
from tests.leases.test_teardown import _CREATED_AT, _lease, _provider

pytestmark = pytest.mark.anyio

_THIRTY_MINUTES = _CREATED_AT + dt.timedelta(minutes=30)


def _vm_provider(adapter: ProviderAdapterId, cost: dict[str, Any]) -> Any:
    return _provider().model_copy(
        update={"adapter_id": adapter, "credential_ref": "VM_KEY", "config": {"cost": cost}}
    )


def _vm_lease() -> Any:
    return _lease().model_copy(update={"external_resource_id": "vm-1", "runpod_pod_id": None})


class _Capabilities:
    def __init__(self, pool: object) -> None:
        pass

    async def get(self, capability_id: str) -> Any:
        from tests.providers.test_vm_lease_billing import _capability

        return _capability()


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(teardown, "CapabilityRepository", _Capabilities)


@pytest.mark.usefixtures("real_adapters")
async def test_a_lambda_lease_settles_at_its_vm_rate_for_the_time_it_ran() -> None:
    provider = _vm_provider(ProviderAdapterId.LAMBDA_CLOUD, {"rate_per_second": "0.00016"})

    rate = await teardown._adapter_rate_per_second(object(), provider)
    cost = teardown.close_lease_cost(
        _vm_lease(), provider=provider, terminated_at=_THIRTY_MINUTES, adapter_rate_per_second=rate
    )

    assert rate == Decimal("0.00016")
    assert cost == Decimal("0.288000")


@pytest.mark.usefixtures("real_adapters")
async def test_a_vast_lease_settles_at_the_higher_of_price_and_bid() -> None:
    provider = _vm_provider(
        ProviderAdapterId.VAST,
        {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"},
    )

    rate = await teardown._adapter_rate_per_second(object(), provider)
    cost = teardown.close_lease_cost(
        _vm_lease(), provider=provider, terminated_at=_THIRTY_MINUTES, adapter_rate_per_second=rate
    )

    assert rate == Decimal("0.0002")
    assert cost == Decimal("0.360000")


@pytest.mark.usefixtures("real_adapters")
async def test_a_vm_lease_whose_rate_is_missing_settles_at_the_fallback_never_zero() -> None:
    provider = _vm_provider(ProviderAdapterId.LAMBDA_CLOUD, {})

    rate = await teardown._adapter_rate_per_second(object(), provider)
    cost = teardown.close_lease_cost(
        _vm_lease(), provider=provider, terminated_at=_THIRTY_MINUTES, adapter_rate_per_second=rate
    )

    assert cost == Decimal("0.250000")


async def test_a_vm_lease_whose_capability_is_unreadable_settles_at_the_fallback() -> None:
    provider = _vm_provider(ProviderAdapterId.VAST, {"price_per_hour": "0.36"})

    rate = await teardown._adapter_rate_per_second(object(), provider)

    assert rate is not None and rate * 3600 == Decimal("0.50")


async def test_runpod_settlement_is_unchanged() -> None:
    """RunPod keeps per_second_active, then max_usd_per_hour, then the fallback (never $0)."""
    provider = _provider()

    rate = await teardown._adapter_rate_per_second(object(), provider)

    assert rate is None
    assert teardown.close_lease_cost(
        _lease(), provider=provider, terminated_at=_THIRTY_MINUTES, adapter_rate_per_second=rate
    ) == Decimal("3.600000")
    capped = _lease().model_copy(update={"max_usd_per_hour": Decimal("1.2")})
    no_rate = provider.model_copy(update={"config": {}})
    assert teardown.close_lease_cost(
        capped, provider=no_rate, terminated_at=_THIRTY_MINUTES
    ) == Decimal("0.600000")
    accrued = _lease().model_copy(update={"cost_accrued_usd": Decimal("0.42")})
    assert teardown.close_lease_cost(
        accrued, provider=no_rate, terminated_at=_THIRTY_MINUTES
    ) == Decimal("0.250000")  # 0.50 USD/h x 30 min
