"""Broker ACS3 signing matches Agent Routing's reference vector; stats and billing parse."""

from __future__ import annotations

import datetime as dt
import io
import json
from decimal import Decimal

import httpx
import pytest

from pitwall.providers.model_studio import openapi

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
KEYS = {openapi.ACCESS_KEY_ID_ENV: "AKID", openapi.ACCESS_KEY_SECRET_ENV: "secret"}


def test_reference_vectors() -> None:
    headers = {
        "x-acs-action": "GetSubscriptionStats",
        "x-acs-version": "2026-02-10",
        "x-acs-date": "2026-09-26T12:00:00Z",
        "x-acs-signature-nonce": "3f1d6c2e-0000-4000-8000-000000000001",
    }
    assert openapi.acs3_authorization(
        "GET",
        "modelstudio.ap-southeast-1.aliyuncs.com",
        "/tokenplan/subscription/stats",
        {},
        headers,
        b"",
        access_key_id="TESTAKID",
        access_key_secret="test-secret",  # pragma: allowlist secret
    ).endswith("Signature=3e90126c0a15bd300a99078246f80342877fa314c10ff4275277d6d5ba98a0ce")
    billing = {
        **headers,
        "x-acs-action": "GetBillingOverview",
        "x-acs-signature-nonce": "3f1d6c2e-0000-4000-8000-000000000002",
    }
    assert openapi.acs3_authorization(
        "GET",
        "modelstudio.ap-southeast-1.aliyuncs.com",
        "/modelstudio/billing/overview",
        {"billMonth": "2026-09", "groupBy": '[{"code":"BASE_MODEL"}]'},
        billing,
        b"",
        access_key_id="TESTAKID",
        access_key_secret="test-secret",  # pragma: allowlist secret
    ).endswith("Signature=5edb3f16d4b3d0448e40c23cc0dda653782f6640f581500129e4eb037bfd4245")


@pytest.mark.anyio
async def test_no_access_key_means_no_request() -> None:
    assert await openapi.get_subscription_stats({}, now=NOW) is None
    assert await openapi.get_billing_month_to_date({}, model="qwen3.8-flash", now=NOW) is None


@pytest.mark.anyio
async def test_stats_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenplan/subscription/stats"
        assert request.headers["authorization"].startswith("ACS3-HMAC-SHA256 Credential=AKID,")
        return httpx.Response(
            200,
            json={
                "Data": {
                    "Items": [
                        {
                            "SeatRefreshTime": 1759622400000,
                            "SeatCredits": 180000,
                            "SeatRemainingCredits": 1000,
                        }
                    ]
                }
            },
        )

    stats = await openapi.get_subscription_stats(
        KEYS, now=NOW, transport=httpx.MockTransport(handler)
    )
    assert stats is not None
    assert (stats.total_credits, stats.remaining_credits) == (Decimal(180000), Decimal(1000))
    assert stats.reset_at == dt.datetime.fromtimestamp(1759622400, tz=dt.UTC)


@pytest.mark.anyio
async def test_billing_month_to_date_for_the_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/modelstudio/billing/overview"
        assert request.url.params["billMonth"] == "2026-09"
        assert json.loads(request.url.params["groupBy"]) == [{"code": "BASE_MODEL"}]
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "currency": "USD",
                    "groups": [
                        {"key": "qwen3.8-flash", "amount": "12.34"},
                        {"key": "qwen3.8-max", "amount": "99"},
                    ],
                },
            },
        )

    spend = await openapi.get_billing_month_to_date(
        KEYS, model="qwen3.8-flash", now=NOW, transport=httpx.MockTransport(handler)
    )
    assert spend == Decimal("12.34")


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def test_sync_stats_read_signs_and_parses_through_the_urllib_transport() -> None:
    captured: dict[str, object] = {}
    body = {
        "Success": True,
        "Data": {
            "Items": [
                {
                    "SeatType": "pro",
                    "SeatRefreshTime": 1759622400000,
                    "SeatCredits": 180000,
                    "SeatRemainingCredits": 123456.5,
                }
            ]
        },
    }

    def opener(request: object, timeout: float) -> _Response:
        captured["request"] = request
        return _Response(json.dumps(body).encode())

    stats = openapi.get_subscription_stats_sync(KEYS, now=NOW, nonce="n-1", opener=opener)
    assert stats is not None
    assert stats.total_credits == Decimal(180000)
    assert stats.remaining_credits == Decimal("123456.5")
    assert stats.reset_at == dt.datetime.fromtimestamp(1759622400, tz=dt.UTC)
    request = captured["request"]
    authorization = request.get_header("Authorization")  # type: ignore[attr-defined]  # reason: the test reads an attribute on a loosely typed test double
    assert authorization.startswith("ACS3-HMAC-SHA256 Credential=AKID,")
    assert "secret" not in authorization


def test_sync_stats_read_without_an_access_key_makes_no_request() -> None:
    def opener(request: object, timeout: float) -> _Response:
        raise AssertionError("no request without an AccessKey pair")

    assert openapi.get_subscription_stats_sync({}, now=NOW, opener=opener) is None


def test_stats_payload_without_items_is_unmeasured() -> None:
    assert openapi.parse_subscription_stats({"Data": {"Items": []}}) is None
    assert openapi.parse_subscription_stats([]) is None
