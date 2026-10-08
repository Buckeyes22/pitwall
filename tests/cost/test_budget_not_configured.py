"""A missing budget setting is a typed refusal, not a generic failure (J34, found at integration).

With PITWALL_MONTHLY_BUDGET_USD unset, the raw-pod preview crashed with a bare ValueError, which
the MCP safe boundary could only report as ``tool_execution_failed``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pitwall.api.leases.launch import preview_raw_pod_lease_budget
from pitwall.cost.budget_gate import BudgetGate, BudgetNotConfigured
from pitwall.mcp.safe_boundary import _stable_error_payload

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _no_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PITWALL_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("PITWALL_PER_REQUEST_MAX_USD", raising=False)


def test_the_gate_raises_a_typed_error_that_is_still_a_value_error() -> None:
    with pytest.raises(BudgetNotConfigured) as raised:
        BudgetGate(MagicMock())
    assert isinstance(raised.value, ValueError)
    assert BudgetNotConfigured.error_code == "budget_not_configured"
    assert "PITWALL_MONTHLY_BUDGET_USD" in str(raised.value)


def test_the_safe_boundary_reports_the_typed_code() -> None:
    try:
        BudgetGate(MagicMock())
    except BudgetNotConfigured as exc:
        tool_error = ToolError("failed")
        tool_error.__cause__ = exc
        payload: dict[str, Any] = _stable_error_payload(tool_error)
    assert payload == {"error": "budget_not_configured"}


async def test_the_raw_pod_preview_returns_an_unconfigured_verdict() -> None:
    verdict = await preview_raw_pod_lease_budget(
        MagicMock(), ttl_minutes=60, max_cost_per_hour=Decimal("0.60")
    )
    assert verdict["admitted"] is False
    assert verdict["reason"] == "budget_not_configured"
    assert verdict["snapshot"]["estimate_usd"] == "0.600000"
    assert "PITWALL_MONTHLY_BUDGET_USD" in verdict["remedy"]
    assert verdict["estimate_basis"] == "ttl_minutes x max_cost_per_hour"
