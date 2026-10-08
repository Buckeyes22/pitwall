"""pitwall budget show|set call the budget-limits service."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from pitwall.cli import budget as cli_budget
from pitwall.cost.budget_limits import BudgetLimits


def test_set_requires_a_reason(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        cli_budget.cmd_budget(["set", "--monthly", "50"])
    assert exited.value.code == 2


def test_set_calls_the_service_and_prints_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def fake_pool() -> Any:
        return object()

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
        return {
            "monthly_budget_usd": "50",
            "per_request_max_usd": "10",
            "source": "runtime",
            "mtd_spend_usd": "4.817201",
            "budget_remaining_usd": "45.182799",
        }

    monkeypatch.setattr(cli_budget, "get_pool", fake_pool)
    monkeypatch.setattr(cli_budget, "set_limits", fake_set)
    monkeypatch.setattr(cli_budget, "budget_status", fake_status)
    assert cli_budget.cmd_budget(["set", "--monthly", "50", "--reason", "batch", "--json"]) == 0
    assert seen == {
        "monthly_budget_usd": "50",
        "per_request_max_usd": None,
        "reason": "batch",
        "actor": "cli",
    }
    assert json.loads(capsys.readouterr().out)["monthly_budget_usd"] == "50"
