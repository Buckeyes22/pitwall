"""Hermetic contract tests for the strict RunPod billing history client."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from pitwall.runpod_client.billing import (
    RunPodBillingClient,
    RunPodBillingError,
    RunPodBillingRecord,
)

pytestmark = pytest.mark.anyio


async def test_pod_history_uses_exact_filter_header_auth_and_decimal_decode() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "billing.test"
        # Keep the official fixture's numeric JSON tokens numeric at the HTTP
        # boundary; dumping Decimal through stdlib json would turn them into strings.
        return httpx.Response(
            200,
            content=(
                b'[{"amount":0.123456789012345678,"podId":"pod-exact-1",'
                b'"time":"2026-08-30T00:00:00Z"},'
                b'{"amount":0.200000000000000001,"podId":"pod-exact-1",'
                b'"time":"2026-08-31T00:00:00Z"}]'
            ),
        )

    client = RunPodBillingClient(
        api_key="rpa_test_secret_canary",  # pragma: allowlist secret
        rest_v1_api_url="https://billing.test/v1",
        transport=httpx.MockTransport(handler),
    )
    records = await client.pod_history(
        start_time=dt.datetime(2026, 8, 30, tzinfo=dt.UTC),
        end_time=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        pod_id="pod-exact-1",
    )
    await client.aclose()

    assert requests[0].url.path == "/v1/billing/pods"
    assert dict(requests[0].url.params) == {
        "bucketSize": "day",
        "startTime": "2026-08-30T00:00:00Z",
        "endTime": "2026-09-01T00:00:00Z",
        "grouping": "podId",
        "podId": "pod-exact-1",
    }
    assert requests[0].headers["authorization"] == "Bearer rpa_test_secret_canary"
    assert "rpa_test_secret_canary" not in str(requests[0].url)
    assert [item.amount for item in records] == [
        Decimal("0.123456789012345678"),
        Decimal("0.200000000000000001"),
    ]


async def test_endpoint_and_volume_history_keep_official_categories_distinct() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    client = RunPodBillingClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_v1_api_url="https://billing.test/v1",
        transport=httpx.MockTransport(handler),
    )
    window = {
        "start_time": dt.datetime(2026, 8, 30, tzinfo=dt.UTC),
        "end_time": dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
    }
    assert await client.endpoint_history(endpoint_id="endpoint-1", **window) == ()
    assert await client.network_volume_history(**window) == ()
    await client.aclose()

    assert requests[0].url.path == "/v1/billing/endpoints"
    assert requests[0].url.params["grouping"] == "endpointId"
    assert requests[0].url.params["endpointId"] == "endpoint-1"
    assert requests[1].url.path == "/v1/billing/networkvolumes"
    assert "grouping" not in requests[1].url.params


async def test_provider_error_is_bounded_and_redacts_response_body() -> None:
    token = "rpa_secret_should_never_escape"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"Authorization: Bearer {token}")

    client = RunPodBillingClient(
        api_key=token,  # pragma: allowlist secret
        rest_v1_api_url="https://billing.test/v1",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RunPodBillingError) as exc_info:
        await client.pod_history(
            start_time=dt.datetime(2026, 8, 30, tzinfo=dt.UTC),
            end_time=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            pod_id="pod-1",
        )
    await client.aclose()

    assert str(exc_info.value) == "GET /billing/pods failed with HTTP 403"
    assert exc_info.value.reason_code == "authentication_failed"
    assert exc_info.value.status_code == 403
    assert token not in str(exc_info.value)


def test_billing_model_rejects_binary_float_boundary() -> None:
    with pytest.raises(ValidationError, match="binary floats"):
        RunPodBillingRecord.model_validate({"amount": 0.1, "time": "2026-08-30T00:00:00Z"})
