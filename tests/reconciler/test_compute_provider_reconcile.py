from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.cost.estimator import PerVmSecondPricing
from pitwall.providers import ProviderCapability, ReconcileRequest
from pitwall.providers.interface import ProviderDeclaration
from pitwall.providers.registry import ProviderRegistry
from pitwall.reconciler import _compute_provider_reconcile

NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


class Credentials(BaseModel):
    api_key: SecretStr


@dataclass
class ComputeAdapterFake:
    id: str
    fail: bool = False
    requests: list[ReconcileRequest] = field(default_factory=list)
    name: str = "fake compute"
    credential_schema: type[BaseModel] = Credentials
    declaration: ProviderDeclaration = ProviderDeclaration()
    capabilities: frozenset[ProviderCapability] = frozenset({ProviderCapability.COMPUTE})

    def pricing_model(self, capability: Any, provider_record: Any) -> PerVmSecondPricing:
        return PerVmSecondPricing(rate_per_second=Decimal("0.01"))

    async def provision(self, request: Any) -> Any:
        raise AssertionError("not used")

    async def status(self, request: Any) -> Any:
        raise AssertionError("not used")

    async def reconcile(self, request: ReconcileRequest) -> Any:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("safe fake failure")
        return None

    async def teardown(self, request: Any) -> Any:
        raise AssertionError("not used")


@dataclass
class RepositoryFake:
    providers: list[Provider]

    async def list(self, **kwargs: Any) -> list[Provider]:
        assert kwargs == {"enabled_only": True, "limit": 100, "offset": 0}
        return self.providers


def _provider(adapter_id: ProviderAdapterId, credential_ref: str) -> Provider:
    return Provider(
        id=f"provider-{adapter_id.value}",
        capability_id="cap-gpu",
        name=f"provider-{adapter_id.value}",
        adapter_id=adapter_id,
        credential_ref=credential_ref,
        provider_type=ProviderType.POD_LEASE,
        config={},
        priority=1,
        source=CapabilitySource.API,
        updated_at=NOW,
    )


@pytest.mark.anyio
async def test_compute_reconcile_uses_exact_adapters_and_skips_missing_credentials() -> None:
    vast = ComputeAdapterFake("vast", fail=True)
    lambda_cloud = ComputeAdapterFake("lambda_cloud")
    registry = ProviderRegistry()
    registry.register(vast)
    registry.register(lambda_cloud)
    repository = RepositoryFake(
        [
            _provider(ProviderAdapterId.RUNPOD, "RUNPOD_API_KEY"),
            _provider(ProviderAdapterId.VAST, "VAST_API_KEY"),
            _provider(ProviderAdapterId.LAMBDA_CLOUD, "LAMBDA_CLOUD_API_KEY"),
        ]
    )

    await _compute_provider_reconcile(
        {
            "db_pool": object(),
            "provider_repository": repository,
            "provider_registry": registry,
            "environ": {
                "RUNPOD_API_KEY": "runpod-secret",
                "VAST_API_KEY": "vast-secret",
                "LAMBDA_CLOUD_API_KEY": "lambda-secret",
            },
            "now": NOW,
        }
    )

    assert len(vast.requests) == 1
    assert len(lambda_cloud.requests) == 1
    assert vast.requests[0].provider_record.adapter_id is ProviderAdapterId.VAST
    assert lambda_cloud.requests[0].provider_record.adapter_id is ProviderAdapterId.LAMBDA_CLOUD
    assert "vast-secret" not in repr(vast.requests[0])
    assert "lambda-secret" not in repr(lambda_cloud.requests[0])

    lambda_cloud.requests.clear()
    await _compute_provider_reconcile(
        {
            "db_pool": object(),
            "provider_repository": repository,
            "provider_registry": registry,
            "environ": {"VAST_API_KEY": "vast-secret"},
            "now": NOW,
        }
    )
    assert lambda_cloud.requests == []
