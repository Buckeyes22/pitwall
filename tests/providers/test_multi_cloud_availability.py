from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.providers import (
    AvailabilityKind,
    AvailabilityRequest,
    CredentialInput,
    ProviderCapability,
    ProviderOperationContext,
    create_default_registry,
)
from pitwall.providers.lambda_cloud import LambdaCloudCredentials, LambdaCloudProvider
from pitwall.providers.together import TogetherCredentials, TogetherProvider, TogetherProviderError
from pitwall.providers.vast import VastCredentials, VastProvider, VastProviderError

NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


def _record(adapter_id: ProviderAdapterId) -> Provider:
    return Provider(
        id=f"prov_{adapter_id.value}",
        capability_id="cap_test",
        name=f"test-{adapter_id.value}",
        adapter_id=adapter_id,
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config={},
        priority=1,
        source=CapabilitySource.API,
        updated_at=NOW,
    )


def _request(
    adapter_id: ProviderAdapterId,
    credentials: CredentialInput,
    *,
    limit: int = 100,
) -> AvailabilityRequest:
    return AvailabilityRequest(
        context=ProviderOperationContext(pool=object(), now=NOW),
        provider_record=_record(adapter_id),
        credentials=credentials,
        limit=limit,
    )


@pytest.mark.anyio
async def test_vast_offer_availability_is_bounded_normalized_and_header_authenticated() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert str(request.url) == "https://console.vast.ai/api/v0/bundles/"
        assert request.headers["Authorization"] == "Bearer vast-key"
        assert body == {
            "limit": 2,
            "type": "on-demand",
            "verified": {"eq": True},
            "rentable": {"eq": True},
            "rented": {"eq": False},
        }
        return httpx.Response(
            200,
            json={
                "offers": [
                    {
                        "id": 9,
                        "gpu_name": "RTX 4090",
                        "num_gpus": 1,
                        "rentable": True,
                        "rented": False,
                        "geolocation": "US-CA",
                        "dph_total_adj": "0.51",
                        "min_bid": "0.31",
                        "public_ipaddr": "192.0.2.1",
                    },
                    {"id": 2, "rentable": False, "rented": False},
                ]
            },
        )

    result = await VastProvider(transport=httpx.MockTransport(handler)).availability(
        _request(ProviderAdapterId.VAST, VastCredentials(api_key=SecretStr("vast-key")), limit=2)
    )

    assert result.observed_at == NOW
    assert result.source_contract == "vast-api-v0-bundles-2026-09-01"
    assert [item.resource_id for item in result.items] == ["2", "9"]
    item = result.items[1]
    assert item.kind is AvailabilityKind.COMPUTE
    assert item.available is True
    assert item.pricing == {
        "usd_per_hour": Decimal("0.51"),
        "usd_min_bid_per_hour": Decimal("0.31"),
    }
    assert "public_ipaddr" not in item.attributes
    assert len(requests) == 1


@pytest.mark.anyio
async def test_lambda_instance_type_availability_preserves_capacity_regions_and_price() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://cloud.lambda.ai/api/v1/instance-types"
        assert request.headers["Authorization"] == "Bearer lambda-key"
        return httpx.Response(
            200,
            json={
                "data": {
                    "gpu_1x_a10": {
                        "instance_type": {
                            "name": "gpu_1x_a10",
                            "description": "1x A10",
                            "gpu_description": "A10 (24 GB)",
                            "price_cents_per_hour": 110,
                            "specs": {"vcpus": 30, "memory_gib": 200, "gpus": 1},
                            "architecture": "x86_64",
                        },
                        "regions_with_capacity_available": [
                            {"name": "us-west-1", "description": "California"}
                        ],
                    },
                    "gpu_8x_h100": {
                        "instance_type": {
                            "name": "gpu_8x_h100",
                            "gpu_description": "H100",
                            "price_cents_per_hour": 2499,
                            "specs": {"gpus": 8},
                        },
                        "regions_with_capacity_available": [],
                    },
                }
            },
        )

    result = await LambdaCloudProvider(
        transport=httpx.MockTransport(handler),
        request_interval_s=0,
    ).availability(
        _request(
            ProviderAdapterId.LAMBDA_CLOUD,
            LambdaCloudCredentials(api_key=SecretStr("lambda-key")),
        )
    )

    assert [item.resource_id for item in result.items] == [
        "gpu_1x_a10@us-west-1",
        "gpu_8x_h100",
    ]
    assert result.items[0].pricing["usd_per_hour"] == Decimal("1.1")
    assert result.items[0].available is True
    assert result.items[1].available is False
    assert result.source_contract == "lambda-cloud-openapi-1.10.0"


@pytest.mark.anyio
async def test_together_model_availability_exposes_token_units_without_float_rounding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.together.ai/v1/models"
        assert request.headers["Authorization"] == "Bearer together-key"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "z/model",
                    "type": "chat",
                    "context_length": 32768,
                    "pricing": {"input": "0.3", "output": "0.6", "cached_input": "0.2"},
                },
                {"id": "a/model", "type": "embedding", "pricing": {"input": "0.01"}},
            ],
        )

    result = await TogetherProvider(transport=httpx.MockTransport(handler)).availability(
        _request(
            ProviderAdapterId.TOGETHER,
            TogetherCredentials(api_key=SecretStr("together-key")),
            limit=1,
        )
    )

    assert [item.resource_id for item in result.items] == ["a/model"]
    assert result.items[0].kind is AvailabilityKind.MODEL
    assert result.items[0].pricing == {"usd_per_million_input_tokens": Decimal("0.01")}
    assert result.source_contract == "together-v1-models-2026-09-01"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "availability_request", "error"),
    [
        (
            VastProvider(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))),
            _request(
                ProviderAdapterId.VAST,
                VastCredentials(api_key=SecretStr("vast-key")),
            ),
            VastProviderError,
        ),
        (
            TogetherProvider(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))
            ),
            _request(
                ProviderAdapterId.TOGETHER,
                TogetherCredentials(api_key=SecretStr("together-key")),
            ),
            TogetherProviderError,
        ),
    ],
)
async def test_availability_malformed_envelopes_fail_closed(
    provider: Any,
    availability_request: AvailabilityRequest,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        await provider.availability(availability_request)


def test_registry_exposes_only_declared_availability_adapters() -> None:
    registry = create_default_registry()

    assert registry.ids_for_capability(ProviderCapability.AVAILABILITY) == (
        "vast",
        "together",
        "lambda_cloud",
        "openai_gateway",
        "model_studio",
    )
    assert registry.lookup_availability("vast").id == "vast"
    assert registry.lookup_availability("lambda_cloud").id == "lambda_cloud"
    assert registry.lookup_availability("together").id == "together"
    assert registry.lookup_availability("openai_gateway").id == "openai_gateway"


def test_availability_request_rejects_unbounded_limit() -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        _request(
            ProviderAdapterId.VAST,
            VastCredentials(api_key=SecretStr("vast-key")),
            limit=101,
        )
