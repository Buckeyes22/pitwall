"""Hosted-provider profile discovery and conversion (port of ``hosted-profiles.ts``)."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

HostedProvider = Literal["minimax", "glm", "zai", "alibaba-tokenplan"]
EndpointClass = Literal["official-api", "compatible-gateway", "unknown"]
ApiKind = Literal["openai-completions", "openai-responses", "anthropic-messages"]
CredentialStore = Literal["opencode-auth.json", "pi-auth.json", "environment"]

HOSTED_METADATA_NAMES = frozenset(
    {"minimax", "minimax-coding-plan", "glm", "zai", "zai-coding-plan", "alibaba-token-plan"}
)
CREDENTIAL_ENV = "PITWALL_PI_HOSTED_KEY"


@dataclass(frozen=True)
class RetryPolicy:
    """Hosted quota errors fail immediately; there is never a retry."""

    max_attempts: int = 1
    quota_errors: Literal["fail"] = "fail"
    backoff_ms: tuple[int, ...] = ()


@dataclass(frozen=True)
class HostedProfile:
    provider: str
    account: str
    #: Stable auth identity shared by provider aliases.
    account_group: str
    model_id: str
    endpoint: str
    endpoint_class: EndpointClass
    api: ApiKind
    account_ref: str
    credential_store: CredentialStore
    resource_group: str
    allow_provider_fallback: Literal[False] = False
    retry: RetryPolicy = field(default_factory=RetryPolicy)


@dataclass(frozen=True)
class ProviderMetadata:
    name: str
    models: Sequence[str] = ()
    base_url: str | None = None
    auth_store_entry: str | None = None
    api: ApiKind | None = None
    endpoint_class: EndpointClass | None = None


@dataclass(frozen=True)
class HostedRunnableProfile:
    profile: dict[str, Any]
    account_ref: str
    account_group: str
    credential_store: CredentialStore


@dataclass(frozen=True)
class AccountPolicy:
    max_concurrent: int
    in_flight_token_budget: int
    unknown_usage: Literal["hold", "release"]


@dataclass(frozen=True)
class HostedProfileReport:
    provider: str
    account: str
    endpoint_class: EndpointClass
    model_configured: bool
    credential_configured: bool
    retry: RetryPolicy
    status: str


def _provider_for(name: str) -> str:
    if "minimax" in name:
        return "minimax"
    if "alibaba" in name:
        return "alibaba-tokenplan"
    if "glm" in name or "zai" in name:
        return "zai"
    return name


def discover_hosted_profiles(metadata: Sequence[ProviderMetadata]) -> list[HostedProfile]:
    """Turn provider metadata into profiles that still need an explicit model and endpoint check."""
    return [
        HostedProfile(
            provider=_provider_for(entry.name),
            account="configured-auth-entry" if entry.auth_store_entry else "metadata-only",
            account_group=entry.auth_store_entry or entry.name,
            account_ref=entry.auth_store_entry or "unconfigured",
            credential_store="opencode-auth.json" if entry.auth_store_entry else "environment",
            model_id="REQUIRES_EXPLICIT_MODEL",
            endpoint=entry.base_url or "REQUIRES_LOCAL_AUTH_DISCOVERY",
            endpoint_class=entry.endpoint_class or "unknown",
            api=entry.api or "openai-completions",
            resource_group=f"hosted-{entry.name}",
        )
        for entry in metadata
        if entry.name in HOSTED_METADATA_NAMES
    ]


def hosted_profile_doctor(
    profile: HostedProfile, env: Mapping[str, str] | None = None
) -> HostedProfileReport:
    env = os.environ if env is None else env
    model_configured = not profile.model_id.startswith("REQUIRES_")
    credential_configured = (
        bool(env.get(profile.account_ref))
        if profile.credential_store == "environment"
        else profile.account_ref != "unconfigured"
    )
    endpoint_verified = profile.endpoint_class != "unknown" and not profile.endpoint.startswith(
        "REQUIRES_"
    )
    if not model_configured:
        status = "discovery-required"
    elif not credential_configured:
        status = "credential-unavailable"
    elif not endpoint_verified:
        status = "endpoint-unverified"
    else:
        status = "ready-for-authorized-smoke"
    return HostedProfileReport(
        provider=profile.provider,
        account=profile.account,
        endpoint_class=profile.endpoint_class,
        model_configured=model_configured,
        credential_configured=credential_configured,
        retry=profile.retry,
        status=status,
    )


def to_workbench_profile(
    profile: HostedProfile,
    model_id: str,
    context_tokens: int,
    max_completion_tokens: int,
    account_policy: AccountPolicy | None = None,
) -> HostedRunnableProfile:
    if profile.endpoint.startswith("REQUIRES_") or profile.endpoint_class == "unknown":
        raise ValueError("hosted endpoint is unverified")
    if not model_id or model_id.startswith("REQUIRES_"):
        raise ValueError("hosted model must be explicit")
    workbench: dict[str, Any] = {
        "provider": profile.provider,
        "modelId": model_id,
        "endpoint": profile.endpoint,
        "api": profile.api,
        "servedContextTokens": context_tokens,
        "maxCompletionTokens": max_completion_tokens,
        "reasoningLevel": "off",
        "resourceGroup": profile.resource_group,
        "allowProviderFallback": False,
        "apiKeyEnv": CREDENTIAL_ENV,
        "accountRef": profile.account_ref,
        "accountGroup": profile.account_group,
    }
    if account_policy is not None:
        workbench["accountMaxConcurrent"] = account_policy.max_concurrent
        workbench["accountInFlightTokenBudget"] = account_policy.in_flight_token_budget
        workbench["accountUnknownUsage"] = account_policy.unknown_usage
    return HostedRunnableProfile(
        profile=workbench,
        account_ref=profile.account_ref,
        account_group=profile.account_group,
        credential_store=profile.credential_store,
    )


def credential_entry_status(
    auth: object, account_ref: str
) -> Literal["available", "missing", "invalid"]:
    """Validate the auth-file schema without returning the secret."""
    if not isinstance(auth, dict):
        return "invalid"
    entry = auth.get(account_ref)
    if not isinstance(entry, dict):
        return "missing"
    key = entry.get("key")
    return "available" if isinstance(key, str) and key else "invalid"


def credential_value(auth: object, account_ref: str) -> str:
    if credential_entry_status(auth, account_ref) != "available" or not isinstance(auth, dict):
        raise ValueError("hosted credential entry unavailable")
    return str(auth[account_ref]["key"])
