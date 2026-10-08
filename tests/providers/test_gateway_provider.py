"""Gateway adapter: keyless auth, 429 → QuotaExhausted, 5xx → provider error, availability from catalog."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import PerTokenPricing, ZeroOrEnergyPricing
from pitwall.providers.gateway import (
    GatewayProvider,
    GatewayProviderError,
    QuotaExhausted,
    classify_429,
)
from pitwall.providers.interface import (
    AvailabilityKind,
    AvailabilityRequest,
    CredentialReference,
    InferenceRequest,
    ProviderOperationContext,
)

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _capability(cost_mode: str = "zero") -> Capability:
    return Capability(
        id="cap_coding",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode(cost_mode),
        source=CapabilitySource.YAML,
        created_at=NOW,
        updated_at=NOW,
    )


FORK_BASE_URL = "http://127.0.0.1:20130/v1"


def _provider(
    cost: dict | None = None,
    base_url: str = FORK_BASE_URL,
    compression: str | None = None,
    **catalog,
) -> ProviderRecord:
    gateway: dict = {
        "base_url": base_url,
        "model_id": "beta/b1",
        "catalog": {"free_type": "keyless", "tos": "ok", "hard_stop_guaranteed": True, **catalog},
    }
    if compression is not None:
        gateway["compression"] = compression
    return ProviderRecord(
        id="prov_gw",
        capability_id="cap_coding",
        name="gw-beta-b1",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=50,
        updated_at=NOW,
        config={
            "cost": cost or {"mode": "zero"},
            "openai_base_url": base_url,
            "gateway": gateway,
        },
    )


def _request(transport_handler, env: dict[str, str]) -> tuple[GatewayProvider, InferenceRequest]:
    provider = GatewayProvider(transport=httpx.MockTransport(transport_handler), environ=env)
    request = InferenceRequest(
        context=ProviderOperationContext(pool=None, now=NOW),
        capability=_capability(),
        provider_record=_provider(),
        credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    return provider, request


@pytest.mark.anyio
async def test_keyless_infer_sends_no_authorization_header_when_env_is_unset() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        body = json.loads(req.content)
        assert body["model"] == "beta/b1"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "model": "beta/b1",
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    provider, request = _request(handler, env={})
    result = await provider.infer(request)
    assert "authorization" not in seen
    assert result.prompt_tokens == 3 and result.completion_tokens == 1
    assert result.provider_id == "prov_gw"


@pytest.mark.anyio
async def test_loopback_infer_sends_the_fork_token_and_compression_policy() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"choices": [], "model": "beta/b1"})

    provider, request = _request(handler, env={"PITWALL_GATEWAY_TOKEN": "fork-tok"})
    await provider.infer(request)
    assert seen["authorization"] == "Bearer fork-tok"
    assert seen["x-pitwall-compression"] == "off"
    # The fork routes by provider: the seed name selects the upstream.
    assert seen["x-pitwall-route"] == "gw-beta-b1"


@pytest.mark.anyio
async def test_loopback_infer_honors_the_configured_compression_policy() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"choices": [], "model": "beta/b1"})

    provider = GatewayProvider(
        transport=httpx.MockTransport(handler), environ={"PITWALL_GATEWAY_TOKEN": "fork-tok"}
    )
    request = InferenceRequest(
        context=ProviderOperationContext(pool=None, now=NOW),
        capability=_capability(),
        provider_record=_provider(compression="rtk"),
        credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    await provider.infer(request)
    assert seen["x-pitwall-compression"] == "rtk"


@pytest.mark.anyio
async def test_loopback_infer_sends_the_fork_token_not_the_pool_key() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"choices": [], "model": "beta/b1"})

    provider, request = _request(
        handler, env={"PITWALL_GATEWAY_API_KEY": "sk-pool", "PITWALL_GATEWAY_TOKEN": "fork-tok"}
    )
    await provider.infer(request)
    assert seen["authorization"] == "Bearer fork-tok"


@pytest.mark.anyio
async def test_direct_keyed_infer_sends_the_pool_bearer_without_compression_header() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"choices": [], "model": "beta/b1"})

    provider = GatewayProvider(
        transport=httpx.MockTransport(handler),
        environ={"PITWALL_GATEWAY_API_KEY": "sk-pool", "PITWALL_GATEWAY_TOKEN": "fork-tok"},
    )
    request = InferenceRequest(
        context=ProviderOperationContext(pool=None, now=NOW),
        capability=_capability(),
        provider_record=_provider(base_url="https://beta.example/v1"),
        credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    await provider.infer(request)
    assert seen["authorization"] == "Bearer sk-pool"
    assert "x-pitwall-compression" not in seen
    assert "x-pitwall-route" not in seen


@pytest.mark.anyio
async def test_direct_keyless_infer_carries_neither_token_nor_compression_header() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"choices": [], "model": "beta/b1"})

    provider = GatewayProvider(
        transport=httpx.MockTransport(handler), environ={"PITWALL_GATEWAY_TOKEN": "fork-tok"}
    )
    request = InferenceRequest(
        context=ProviderOperationContext(pool=None, now=NOW),
        capability=_capability(),
        provider_record=_provider(base_url="https://beta.example/v1"),
        credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    await provider.infer(request)
    assert "authorization" not in seen
    assert "x-pitwall-compression" not in seen


@pytest.mark.anyio
async def test_429_with_reset_header_maps_to_quota_exhausted_with_reset_at() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-ratelimit-reset": "2026-09-10T13:00:00Z"},
            json={"error": {"message": "daily quota exceeded"}},
        )

    provider, request = _request(handler, env={})
    with pytest.raises(QuotaExhausted) as excinfo:
        await provider.infer(request)
    assert excinfo.value.reason == "quota_exhausted"
    assert excinfo.value.reset_at == dt.datetime(2026, 9, 10, 13, 0, tzinfo=dt.UTC)


@pytest.mark.anyio
async def test_5xx_raises_plain_provider_error_for_the_cooldown_path() -> None:
    provider, request = _request(lambda req: httpx.Response(503, text="overloaded"), env={})
    with pytest.raises(GatewayProviderError) as excinfo:
        await provider.infer(request)
    assert not isinstance(excinfo.value, QuotaExhausted)
    assert excinfo.value.status_code == 503


def test_pricing_model_follows_the_provider_row_not_the_adapter() -> None:
    provider = GatewayProvider()
    assert isinstance(provider.pricing_model(_capability("zero"), _provider()), ZeroOrEnergyPricing)
    metered = _provider(
        cost={
            "mode": "per_token",
            "per_million_input_tokens": "0.27",
            "per_million_output_tokens": "1.10",
        }
    )
    assert isinstance(provider.pricing_model(_capability("per_token"), metered), PerTokenPricing)


@pytest.mark.anyio
async def test_availability_is_read_from_catalog_evidence_without_egress() -> None:
    def handler(req: httpx.Request) -> httpx.Response:  # any egress is a test failure
        raise AssertionError("availability must not call upstream")

    provider = GatewayProvider(transport=httpx.MockTransport(handler), environ={})
    result = await provider.availability(
        AvailabilityRequest(
            context=ProviderOperationContext(pool=None, now=NOW),
            provider_record=_provider(trains_on_prompts=True),
            credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        )
    )
    [item] = result.items
    assert item.kind == AvailabilityKind.MODEL and item.resource_id == "beta/b1"
    assert item.attributes["trains_on_prompts"] is True
    assert result.source_contract == "gateway-catalog-2026-09-10"


def test_classify_429_reads_retry_after_seconds_and_permanent_ban() -> None:
    reason, reset = classify_429(429, "please retry in 30s", {}, now=NOW)
    assert (reason, reset) == ("rate_limit_exceeded", NOW + dt.timedelta(seconds=30))
    reason, reset = classify_429(429, "account has been deactivated", {}, now=NOW)
    assert (reason, reset) == ("permanent_ban", None)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("30", NOW + dt.timedelta(seconds=30)),  # delta seconds
        ("1757516400", dt.datetime.fromtimestamp(1757516400, tz=dt.UTC)),  # epoch seconds
        ("1757516400000", dt.datetime.fromtimestamp(1757516400, tz=dt.UTC)),  # epoch milliseconds
        ("2026-09-10T13:00:00", dt.datetime(2026, 9, 10, 13, 0, tzinfo=dt.UTC)),  # naive ISO -> UTC
        ("2026-09-10T13:00:00Z", dt.datetime(2026, 9, 10, 13, 0, tzinfo=dt.UTC)),
        ("not-a-time", None),  # unparseable -> default backoff
    ],
)
def test_classify_429_parses_every_reset_header_shape_safely(
    header: str, expected: dt.datetime | None
) -> None:
    reason, reset = classify_429(429, "", {"x-ratelimit-reset": header}, now=NOW)
    assert reset == expected
    if reset is not None:
        assert reset.tzinfo is not None
        assert reset <= NOW + dt.timedelta(days=366)
    assert reason in {"quota_exhausted", "rate_limit_exceeded"}


def test_classify_429_caps_a_delta_reset_at_twenty_four_hours() -> None:
    _, reset = classify_429(429, "", {"retry-after": "999999"}, now=NOW)
    assert reset == NOW + dt.timedelta(hours=24)


def test_classify_429_bare_quota_is_a_short_rate_limit_not_a_monthly_lockout() -> None:
    reason, reset = classify_429(
        429, "Allocated quota exceeded, please increase your quota limit.", {}, now=NOW
    )
    assert (reason, reset) == ("rate_limit_exceeded", None)


@pytest.mark.parametrize(
    "body",
    [
        "monthly limit reached",
        "you have used your quota for this month",
        "5,000,000 tokens per month exceeded",
    ],
)
def test_classify_429_explicit_monthly_wording_still_locks_until_next_month(body: str) -> None:
    reason, reset = classify_429(429, body, {}, now=NOW)
    assert (reason, reset) == ("quota_exhausted", dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
