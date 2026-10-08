"""A raw-pod preview and the admission after it agree."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.api.leases.launch import admit_raw_pod_lease, preview_raw_pod_lease_budget
from pitwall.cost.budget_gate import BudgetGate, BudgetRejected

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("ttl", "admitted"), [(150, True), (1000, False)])
async def test_preview_verdict_matches_apply_outcome(pg_pool, ttl: int, admitted: bool) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    gate = BudgetGate(pg_pool, monthly_budget_usd=Decimal("5"), per_request_max_usd=Decimal("10"))
    preview = await preview_raw_pod_lease_budget(
        pg_pool, ttl_minutes=ttl, max_cost_per_hour=Decimal("0.49"), budget_gate=gate
    )
    assert preview["admitted"] is admitted
    if admitted:
        admission = await admit_raw_pod_lease(
            pg_pool,
            ttl_minutes=ttl,
            max_cost_per_hour=Decimal("0.49"),
            idempotency_key=f"agree-{ttl}",
            budget_gate=gate,
        )
        assert admission.is_new
    else:
        with pytest.raises(BudgetRejected):
            await admit_raw_pod_lease(
                pg_pool,
                ttl_minutes=ttl,
                max_cost_per_hour=Decimal("0.49"),
                idempotency_key=f"agree-{ttl}",
                budget_gate=gate,
            )
