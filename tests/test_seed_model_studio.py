"""Seeding a Model Studio provider derives its URL, cost, and settings from the catalog."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall import seed
from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.seed import SeedValidationError, apply_seed_data


def _fake_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    cap_repo = MagicMock()
    cap_repo.get_by_name = AsyncMock(return_value=None)
    cap_repo.get = AsyncMock(return_value=None)
    cap_repo.create = AsyncMock(side_effect=lambda cap: cap)
    prov_repo = MagicMock()
    prov_repo.get_by_name = AsyncMock(return_value=None)
    prov_repo.create = AsyncMock(side_effect=lambda prov: prov)
    monkeypatch.setattr(seed, "CapabilityRepository", lambda _pool: cap_repo)
    monkeypatch.setattr(seed, "ProviderRepository", lambda _pool: prov_repo)


def _payload(**provider_changes: object) -> dict:
    provider: dict[str, object] = {
        "name": "ms-qwen-flash",
        "capability": "coding.chat",
        "provider_type": "model_studio",
        "adapter": "model_studio",
        "credential_ref": "MODEL_STUDIO_API_KEY",
        "priority": 50,
        "model_studio": {
            "plan": "token-plan-personal",
            "tier": "pro",
            "model": "qwen3.8-flash",
            "renews_on": "2026-09-12",
        },
    }
    provider.update(provider_changes)
    return {
        "capabilities": [
            {
                "name": "coding.chat",
                "class": "llm",
                "cost_mode": "zero",
                "served_model_id": "qwen3.8-flash",
            }
        ],
        "providers": [provider],
    }


@pytest.mark.anyio
async def test_model_studio_seed_derives_url_cost_and_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(_payload(), pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.provider_type == ProviderType.MODEL_STUDIO
    assert provider.adapter_id == ProviderAdapterId.MODEL_STUDIO
    assert (
        provider.config["openai_base_url"]
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert provider.config["cost"] == {"kind": "zero"}
    assert provider.config["model_studio"]["region"] == "ap-southeast-1"
    assert provider.config["supports_streaming"] is True
    assert "gpu_class" not in provider.config


@pytest.mark.anyio
async def test_model_studio_seed_refuses_bad_settings_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    bad = {"plan": "token-plan-personal", "tier": "pro", "model": "kimi-k2.7-code"}
    with pytest.raises(SeedValidationError, match="model_not_eligible"):
        await apply_seed_data(
            _payload(model_studio=bad), pool=MagicMock(), source=CapabilitySource.YAML
        )


@pytest.mark.anyio
async def test_model_studio_requires_the_model_studio_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(
        SeedValidationError, match="model_studio providers require adapter: model_studio"
    ):
        await apply_seed_data(
            _payload(adapter="together"), pool=MagicMock(), source=CapabilitySource.YAML
        )
