"""Hermetic shared-model fixtures for MC-01 surface adapters."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pitwall.providers.service import (
    ProviderAvailabilityEntry,
    ProviderAvailabilityRead,
    ProviderDescriptor,
    ProviderHealthRead,
    ProviderOperationsService,
)

NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


def descriptor() -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id="prov-vast",
        name="vast-gpu",
        adapter_id="vast",
        credential_ref="VAST_API_KEY",
        credential_configured=True,
        provider_type="pod_lease",
        enabled=True,
        persisted_health="healthy",
        capabilities=("availability", "compute"),
        pricing_kind="per_second",
        config={"cost": {"kind": "per_second", "price_per_hour": "1.250000"}},
    )


def availability() -> ProviderAvailabilityRead:
    return ProviderAvailabilityRead(
        provider_id="prov-vast",
        status="available",
        observed_at=NOW,
        source_contract="vast-api-v0-bundles-2026-09-01",
        items=(
            ProviderAvailabilityEntry(
                resource_id="offer-7",
                kind="compute",
                available=True,
                region="US-CA",
                accelerator="A100",
                accelerator_count=1,
                pricing={"usd_per_hour": Decimal("1.250000")},
                attributes={},
            ),
        ),
    )


def health(*, probe: bool = True) -> ProviderHealthRead:
    return ProviderHealthRead(
        provider_id="prov-vast",
        persisted_health="healthy",
        live_status="healthy" if probe else "not_probed",
        observed_at=NOW,
        availability_count=1 if probe else None,
        error_code=None,
    )


class StubProviderOperationsService(ProviderOperationsService):
    """Recording subtype accepted by adapter dependencies without provider egress."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def list_descriptors(
        self,
        *,
        capability_id: str | None = None,
        enabled_only: bool = False,
        limit: int = 100,
    ) -> tuple[ProviderDescriptor, ...]:
        self.calls.append(("list", capability_id, enabled_only, limit))
        return (descriptor(),)

    async def describe(self, provider_id: str) -> ProviderDescriptor | None:
        self.calls.append(("describe", provider_id))
        return None if provider_id == "missing" else descriptor()

    async def availability(
        self,
        provider_id: str,
        *,
        limit: int = 100,
        now: dt.datetime | None = None,
    ) -> ProviderAvailabilityRead | None:
        self.calls.append(("availability", provider_id, limit, now))
        return None if provider_id == "missing" else availability()

    async def health(
        self,
        provider_id: str,
        *,
        probe: bool = False,
        now: dt.datetime | None = None,
    ) -> ProviderHealthRead | None:
        self.calls.append(("health", provider_id, probe, now))
        return None if provider_id == "missing" else health(probe=probe)


__all__ = [
    "NOW",
    "StubProviderOperationsService",
    "availability",
    "descriptor",
    "health",
]
