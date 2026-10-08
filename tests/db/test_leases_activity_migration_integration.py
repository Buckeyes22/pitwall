from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import asyncpg
import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, Provider
from pitwall.db import _register_codecs
from pitwall.db.repository import CapabilityRepository, LeaseRepository, ProviderRepository

_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set"),
]


async def test_0024_constraints_and_activity_repository_round_trip() -> None:
    pool = await asyncpg.create_pool(
        _PG_URL,
        min_size=1,
        max_size=1,
        init=_register_codecs,
    )
    assert pool is not None
    now = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.UTC)
    repo = LeaseRepository(pool)
    lease_id = "lease_0024_integration"
    capability_id = "capability_0024_integration"
    provider_id = "provider_0024_integration"
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.leases WHERE id = $1", lease_id)
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
            await conn.execute("DELETE FROM pitwall.capabilities WHERE id = $1", capability_id)
        await CapabilityRepository(pool).create(
            Capability(
                id=capability_id,
                name="integration.lease-activity",
                version="1.0.0",
                class_=CapabilityClass.LLM,
                cost_mode=CostMode.PER_SECOND,
                source=CapabilitySource.API,
                created_at=now,
                updated_at=now,
            )
        )
        await ProviderRepository(pool).create(
            Provider(
                id=provider_id,
                capability_id=capability_id,
                name="integration-lease-activity",
                provider_type=ProviderType.POD_LEASE,
                config={},
                priority=0,
                source=CapabilitySource.API,
                updated_at=now,
            )
        )
        created = await repo.create(
            Lease(
                id=lease_id,
                provider_id=provider_id,
                runpod_pod_id="pod_0024_integration",
                state=LeaseState.CREATING,
                created_at=now,
                expires_at=now + dt.timedelta(hours=1),
                renewal_policy=LeaseRenewalPolicy.ACTIVITY,
                idle_timeout_min=20,
                max_usd_per_hour=Decimal("2.5000"),
            )
        )
        assert created.idle_timeout_min == 20
        assert await repo.capability_name(lease_id) == "integration.lease-activity"
        assert await repo.claim_expiry_warning(
            lease_id,
            expires_at=created.expires_at,
            threshold_minutes=15,
        )
        moved_expiry = created.expires_at + dt.timedelta(minutes=30)
        moved = await repo.update_expires_at(lease_id, moved_expiry)
        assert moved is not None
        async with pool.acquire() as conn:
            marker = await conn.fetchrow(
                "SELECT warning_expires_at, warning_thresholds FROM pitwall.leases WHERE id = $1",
                lease_id,
            )
        assert marker is not None
        assert marker["warning_expires_at"] is None
        assert marker["warning_thresholds"] == []
        ready = await repo.mark_ready(lease_id, ready_at=now + dt.timedelta(seconds=30))
        assert ready.ready_at == now + dt.timedelta(seconds=30)
        await repo.record_traffic(lease_id, seen_at=now + dt.timedelta(minutes=2))
        fetched = await repo.get(lease_id)
        assert fetched is not None
        assert fetched.last_traffic_at == now + dt.timedelta(minutes=2)
        assert fetched.max_usd_per_hour == Decimal("2.5000")
        assert lease_id in {item.id for item in await repo.list_active_for_activity_control()}
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE pitwall.leases SET idle_timeout_min = 4 WHERE id = $1",
                    lease_id,
                )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.leases WHERE id = $1", lease_id)
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
            await conn.execute("DELETE FROM pitwall.capabilities WHERE id = $1", capability_id)
        await pool.close()


async def test_0026_capability_trigger_keeps_orphan_lease_name_null() -> None:
    pool = await asyncpg.create_pool(
        _PG_URL,
        min_size=1,
        max_size=1,
        init=_register_codecs,
    )
    assert pool is not None
    now = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.UTC)
    lease_id = "lease_0026_orphan"
    provider_id = "provider_0026_missing_capability"
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.leases WHERE id = $1", lease_id)
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
            await conn.execute(
                """
                INSERT INTO pitwall.providers
                    (id, capability_id, name, provider_type, config, priority, source, updated_at)
                VALUES ($1, NULL, $2, 'pod_lease', '{}'::jsonb, 0, 'api', $3)
                """,
                provider_id,
                "integration-missing-capability",
                now,
            )
        created = await LeaseRepository(pool).create(
            Lease(
                id=lease_id,
                provider_id=provider_id,
                runpod_pod_id="pod_0026_orphan",
                state=LeaseState.CREATING,
                created_at=now,
                expires_at=now + dt.timedelta(hours=1),
                renewal_policy=LeaseRenewalPolicy.MANUAL,
            )
        )
        assert created.id == lease_id
        assert await LeaseRepository(pool).capability_name(lease_id) is None
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.leases WHERE id = $1", lease_id)
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
        await pool.close()
