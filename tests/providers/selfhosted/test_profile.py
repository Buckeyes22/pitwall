from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pitwall.api.leases.launch import InvalidProviderConfig
from pitwall.core.enums import ProviderType
from pitwall.core.models import Provider
from pitwall.providers.selfhosted.profile import self_hosted_profile

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _provider(config: dict[str, object]) -> Provider:
    return Provider(
        id="provider-selfhosted",
        capability_id="capability-chat",
        name="self-hosted fixture",
        provider_type="public_endpoint",
        runpod_endpoint_id="endpoint-fixture",
        config=config,
        priority=1,
        updated_at=_NOW,
    )


def test_absent_profile_returns_none() -> None:
    assert self_hosted_profile(_provider({})) is None


@pytest.mark.parametrize(
    "provider_type",
    [
        ProviderType.SERVERLESS_QUEUE,
        ProviderType.SERVERLESS_LB,
        ProviderType.POD_LEASE,
    ],
)
def test_profile_is_inert_for_every_non_public_endpoint_type(
    provider_type: ProviderType,
) -> None:
    candidate = _provider({"self_hosted": {"readiness": {"kind": "llama-swap"}}})
    candidate = candidate.model_copy(update={"provider_type": provider_type})

    assert self_hosted_profile(candidate) is None


def test_complete_profile_parses_decimal_and_tuple_fields() -> None:
    profile = self_hosted_profile(
        _provider(
            {
                "self_hosted": {
                    "readiness": {"kind": "llama-swap"},
                    "cold_start_timeout_s": 600,
                    "warmup": {"prompt": "ping", "max_tokens": 1},
                    "models": [
                        {
                            "id": "<model-id>",
                            "slot_group": "slot-a",
                            "exclusive": False,
                            "vram_gb": "22.5",
                            "context_length": 32768,
                            "tool_calling": "enabled",
                            "tool_call_parser": "<parser-family>",
                        }
                    ],
                    "idle_unload_s": 1800,
                    "strict_slots": True,
                    "cost": {
                        "mode": "zero",
                        "watts": "300",
                        "usd_per_kwh": "0.20",
                    },
                }
            }
        )
    )

    assert profile is not None
    assert profile.cold_start_timeout_s == 600
    assert profile.models[0].id == "<model-id>"
    assert profile.models[0].vram_gb == Decimal("22.5")
    assert profile.cost is not None
    assert profile.cost.usd_per_kwh == Decimal("0.20")


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        ({"cold_start_timeout_s": 330}, "readiness"),
        (
            {"readiness": {"kind": "http-health"}},
            "http-health readiness requires path",
        ),
        (
            {"readiness": {"kind": "llama-swap", "path": "/health"}},
            "path is only valid",
        ),
        (
            {
                "readiness": {"kind": "llama-swap"},
                "models": [{"id": "<model-id>"}, {"id": "<model-id>"}],
            },
            "model ids must be unique",
        ),
        (
            {"readiness": {"kind": "llama-swap"}, "unexpected": True},
            "unexpected",
        ),
        (
            {"readiness": {"kind": "llama-swap"}, "cold_start_timeout_s": 0},
            "greater than or equal to 1",
        ),
    ],
)
def test_invalid_profile_is_wrapped_as_provider_config_error(
    profile: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(InvalidProviderConfig, match=message):
        self_hosted_profile(_provider({"self_hosted": profile}))
