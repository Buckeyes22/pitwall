"""Model Studio catalog rules for provider settings, pricing, and proxy rewriting."""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.providers.model_studio import catalog as ms

NOW = dt.datetime(2026, 11, 20, 9, 0, tzinfo=dt.UTC)


def settings(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "plan": "token-plan-personal",
        "tier": "pro",
        "model": "qwen3.8-flash",
    }
    base.update(changes)
    return {"model_studio": base}


def test_token_plan_settings_normalize_and_derive_the_base_url() -> None:
    normalized = ms.provider_settings(settings())
    assert normalized["region"] == "ap-southeast-1"
    assert (
        ms.base_url(normalized)
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert ms.base_url(normalized, "native").endswith("/api/v1")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"region": "us-east-1"}, "region_not_allowed"),
        ({"model": "kimi-k2.7-code"}, "model_not_eligible"),
        ({"model": "wan2.7-image"}, "model_not_text"),
        ({"tier": None}, "invalid_config"),
        ({"plan": "pay-as-you-go", "tier": None}, "unknown_region"),
        ({"plan": "pay-as-you-go", "tier": None, "region": "eu-central-1"}, "workspace_required"),
        ({"renews_on": "12"}, "invalid_config"),
    ],
)
def test_invalid_settings_are_refused_by_name(changes: dict[str, object], code: str) -> None:
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.provider_settings(settings(**changes))
    assert caught.value.code == code


def test_key_pairing_never_echoes_the_key() -> None:
    ms.check_key("token-plan-personal", "sk-sp-ok")
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.check_key("token-plan-personal", "sk-leakcheck")
    assert caught.value.code == "key_plan_mismatch"
    assert "leakcheck" not in str(caught.value)


def test_automation_from_config_or_environment() -> None:
    plain = ms.provider_settings(settings())
    assert not ms.automation_accepted(plain, {})
    assert ms.automation_accepted(plain, {ms.AUTOMATION_ENV: "accept"})
    assert ms.automation_accepted(ms.provider_settings(settings(automation="accept")), {})
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.require_automation(plain, {})
    assert caught.value.code == "automation_not_accepted"
    assert ms.AUTOMATION_ENV in str(caught.value)
    ms.require_automation(
        ms.provider_settings(settings(plan="pay-as-you-go", tier=None, region="ap-southeast-1")), {}
    )


def test_pricing_config_zero_for_token_plan_and_tiered_for_pay_as_you_go() -> None:
    assert ms.pricing_config(ms.provider_settings(settings())) == {"kind": "zero"}
    payg = ms.pricing_config(
        ms.provider_settings(
            settings(plan="pay-as-you-go", tier=None, region="ap-southeast-1", model="qwen3.7-plus")
        )
    )
    assert payg == {
        "kind": "per_token",
        "per_million_input_tokens": "0.4",
        "per_million_output_tokens": "1.6",
        "per_million_cached_input_tokens": "0.08",
        "input_tiers": [
            {
                "above_input_tokens": 256000,
                "per_million_input_tokens": "1.2",
                "per_million_output_tokens": "4.8",
                "per_million_cached_input_tokens": "0.24",
            }
        ],
        "default_max_output_tokens": 131072,
    }


def test_unpriced_region_or_model_needs_operator_cost() -> None:
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.pricing_config(
            ms.provider_settings(settings(plan="pay-as-you-go", tier=None, region="us-east-1"))
        )
    assert caught.value.code == "unpriced"


def test_classification_and_renewal_match_agent_routing() -> None:
    assert ms.classify_error(
        429,
        '{"error": {"code": "insufficient_quota", "message": "Your token-plan quota has been exhausted."}}',
    ) == ("credits_exhausted", None)
    assert ms.classify_error(429, '{"code": "Throttling.RateQuota"}') == ("rate_limit", 60)
    start, reset = ms.credits_window("2026-09-12", NOW)
    assert (start, reset) == (
        dt.datetime(2026, 11, 11, tzinfo=dt.UTC),
        dt.datetime(2026, 12, 11, tzinfo=dt.UTC),
    )
    assert ms.next_renewal(ms.provider_settings(settings(renews_on="2026-09-12")), NOW) == reset
    assert ms.next_renewal(ms.provider_settings(settings()), NOW) is None
