"""An operator stop and the reconciler's stuck-teardown retry never tear one lease down twice.

A lease left ``stopping`` by a failed provider call is retried every reconciler tick. When
an operator stops the same lease while that retry runs (or the retry fires while the
operator's teardown is still waiting on the provider), exactly one teardown terminates the
pod, closes the lease, and writes the audit row; the other finds the lease busy or closed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import pytest

from pitwall.api.leases import teardown
from pitwall.reconciler import _retry_stuck_teardowns
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.integration

_NOW = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.UTC)


async def _seed_stopping_lease(conn: Any) -> None:
    await conn.execute(
        "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config) "
        "VALUES ('cap-race', 'llm.race', '1.0.0', 'gpu_lease', 'per_second', '{}')"
    )
    await conn.execute(
        "INSERT INTO pitwall.providers (id, capability_id, name, provider_type, config, priority) "
        "VALUES ('prov-race', 'cap-race', 'Race provider', 'pod_lease', '{}', 1)"
    )
    await conn.execute(
        "INSERT INTO pitwall.leases (id, provider_id, runpod_pod_id, state, created_at, "
        "expires_at, renewal_policy) VALUES ('lease-race', 'prov-race', 'pod-race', "
        "'stopping', $1, $2, 'manual')",
        _NOW - dt.timedelta(hours=1),
        _NOW + dt.timedelta(hours=1),
    )


async def test_operator_stop_and_stuck_retry_tear_the_lease_down_once(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with pg_pool.acquire() as conn:
        await _seed_stopping_lease(conn)
    terminated: list[str] = []
    released = asyncio.Event()

    async def slow_terminate(pod_id: str, **_kwargs: object) -> None:
        terminated.append(pod_id)
        await released.wait()

    monkeypatch.setattr(teardown, "terminate_pod", slow_terminate)
    operator = asyncio.create_task(
        teardown.run_teardown("lease-race", pool=pg_pool, reason="operator", now=_NOW)
    )
    await asyncio.sleep(0.2)  # the operator's teardown is now waiting on the provider
    retry = asyncio.create_task(_retry_stuck_teardowns(pg_pool, None, now=_NOW))
    await asyncio.sleep(0.2)
    released.set()
    await asyncio.wait_for(asyncio.gather(operator, retry), timeout=HANG_GUARD_SECS)

    async with pg_pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM pitwall.leases WHERE id = 'lease-race'")
        audits = await conn.fetchval(
            "SELECT count(*) FROM pitwall.config_audit "
            "WHERE entity_type = 'lease' AND entity_id = 'lease-race' AND action = 'stop'"
        )
    assert terminated == ["pod-race"]
    assert (state, audits) == ("stopped", 1)
