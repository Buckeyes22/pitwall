"""Budget gate input handling: floats are rejected and rejection snapshots carry real spend."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.cost.budget_gate import BudgetGate, BudgetRejected


def _pool(spend: Decimal) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="SELECT 1")
    conn.fetchrow = AsyncMock(
        side_effect=lambda q, *a: None if "pitwall.budget_limits" in q else {"s": spend}
    )
    conn.fetchval = AsyncMock(return_value="wkl_test")
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def test_float_rejected() -> None:
    with pytest.raises(ValueError, match="monthly_budget_usd.*Decimal"):
        BudgetGate(_pool(Decimal("0")), monthly_budget_usd=10.5, per_request_max_usd=1)
    with pytest.raises(ValueError, match="per_request_max_usd.*Decimal"):
        BudgetGate(_pool(Decimal("0")), monthly_budget_usd=10, per_request_max_usd=0.1)


@pytest.mark.anyio
async def test_float_estimate_rejected() -> None:
    gate = BudgetGate(_pool(Decimal("0")), monthly_budget_usd=10, per_request_max_usd=5)
    with pytest.raises(ValueError, match="estimate_usd.*Decimal"):
        await gate.check_available(0.1)  # type: ignore[arg-type]  # reason: a float is the rejected input
    with pytest.raises(ValueError, match="estimate_usd.*Decimal"):
        await gate.try_launch(
            capability_id="cap",
            provider_id="prv",
            estimate_usd=0.1,  # type: ignore[arg-type]  # reason: a float is the rejected input
        )


@pytest.mark.anyio
async def test_decimal_string_and_int_inputs_still_work() -> None:
    gate = BudgetGate(
        _pool(Decimal("0")), monthly_budget_usd="10", per_request_max_usd=Decimal("5")
    )
    await gate.check_available("0.1")
    await gate.check_available(1)


@pytest.mark.anyio
async def test_per_request_rejection_snapshot_reports_real_spend() -> None:
    gate = BudgetGate(
        _pool(Decimal("7.25")),
        monthly_budget_usd=Decimal("10"),
        per_request_max_usd=Decimal("1"),
    )

    with pytest.raises(BudgetRejected) as check:
        await gate.check_available(Decimal("2"))
    assert check.value.reason == "per_request_cap"
    assert check.value.snapshot.mtd_spend_usd == Decimal("7.25")
    assert check.value.snapshot.budget_remaining_usd == Decimal("2.75")

    with pytest.raises(BudgetRejected) as launch:
        await gate.try_launch(capability_id="cap", provider_id="prv", estimate_usd=Decimal("2"))
    assert launch.value.reason == "per_request_cap"
    assert launch.value.snapshot.mtd_spend_usd == Decimal("7.25")
    assert launch.value.snapshot.budget_remaining_usd == Decimal("2.75")
