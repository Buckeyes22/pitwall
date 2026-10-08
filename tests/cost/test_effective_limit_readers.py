"""Budget readers outside the gate use the runtime limits, not the environment values."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from pitwall.cost.budget_limits import BudgetLimits

RUNTIME = BudgetLimits(Decimal("50"), Decimal("12"), "runtime")


class _Gate:
    monthly_budget_usd = Decimal("5")
    per_request_max_usd = Decimal("10")

    async def current_mtd_spend(self) -> Decimal:
        return Decimal("4.82")

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        return RUNTIME


@pytest.mark.anyio
async def test_routing_headroom_uses_runtime_limits() -> None:
    from pitwall.routing.production import ProductionRoutingService

    service = ProductionRoutingService.__new__(ProductionRoutingService)
    service._budget = _Gate()
    assert await service._available_budget_limit() == Decimal("12")
    rejection = await service._budget_rejection(Decimal("13"))
    assert (rejection.reason, rejection.snapshot.monthly_budget_usd) == (
        "per_request_cap",
        Decimal("50"),
    )


@pytest.mark.anyio
async def test_billing_reconciliation_uses_runtime_monthly_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.cost import billing_read

    class _Billing:
        client_balance_usd = Decimal("48.48")
        current_spend_per_hr_usd = Decimal("0")
        spend_limit_usd = Decimal("80")
        under_balance = False

    async def fake_snapshot(_client: Any) -> Any:
        return _Billing()

    monkeypatch.setattr(billing_read, "read_billing_snapshot", fake_snapshot)
    result = await billing_read.reconcile_with_budget(object(), _Gate())  # type: ignore[arg-type]  # reason: test passes a bare object() where RunpodGraphQLClient is expected
    assert result.pitwall_monthly_budget_usd == Decimal("50")
