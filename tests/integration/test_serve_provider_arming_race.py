"""Arming and disarming a serve provider decide on the live row, under a row lock."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pitwall.api.leases.launch import arm_serve_provider
from pitwall.api.leases.teardown import disarm_serve_provider
from pitwall.db.repository import ProviderRepository
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_PROVIDER_ID = "prov-arming-race"


async def _seed_provider(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)
            VALUES ('cap-arming-race', 'cap-arming-race', '1.0.0', 'gpu_lease', 'per_second', '{}')
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers (id, capability_id, name, provider_type, config, priority)
            VALUES ($1, 'cap-arming-race', $1, 'pod_lease', '{"openai_proxy_port": 8000}', 1)
            """,
            _PROVIDER_ID,
        )


async def _live(pool: Any) -> Any:
    provider = await ProviderRepository(pool).get(_PROVIDER_ID)
    assert provider is not None
    return provider


async def _audit_actions(pool: Any) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT action FROM pitwall.config_audit WHERE entity_id = $1 ORDER BY id", _PROVIDER_ID
        )
    return [row["action"] for row in rows]


async def test_a_stale_snapshot_never_disarms_a_lease_armed_since(pg_pool: Any) -> None:
    await _seed_provider(pg_pool)
    await arm_serve_provider(
        pg_pool, provider=await _live(pg_pool), lease_id="lease-a", pod_id="pod-a"
    )
    stale_snapshot = await _live(pg_pool)
    await arm_serve_provider(
        pg_pool, provider=await _live(pg_pool), lease_id="lease-b", pod_id="pod-b"
    )
    audits_before = await _audit_actions(pg_pool)

    disarmed = await disarm_serve_provider(pg_pool, provider=stale_snapshot, lease_id="lease-a")

    live = await _live(pg_pool)
    assert disarmed is False
    assert live.config["active_lease_id"] == "lease-b"
    assert live.config["active_pod_id"] == "pod-b"
    assert live.health_status == "healthy"
    assert await _audit_actions(pg_pool) == audits_before
    assert "lease_closed" not in audits_before


async def test_a_concurrent_disarm_and_arm_leave_the_newer_lease_armed(pg_pool: Any) -> None:
    await _seed_provider(pg_pool)
    await arm_serve_provider(
        pg_pool, provider=await _live(pg_pool), lease_id="lease-a", pod_id="pod-a"
    )
    stale_snapshot = await _live(pg_pool)

    async with pg_pool.acquire() as holder, holder.transaction():
        await holder.fetchrow(
            "SELECT 1 FROM pitwall.providers WHERE id = $1 FOR UPDATE", _PROVIDER_ID
        )
        disarm = asyncio.ensure_future(
            disarm_serve_provider(pg_pool, provider=stale_snapshot, lease_id="lease-a")
        )
        arm = asyncio.ensure_future(
            arm_serve_provider(pg_pool, provider=stale_snapshot, lease_id="lease-b", pod_id="pod-b")
        )
        await asyncio.sleep(0.2)
        assert not disarm.done()
        assert not arm.done()

    await asyncio.gather(disarm, arm)

    live = await _live(pg_pool)
    assert live.config["active_lease_id"] == "lease-b"
    assert live.config["active_pod_id"] == "pod-b"
    assert live.health_status == "healthy"


async def test_arm_and_disarm_decide_on_the_row_not_a_snapshot_taken_before_arming(
    pg_pool: Any,
) -> None:
    await _seed_provider(pg_pool)
    snapshot_before_arming = await _live(pg_pool)
    emptied = snapshot_before_arming.model_copy(update={"config": {}})

    assert await arm_serve_provider(pg_pool, provider=emptied, lease_id="lease-a", pod_id="pod-a")
    assert await disarm_serve_provider(pg_pool, provider=emptied, lease_id="lease-a")

    live = await _live(pg_pool)
    assert live.health_status == "disarmed"
    assert "active_lease_id" not in live.config
    assert live.config["openai_proxy_port"] == 8000
