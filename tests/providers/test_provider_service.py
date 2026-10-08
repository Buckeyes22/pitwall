from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.providers.registry import ProviderRegistry
from pitwall.providers.service import ProviderOperationsService
from pitwall.providers.vast import VastProvider

NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


@dataclass
class FakeRepository:
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
        values = [
            provider
            for provider in self.providers
            if (capability_id is None or provider.capability_id == capability_id)
            and (not enabled_only or provider.enabled)
            and (provider_type is None or provider.provider_type.value == provider_type)
        ]
        return values[offset : offset + limit]


def _provider(*, enabled: bool = True) -> Provider:
    return Provider(
        id="prov-vast",
        capability_id="cap-gpu",
        name="vast-gpu",
        adapter_id=ProviderAdapterId.VAST,
        credential_ref="VAST_API_KEY",
        provider_type=ProviderType.POD_LEASE,
        config={"cost": {"kind": "per_second", "price_per_hour": "0.4"}},
        priority=1,
        enabled=enabled,
        health_status="healthy",
        source=CapabilitySource.API,
        updated_at=NOW,
    )


def _service(
    provider: Provider,
    handler: Any,
    *,
    environ: dict[str, str],
) -> ProviderOperationsService:
    registry = ProviderRegistry()
    registry.register(VastProvider(transport=httpx.MockTransport(handler)))
    return ProviderOperationsService(
        object(),
        repository=FakeRepository([provider]),
        registry=registry,
        environ=environ,
    )


@pytest.mark.anyio
async def test_descriptor_exposes_capabilities_and_reference_without_secret() -> None:
    service = _service(
        _provider(),
        lambda _: httpx.Response(500),
        environ={"VAST_API_KEY": "secret-value"},
    )

    descriptors = await service.list_descriptors(enabled_only=False)

    assert len(descriptors) == 1
    payload = descriptors[0].as_dict()
    assert payload["credential_ref"] == "VAST_API_KEY"
    assert payload["credential_configured"] is True
    assert payload["capabilities"] == ["availability", "compute"]
    assert payload["pricing_kind"] == "per_second"
    assert "secret-value" not in repr(payload)


@pytest.mark.anyio
async def test_missing_credential_returns_unavailable_with_zero_egress() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    snapshot = await _service(_provider(), handler, environ={}).availability(
        "prov-vast",
        now=NOW,
    )

    assert snapshot is not None
    assert snapshot.status == "unavailable"
    assert snapshot.error_code == "credential_unavailable"
    assert calls == []


@pytest.mark.anyio
async def test_availability_serializes_decimal_rates_identically_for_surfaces() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "offers": [
                    {
                        "id": 7,
                        "gpu_name": "A100",
                        "num_gpus": 2,
                        "rentable": True,
                        "rented": False,
                        "dph_total": "1.250000",
                    }
                ]
            },
        )

    snapshot = await _service(
        _provider(),
        handler,
        environ={"VAST_API_KEY": "secret-value"},
    ).availability("prov-vast", now=NOW)

    assert snapshot is not None
    assert snapshot.as_dict() == {
        "provider_id": "prov-vast",
        "status": "available",
        "observed_at": "2026-09-01T12:00:00+00:00",
        "source_contract": "vast-api-v0-bundles-2026-09-01",
        "items": [
            {
                "resource_id": "7",
                "kind": "compute",
                "available": True,
                "region": None,
                "accelerator": "A100",
                "accelerator_count": 2,
                "pricing": {"usd_per_hour": "1.250000"},
                "attributes": {},
            }
        ],
        "error_code": None,
    }


@pytest.mark.anyio
async def test_provider_failure_returns_safe_error_without_response_body() -> None:
    leaked = "provider-echoed-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=leaked)

    snapshot = await _service(
        _provider(),
        handler,
        environ={"VAST_API_KEY": "secret-value"},
    ).availability("prov-vast", now=NOW)

    assert snapshot is not None
    assert snapshot.status == "error"
    assert snapshot.error_code == "provider_error"
    assert leaked not in repr(snapshot)


@pytest.mark.anyio
async def test_provider_timeout_has_a_stable_safe_error_code() -> None:
    leaked = "provider-timeout-credential-canary"

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(leaked, request=request)

    snapshot = await _service(
        _provider(),
        handler,
        environ={"VAST_API_KEY": "secret-value"},
    ).availability("prov-vast", now=NOW)

    assert snapshot is not None
    assert snapshot.status == "error"
    assert snapshot.error_code == "provider_timeout"
    assert leaked not in repr(snapshot)


@pytest.mark.anyio
async def test_provider_cancellation_is_not_converted_into_a_safe_error_snapshot() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await _service(
            _provider(),
            handler,
            environ={"VAST_API_KEY": "secret-value"},
        ).availability("prov-vast", now=NOW)


@pytest.mark.anyio
async def test_health_without_probe_is_persisted_only_and_zero_egress() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    health = await _service(
        _provider(),
        handler,
        environ={"VAST_API_KEY": "secret-value"},
    ).health("prov-vast", probe=False, now=NOW)

    assert health is not None
    assert health.persisted_health == "healthy"
    assert health.live_status == "not_probed"
    assert calls == []


@pytest.mark.anyio
async def test_availability_limit_is_bounded_in_the_shared_service_before_provider_egress() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"offers": []})

    with pytest.raises(ValueError, match="between 1 and 100"):
        await _service(
            _provider(),
            handler,
            environ={"VAST_API_KEY": "secret-value"},
        ).availability("prov-vast", limit=101, now=NOW)

    assert calls == []
