"""Admin REST for runtime budget limits (behind the /v1/admin secret)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from pitwall.core.models import StoredText
from pitwall.cost.budget_limits import BudgetLimitsError, budget_status, set_limits

router = APIRouter(tags=["budget"])


class BudgetLimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: StoredText
    monthly_budget_usd: str | None = None
    per_request_max_usd: str | None = None


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return pool


@router.get("/v1/admin/budget")
async def get_budget(request: Request) -> dict[str, Any]:
    return await budget_status(_pool(request))


@router.put("/v1/admin/budget", response_model=None)
async def put_budget(request: Request, update: BudgetLimitsUpdate) -> dict[str, Any] | JSONResponse:
    pool = _pool(request)
    try:
        limits = await set_limits(
            pool,
            monthly_budget_usd=update.monthly_budget_usd,
            per_request_max_usd=update.per_request_max_usd,
            reason=update.reason,
            actor="api:admin",
        )
    except BudgetLimitsError as exc:
        return JSONResponse(
            status_code=exc.status_code, content={"error": exc.error_code, "detail": str(exc)}
        )
    return {"limits": limits.to_dict(), "status": await budget_status(pool)}


__all__ = ["router"]
