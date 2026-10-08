"""Shared read service for provider configuration, health, and availability."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, get_args

import httpx
from pydantic import BaseModel, SecretStr

from pitwall.core.models import Provider, redact_provider_serialized_config
from pitwall.db.repository import ProviderRepository
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityRequest,
    CredentialResolutionError,
    ProviderCapability,
    ProviderOperationContext,
)
from pitwall.providers.registry import (
    ProviderNotRegisteredError,
    ProviderRegistry,
    UnsupportedProviderCapabilityError,
    get_default_registry,
)

ProviderReadStatus = str


class ProviderRecordReader(Protocol):
    """Persistence reads required by the provider operations service."""

    async def get(self, provider_id_or_name: str) -> Provider | None: ...

    async def list(
        self,
        capability_id: str | None = None,
        enabled_only: bool = True,
        provider_type: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Provider]: ...


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Safe provider configuration and declared operational capabilities."""

    provider_id: str
    name: str
    adapter_id: str
    credential_ref: str
    credential_configured: bool
    provider_type: str
    enabled: bool
    persisted_health: str
    capabilities: tuple[str, ...]
    pricing_kind: str
    config: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "name": self.name,
            "adapter_id": self.adapter_id,
            "credential_ref": self.credential_ref,
            "credential_configured": self.credential_configured,
            "provider_type": self.provider_type,
            "enabled": self.enabled,
            "persisted_health": self.persisted_health,
            "capabilities": list(self.capabilities),
            "pricing_kind": self.pricing_kind,
            "config": dict(self.config),
        }


@dataclass(frozen=True, slots=True)
class ProviderAvailabilityEntry:
    """JSON-stable availability entry shared by every operator surface."""

    resource_id: str
    kind: str
    available: bool | None
    region: str | None
    accelerator: str | None
    accelerator_count: int | None
    pricing: Mapping[str, Decimal]
    attributes: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "available": self.available,
            "region": self.region,
            "accelerator": self.accelerator,
            "accelerator_count": self.accelerator_count,
            "pricing": {key: format(value, "f") for key, value in self.pricing.items()},
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class ProviderAvailabilityRead:
    """Available, empty, unavailable, or error snapshot with no secret text."""

    provider_id: str
    status: ProviderReadStatus
    observed_at: dt.datetime
    source_contract: str | None
    items: tuple[ProviderAvailabilityEntry, ...] = ()
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "observed_at": self.observed_at.isoformat(),
            "source_contract": self.source_contract,
            "items": [item.as_dict() for item in self.items],
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class ProviderHealthRead:
    """Persisted health plus an optional bounded live probe result."""

    provider_id: str
    persisted_health: str
    live_status: str
    observed_at: dt.datetime
    availability_count: int | None
    error_code: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "persisted_health": self.persisted_health,
            "live_status": self.live_status,
            "observed_at": self.observed_at.isoformat(),
            "availability_count": self.availability_count,
            "error_code": self.error_code,
        }


