from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pitwall.api.admin.kill_switch import KillSwitchEngaged
from pitwall.api.exceptions import ServeBudgetExhausted, ServeKillSwitchEngaged
from pitwall.api.schemas.serve import ServeCreate, ServeResponse
from pitwall.config import get_settings
from pitwall.cost.budget_gate import BudgetRejected
from pitwall.models import load_catalogue
from pitwall.serve import ServeRequest, serve_model

router = APIRouter()


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("app.state.pool is not configured")
    return pool


@router.post(
    "/v1/serve",
    response_model=ServeResponse,
    responses={
        409: {"description": "A live lease serves another model"},
        422: {"description": "Rate, variant, or template is invalid"},
        502: {"description": "Served model verification failed"},
        503: {"description": "Pod launch failed"},
    },
)
async def serve(
    body: ServeCreate,
    request: Request,
    pool: Any = Depends(_pool),
) -> dict[str, Any] | JSONResponse:
    settings = get_settings()
    base_url = settings.pitwall_base_url.strip() or str(request.base_url).rstrip("/")
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    values["capability_name"] = values.pop("capability")
    if body.model is not None and body.gpu_class is not None and "engine" not in values:
        values["engine"] = "vllm"
    try:
        # ServeRequest enforces rules ServeCreate does not (start_args secrets, for one);
        # a violation is the caller's 422, never a 500.
        serve_request = ServeRequest.model_validate(values)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_input=False, include_url=False)) from exc
    try:
        result = await serve_model(
            pool,
            serve_request,
            base_url=base_url,
            settings=settings,
            catalogue=load_catalogue(),
            redis=getattr(request.app.state, "redis", None),
            market_service=getattr(request.app.state, "runpod_market_service", None),
        )
    except BudgetRejected as exc:
        mapped = ServeBudgetExhausted(
            reason=exc.reason,
            snapshot=exc.snapshot.to_serializable_dict(),
        )
        return JSONResponse(
            status_code=mapped.status_code,
            content=mapped.to_response_body(),
        )
    except Exception as exc:  # reason: map kill-switch admission across reloaded API modules
        if getattr(exc, "error_code", None) != KillSwitchEngaged.error_code:
            raise
        kill_mapped = ServeKillSwitchEngaged()
        return JSONResponse(
            status_code=kill_mapped.status_code,
            content=kill_mapped.to_response_body(),
        )
    return result.to_dict()


__all__ = ["router"]
