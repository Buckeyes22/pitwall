"""Admin tools — capability and provider management for the MCP surface.

These tools expose the same write operations as the REST admin API endpoints:
- POST /v1/admin/capabilities      → pitwall_create_capability
- PATCH /v1/admin/capabilities    → pitwall_update_capability
- POST /v1/admin/providers       → pitwall_create_provider
- PATCH /v1/admin/providers/{id}  → pitwall_update_provider
- POST /v1/admin/providers/{id}/disable → pitwall_disable_provider
- POST /v1/admin/providers/{id}/hibernate → pitwall_hibernate_provider

All handlers delegate to the repository layer and insert audit entries.
Audit context uses actor="mcp:admin" to distinguish MCP-initiated changes
from REST-initiated ones. It is a surface label, not an identity: any local process
that can start the stdio server has full access.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pitwall.api.provider_schemas import validate_provider_registration_config
from pitwall.api.serializers import capability_to_response, provider_to_response
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
    ResultDelivery,
)
from pitwall.core.ids import ulid_new
from pitwall.core.models import (
    Capability,
    CapabilityDefaults,
    Provider,
    default_credential_reference,
    is_credential_reference_name,
    validate_provider_storage_payload,
)
from pitwall.db import get_pool
from pitwall.db.repository import CapabilityRepository, ProviderRepository, insert_audit


async def pitwall_create_capability(
    name: str,
    version: str,
    capability_class: str,
    cost_mode: str,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    repo = CapabilityRepository(pool)

    existing = await repo.get_by_name(name)
    if existing is not None:
        from pitwall.api.exceptions import CapabilityConflict

        raise CapabilityConflict(name)

    now = dt.datetime.now(dt.UTC)
    cap_id = f"cap_{ulid_new()}"

    try:
        class_enum = CapabilityClass(capability_class)
    except ValueError:
        raise ValueError(f"Invalid capability_class: {capability_class!r}") from None

    try:
        cost_mode_enum = CostMode(cost_mode)
    except ValueError:
        raise ValueError(f"Invalid cost_mode: {cost_mode!r}") from None

    cap = Capability(
        id=cap_id,
        name=name,
        version=version,
        class_=class_enum,
        description=description,
        input_schema=input_schema or {},
        output_schema=output_schema or {},
        defaults=CapabilityDefaults(
            execution_timeout_ms=60_000,
            ttl_ms=300_000,
            result_delivery=ResultDelivery.SYNC,
        ),
        cost_mode=cost_mode_enum,
        hints_supported=[],
        source=CapabilitySource.API,
        created_at=now,
        updated_at=now,
    )

    result = await repo.create(cap)
    await insert_audit(
        pool,
        actor="mcp:admin",
        action="create",
        entity_type="capability",
        entity_id=result.id,
        new_value={"name": result.name, "version": result.version},
    )
    return capability_to_response(result)


async def pitwall_update_capability(
    capability_id: str,
    name: str | None = None,
    version: str | None = None,
    description: str | None = None,
    cost_mode: str | None = None,
    enabled: bool | None = None,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = await get_pool()
    repo = CapabilityRepository(pool)

    existing = await repo.get(capability_id)
    if existing is None:
        from pitwall.api.exceptions import CapabilityNotFound

        raise CapabilityNotFound(capability_id)

    old_snapshot = {
        "description": existing.description,
        "input_schema": existing.input_schema,
        "output_schema": existing.output_schema,
        "defaults": existing.defaults.model_dump(mode="json"),
        "enabled": existing.enabled,
    }

    config_patch: dict[str, Any] | None = None
    if description is not None or input_schema is not None or output_schema is not None:
        config_patch = {
            "description": description if description is not None else existing.description,
            "input_schema": input_schema if input_schema is not None else existing.input_schema,
            "output_schema": output_schema if output_schema is not None else existing.output_schema,
        }

    if cost_mode is not None:
        try:
            CostMode(cost_mode)
        except ValueError:
            raise ValueError(f"Invalid cost_mode: {cost_mode!r}") from None

    # The patch and the enable/disable toggle are two writes; one transaction keeps a toggle that
    # fails from leaving the patch behind.
    async with pool.acquire() as conn, conn.transaction():
        result = await repo.patch(
            capability_id,
            name=name,
            version=version,
            cost_mode=cost_mode,
            config=config_patch,
            conn=conn,
        )
        if result is None:
            from pitwall.api.exceptions import CapabilityNotFound

            raise CapabilityNotFound(capability_id)

        # The patch holds the row lock, so this is the value the toggle replaces.
        old_enabled = result.enabled
        toggled = False
        if enabled is not None and enabled != old_enabled:
            toggled_row = await (repo.enable if enabled else repo.disable)(capability_id, conn=conn)
            if toggled_row is None:
                from pitwall.api.exceptions import CapabilityNotFound

                raise CapabilityNotFound(capability_id)
            result = toggled_row
            toggled = True

    new_snapshot = {
        "description": result.description,
        "input_schema": result.input_schema,
        "output_schema": result.output_schema,
        "defaults": result.defaults.model_dump(mode="json"),
        "enabled": result.enabled,
    }
    if toggled:
        # Its own row, so a pitwall_audit_log action filter of enable/disable finds it.
        await insert_audit(
            pool,
            actor="mcp:admin",
            action="enable" if result.enabled else "disable",
            entity_type="capability",
            entity_id=capability_id,
            old_value={"enabled": old_enabled},
            new_value={"enabled": result.enabled},
        )
    old_snapshot["enabled"] = old_enabled
    other_fields = (name, version, description, cost_mode, input_schema, output_schema)
    if any(value is not None for value in other_fields):
        await insert_audit(
            pool,
            actor="mcp:admin",
            action="update",
            entity_type="capability",
            entity_id=capability_id,
            old_value=old_snapshot,
            new_value=new_snapshot,
        )
    return capability_to_response(result)


async def pitwall_create_provider(
    capability_id: str,
    name: str,
    provider_type: str,
    adapter_id: str = "runpod",
    credential_ref: str | None = None,
    runpod_endpoint_id: str | None = None,
    runpod_template_id: str | None = None,
    region: str | None = None,
    cloud_type: str | None = None,
    config: dict[str, Any] | None = None,
    priority: int = 0,
    enabled: bool = True,
) -> dict[str, Any]:
    validate_provider_storage_payload(
        {
            "capability_id": capability_id,
            "name": name,
            "provider_type": provider_type,
            "adapter_id": adapter_id,
            "credential_ref": credential_ref,
            "runpod_endpoint_id": runpod_endpoint_id,
            "runpod_template_id": runpod_template_id,
            "region": region,
            "cloud_type": cloud_type,
            "config": config,
        }
    )
    pool = await get_pool()
    repo = ProviderRepository(pool)

    existing = await repo.get_by_name(name)
    if existing is not None:
        from pitwall.api.exceptions import ProviderConflict

        raise ProviderConflict(name)

    try:
        provider_type_enum = ProviderType(provider_type)
    except ValueError:
        raise ValueError(f"Invalid provider_type: {provider_type!r}") from None

    try:
        adapter_id_enum = ProviderAdapterId(adapter_id)
    except ValueError:
        raise ValueError(f"Invalid adapter_id: {adapter_id!r}") from None
    resolved_credential_ref = (
        credential_ref
        if credential_ref is not None
        else default_credential_reference(adapter_id_enum)
    )
    if not is_credential_reference_name(resolved_credential_ref):
        raise ValueError("credential_ref must be an environment-variable name")

    if config is not None:
        validate_provider_registration_config(
            provider_type=provider_type_enum,
            endpoint_id=runpod_endpoint_id,
            cloud_type=cloud_type,
            config=config,
        )

    now = dt.datetime.now(dt.UTC)
    prov_id = f"prov_{ulid_new()}"

    prov = Provider(
        id=prov_id,
        capability_id=capability_id,
        name=name,
        adapter_id=adapter_id_enum,
        credential_ref=resolved_credential_ref,
        provider_type=provider_type_enum,
        runpod_endpoint_id=runpod_endpoint_id,
        runpod_template_id=runpod_template_id,
        region=region,
        cloud_type=cloud_type,
        config=config or {},
        priority=priority,
        enabled=enabled,
        health_status="unknown",
        consecutive_failures=0,
        cooldown_trips=0,
        cold_start_p50_ms=None,
        cold_start_p95_ms=None,
        recent_error_rate=0.0,
        cooldown_until=None,
        source=CapabilitySource.API,
        updated_at=now,
    )

    result = await repo.create(prov)
    await insert_audit(
        pool,
        actor="mcp:admin",
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


async def pitwall_update_provider(
    provider_id: str,
    name: str | None = None,
    adapter_id: str | None = None,
    credential_ref: str | None = None,
    provider_type: str | None = None,
    runpod_endpoint_id: str | None = None,
    runpod_template_id: str | None = None,
    region: str | None = None,
    cloud_type: str | None = None,
    config: dict[str, Any] | None = None,
    priority: int | None = None,
    enabled: bool | None = None,
    health_status: str | None = None,
    consecutive_failures: int | None = None,
    cooldown_trips: int | None = None,
    cold_start_p50_ms: int | None = None,
    cold_start_p95_ms: int | None = None,
    recent_error_rate: float | None = None,
) -> dict[str, Any]:
    validate_provider_storage_payload(
        {
            "provider_id": provider_id,
            "name": name,
            "adapter_id": adapter_id,
            "credential_ref": credential_ref,
            "provider_type": provider_type,
            "runpod_endpoint_id": runpod_endpoint_id,
            "runpod_template_id": runpod_template_id,
            "region": region,
            "cloud_type": cloud_type,
            "config": config,
        }
    )
    pool = await get_pool()
    repo = ProviderRepository(pool)

    existing = await repo.get(provider_id)
    if existing is None:
        from pitwall.api.exceptions import ProviderNotFound

        raise ProviderNotFound(provider_id)

    old_snapshot = {
        "adapter_id": existing.adapter_id.value,
        "credential_ref": existing.credential_ref,
        "health_status": existing.health_status,
        "priority": existing.priority,
        "enabled": existing.enabled,
    }

    provider_type_enum: ProviderType | None = None
    if provider_type is not None:
        try:
            provider_type_enum = ProviderType(provider_type)
        except ValueError:
            raise ValueError(f"Invalid provider_type: {provider_type!r}") from None

    adapter_id_enum: ProviderAdapterId | None = None
    if adapter_id is not None:
        try:
            adapter_id_enum = ProviderAdapterId(adapter_id)
        except ValueError:
            raise ValueError(f"Invalid adapter_id: {adapter_id!r}") from None
    resolved_credential_ref = credential_ref
    if resolved_credential_ref is not None and not is_credential_reference_name(
        resolved_credential_ref
    ):
        raise ValueError("credential_ref must be an environment-variable name")
    if adapter_id_enum is not None and resolved_credential_ref is None:
        resolved_credential_ref = default_credential_reference(adapter_id_enum)

    if config is not None or provider_type_enum is not None or runpod_endpoint_id is not None:
        validate_provider_registration_config(
            provider_type=provider_type_enum or existing.provider_type,
            endpoint_id=runpod_endpoint_id
            if runpod_endpoint_id is not None
            else existing.runpod_endpoint_id,
            cloud_type=cloud_type if cloud_type is not None else existing.cloud_type,
            config=config if config is not None else existing.config,
        )

    # The patch and the enable/disable toggle are two writes; one transaction keeps a toggle that
    # fails from leaving the patch behind.
    async with pool.acquire() as conn, conn.transaction():
        result = await repo.patch(
            provider_id,
            name=name,
            adapter_id=adapter_id_enum.value if adapter_id_enum is not None else None,
            credential_ref=resolved_credential_ref,
            provider_type=provider_type_enum.value if provider_type_enum is not None else None,
            runpod_endpoint_id=runpod_endpoint_id,
            runpod_template_id=runpod_template_id,
            region=region,
            cloud_type=cloud_type,
            config=config,
            priority=priority,
            health_status=health_status,
            consecutive_failures=consecutive_failures,
            cooldown_trips=cooldown_trips,
            cold_start_p50_ms=cold_start_p50_ms,
            cold_start_p95_ms=cold_start_p95_ms,
            recent_error_rate=recent_error_rate,
            conn=conn,
        )
        if result is None:
            from pitwall.api.exceptions import ProviderNotFound

            raise ProviderNotFound(provider_id)

        # The patch holds the row lock, so this is the value the toggle replaces.
        old_enabled = result.enabled
        toggled = False
        if enabled is not None and enabled != old_enabled:
            toggled_row = await (repo.enable if enabled else repo.disable)(provider_id, conn=conn)
            if toggled_row is None:
                from pitwall.api.exceptions import ProviderNotFound

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
    other_fields = (
        name,
        adapter_id,
        credential_ref,
        provider_type,
        runpod_endpoint_id,
        runpod_template_id,
        region,
        cloud_type,
        config,
        priority,
        health_status,
        consecutive_failures,
        cooldown_trips,
        cold_start_p50_ms,
        cold_start_p95_ms,
        recent_error_rate,
    )
    if toggled:
        # Its own row, so a pitwall_audit_log action filter of enable/disable finds it.
        await insert_audit(
            pool,
            actor="mcp:admin",
            action="enable" if result.enabled else "disable",
            entity_type="provider",
            entity_id=provider_id,
            old_value={"enabled": old_enabled},
            new_value={"enabled": result.enabled},
        )
    old_snapshot["enabled"] = old_enabled
    if any(value is not None for value in other_fields):
        await insert_audit(
            pool,
            actor="mcp:admin",
            action="update",
            entity_type="provider",
            entity_id=provider_id,
            old_value=old_snapshot,
            new_value=new_snapshot,
        )
    return provider_to_response(result)


async def pitwall_disable_provider(
    provider_id: str,
) -> dict[str, Any]:
    pool = await get_pool()
    repo = ProviderRepository(pool)

    existing = await repo.get(provider_id)
    if existing is None:
        from pitwall.api.exceptions import ProviderNotFound

        raise ProviderNotFound(provider_id)

    result = await repo.disable(provider_id)
    if result is None:
        from pitwall.api.exceptions import ProviderNotFound

        raise ProviderNotFound(provider_id)

    await insert_audit(
        pool,
        actor="mcp:admin",
        action="disable",
        entity_type="provider",
        entity_id=provider_id,
        old_value={"enabled": True},
        new_value={"enabled": False},
    )
    return provider_to_response(result)


async def pitwall_hibernate_provider(
    provider_id: str,
) -> dict[str, Any]:
    pool = await get_pool()
    repo = ProviderRepository(pool)

    existing = await repo.get(provider_id)
    if existing is None:
        from pitwall.api.exceptions import ProviderNotFound

        raise ProviderNotFound(provider_id)

    result = await repo.patch(
        provider_id,
        health_status="hibernated",
    )
    if result is None:
        from pitwall.api.exceptions import ProviderNotFound

        raise ProviderNotFound(provider_id)

    await insert_audit(
        pool,
        actor="mcp:admin",
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
