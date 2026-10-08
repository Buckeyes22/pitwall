"""Providers declare their own behaviour; shared layers only read the declaration (task 2.4)."""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from pitwall.core.enums import ProviderType
from pitwall.providers import registry as registry_module
from pitwall.providers.interface import ProviderCapability, ProviderDeclaration
from pitwall.providers.registry import ProviderRegistry, create_default_registry
from pitwall.routing import fallback, lockout

SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"
SHARED_LAYERS = (
    SRC / "routing" / "fallback.py",
    SRC / "routing" / "lockout.py",
    SRC / "routing" / "openai.py",
    SRC / "api" / "provider_schemas.py",
    SRC / "seed.py",
    SRC / "reconciler" / "__init__.py",
)
PROVIDER_TYPE_VALUES = {member.value for member in ProviderType}


def _provider_type_branches(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ProviderType"
        ):
            found.append(f"{path.name}:{node.lineno} ProviderType.{node.attr}")
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                values = (
                    list(operand.elts)
                    if isinstance(operand, ast.Tuple | ast.Set | ast.List)
                    else [operand]
                )
                for value in values:
                    if isinstance(value, ast.Constant) and value.value in PROVIDER_TYPE_VALUES:
                        found.append(f"{path.name}:{node.lineno} == {value.value!r}")
    return found


def test_no_provider_type_branches_in_shared_layers() -> None:
    offenders = [hit for path in SHARED_LAYERS for hit in _provider_type_branches(path)]
    assert offenders == []


def test_single_provider_type_enum() -> None:
    import pitwall.core.enums as enums

    provider_type_enums = [
        name
        for name, value in vars(enums).items()
        if isinstance(value, type) and name.startswith("ProviderType")
    ]
    assert provider_type_enums == ["ProviderType"]


def _hook_fields() -> list[str]:
    return [
        field.name for field in dataclasses.fields(ProviderDeclaration) if field.default is None
    ]


def test_every_registered_adapter_declares_all_fields() -> None:
    registry = create_default_registry()
    declarations = registry.declarations()
    assert {adapter_id for adapter_id, _ in declarations} == set(registry.ids)
    owned: set[str] = set()
    for adapter_id, declaration in declarations:
        assert isinstance(declaration, ProviderDeclaration), adapter_id
        assert isinstance(registry.lookup(adapter_id).declaration, ProviderDeclaration)
        for field in dataclasses.fields(ProviderDeclaration):
            value = getattr(declaration, field.name)
            if field.name in _hook_fields():
                assert value is None or callable(value), (adapter_id, field.name)
            elif field.name.endswith("_types"):
                assert isinstance(value, frozenset | tuple), (adapter_id, field.name)
                assert all(isinstance(item, str) for item in value)
            elif field.name == "lockout_model_paths":
                assert all(path and all(isinstance(key, str) for key in path) for path in value), (
                    adapter_id
                )
            else:
                assert isinstance(value, bool), (adapter_id, field.name)
        assert declaration.openai_url_types <= declaration.provider_types
        assert declaration.lb_url_types <= declaration.provider_types
        assert declaration.self_hosted_types <= declaration.provider_types
        if declaration.openai_url_types:
            assert declaration.openai_base_url is not None, adapter_id
        if declaration.lb_url_types:
            assert declaration.lb_base_url is not None, adapter_id
        owned |= declaration.provider_types
    assert owned == PROVIDER_TYPE_VALUES
    defaults = [a for a, d in declarations if d.default_for_untyped]
    assert defaults == ["runpod"]


class _FakeCredentials(BaseModel):
    api_key: str


_seen_ticks: list[str] = []


async def _fake_tick(
    repo: Any, prov: Mapping[str, Any], now: dt.datetime, existing: Mapping[Any, Any]
) -> None:
    _seen_ticks.append(prov["id"])


def _fake_seed_config(
    spec: Mapping[str, Any],
    config: dict[str, Any],
    provider_type: str,
    endpoint_id: str | None,
) -> dict[str, Any]:
    config["fake_seeded"] = spec.get("fake_option", "default")
    return config


def _fake_headers(provider: Any, outbound: dict[str, str]) -> dict[str, str] | None:
    outbound["x-fake-route"] = provider.config["route"]
    return outbound


def _fake_skip(provider: Any) -> str | None:
    return "fake adapter is paused" if provider.config.get("paused") else None


