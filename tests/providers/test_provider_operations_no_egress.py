"""No-egress safety tests for current-provider operator reads."""

from __future__ import annotations

import datetime as dt
import socket
from dataclasses import dataclass

import pytest

from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.providers.registry import create_default_registry
from pitwall.providers.service import ProviderOperationsService

NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


@dataclass
class _Repository:
    providers: list[Provider]

    async def get(self, provider_id_or_name: str) -> Provider | None:
        return next(
            (
                provider
                for provider in self.providers
                if provider.id == provider_id_or_name or provider.name == provider_id_or_name
            ),
            None,
        )

    async def list(
        self,
        capability_id: str | None = None,
        enabled_only: bool = True,
        provider_type: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Provider]:
        del capability_id, enabled_only, provider_type
        return self.providers[offset : offset + limit]


def _provider(adapter_id: ProviderAdapterId, credential_ref: str) -> Provider:
    return Provider(
        id=f"prov-{adapter_id.value}",
        capability_id="cap-gpu",
        name=f"{adapter_id.value}-provider",
        adapter_id=adapter_id,
        credential_ref=credential_ref,
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config={},
        priority=1,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.API,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_missing_current_provider_credentials_never_resolve_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = [
        _provider(ProviderAdapterId.VAST, "VAST_API_KEY"),
        _provider(ProviderAdapterId.TOGETHER, "TOGETHER_API_KEY"),
        _provider(ProviderAdapterId.LAMBDA_CLOUD, "LAMBDA_CLOUD_API_KEY"),
    ]
    service = ProviderOperationsService(
        object(),
        repository=_Repository(providers),
        registry=create_default_registry(),
        environ={},
    )

    def _no_dns(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("provider operator read attempted DNS without a credential")

    monkeypatch.setattr(socket, "getaddrinfo", _no_dns)

    for provider in providers:
        availability = await service.availability(provider.id, now=NOW)
        health = await service.health(provider.id, probe=True, now=NOW)

        assert availability is not None
        assert availability.status == "unavailable"
        assert availability.error_code == "credential_unavailable"
        assert health is not None
        assert health.live_status == "unavailable"
        assert health.error_code == "credential_unavailable"
