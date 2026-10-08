"""Lease traffic write-through reads providers and Redis stamps once per tick."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.core.enums import CapabilitySource, LeaseRenewalPolicy, ProviderType
from pitwall.core.models import Lease, Provider
from pitwall.reconciler import _write_through_lease_traffic

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)


def _lease(index: int) -> Lease:
    return Lease.model_validate(
        {
            "id": f"lease-{index}",
            "provider_id": "provider-1" if index % 2 else "provider-2",
            "runpod_pod_id": f"pod-{index}",
            "state": "creating",
            "created_at": _NOW - dt.timedelta(hours=1),
            "expires_at": _NOW + dt.timedelta(hours=1),
            "renewal_policy": LeaseRenewalPolicy.ACTIVITY,
            "ready_at": _NOW - dt.timedelta(minutes=30),
            "idle_timeout_min": 20,
            "max_usd_per_hour": Decimal("1.00"),
        }
    )


def _provider(provider_id: str) -> Provider:
    return Provider(
        id=provider_id,
        capability_id="capability-1",
        name=provider_id,
        provider_type=ProviderType.POD_LEASE,
        config={"gpu_types": ["NVIDIA L4"], "lease_ttl_ms": 3_600_000},
        priority=0,
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


class _Pipeline:
    def __init__(self, store: dict[str, str], log: list[str]) -> None:
        self._store = store
        self._log = log
        self._keys: list[str] = []

    async def __aenter__(self) -> _Pipeline:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def get(self, key: str) -> _Pipeline:
        self._keys.append(key)
        return self

    async def execute(self) -> list[str | None]:
        self._log.append("execute")
        return [self._store.get(key) for key in self._keys]


class _Redis:
    def __init__(self, store: dict[str, str]) -> None:
        self.store = store
        self.log: list[str] = []

    def pipeline(self, *, transaction: bool = True) -> _Pipeline:
        assert transaction is False
        return _Pipeline(self.store, self.log)

    async def get(self, key: str) -> str | None:
        self.log.append(f"get:{key}")
        return self.store.get(key)


async def test_single_batched_read_per_tick() -> None:
    leases = [_lease(index) for index in range(5)]
    seen = _NOW - dt.timedelta(minutes=2)
    redis = _Redis(
        {"pitwall:lease:lease-3:last_traffic_at": seen.isoformat().replace("+00:00", "Z")}
    )
    repo: Any = AsyncMock()
    repo.list_active_for_activity_control.return_value = leases
    provider_repo: Any = AsyncMock()
    provider_repo.get_many.return_value = {
        "provider-1": _provider("provider-1"),
        "provider-2": _provider("provider-2"),
    }

    controlled, unavailable, providers = await _write_through_lease_traffic(
        repo, provider_repo, redis
    )

    provider_repo.get_many.assert_awaited_once()
    assert set(provider_repo.get_many.await_args.args[0]) == {"provider-1", "provider-2"}
    provider_repo.get.assert_not_awaited()
    assert redis.log == ["execute"]
    assert set(controlled) == {lease.id for lease in leases}
    assert unavailable == set()
    assert providers["lease-3"] is not None
    repo.record_traffic.assert_awaited_once_with("lease-3", seen_at=seen)


async def test_redis_failure_marks_every_lease_unavailable() -> None:
    class _BrokenRedis:
        def pipeline(self, *, transaction: bool = True) -> Any:
            raise ConnectionError("redis is down")

    leases = [_lease(1), _lease(2)]
    repo: Any = AsyncMock()
    repo.list_active_for_activity_control.return_value = leases
    provider_repo: Any = AsyncMock()
    provider_repo.get_many.return_value = {}

    controlled, unavailable, _providers = await _write_through_lease_traffic(
        repo, provider_repo, _BrokenRedis()
    )

    assert unavailable == {"lease-1", "lease-2"}
    assert set(controlled) == {"lease-1", "lease-2"}
