"""``WorkloadRepository.fail_and_release_idempotency_key`` against the real budget gate.

A raw-pod create that fails closes its admitted workload as ``failed`` and clears that
workload's idempotency key in one statement, so a same-key retry admits a fresh workload
through the budget check instead of reusing the closed, zero-cost one.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.cost.budget_gate import BudgetGate, BudgetRejected
from pitwall.db.repository import WorkloadRepository
from tests.integration.conftest import requires_pg
from tests.integration.test_budget_gate import _now_utc, _seed

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def _admit(gate: BudgetGate, key: str) -> Any:
    return await gate.try_launch_admission(
        capability_id="cap_bge_m3",
        provider_id="prov_bge_m3",
        estimate_usd=Decimal("10"),
        submitted_at=_now_utc(),
        idempotency_key=key,
    )


async def _fail_and_release(workloads: WorkloadRepository, workload_id: str) -> Any:
    return await workloads.fail_and_release_idempotency_key(
        workload_id,
        cost_actual_provenance="raw_pod_create_failed",
        cost_reconciled_at=dt.datetime.now(dt.UTC),
    )


async def test_closing_a_workload_frees_its_key_for_a_new_admission(pg_pool: Any) -> None:
    await _seed(pg_pool)
    gate = BudgetGate(pg_pool, monthly_budget_usd="100", per_request_max_usd="100")
    workloads = WorkloadRepository(pg_pool)

    first = await _admit(gate, "raw-pod-retry-key")
    reused = await _admit(gate, "raw-pod-retry-key")
    assert first.is_new is True
    assert (reused.workload_id, reused.is_new) == (first.workload_id, False)

    closed = await _fail_and_release(workloads, first.workload_id)
    assert closed is not None
    assert closed.state == "failed"
    assert closed.cost_actual_usd == Decimal("0")
    async with pg_pool.acquire() as conn:
        key = await conn.fetchval(
            "SELECT idempotency_key FROM pitwall.workloads WHERE id = $1", first.workload_id
        )
    assert key is None

    retried = await _admit(gate, "raw-pod-retry-key")

    assert retried.is_new is True
    assert retried.workload_id != first.workload_id
    assert await _fail_and_release(workloads, "wkl_does_not_exist") is None


async def test_the_retry_after_release_is_budget_checked(pg_pool: Any) -> None:
    await _seed(pg_pool)
    workloads = WorkloadRepository(pg_pool)
    first = await _admit(
        BudgetGate(pg_pool, monthly_budget_usd="100", per_request_max_usd="100"),
        "raw-pod-budget-key",
    )
    await _fail_and_release(workloads, first.workload_id)

    with pytest.raises(BudgetRejected):
        await _admit(
            BudgetGate(pg_pool, monthly_budget_usd="100", per_request_max_usd="5"),
            "raw-pod-budget-key",
        )
