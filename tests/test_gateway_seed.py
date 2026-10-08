from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall import seed
from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.seed import SeedValidationError, apply_seed_data


def _fake_repos(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    cap_repo = MagicMock()
    cap_repo.get_by_name = AsyncMock(return_value=None)
    cap_repo.get = AsyncMock(return_value=None)
    cap_repo.create = AsyncMock(side_effect=lambda cap: cap)
    prov_repo = MagicMock()
    prov_repo.get_by_name = AsyncMock(return_value=None)
    prov_repo.create = AsyncMock(side_effect=lambda prov: prov)
    monkeypatch.setattr(seed, "CapabilityRepository", lambda _pool: cap_repo)
    monkeypatch.setattr(seed, "ProviderRepository", lambda _pool: prov_repo)
    return cap_repo, prov_repo


def _payload(
    provider_type: str = "openai_gateway",
    adapter: str | None = "openai_gateway",
    credential_ref: str | None = None,
) -> dict:
    provider = {
        "name": "gw-keyless-demo",
        "capability": "coding.chat",
        "endpoint_id": "keyless-demo",
        "provider_type": provider_type,
        "priority": 50,
        "cost": {"mode": "zero"},
        "gateway": {
            "base_url": "http://127.0.0.1:20130/v1",
            "model_id": "demo/free",
            "catalog": {"free_type": "keyless", "tos": "ok", "hard_stop_guaranteed": True},
        },
    }
    if adapter is not None:
        provider["adapter"] = adapter
    if credential_ref is not None:
        provider["credential_ref"] = credential_ref
    return {
        "capabilities": [
            {
                "name": "coding.chat",
                "class": "llm",
                "cost_mode": "zero",
                "served_model_id": "demo/free",
            }
        ],
        "providers": [provider],
    }


@pytest.mark.anyio
async def test_gateway_seed_row_needs_no_gpu_class_and_sets_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(_payload(), pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    [capability] = result.capabilities
    assert capability.served_model_id == "demo/free"
    assert provider.provider_type == ProviderType.OPENAI_GATEWAY
    assert provider.adapter_id == ProviderAdapterId.GATEWAY
    assert provider.credential_ref == "PITWALL_GATEWAY_API_KEY"
    assert provider.config["gateway"]["model_id"] == "demo/free"
    assert provider.config["openai_base_url"] == "http://127.0.0.1:20130/v1"
    assert provider.config["supports_streaming"] is False
    assert "gpu_class" not in provider.config


@pytest.mark.anyio
async def test_gateway_seed_row_preserves_explicit_streaming_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    payload = _payload()
    payload["providers"][0]["supports_streaming"] = True
    result = await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.config["supports_streaming"] is True


@pytest.mark.anyio
async def test_gateway_seed_row_rejects_non_boolean_streaming_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    payload = _payload()
    payload["providers"][0]["supports_streaming"] = "false"
    with pytest.raises(SeedValidationError, match="supports_streaming.*boolean"):
        await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.YAML)


@pytest.mark.anyio
async def test_gateway_seed_row_preserves_explicit_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(
        _payload(credential_ref="PITWALL_GATEWAY_TOKEN"),
        pool=MagicMock(),
        source=CapabilitySource.YAML,
    )
    [provider] = result.providers
    assert provider.credential_ref == "PITWALL_GATEWAY_TOKEN"


@pytest.mark.anyio
async def test_gateway_seed_row_rejects_invalid_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(SeedValidationError, match="credential_ref"):
        await apply_seed_data(
            _payload(credential_ref="not-an-environment-reference"),
            pool=MagicMock(),
            source=CapabilitySource.YAML,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "credential_ref",
    [True, False, 0, 1, 1.5, ["PITWALL_GATEWAY_TOKEN"], {"ref": "PITWALL_GATEWAY_TOKEN"}],
    ids=["true", "false", "zero", "one", "float", "list", "dict"],
)
@pytest.mark.parametrize("key", ["credential_ref", "credentialRef"], ids=["snake", "camel"])
async def test_gateway_seed_row_rejects_non_string_credential_reference_before_writes(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    credential_ref: object,
) -> None:
    cap_repo, prov_repo = _fake_repos(monkeypatch)
    payload = _payload()
    payload["providers"][0][key] = credential_ref
    with pytest.raises(SeedValidationError, match="credential_ref"):
        await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.YAML)
    assert cap_repo.create.await_count == 0
    assert prov_repo.create.await_count == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("key", "credential_ref"),
    [("credential_ref", "PITWALL_GATEWAY_TOKEN"), ("credentialRef", "GATEWAY_TOKEN")],
    ids=["snake_case", "camel_case"],
)
async def test_gateway_seed_row_accepts_string_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    credential_ref: str,
) -> None:
    _fake_repos(monkeypatch)
    payload = _payload()
    payload["providers"][0][key] = credential_ref
    result = await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.credential_ref == credential_ref


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key",
    [None, "credential_ref", "credentialRef"],
    ids=["omitted", "null", "camel_null"],
)
async def test_gateway_seed_row_accepts_omitted_or_null_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
    key: str | None,
) -> None:
    _fake_repos(monkeypatch)
    payload = _payload()
    if key is not None:
        payload["providers"][0][key] = None
    result = await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.credential_ref == "PITWALL_GATEWAY_API_KEY"


@pytest.mark.anyio
async def test_gateway_row_without_matching_adapter_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(SeedValidationError, match="adapter: openai_gateway"):
        await apply_seed_data(
            _payload(adapter=None), pool=MagicMock(), source=CapabilitySource.YAML
        )


@pytest.mark.anyio
async def test_non_gateway_seed_row_still_requires_gpu_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(SeedValidationError, match="gpu_class"):
        await apply_seed_data(
            _payload("public_endpoint", adapter=None),
            pool=MagicMock(),
            source=CapabilitySource.YAML,
        )
