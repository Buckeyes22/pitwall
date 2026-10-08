"""Budget limit tools: read the effective limits and change them without a restart.

Every change needs a reason and is written to config_audit by the service layer.
"""

from __future__ import annotations

from typing import Any

from pitwall.cost.budget_limits import BudgetLimitsError, budget_status, set_limits
from pitwall.db import get_pool
from pitwall.mcp.error_adapter import adapt_error


async def pitwall_budget_status() -> dict[str, Any]:
    return await budget_status(await get_pool())


async def pitwall_budget_set(
    reason: str,
    monthly_budget_usd: str | None = None,
    per_request_max_usd: str | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    try:
        limits = await set_limits(
            pool,
            monthly_budget_usd=monthly_budget_usd,
            per_request_max_usd=per_request_max_usd,
            reason=reason,
            actor="mcp",
        )
    except BudgetLimitsError as exc:
        raise adapt_error(exc) from exc
    return {"limits": limits.to_dict(), "status": await budget_status(pool)}


__all__ = ["pitwall_budget_set", "pitwall_budget_status"]
