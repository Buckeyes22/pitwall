"""Renewal and teardown price a lease at one rate (``teardown.settlement_rate_per_second``).

For every adapter case, the rate the resolver returns is the rate ``run_teardown`` settles
the lease at: ``cost = rate x (terminated_at - created_at)``, never $0.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.api.leases import teardown
from pitwall.core.enums import ProviderAdapterId
from pitwall.core.models import Lease, Provider
from tests.fakes.teardown import UnlockedTeardown
from tests.leases.test_teardown import _CREATED_AT, _lease, _provider
from tests.leases.test_vm_lease_settlement import _Capabilities, _vm_lease, _vm_provider

pytestmark = pytest.mark.anyio

_NINETY_MINUTES = _CREATED_AT + dt.timedelta(minutes=90)


class _JournalPool:
    """A pool whose journal lookup answers RunPod's recorded ``costPerHr`` for the pod."""

    def __init__(self, cost_per_hour: str | None) -> None:
        self._cost_per_hour = cost_per_hour

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        yield self

    async def fetchval(self, sql: str, *args: Any) -> str | None:
        assert "pod.create" in sql
        return self._cost_per_hour


def _raw_pod(max_usd_per_hour: str | None = None) -> Lease:
    return _lease().model_copy(
        update={
            "provider_id": teardown.RAW_POD_PROVIDER_ID,
            "max_usd_per_hour": Decimal(max_usd_per_hour) if max_usd_per_hour else None,
        }
    )


_RUNPOD_NO_RATE = _provider().model_copy(update={"config": {}})
_RUNPOD_TAGGED = _provider().model_copy(
    update={"config": {"cost": {"kind": "per_second", "rate_per_second": "0.001"}}}
)

#: (lease, provider, recorded costPerHr, expected hourly rate)
_CASES = {
    "lambda-vm-rate": (
        _vm_lease(),
        _vm_provider(ProviderAdapterId.LAMBDA_CLOUD, {"rate_per_second": "0.00016"}),
        None,
        "0.576",
    ),
    "lambda-missing-rate-fallback": (
        _vm_lease(),
        _vm_provider(ProviderAdapterId.LAMBDA_CLOUD, {}),
        None,
        "0.50",
    ),
    "vast-higher-of-price-and-bid": (
        _vm_lease(),
        _vm_provider(
            ProviderAdapterId.VAST,
            {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"},
        ),
        None,
        "0.72",
    ),
    "runpod-per-second-active": (_lease(), _provider(), None, "7.2"),
    "runpod-cap": (
        _lease().model_copy(update={"max_usd_per_hour": Decimal("1.2")}),
        _RUNPOD_NO_RATE,
        None,
        "1.2",
    ),
    "runpod-tagged-per-second": (_lease(), _RUNPOD_TAGGED, None, "3.6"),
    "runpod-no-rate-fallback-never-zero": (_lease(), _RUNPOD_NO_RATE, None, "0.50"),
    "raw-pod-capped": (_raw_pod("0.60"), None, "3.00", "0.60"),
    "raw-pod-uncapped-recorded-costPerHr": (_raw_pod(), None, "3.00", "3.00"),
    "raw-pod-uncapped-no-costPerHr": (_raw_pod(), None, None, "0.50"),
    "raw-pod-uncapped-sub-cent-costPerHr": (_raw_pod(), None, "0.00004", "0.50"),
    "raw-pod-uncapped-absurd-costPerHr": (_raw_pod(), None, "1000000000", "0.50"),
}


def _world(monkeypatch: pytest.MonkeyPatch, lease: Lease, provider: Provider | None) -> list[Any]:
    closes: list[Any] = []

    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            return lease

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return lease.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            closes.append(changes["cost_accrued_usd"])
            changes.pop("closable_states", None)
            return lease.model_copy(update=changes)

        async def capability_name(self, lease_id: str) -> str:
            return "gpu.resolver"

    class ProviderRepo:
        async def get(self, provider_id: str) -> Provider | None:
            return provider

    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: ProviderRepo())
    monkeypatch.setattr(teardown, "CapabilityRepository", _Capabilities)
    monkeypatch.setattr(teardown, "_terminate_resource", AsyncMock())
    monkeypatch.setattr(teardown, "disarm_serve_provider", AsyncMock(return_value=False))
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
    monkeypatch.setattr(teardown, "publish_lease_event", AsyncMock())
    return closes


