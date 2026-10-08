"""Hosted profile tests, translated from packages/pi-workbench/tests/hosted-profiles.test.ts.

The cases in that file that exercise ``doctor.ts`` (``doctor``, ``runtimeDoctor``,
``readOpenCodeMetadata``) belong to Task 4.6, which ports the doctor module.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from pitwall.workbench.hosted_profiles import (
    AccountPolicy,
    ProviderMetadata,
    RetryPolicy,
    credential_entry_status,
    credential_value,
    discover_hosted_profiles,
    hosted_profile_doctor,
    to_workbench_profile,
)


@pytest.mark.parity
def test_hosted_inventory_is_explicit_no_fallback_and_quota_errors_fail_without_retry() -> None:
    """Source: hosted-profiles.test.ts 'hosted inventory is explicit, no fallback, and quota errors fail without retry'."""
    profiles = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="alibaba-token-plan",
                base_url="https://token-plan.example/v1",
                models=["qwen3.8-flash"],
                auth_store_entry="alibaba-token-plan",
                api="anthropic-messages",
            )
        ]
    )
    assert len(profiles) == 1
    for profile in profiles:
        assert profile.allow_provider_fallback is False
        assert profile.retry == RetryPolicy(max_attempts=1, quota_errors="fail", backoff_ms=())
        assert profile.endpoint_class == "unknown"


@pytest.mark.parity
def test_hosted_aliases_sharing_one_auth_entry_share_an_account_group_identity() -> None:
    """Source: hosted-profiles.test.ts 'hosted aliases sharing one auth entry share an account-group identity'."""
    profiles = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="zai-coding-plan", models=["model-a"], auth_store_entry="shared-hosted-account"
            ),
            ProviderMetadata(
                name="minimax-coding-plan",
                models=["model-b"],
                auth_store_entry="shared-hosted-account",
            ),
        ]
    )
    assert [profile.account_group for profile in profiles] == ["shared-hosted-account"] * 2
    assert [profile.provider for profile in profiles] == ["zai", "minimax"]


@pytest.mark.parity
def test_profile_doctor_distinguishes_missing_auth_and_undiscovered_model() -> None:
    """Source: hosted-profiles.test.ts 'profile doctor distinguishes missing auth and undiscovered model'."""
    profile = discover_hosted_profiles([ProviderMetadata(name="zai-coding-plan", models=[])])[0]
    assert hosted_profile_doctor(profile, {}).status == "discovery-required"


@pytest.mark.parity
def test_profile_doctor_does_not_claim_readiness_without_the_selected_credential_or_verified_endpoint() -> (
    None
):
    """Source: hosted-profiles.test.ts 'profile doctor does not claim readiness without the selected credential or verified endpoint'."""
    discovered = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="zai-coding-plan",
                base_url="https://api.z.ai/api/coding/paas/v4",
                models=["glm-5.2"],
                auth_store_entry="MISSING_KEY",
                endpoint_class="official-api",
            )
        ]
    )[0]
    missing_credential = replace(
        discovered, model_id="glm-5.2", credential_store="environment", account_ref="MISSING_KEY"
    )
    assert hosted_profile_doctor(missing_credential, {}).status == "credential-unavailable"
    unverified = replace(
        missing_credential,
        credential_store="opencode-auth.json",
        endpoint_class="unknown",
        account_ref="configured",
    )
    assert hosted_profile_doctor(unverified, {}).status == "endpoint-unverified"


def test_profile_doctor_reports_ready_only_with_credential_and_verified_endpoint() -> None:
    discovered = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="zai-coding-plan",
                base_url="https://api.z.ai/api/coding/paas/v4",
                auth_store_entry="HOSTED_FIXTURE_KEY",
                endpoint_class="official-api",
            )
        ]
    )[0]
    profile = replace(
        discovered,
        model_id="glm-5.2",
        credential_store="environment",
        account_ref="HOSTED_FIXTURE_KEY",
    )
    secret = "value-that-must-not-appear"  # pragma: allowlist secret
    report = hosted_profile_doctor(profile, {"HOSTED_FIXTURE_KEY": secret})
    assert report.status == "ready-for-authorized-smoke"
    assert secret not in repr(report)


@pytest.mark.parity
def test_conversion_refuses_unverified_endpoints_and_requires_explicit_model() -> None:
    """Source: hosted-profiles.test.ts 'conversion refuses unverified endpoints and requires explicit model'."""
    profile = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="alibaba-token-plan",
                models=["qwen3.8-flash"],
                auth_store_entry="alibaba-token-plan",
            )
        ]
    )[0]
    with pytest.raises(ValueError, match="unverified"):
        to_workbench_profile(profile, "qwen3.8-flash", 1000, 128)
    verified = replace(profile, endpoint="https://example.test/v1", endpoint_class="official-api")
    with pytest.raises(ValueError, match="explicit"):
        to_workbench_profile(verified, "REQUIRES_EXPLICIT_MODEL", 1000, 128)


@pytest.mark.parity
def test_conversion_emits_a_runnable_profile_only_for_verified_metadata() -> None:
    """Source: hosted-profiles.test.ts 'conversion emits a runnable profile only for verified metadata'."""
    profile = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="alibaba-token-plan",
                base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1",
                models=["qwen3.8-flash"],
                auth_store_entry="alibaba-token-plan",
                api="anthropic-messages",
            )
        ]
    )[0]
    runnable = to_workbench_profile(
        replace(profile, endpoint_class="official-api"), "qwen3.8-flash", 983616, 131072
    )
    assert runnable.profile["api"] == "anthropic-messages"
    assert runnable.profile["apiKeyEnv"] == "PITWALL_PI_HOSTED_KEY"  # pragma: allowlist secret
    assert runnable.account_ref == "alibaba-token-plan"


@pytest.mark.parity
def test_hosted_conversion_carries_an_explicit_account_budget_policy_without_inventing_allowance_values() -> (
    None
):
    """Source: hosted-profiles.test.ts 'hosted conversion carries an explicit account budget policy without inventing allowance values'."""
    discovered = discover_hosted_profiles(
        [
            ProviderMetadata(
                name="zai-coding-plan",
                base_url="https://api.z.ai/api/coding/paas/v4",
                models=["model"],
                auth_store_entry="shared-hosted-account",
            )
        ]
    )[0]
    profile = replace(discovered, endpoint_class="official-api")
    runnable = to_workbench_profile(profile, "model", 1000, 128, AccountPolicy(1, 256, "hold"))
    assert runnable.account_group == "shared-hosted-account"
    assert runnable.profile["accountGroup"] == "shared-hosted-account"
    assert runnable.profile["accountMaxConcurrent"] == 1
    assert runnable.profile["accountInFlightTokenBudget"] == 256
    assert runnable.profile["accountUnknownUsage"] == "hold"
    without_policy = to_workbench_profile(profile, "model", 1000, 128)
    assert "accountMaxConcurrent" not in without_policy.profile


@pytest.mark.parity
def test_credential_status_validates_auth_schema_without_returning_secrets() -> None:
    """Source: hosted-profiles.test.ts 'credential status validates auth schema without returning secrets'."""
    secret = "secret"  # pragma: allowlist secret
    assert (
        credential_entry_status(
            {"alibaba-token-plan": {"type": "api", "key": secret}}, "alibaba-token-plan"
        )
        == "available"
    )
    assert (
        credential_entry_status({"alibaba-token-plan": {"type": "api"}}, "alibaba-token-plan")
        == "invalid"
    )
    assert credential_entry_status({}, "alibaba-token-plan") == "missing"
    assert credential_entry_status([], "alibaba-token-plan") == "invalid"


def test_credential_value_only_returns_an_available_key() -> None:
    fixture = "fixture-value"  # pragma: allowlist secret
    assert credential_value({"a": {"key": fixture}}, "a") == fixture
    with pytest.raises(ValueError, match="unavailable"):
        credential_value({"a": {"type": "api"}}, "a")
