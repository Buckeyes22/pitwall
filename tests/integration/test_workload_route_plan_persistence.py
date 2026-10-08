"""Real-Postgres JSON/plan identity fidelity for ROUTE-01."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import asyncpg
import pytest

from pitwall.core.enums import WorkloadState
from pitwall.core.models import Workload
from pitwall.db.repository import WorkloadRepository

pytestmark = pytest.mark.integration


async def test_workload_repository_round_trips_route_plan(pg_pool) -> None:
    now = dt.datetime.now(dt.UTC)
    plan = {
        "plan_id": "plan_0123456789abcdef0123456789abcdef",
        "selected_provider_id": "prov_route",
        "weights": {"cost": "1.25", "latency": "0.0005"},
        "ranked_candidates": [],
    }
    workload = Workload(
        id="wkl_route_plan_fidelity",
        capability_id="cap_route",
        provider_id="prov_route",
        type="async_job",
        state=WorkloadState.QUEUED,
        submitted_at=now,
        cost_estimate_usd=Decimal("0.000001"),
        cost_ceiling_usd=Decimal("0.000002"),
        cost_quote={"model": "route_plan", "estimate": "0.000001", "ceiling": "0.000002"},
        route_plan_id=plan["plan_id"],
        route_plan=plan,
        route_attempts=[
            {
                "sequence": 1,
                "operation": "submit",
                "provider_id": "prov_route",
                "adapter_id": "runpod",
                "outcome": "accepted",
            }
        ],
        fallback_chain=["prov_route"],
    )

    inserted = await WorkloadRepository(pg_pool).insert(workload)
    loaded = await WorkloadRepository(pg_pool).get(workload.id)

    assert inserted.route_plan_id == plan["plan_id"]
    assert loaded is not None
    assert loaded.route_plan == plan
    assert loaded.route_attempts == workload.route_attempts
    assert loaded.cost_ceiling_usd == Decimal("0.000002")

    async with pg_pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """UPDATE pitwall.workloads
                   SET route_plan = jsonb_set(route_plan, '{plan_id}', 'null'::jsonb)
                   WHERE id = $1""",
                workload.id,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """UPDATE pitwall.workloads
                   SET route_plan = route_plan - 'plan_id'
                   WHERE id = $1""",
                workload.id,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """UPDATE pitwall.workloads
                   SET route_plan = jsonb_set(
                       route_plan, '{plan_id}', '"plan_ffffffffffffffffffffffffffffffff"'
                   )
                   WHERE id = $1""",
                workload.id,
            )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                """UPDATE pitwall.workloads
                   SET route_plan = route_plan || jsonb_build_object(
                       'padding', (
                           SELECT string_agg(md5(value::text), '')
                           FROM generate_series(1, 40000) AS value
                       )
                   )
                   WHERE id = $1""",
                workload.id,
            )
