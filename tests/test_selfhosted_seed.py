"""A self-hosted endpoint registers through a seed with its own base URL."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml  # type: ignore[import-untyped]  # reason: PyYAML ships no type stubs

from pitwall import seed
from pitwall.core.enums import CapabilitySource, ProviderType
from pitwall.seed import apply_seed_data


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


def _payload(base_url: str = "http://127.0.0.1:9292/v1", *, self_hosted: bool = True) -> dict:
    """The provider shape from docs/operator/self-hosted-endpoint.md, with concrete values."""

    config: dict = {"openai_base_url": base_url, "api_key_env": "LOCAL_LLM_KEY"}
    if self_hosted:
        config["self_hosted"] = {
            "readiness": {"kind": "llama-swap"},
            "cold_start_timeout_s": 600,
            "warmup": {"prompt": "ping", "max_tokens": 1},
            "models": [
                {
                    "id": "qwen3.8-27b",
                    "slot_group": "gpu0",
                    "exclusive": False,
                    "vram_gb": 22,
                    "context_length": 32768,
                    "tool_calling": "enabled",
                    "tool_call_parser": "qwen3_coder",
                }
            ],
            "idle_unload_s": 1800,
            "strict_slots": False,
            "cost": {"mode": "zero", "watts": 300, "usd_per_kwh": "0.20"},
        }
    return {
        "capabilities": [
            {
                "name": "llm.local",
                "class": "llm",
                "cost_mode": "zero",
                "served_model_id": "qwen3.8-27b",
            }
        ],
        "providers": [
            {
                "name": "self-hosted-local",
                "capability": "llm.local",
                "provider_type": "public_endpoint",
                "gpu_class": "NVIDIA GeForce RTX 4090",
                "health_status": "unknown",
                "cost": {"mode": "zero"},
                "config": config,
            }
        ],
    }


@pytest.mark.anyio
async def test_self_hosted_seed_registers_its_own_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(_payload(), pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.provider_type == ProviderType.PUBLIC_ENDPOINT
    assert provider.runpod_endpoint_id is None
    assert provider.config["openai_base_url"] == "http://127.0.0.1:9292/v1"
    assert provider.config["self_hosted"]["readiness"]["kind"] == "llama-swap"


@pytest.mark.anyio
async def test_self_hosted_seed_accepts_a_lan_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(
        _payload("http://192.168.1.50:8080/v1"), pool=MagicMock(), source=CapabilitySource.YAML
    )
    assert result.providers[0].config["openai_base_url"] == "http://192.168.1.50:8080/v1"


@pytest.mark.anyio
async def test_self_hosted_base_url_still_rejects_query_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(ValueError, match="query strings"):
        await apply_seed_data(
            _payload("http://127.0.0.1:9292/v1?x=1"), pool=MagicMock(), source=CapabilitySource.YAML
        )


@pytest.mark.anyio
async def test_public_endpoint_without_self_hosted_profile_still_needs_runpod_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(ValueError, match="requires runpod_endpoint_id"):
        await apply_seed_data(
            _payload(self_hosted=False), pool=MagicMock(), source=CapabilitySource.YAML
        )


@pytest.mark.anyio
async def test_the_operator_guide_seed_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The YAML in docs/operator/self-hosted-endpoint.md seeds once its placeholders are filled."""

    guide = Path(__file__).resolve().parents[1] / "docs/operator/self-hosted-endpoint.md"
    block = guide.read_text(encoding="utf-8").split("```yaml\n", 1)[1].split("```", 1)[0]
    for placeholder, value in {
        "<model-id>": "qwen3.8-27b",
        "<api-key-env-name>": "LOCAL_LLM_KEY",
        "<family>": "qwen3_coder",
    }.items():
        block = block.replace(placeholder, value)
    _fake_repos(monkeypatch)
    result = await apply_seed_data(
        yaml.safe_load(block), pool=MagicMock(), source=CapabilitySource.YAML
    )
    [provider] = result.providers
    assert provider.config["openai_base_url"] == "http://localhost:9292/v1"
