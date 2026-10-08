"""ProviderOperationsService availability: keyless pools, credential shape, error codes."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.providers.gateway import GatewayProvider
from pitwall.providers.interface import (
    AvailabilityRequest,
    AvailabilityResult,
    CredentialResolutionError,
    ProviderCapability,
    ProviderDeclaration,
)
from pitwall.providers.registry import ProviderRegistry
from pitwall.providers.service import ProviderOperationsService, _provider_error_code

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)


class _Repo:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    async def get(self, provider_id_or_name: str) -> Provider | None:
        return self.provider

    async def list(self, *_: Any, **__: Any) -> list[Provider]:
        return [self.provider]


def _gateway_provider() -> Provider:
    return Provider(
        id="prov_gw",
        capability_id="cap_chat",
        name="gw",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"kind": "zero"},
            "gateway": {"base_url": "http://127.0.0.1:20130/v1", "model_id": "beta/b1"},
        },
        priority=1,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.API,
        updated_at=NOW,
    )


async def test_keyless_provider_available() -> None:
    registry = ProviderRegistry()
    registry.register(GatewayProvider())
    service = ProviderOperationsService(
        object(), repository=_Repo(_gateway_provider()), registry=registry, environ={}
    )

    snapshot = await service.availability("prov_gw", now=NOW)

    assert snapshot is not None
    assert snapshot.error_code is None
    assert snapshot.status == "available"


class _RecordingAdapter:
    """Declares a required ``token`` secret and a non-secret ``region``."""

    id = "vast"
    name = "recording"
    capabilities = frozenset({ProviderCapability.AVAILABILITY})

    def __init__(self) -> None:
        from pydantic import BaseModel, SecretStr

        class Credentials(BaseModel):
            token: SecretStr
            region: str = "eu"

        self.credential_schema = Credentials
        self.declaration = ProviderDeclaration()
        self.seen: Any = None

    def pricing_model(self, capability: Any, provider_record: Any) -> Any:
        raise NotImplementedError

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        self.seen = request.credentials
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=NOW,
            source_contract="test",
            items=(),
        )


async def test_credentials_follow_the_declared_shape() -> None:
    adapter = _RecordingAdapter()
    registry = ProviderRegistry()
    registry.register(adapter)  # type: ignore[arg-type]  # reason: minimal test adapter
    provider = _gateway_provider().model_copy(
        update={"adapter_id": ProviderAdapterId.VAST, "credential_ref": "VAST_API_KEY"}
    )
    service = ProviderOperationsService(
        object(),
        repository=_Repo(provider),
        registry=registry,
        environ={"VAST_API_KEY": "s3cret"},  # pragma: allowlist secret
    )

    snapshot = await service.availability("prov_gw", now=NOW)

    assert snapshot is not None and snapshot.status == "empty"
    assert adapter.seen.token.get_secret_value() == "s3cret"  # pragma: allowlist secret


async def test_required_secret_still_needs_a_credential() -> None:
    adapter = _RecordingAdapter()
    registry = ProviderRegistry()
    registry.register(adapter)  # type: ignore[arg-type]  # reason: minimal test adapter
    provider = _gateway_provider().model_copy(
        update={"adapter_id": ProviderAdapterId.VAST, "credential_ref": "VAST_API_KEY"}
    )
    service = ProviderOperationsService(
        object(), repository=_Repo(provider), registry=registry, environ={}
    )

    snapshot = await service.availability("prov_gw", now=NOW)

    assert snapshot is not None
    assert snapshot.error_code == "credential_unavailable"
    assert adapter.seen is None


def test_error_code_by_type() -> None:
    class CredentialResolutionError_(Exception):  # noqa: N801  # reason: deliberately name-alike to prove matching is by type
        pass

    CredentialResolutionError_.__name__ = "CredentialResolutionError"

    class SubclassedTimeout(httpx.ReadTimeout):
        pass

    assert (
        _provider_error_code(CredentialResolutionError("vast", "VAST_API_KEY", ("api_key",)))
        == "credential_unavailable"
    )
    assert _provider_error_code(SubclassedTimeout("slow")) == "provider_timeout"
    assert _provider_error_code(httpx.ConnectTimeout("slow")) == "provider_timeout"
    assert _provider_error_code(TimeoutError()) == "provider_timeout"
    assert _provider_error_code(CredentialResolutionError_()) == "provider_error"
    assert _provider_error_code(RuntimeError("boom")) == "provider_error"
