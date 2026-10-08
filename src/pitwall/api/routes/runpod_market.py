"""Feature-local REST adapter for the shared RunPod market read."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Query, Request

from pitwall.runpod_market import RunpodMarketService

router = APIRouter(tags=["runpod"])


def _service(request: Request) -> RunpodMarketService:
    service = getattr(request.app.state, "runpod_market_service", None)
    if service is None:
        raise RuntimeError("app.state.runpod_market_service is not configured")
    return cast(RunpodMarketService, service)


@router.get("/v1/runpod/catalogue")
async def get_runpod_catalogue(
    request: Request,
    refresh: Annotated[bool, Query()] = False,
) -> dict[str, object]:
    """Return catalogue, availability, pricing, balance, and billing support."""

    snapshot = await _service(request).read(force_refresh=refresh)
    return snapshot.to_serializable_dict()


__all__ = ["get_runpod_catalogue", "router"]
