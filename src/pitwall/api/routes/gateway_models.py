"""Feature-local REST read for the gateway proxy model-id map (Task 10).

The route joins ``model_id_map`` (model_id -> capability -> provider) with the
provider's ``config.gateway.catalog`` evidence so consumers can render the
``trains_on_prompts`` and ``tos`` privacy flags alongside the model id.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from fastapi import APIRouter, Request
from pydantic import ValidationError

from pitwall.api.schemas.quotas import GatewayModelList, GatewayModelRow
from pitwall.db.quota_repository import QuotaRepository

log = logging.getLogger("pitwall.api.routes.gateway_models")

router = APIRouter(tags=["gateway_models"])


def _resolve_repo(request: Request) -> QuotaRepository | None:
    configured: object = getattr(request.app.state, "quota_repository", None)
    if configured is not None:
        return cast(QuotaRepository, configured)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return None
    return QuotaRepository(pool)


def _provider_lookup(request: Request) -> Callable[[str], Awaitable[Any]] | None:
    configured: object = getattr(request.app.state, "provider_repository", None)
    if configured is not None:
        getter = getattr(configured, "get", None)
        if callable(getter):
            return cast(Callable[[str], Awaitable[Any]], getter)
        return None
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return None
    from pitwall.db.repository import ProviderRepository

    provider_repo = ProviderRepository(pool)
    return provider_repo.get


def _gateway_catalog(provider_config: Any) -> dict[str, Any]:
    if not isinstance(provider_config, dict):
        return {}
    gateway = provider_config.get("gateway")
    if not isinstance(gateway, dict):
        return {}
    catalog = gateway.get("catalog")
    return catalog if isinstance(catalog, dict) else {}


async def _load_rows(
    request: Request,
) -> list[GatewayModelRow]:
    repo = _resolve_repo(request)
    if repo is None:
        return []
    model_ids = await repo.list_model_ids()
    if not model_ids:
        return []
    lookup = _provider_lookup(request)
    if lookup is None:
        return []
    rows: list[GatewayModelRow] = []
    for mapping in model_ids:
        provider = await lookup(mapping.provider)
        if provider is None:
            log.debug("skipping unmapped provider %s for %s", mapping.provider, mapping.model_id)
            continue
        catalog = _gateway_catalog(provider.config)
        try:
            rows.append(
                GatewayModelRow(
                    id=mapping.model_id,
                    capability=mapping.capability,
                    provider=mapping.provider,
                    trains_on_prompts=bool(catalog.get("trains_on_prompts", False)),
                    tos=str(catalog.get("tos", "unknown")),
                )
            )
        except ValidationError as exc:
            log.warning(
                "gateway model row validation failed for %s: %s",
                mapping.model_id,
                exc,
            )
    return rows


@router.get("/v1/gateway/models", response_model=GatewayModelList)
async def list_gateway_models(request: Request) -> dict[str, Any]:
    """Return the proxy model-id inventory with privacy flags from catalog evidence."""

    rows = await _load_rows(request)
    return GatewayModelList(data=rows).model_dump(mode="json")


__all__ = ["list_gateway_models", "router"]
