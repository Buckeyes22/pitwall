"""FastAPI route handlers for the Provider CRUD surface.

Admin routes (POST/PATCH/enable/disable/hibernate) are mounted under
``/v1/admin/providers``. Discovery routes (GET list/health) are mounted
under ``/v1/providers``.

Each handler delegates to :class:`pitwall.db.repository.ProviderRepository`
and raises mapped API exceptions that FastAPI exception handlers translate to
the correct HTTP status codes.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from pitwall.api.exceptions import (
    ProviderCapabilityMissing,
    ProviderConflict,
    ProviderNotFound,
)
from pitwall.api.provider_schemas import (
    ProviderCreate,
    ProviderHealthResponse,
    ProviderHibernateResponse,
    ProviderPatch,
    ProviderResponse,
    validate_provider_registration_config,
)
from pitwall.api.schemas.params import OptionalStrQuery, PathId
from pitwall.api.serializers import provider_to_response
from pitwall.core.enums import ProviderType
from pitwall.core.ids import ulid_new
from pitwall.core.models import (
    Provider,
    ProviderStoragePolicyError,
    default_credential_reference,
)
from pitwall.db.repository import CapabilityRepository, ProviderRepository, insert_audit

router = APIRouter()


def _repo(request: Request) -> ProviderRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return ProviderRepository(pool)


def _capability_repo(request: Request) -> CapabilityRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return CapabilityRepository(pool)


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return pool


async def _patch_or_422(
    repo: ProviderRepository, provider_id: str, **fields: Any
) -> Provider | None:
    """Run ``repo.patch``; a storage-policy rejection is a 422.

    Any other error, such as a stored row that no longer parses, stays a server error.
    """
    try:
        return await repo.patch(provider_id, **fields)
    except ProviderStoragePolicyError as exc:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ["provider"],
                    "msg": "provider update rejected by the provider storage policy",
                    "type": "value_error",
                }
            ],
        ) from exc


@router.post(
    "/v1/admin/providers",
    status_code=201,
    response_model=ProviderResponse,
)
async def create_provider(
    body: ProviderCreate,
    repo: ProviderRepository = Depends(_repo),
    capability_repo: CapabilityRepository = Depends(_capability_repo),
    pool: Any = Depends(_pool),
) -> dict[str, Any]:
    existing = await repo.get(body.name)
    if existing is not None:
        raise ProviderConflict(body.name)
    capability = await capability_repo.get(body.capability_id)
    if capability is None:
        raise ProviderCapabilityMissing(body.capability_id)

    now = dt.datetime.now(dt.UTC)
    prov_id = f"prov_{ulid_new()}"
    prov = Provider(
        id=prov_id,
        capability_id=body.capability_id,
        name=body.name,
        adapter_id=body.adapter_id,
        credential_ref=body.credential_ref,
        provider_type=body.provider_type,
        runpod_endpoint_id=body.runpod_endpoint_id,
        runpod_template_id=body.runpod_template_id,
        region=body.region,
        cloud_type=body.cloud_type,
        config=body.config,
        priority=body.priority,
        enabled=body.enabled,
        health_status=body.health_status,
        consecutive_failures=body.consecutive_failures,
        cooldown_trips=body.cooldown_trips,
        cold_start_p50_ms=body.cold_start_p50_ms,
        cold_start_p95_ms=body.cold_start_p95_ms,
        recent_error_rate=body.recent_error_rate,
        cooldown_until=None,
        source=body.source,
        updated_at=now,
    )
    result = await repo.create(prov)
    await insert_audit(
        pool,
        actor="rest:admin",
        action="create",
        entity_type="provider",
        entity_id=result.id,
        new_value={
            "name": result.name,
            "capability_id": result.capability_id,
            "adapter_id": result.adapter_id.value,
            "credential_ref": result.credential_ref,
        },
    )
    return provider_to_response(result)


@router.patch(
    "/v1/admin/providers/{provider_id}",
    response_model=ProviderResponse,
)
async def patch_provider(
    provider_id: PathId,
    body: ProviderPatch,
    repo: ProviderRepository = Depends(_repo),
    pool: Any = Depends(_pool),
) -> dict[str, Any]:
    existing = await repo.get(provider_id)
    if existing is None:
        raise ProviderNotFound(provider_id)

    old_snapshot = {
        "adapter_id": existing.adapter_id.value,
        "credential_ref": existing.credential_ref,
        "health_status": existing.health_status,
        "priority": existing.priority,
        "enabled": existing.enabled,
    }
    if (
        body.adapter_id is not None
        or body.credential_ref is not None
        or body.provider_type is not None
        or body.runpod_endpoint_id is not None
        or body.config is not None
    ):
        provider_type = body.provider_type or existing.provider_type
        endpoint_id = (
            body.runpod_endpoint_id
            if body.runpod_endpoint_id is not None
            else existing.runpod_endpoint_id
        )
        config = body.config if body.config is not None else existing.config
        cloud_type = body.cloud_type if body.cloud_type is not None else existing.cloud_type
        try:
            validate_provider_registration_config(
                provider_type=provider_type,
                endpoint_id=endpoint_id,
                cloud_type=cloud_type,
                config=config,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "loc": ["body", "config"],
                        "msg": str(exc),
                        "type": "value_error",
                    }
                ],
            ) from exc

    credential_ref = body.credential_ref
    if body.adapter_id is not None and credential_ref is None:
        credential_ref = default_credential_reference(body.adapter_id)

    # The patch and the enable/disable toggle are two writes; one transaction keeps a toggle that
    # fails from leaving the patch behind.
    async with pool.acquire() as conn, conn.transaction():
        result = await _patch_or_422(
            repo,
            provider_id,
            name=body.name,
            adapter_id=body.adapter_id.value if body.adapter_id is not None else None,
            credential_ref=credential_ref,
            provider_type=body.provider_type.value if body.provider_type is not None else None,
            runpod_endpoint_id=body.runpod_endpoint_id,
            runpod_template_id=body.runpod_template_id,
            region=body.region,
            cloud_type=body.cloud_type,
            config=body.config,
            priority=body.priority,
            health_status=body.health_status,
            consecutive_failures=body.consecutive_failures,
            cooldown_trips=body.cooldown_trips,
            cold_start_p50_ms=body.cold_start_p50_ms,
            cold_start_p95_ms=body.cold_start_p95_ms,
            recent_error_rate=body.recent_error_rate,
            conn=conn,
        )
        if result is None:
            raise ProviderNotFound(provider_id)

        # The patch holds the row lock, so this is the value the toggle replaces.
        old_enabled = result.enabled
        toggled = False
        if body.enabled is not None and body.enabled != old_enabled:
            toggled_row = await (repo.enable if body.enabled else repo.disable)(
                provider_id, conn=conn
            )
            if toggled_row is None:
                raise ProviderNotFound(provider_id)
            result = toggled_row
            toggled = True

    new_snapshot = {
        "adapter_id": result.adapter_id.value,
        "credential_ref": result.credential_ref,
        "health_status": result.health_status,
        "priority": result.priority,
        "enabled": result.enabled,
    }
    if toggled:
        # Its own row, so an audit-log action filter of enable/disable finds it.
        await insert_audit(
            pool,
            actor="rest:admin",
            action="enable" if result.enabled else "disable",
            entity_type="provider",
            entity_id=provider_id,
            old_value={"enabled": old_enabled},
            new_value={"enabled": result.enabled},
        )
    old_snapshot["enabled"] = old_enabled
    if set(body.model_dump(exclude_none=True)) - {"enabled"}:
        await insert_audit(
            pool,
            actor="rest:admin",
            action="update",
            entity_type="provider",
            entity_id=provider_id,
            old_value=old_snapshot,
            new_value=new_snapshot,
        )
    return provider_to_response(result)


@router.post(
    "/v1/admin/providers/{provider_id}/enable",
    response_model=ProviderResponse,
)
async def enable_provider(
    provider_id: PathId,
    repo: ProviderRepository = Depends(_repo),
    pool: Any = Depends(_pool),
) -> dict[str, Any]:
    existing = await repo.get(provider_id)
    if existing is None:
        raise ProviderNotFound(provider_id)
    result = await repo.enable(provider_id)
    if result is None:
        raise ProviderNotFound(provider_id)
    await insert_audit(
        pool,
        actor="rest:admin",
        action="enable",
        entity_type="provider",
        entity_id=provider_id,
        old_value={"enabled": False},
        new_value={"enabled": True},
    )
    return provider_to_response(result)


@router.post(
    "/v1/admin/providers/{provider_id}/disable",
    response_model=ProviderResponse,
)
async def disable_provider(
    provider_id: PathId,
    repo: ProviderRepository = Depends(_repo),
    pool: Any = Depends(_pool),
) -> dict[str, Any]:
    existing = await repo.get(provider_id)
    if existing is None:
        raise ProviderNotFound(provider_id)
    result = await repo.disable(provider_id)
    if result is None:
        raise ProviderNotFound(provider_id)
    await insert_audit(
        pool,
        actor="rest:admin",
        action="disable",
        entity_type="provider",
        entity_id=provider_id,
        old_value={"enabled": True},
        new_value={"enabled": False},
    )
    return provider_to_response(result)


@router.post(
    "/v1/admin/providers/{provider_id}/hibernate",
    response_model=ProviderHibernateResponse,
)
async def hibernate_provider(
    provider_id: PathId,
    repo: ProviderRepository = Depends(_repo),
    pool: Any = Depends(_pool),
) -> dict[str, Any]:
    existing = await repo.get(provider_id)
    if existing is None:
        raise ProviderNotFound(provider_id)
    result = await _patch_or_422(
        repo,
        provider_id,
        health_status="hibernated",
    )
    if result is None:
        raise ProviderNotFound(provider_id)
    await insert_audit(
        pool,
        actor="rest:admin",
        action="hibernate",
        entity_type="provider",
        entity_id=provider_id,
        old_value={"health_status": existing.health_status},
        new_value={"health_status": "hibernated"},
    )
    return {
        "id": result.id,
        "name": result.name,
        "health_status": result.health_status,
        "cooldown_until": result.cooldown_until.isoformat() if result.cooldown_until else None,
        "enabled": result.enabled,
    }


@router.get(
    "/v1/providers",
)
async def list_providers(
    capability_id: OptionalStrQuery = None,
    enabled: bool | None = None,
    provider_type: ProviderType | None = None,
    repo: ProviderRepository = Depends(_repo),
) -> dict[str, Any]:
    list_kwargs: dict[str, Any] = {
        "capability_id": capability_id,
        "enabled_only": enabled is True,
        "provider_type": provider_type.value if provider_type is not None else None,
        "limit": 100,
        "offset": 0,
    }
    if enabled is False:
        list_kwargs["enabled"] = False
    provs = await repo.list(**list_kwargs)
    return {
        "items": [provider_to_response(p) for p in provs],
        "total": len(provs),
    }


@router.get(
    "/v1/providers/{provider_id}",
    response_model=ProviderResponse,
)
async def get_provider(
    provider_id: PathId,
    repo: ProviderRepository = Depends(_repo),
) -> dict[str, Any]:
    prov = await repo.get(provider_id)
    if prov is None:
        raise ProviderNotFound(provider_id)
    return provider_to_response(prov)


@router.get(
    "/v1/providers/{provider_id}/health",
    response_model=ProviderHealthResponse,
)
async def get_provider_health(
    provider_id: PathId,
    repo: ProviderRepository = Depends(_repo),
) -> dict[str, Any]:
    prov = await repo.get(provider_id)
    if prov is None:
        raise ProviderNotFound(provider_id)
    return {
        "id": prov.id,
        "name": prov.name,
        "health_status": prov.health_status,
        "cooldown_until": prov.cooldown_until.isoformat() if prov.cooldown_until else None,
        "consecutive_failures": prov.consecutive_failures,
        "cooldown_trips": prov.cooldown_trips,
        "recent_error_rate": prov.recent_error_rate,
        "updated_at": prov.updated_at.isoformat(),
    }


__all__ = ["router"]
