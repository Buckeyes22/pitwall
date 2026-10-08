"""The budget gate enforces runtime limits at admission time, without a restart."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.cost.budget_gate import BudgetGate, BudgetRejected
from pitwall.cost.budget_limits import set_limits

pytestmark = pytest.mark.integration


async def test_raising_the_monthly_budget_admits_what_the_environment_limit_rejected(
    pg_pool,
) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("5"), per_request_max_usd=Decimal("10"))
    with pytest.raises(BudgetRejected) as rejected:
        await gate.try_launch(
            capability_id="runpod_direct", provider_id="runpod_direct", estimate_usd=Decimal("6")
        )
    assert rejected.value.reason == "monthly_budget"
    await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("50"),
        per_request_max_usd=None,
        reason="test",
        actor="cli",
    )
    workload_id = await gate.try_launch(
        capability_id="runpod_direct", provider_id="runpod_direct", estimate_usd=Decimal("6")
    )
    assert workload_id.startswith("wkl_")


async def test_lowering_the_per_request_cap_takes_effect_on_the_same_gate(pg_pool) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("50"), per_request_max_usd=Decimal("10"))
    await set_limits(
        pg_pool,
        monthly_budget_usd=None,
        per_request_max_usd=Decimal("1"),
        reason="test",
        actor="cli",
    )
    with pytest.raises(BudgetRejected) as rejected:
        await gate.try_launch(
            capability_id="runpod_direct", provider_id="runpod_direct", estimate_usd=Decimal("2")
        )
    assert rejected.value.reason == "per_request_cap"
    assert rejected.value.snapshot.per_request_max_usd == Decimal("1")


async def test_evaluate_matches_admission_and_writes_nothing(pg_pool) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("5"), per_request_max_usd=Decimal("10"))
    verdict = await gate.evaluate(Decimal("6"))
    assert (verdict.admitted, verdict.reason) == (False, "monthly_budget")
    assert verdict.to_dict()["snapshot"]["budget_remaining_usd"] == "5"
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads") == 0
    ok = await gate.evaluate(Decimal("1"))
    assert (ok.admitted, ok.reason) == (True, None)
