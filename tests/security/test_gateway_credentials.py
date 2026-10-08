"""Gateway credentials: keyless path requires no env var; SSRF guard rejects hostile HTTP base URLs."""

from __future__ import annotations

import datetime as dt

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
from pitwall.providers.gateway import GatewayCredentials, GatewayProvider
from pitwall.providers.interface import CredentialReference

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_coding",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        source=CapabilitySource.YAML,
        created_at=NOW,
        updated_at=NOW,
    )


def _provider() -> ProviderRecord:
    return ProviderRecord(
        id="prov_gw",
        capability_id="cap_coding",
        name="gw-beta-b1",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=50,
        updated_at=NOW,
        config={
            "cost": {"mode": "zero"},
            "openai_base_url": "http://127.0.0.1:20130/v1",
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "beta/b1",
                "catalog": {"free_type": "keyless", "tos": "ok", "hard_stop_guaranteed": True},
            },
        },
    )


pytestmark = pytest.mark.security


def test_gateway_keyless_never_reads_env_when_key_absent(monkeypatch) -> None:
    monkeypatch.delenv("PITWALL_GATEWAY_API_KEY", raising=False)

    provider = GatewayProvider(environ={})
    creds = provider._resolve(CredentialReference("PITWALL_GATEWAY_API_KEY"), _provider())
    assert creds.api_key is None


def test_gateway_base_url_rejects_non_loopback_http() -> None:
    with pytest.raises(ValueError):
        GatewayCredentials(base_url="http://10.0.0.5/v1")
