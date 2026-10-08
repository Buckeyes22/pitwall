"""Workloads left queued/running with no job id and no lease are reaped (review finding #4).

A request that crashed after admission but before a provider job id was recorded stayed
non-terminal forever: the gate counted its ceiling every month, but the daily rollup, which
only counts terminal states, never did.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

import pitwall.reconciler as recon
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def _seed(conn: Any) -> None:
    for cap_id, timeout_ms in (("cap_fast", 60_000), ("cap_slow", 3 * 3_600_000)):
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)"
            " VALUES ($1, $1, '1.0.0', 'llm', 'per_token', $2::jsonb)",
            cap_id,
            json.dumps({"defaults": {"execution_timeout_ms": timeout_ms}}),
        )
    rows = [
        # id, capability, state, job id, age
        ("wkl_orphan", "cap_fast", "queued", None, "2 hours"),
        ("wkl_orphan_running", "cap_fast", "running", None, "2 hours"),
        ("wkl_raw_orphan", "runpod_direct", "running", None, "2 hours"),
        ("wkl_young", "cap_fast", "queued", None, "30 minutes"),
        ("wkl_with_job", "cap_fast", "running", "job-1", "2 hours"),
        ("wkl_slow_cap", "cap_slow", "running", None, "2 hours"),
        ("wkl_leased", "runpod_direct", "running", None, "2 hours"),
        ("wkl_done", "cap_fast", "completed", None, "2 hours"),
    ]
    for workload_id, capability, state, job_id, age in rows:
        await conn.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state,"
            " runpod_job_id, submitted_at, cost_estimate_usd, cost_ceiling_usd)"
            f" VALUES ($1, $2, 'prov_x', 'inference', $3, $4, now() - interval '{age}', 0.10, 0.40)",
            workload_id,
            capability,
            state,
            job_id,
        )
    await conn.execute(
        "INSERT INTO pitwall.leases (id, provider_id, runpod_pod_id, state, created_at,"
        " expires_at, renewal_policy, workload_id)"
        " VALUES ('lease_x', 'runpod_direct', 'pod-x', 'stopped', now() - interval '2 hours',"
        " now(), 'manual', 'wkl_leased')"
    )


async def test_reaps_only_old_unleased_workloads_without_a_job_id(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        await _seed(conn)

    reaped = await recon.reap_orphaned_workloads(pg_pool)

    async with pg_pool.acquire() as conn:
        rows = {
            row["id"]: row
            for row in await conn.fetch(
                "SELECT id, state, cost_actual_usd, cost_actual_provenance, completed_at, error"
                " FROM pitwall.workloads"
            )
        }
    assert reaped == 3
    for workload_id in ("wkl_orphan", "wkl_orphan_running", "wkl_raw_orphan"):
        row = rows[workload_id]
        assert row["state"] == "timed_out", workload_id
        assert row["cost_actual_usd"] == Decimal("0.400000")
        assert row["cost_actual_provenance"] == "reaped_unfinished"
        assert row["completed_at"] is not None
        assert row["error"]["type"] == "ReapedUnfinished"
    for workload_id, state in (
        ("wkl_young", "queued"),
        ("wkl_with_job", "running"),
        ("wkl_slow_cap", "running"),
        ("wkl_leased", "running"),
        ("wkl_done", "completed"),
    ):
        assert rows[workload_id]["state"] == state, workload_id
        assert rows[workload_id]["cost_actual_provenance"] is None, workload_id

    assert await recon.reap_orphaned_workloads(pg_pool) == 0  # idempotent
