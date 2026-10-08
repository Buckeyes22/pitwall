"""Real-Postgres proof that the initial lease upsert persists the controls.

Migration 0024 added ``idle_timeout_min`` and ``max_usd_per_hour`` for the
activity and spend controls, and the Lease model carries both, but the INSERT
omitted them: a serve therefore recorded no idle timeout, and the partial index
the idle sweep relies on (``WHERE idle_timeout_min IS NOT NULL``) could never
match a serve-created lease. This lives in the integration lane because the
``pitwall_pool`` fixture in ``tests/leases`` does not exist; ``pg_pool`` here is
the real fixture that raw-applies every migration. The columns are read back
from Postgres itself — no mocked DB — for both the initial insert and the
``ON CONFLICT`` update path.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.api.leases.launch import _upsert_initial_lease
from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Lease
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def _seed(pool: Any, suffix: str) -> tuple[str, str]:
    capability_id = f"cap-lease-controls-{suffix}"
    provider_id = f"prov-lease-controls-{suffix}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config)
            VALUES ($1, $2, '1.0.0', 'gpu_lease', 'per_second', '{}')
            """,
            capability_id,
            capability_id,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, config, priority)
            VALUES ($1, $2, $1, 'pod_lease', '{}', 1)
            """,
            provider_id,
            capability_id,
        )
    return capability_id, provider_id


def _lease(provider_id: str, *, idle_timeout_min: int, max_usd_per_hour: Decimal) -> Lease:
    now = dt.datetime.now(dt.UTC)
    return Lease(
        id=f"lease-controls-{provider_id}",
        provider_id=provider_id,
        runpod_pod_id=f"pod-controls-{provider_id}",
        state=LeaseState.CREATING,
        created_at=now,
        expires_at=now + dt.timedelta(minutes=15),
        renewal_policy=LeaseRenewalPolicy.ACTIVITY,
        idle_timeout_min=idle_timeout_min,
        max_usd_per_hour=max_usd_per_hour,
    )


async def _read_controls(pool: Any, lease_id: str) -> tuple[int | None, Decimal | None]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT idle_timeout_min, max_usd_per_hour FROM pitwall.leases WHERE id = $1",
            lease_id,
        )
    assert row is not None
    return row["idle_timeout_min"], row["max_usd_per_hour"]


async def test_controls_round_trip_through_postgres(pg_pool: Any) -> None:
    _, provider_id = await _seed(pg_pool, "roundtrip")
    lease = _lease(provider_id, idle_timeout_min=5, max_usd_per_hour=Decimal("0.8000"))

    await _upsert_initial_lease(pg_pool, lease)

    idle_timeout_min, max_usd_per_hour = await _read_controls(pg_pool, lease.id)
    assert idle_timeout_min == 5
    assert max_usd_per_hour == Decimal("0.8000")


async def test_on_conflict_updates_changed_controls_in_postgres(pg_pool: Any) -> None:
    _, provider_id = await _seed(pg_pool, "upsert")
    lease = _lease(provider_id, idle_timeout_min=5, max_usd_per_hour=Decimal("0.8000"))
    await _upsert_initial_lease(pg_pool, lease)

    changed = lease.model_copy(
        update={"idle_timeout_min": 30, "max_usd_per_hour": Decimal("1.2500")}
    )
    await _upsert_initial_lease(pg_pool, changed)

    idle_timeout_min, max_usd_per_hour = await _read_controls(pg_pool, lease.id)
    assert idle_timeout_min == 30
    assert max_usd_per_hour == Decimal("1.2500")