class _FakeAdapter:
    id = "fake_cloud"
    name = "Fake cloud"
    credential_schema = _FakeCredentials
    capabilities: frozenset[ProviderCapability] = frozenset()
    declaration = ProviderDeclaration(
        provider_types=frozenset({"fake_type"}),
        dedicated_provider_type=True,
        lockout_model_paths=(("fake", "model"),),
        requires_gpu_class=False,
        seed_config=_fake_seed_config,
        proxy_outbound_headers=_fake_headers,
        proxy_skip_reason=_fake_skip,
        quota_tick=_fake_tick,
    )

    def pricing_model(self, capability: object, provider_record: object) -> Any:
        raise NotImplementedError


def _fake_provider(**config: Any) -> Any:
    return SimpleNamespace(
        id="prov_fake",
        name="fake",
        adapter_id="fake_cloud",
        provider_type="fake_type",
        credential_ref="FAKE_CLOUD_API_KEY",
        config={"openai_base_url": "https://fake.example/v1", "route": "r1", **config},
    )


@pytest.fixture
def registry_with_fake(monkeypatch: pytest.MonkeyPatch) -> ProviderRegistry:
    registry = create_default_registry()
    registry.register(_FakeAdapter())  # type: ignore[arg-type]  # reason: minimal test adapter
    monkeypatch.setattr(registry_module, "_DEFAULT_REGISTRY", registry)
    return registry


def test_registry_rejects_two_adapters_owning_one_provider_type() -> None:
    registry = create_default_registry()

    class _Clash(_FakeAdapter):
        id = "clash"
        declaration = ProviderDeclaration(provider_types=frozenset({"pod_lease"}))

    with pytest.raises(registry_module.InvalidProviderRegistrationError, match="already owned"):
        registry.register(_Clash())  # type: ignore[arg-type]  # reason: minimal test adapter


async def test_new_provider_needs_only_adapter_module(
    registry_with_fake: ProviderRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall import seed
    from pitwall.reconciler import _quota_poll

    provider = _fake_provider()

    # routing: eligibility, skip reason, and outbound headers come from the declaration
    eligible, skipped = fallback._providers_with_openai_urls([provider], max_attempts=3)
    assert eligible == (provider,) and skipped == {}
    paused = _fake_provider(paused=True)
    eligible, skipped = fallback._providers_with_openai_urls([paused], max_attempts=3)
    assert eligible == () and skipped == {"prov_fake": "fake adapter is paused"}
    monkeypatch.setenv("FAKE_CLOUD_API_KEY", "fake-key")  # pragma: allowlist secret
    headers = fallback._provider_headers({"authorization": "Bearer caller"}, provider)
    assert headers["x-fake-route"] == "r1"
    assert "authorization" not in headers  # the fake declaration owns credential handling

    # lockout: the fake adapter says where its model id lives
    provider.config["fake"] = {"model": "fake-model-1"}
    key = lockout.model_lockout_key(provider)
    assert key == lockout.LockoutKey("prov_fake", "fake-model-1")

    # seed: the row is resolved and shaped through the declaration alone
    spec = {"provider_type": "fake_type", "adapter": "fake_cloud", "fake_option": "x"}
    provider_type, adapter_id, declaration = seed._resolve_declaration(spec)
    assert (provider_type, adapter_id) == ("fake_type", "fake_cloud")
    assert declaration.requires_gpu_class is False
    config = seed._provider_config(
        spec, declaration=declaration, provider_type=provider_type, endpoint_id=None
    )
    assert config["fake_seeded"] == "x"
    with pytest.raises(seed.SeedValidationError, match="fake_type providers require adapter"):
        seed._resolve_declaration({"provider_type": "fake_type", "adapter": "runpod"})

    # reconcile: the quota tick runs for the fake adapter's rows
    repo = AsyncMock()
    repo.list_all.return_value = ()
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)

    async def rows_of(_pool: Any, provider_types: Any) -> list[dict[str, Any]]:
        return [{"id": "prov_fake", "config": {}}] if "fake_type" in provider_types else []

    monkeypatch.setattr("pitwall.reconciler.fetch_providers_of_types", rows_of)
    _seen_ticks.clear()
    pool = SimpleNamespace()
    await _quota_poll({"db_pool": pool, "now": dt.datetime(2026, 9, 29, tzinfo=dt.UTC)})
    assert _seen_ticks == ["prov_fake"]