class ProviderOperationsService:
    """One provider-neutral service consumed by REST, MCP, CLI, and TUI."""

    def __init__(
        self,
        pool: Any,
        *,
        repository: ProviderRecordReader | None = None,
        registry: ProviderRegistry | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository or ProviderRepository(pool)
        self._registry = registry or get_default_registry()
        self._environ = environ if environ is not None else os.environ

    async def list_descriptors(
        self,
        *,
        capability_id: str | None = None,
        enabled_only: bool = False,
        limit: int = 100,
    ) -> tuple[ProviderDescriptor, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("provider list limit must be between 1 and 100")
        providers = await self._repository.list(
            capability_id=capability_id,
            enabled_only=enabled_only,
            limit=limit,
            offset=0,
        )
        return tuple(self._descriptor(provider) for provider in providers)

    async def describe(self, provider_id: str) -> ProviderDescriptor | None:
        provider = await self._repository.get(provider_id)
        return None if provider is None else self._descriptor(provider)

    async def availability(
        self,
        provider_id: str,
        *,
        limit: int = 100,
        now: dt.datetime | None = None,
    ) -> ProviderAvailabilityRead | None:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("provider availability limit must be between 1 and 100")
        provider = await self._repository.get(provider_id)
        if provider is None:
            return None
        observed_at = _utc_now(now)
        if not provider.enabled:
            return ProviderAvailabilityRead(
                provider_id=provider.id,
                status="unavailable",
                observed_at=observed_at,
                source_contract=None,
                error_code="provider_disabled",
            )
        try:
            adapter = self._registry.lookup_availability(provider.adapter_id.value)
        except ProviderNotRegisteredError:
            return ProviderAvailabilityRead(
                provider_id=provider.id,
                status="unavailable",
                observed_at=observed_at,
                source_contract=None,
                error_code="adapter_unregistered",
            )
        except UnsupportedProviderCapabilityError:
            return ProviderAvailabilityRead(
                provider_id=provider.id,
                status="unavailable",
                observed_at=observed_at,
                source_contract=None,
                error_code="capability_unsupported",
            )
        if ProviderCapability.AVAILABILITY not in adapter.capabilities:
            return ProviderAvailabilityRead(
                provider_id=provider.id,
                status="unavailable",
                observed_at=observed_at,
                source_contract=None,
                error_code="capability_unsupported",
            )
        secret = self._environ.get(provider.credential_ref, "").strip()
        secret_fields = _secret_fields(adapter.credential_schema)
        if not secret and any(required for _, required in secret_fields):
            return ProviderAvailabilityRead(
                provider_id=provider.id,
                status="unavailable",
                observed_at=observed_at,
                source_contract=None,
                error_code="credential_unavailable",
            )
        try:
            snapshot = await adapter.availability(
                AvailabilityRequest(
                    context=ProviderOperationContext(pool=self._pool, now=observed_at),
                    provider_record=provider,
                    credentials=adapter.credential_schema.model_validate(
                        {name: secret for name, _ in secret_fields[:1]} if secret else {}
                    ),
                    limit=limit,
                )
            )
        except Exception as exc:  # reason: expose safe state without provider exception text
            return ProviderAvailabilityRead(
                provider_id=provider.id,
                status="error",
                observed_at=observed_at,
                source_contract=None,
                error_code=_provider_error_code(exc),
            )
        items = tuple(_availability_entry(item) for item in snapshot.items)
        return ProviderAvailabilityRead(
            provider_id=provider.id,
            status="available" if items else "empty",
            observed_at=snapshot.observed_at,
            source_contract=snapshot.source_contract,
            items=items,
        )

    async def health(
        self,
        provider_id: str,
        *,
        probe: bool = False,
        now: dt.datetime | None = None,
    ) -> ProviderHealthRead | None:
        provider = await self._repository.get(provider_id)
        if provider is None:
            return None
        observed_at = _utc_now(now)
        if not probe:
            return ProviderHealthRead(
                provider_id=provider.id,
                persisted_health=provider.health_status,
                live_status="not_probed",
                observed_at=observed_at,
                availability_count=None,
                error_code=None,
            )
        availability = await self.availability(provider_id, limit=1, now=observed_at)
        assert availability is not None
        live_status = {
            "available": "healthy",
            "empty": "healthy",
            "unavailable": "unavailable",
            "error": "unhealthy",
        }[availability.status]
        return ProviderHealthRead(
            provider_id=provider.id,
            persisted_health=provider.health_status,
            live_status=live_status,
            observed_at=availability.observed_at,
            availability_count=(
                len(availability.items) if availability.status in {"available", "empty"} else None
            ),
            error_code=availability.error_code,
        )

    def _descriptor(self, provider: Provider) -> ProviderDescriptor:
        try:
            adapter = self._registry.lookup(provider.adapter_id.value)
        except ProviderNotRegisteredError:
            capabilities: tuple[str, ...] = ()
        else:
            capabilities = tuple(sorted(capability.value for capability in adapter.capabilities))
        return ProviderDescriptor(
            provider_id=provider.id,
            name=provider.name,
            adapter_id=provider.adapter_id.value,
            credential_ref=provider.credential_ref,
            credential_configured=bool(self._environ.get(provider.credential_ref, "").strip()),
            provider_type=provider.provider_type.value,
            enabled=provider.enabled,
            persisted_health=provider.health_status,
            capabilities=capabilities,
            pricing_kind=_pricing_kind(provider.config),
            config=redact_provider_serialized_config(provider.config),
        )


def _availability_entry(item: AvailabilityItem) -> ProviderAvailabilityEntry:
    return ProviderAvailabilityEntry(
        resource_id=item.resource_id,
        kind=item.kind.value,
        available=item.available,
        region=item.region,
        accelerator=item.accelerator,
        accelerator_count=item.accelerator_count,
        pricing=dict(item.pricing),
        attributes=dict(item.attributes),
    )


def _pricing_kind(config: Mapping[str, Any]) -> str:
    cost = config.get("cost")
    if not isinstance(cost, Mapping):
        return "unavailable"
    for key in ("kind", "mode"):
        value = cost.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unavailable"


def _secret_fields(schema: type[BaseModel]) -> list[tuple[str, bool]]:
    """The ``(name, required)`` of each secret field an adapter's credential schema declares."""

    fields: list[tuple[str, bool]] = []
    for name, info in schema.model_fields.items():
        annotation = info.annotation
        if annotation is SecretStr or SecretStr in get_args(annotation):
            fields.append((name, info.is_required()))
    return fields


def _provider_error_code(exc: Exception) -> str:
    if isinstance(exc, CredentialResolutionError):
        return "credential_unavailable"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "provider_timeout"
    return "provider_error"


def _utc_now(value: dt.datetime | None) -> dt.datetime:
    result = value or dt.datetime.now(dt.UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("provider service now must be timezone-aware")
    return result.astimezone(dt.UTC)


__all__ = [
    "ProviderAvailabilityEntry",
    "ProviderAvailabilityRead",
    "ProviderDescriptor",
    "ProviderHealthRead",
    "ProviderOperationsService",
    "ProviderRecordReader",
]