@pytest.mark.parametrize(
    ("lease", "provider", "cost_per_hour", "hourly"), _CASES.values(), ids=_CASES
)
async def test_the_resolver_rate_is_the_rate_teardown_settles_at(
    monkeypatch: pytest.MonkeyPatch,
    lease: Lease,
    provider: Provider | None,
    cost_per_hour: str | None,
    hourly: str,
) -> None:
    closes = _world(monkeypatch, lease, provider)
    pool = _JournalPool(cost_per_hour)

    rate = await teardown.settlement_rate_per_second(pool, lease, provider)
    await teardown.run_teardown(lease.id, pool=pool, now=_NINETY_MINUTES)

    elapsed = Decimal(str((_NINETY_MINUTES - lease.created_at).total_seconds()))
    assert rate is not None and rate > 0
    assert (rate * 3600).quantize(Decimal("0.0001")) == Decimal(hourly).quantize(Decimal("0.0001"))
    expected = (rate * elapsed).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    assert closes == [expected]


async def test_renewing_an_uncapped_raw_pod_reserves_its_recorded_cost_per_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.leases import mutations

    lease = _raw_pod()

    class Repo:
        async def get(self, lease_id: str) -> Lease:
            return lease

    class ProviderRepo:
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, provider_id: str) -> None:
            return None

    monkeypatch.setattr(mutations, "ProviderRepository", ProviderRepo)

    extension = await mutations.renewal_extension_usd(
        _JournalPool("3.00"),
        Repo(),  # type: ignore[arg-type]  # reason: only get() is used
        lease.id,
        60,
    )

    assert extension == Decimal("3.000000")


async def test_renewing_a_tagged_per_second_runpod_lease_reserves_its_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.leases import mutations

    lease = _lease()

    class Repo:
        async def get(self, lease_id: str) -> Lease:
            return lease

    class ProviderRepo:
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, provider_id: str) -> Provider:
            return _RUNPOD_TAGGED

    monkeypatch.setattr(mutations, "ProviderRepository", ProviderRepo)

    extension = await mutations.renewal_extension_usd(
        object(),
        Repo(),  # type: ignore[arg-type]  # reason: only get() is used
        lease.id,
        60,
    )

    assert extension == Decimal("3.600000")


@pytest.mark.parametrize(
    "cost",
    [
        {"per_second_active": "0.002"},
        {"kind": "gpu_hour", "per_second_active": "0.002"},
        {"kind": "per_second", "rate_per_second": "0.001"},
        {"kind": "per_second", "rate_per_second": "0.001", "bid_rate_per_second": "0.0015"},
        {"kind": "per_vm_second", "rate_per_second": "0.0005"},
        {"kind": "zero"},
        {"kind": "per_request", "per_request": "0.01"},
        {"kind": "per_token", "per_million_input_tokens": "1", "per_million_output_tokens": "2"},
    ],
    ids=lambda cost: (
        str(cost.get("kind", "legacy")) + ("-bid" if "bid_rate_per_second" in cost else "")
    ),
)
async def test_launch_reservation_equals_settlement_over_the_full_ttl(cost: dict[str, str]) -> None:
    """A RunPod lease that runs its whole TTL settles at exactly what its launch reserved."""
    from pitwall.api.leases.launch import estimate_lease_launch_cost
    from tests.leases.test_launch import _capability
    from tests.leases.test_launch import _provider as _launch_provider

    provider = _launch_provider({"cost": cost, "lease_ttl_ms": 7_200_000})
    lease = _lease().model_copy(update={"provider_id": provider.id})

    reserved = estimate_lease_launch_cost(_capability(), provider)
    rate = await teardown.settlement_rate_per_second(object(), lease, provider)
    settled = teardown.close_lease_cost(
        lease,
        provider=provider,
        terminated_at=lease.created_at + dt.timedelta(hours=2),
        adapter_rate_per_second=None,
    )

    assert settled == reserved
    assert (rate * Decimal(7_200)).quantize(Decimal("0.000001")) == reserved
