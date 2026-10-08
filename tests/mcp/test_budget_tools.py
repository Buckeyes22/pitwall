"""MCP budget tools delegate to the budget-limits service and surface its refusals."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from mcp.shared.exceptions import MCPError

from pitwall.cost.budget_limits import BudgetLimits, BudgetLimitsError
from pitwall.mcp.tools import budget as tools


@pytest.mark.anyio
async def test_status_returns_the_service_view(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_status(pool: Any) -> dict[str, Any]:
        return {"monthly_budget_usd": "5", "source": "environment", "mtd_spend_usd": "4.817201"}

    monkeypatch.setattr(tools, "get_pool", lambda: _async(object()))
    monkeypatch.setattr(tools, "budget_status", fake_status)
    assert (await tools.pitwall_budget_status())["monthly_budget_usd"] == "5"


@pytest.mark.anyio
async def test_set_passes_values_reason_and_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        seen.update(kwargs)
        return BudgetLimits(
            Decimal("50"),
            Decimal("10"),
            "runtime",
            reason=kwargs["reason"],
            updated_by=kwargs["actor"],
        )

    async def fake_status(pool: Any) -> dict[str, Any]:
        return {"monthly_budget_usd": "50"}

    monkeypatch.setattr(tools, "get_pool", lambda: _async(object()))
    monkeypatch.setattr(tools, "set_limits", fake_set)
    monkeypatch.setattr(tools, "budget_status", fake_status)
    result = await tools.pitwall_budget_set(reason="4x4090 batch", monthly_budget_usd="50")
    assert seen == {
        "monthly_budget_usd": "50",
        "per_request_max_usd": None,
        "reason": "4x4090 batch",
        "actor": "mcp",
    }
    assert (
        result["limits"]["monthly_budget_usd"] == "50"
        and result["status"]["monthly_budget_usd"] == "50"
    )


@pytest.mark.anyio
async def test_invalid_change_is_a_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        raise BudgetLimitsError("a reason is required for every budget change")

    monkeypatch.setattr(tools, "get_pool", lambda: _async(object()))
    monkeypatch.setattr(tools, "set_limits", fake_set)
    with pytest.raises(MCPError) as caught:
        await tools.pitwall_budget_set(reason=" ", monthly_budget_usd="50")
    assert caught.value.error.data["error"] == "invalid_budget_limits"


async def _async(value: Any) -> Any:
    return value
