"""Unit tests for the seed loader (pitwall.seed).

Targets the previously-undercovered parsing/validation internals: the local
YAML-subset parser (used when PyYAML is absent), the scalar/validation helpers,
and the capability/provider application path with faked repositories.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall import seed
from pitwall.core.enums import CapabilitySource
from pitwall.seed import (
    SeedApplyResult,
    SeedValidationError,
    apply_seed_data,
    load_seed_documents,
    seed_files_from_paths,
)

# --------------------------------------------------------------------------- #
# Scalar / validation helpers                                                 #
# --------------------------------------------------------------------------- #


def test_int_value_rejects_bool_and_non_int() -> None:
    with pytest.raises(SeedValidationError):
        seed._int_value(True, "provider.priority")
    with pytest.raises(SeedValidationError):
        seed._int_value("not-an-int", "provider.priority")
    assert seed._int_value("7", "provider.priority") == 7


def test_dict_and_list_value_type_checks() -> None:
    assert seed._dict_value(None, "f") == {}
    assert seed._list_value(None, "f") == []
    with pytest.raises(SeedValidationError):
        seed._dict_value([1], "f")
    with pytest.raises(SeedValidationError):
        seed._list_value({"a": 1}, "f")


def test_required_string_and_id_from_name() -> None:
    with pytest.raises(SeedValidationError):
        seed._required_string({}, "name", "capability.name")
    with pytest.raises(SeedValidationError, match="cannot be generated"):
        seed._id_from_name("cap", "!!!")
    assert seed._id_from_name("cap", "Embedding Demo") == "cap_embedding_demo"


def test_string_choice_rejects_unknown_enum_value() -> None:
    from pitwall.core.enums import CostMode

    with pytest.raises(SeedValidationError, match="must be one of"):
        seed._string_choice("bogus", CostMode, "capability.cost_mode")


# --------------------------------------------------------------------------- #
# seed_files_from_paths / load_seed_documents                                 #
# --------------------------------------------------------------------------- #


def test_seed_files_from_paths_requires_paths() -> None:
    with pytest.raises(SeedValidationError, match="at least one seed file"):
        seed_files_from_paths([])


def test_seed_files_from_paths_missing_path() -> None:
    with pytest.raises(SeedValidationError, match="does not exist"):
        seed_files_from_paths(["/no/such/seed.yaml"])


def test_seed_files_from_paths_dir_filters_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "b.yaml").write_text("name: b", encoding="utf-8")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("nope", encoding="utf-8")
    found = seed_files_from_paths([tmp_path])
    assert [p.name for p in found] == ["a.json", "b.yaml"]


def test_seed_files_from_paths_empty_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(SeedValidationError, match="no seed files found"):
        seed_files_from_paths([tmp_path])


def test_load_seed_documents_json_and_hash(tmp_path: Path) -> None:
    f = tmp_path / "seed.json"
    f.write_text(json.dumps({"capabilities": []}), encoding="utf-8")
    docs = load_seed_documents([f])
    assert len(docs) == 1
    assert docs[0].payload == {"capabilities": []}
    assert len(docs[0].content_hash) == 64


def test_load_seed_documents_rejects_non_object_root(tmp_path: Path) -> None:
    f = tmp_path / "seed.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SeedValidationError, match="seed root must be an object"):
        load_seed_documents([f])


# --------------------------------------------------------------------------- #
# apply_seed_data — capability + provider application with faked repos        #
# --------------------------------------------------------------------------- #


def _fake_repos(monkeypatch: pytest.MonkeyPatch, *, existing_provider: object = None) -> None:
    """Patch the repository classes so apply_seed_data runs without a DB.

    create() echoes its argument back (the persisted row); get_by_name returns
    None for capabilities and `existing_provider` for providers.
    """

    cap_repo = MagicMock()
    cap_repo.get_by_name = AsyncMock(return_value=None)
    cap_repo.get = AsyncMock(return_value=None)
    cap_repo.create = AsyncMock(side_effect=lambda cap: cap)

    prov_repo = MagicMock()
    prov_repo.get_by_name = AsyncMock(return_value=existing_provider)
    prov_repo.create = AsyncMock(side_effect=lambda prov: prov)

    monkeypatch.setattr(seed, "CapabilityRepository", lambda _pool: cap_repo)
    monkeypatch.setattr(seed, "ProviderRepository", lambda _pool: prov_repo)


_SEED_PAYLOAD = {
    "capabilities": [{"name": "embedding.demo", "class": "embedding", "cost_mode": "per_second"}],
    "providers": [
        {
            "name": "demo-runpod-lb",
            "capability": "embedding.demo",
            "endpoint_id": "eptest00000000",
            "provider_type": "serverless_lb",
            "region": "US-EXAMPLE-1",
            "gpu_class": "NVIDIA L4",
            "priority": 1,
            "cost": {"mode": "per_second", "per_second_active": "0.001"},
        }
    ],
}


@pytest.mark.anyio
async def test_apply_seed_data_applies_capability_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(_SEED_PAYLOAD, pool=MagicMock(), source=CapabilitySource.YAML)
    assert isinstance(result, SeedApplyResult)
    assert [c.name for c in result.capabilities] == ["embedding.demo"]
    assert len(result.providers) == 1
    prov = result.providers[0]
    assert prov.name == "demo-runpod-lb"
    assert prov.config["gpu_class"] == "NVIDIA L4"
    # _provider_config defaults applied
    assert prov.config["cost"]["mode"] == "per_second"
    assert prov.config["request_timeout_s"] == 330
    assert prov.config["lb_base_url"].endswith(".api.runpod.ai")


@pytest.mark.anyio
async def test_apply_seed_data_duplicate_provider_name_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clashing = MagicMock()
    clashing.id = "prov_someone_else"
    _fake_repos(monkeypatch, existing_provider=clashing)
    with pytest.raises(SeedValidationError, match="already exists"):
        await apply_seed_data(_SEED_PAYLOAD, pool=MagicMock(), source=CapabilitySource.YAML)


@pytest.mark.anyio
async def test_apply_seed_data_provider_missing_capability_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    payload = {
        "providers": [
            {
                "name": "orphan",
                "endpoint_id": "eptest00000000",
                "provider_type": "serverless_lb",
                "gpu_class": "NVIDIA L4",
            }
        ]
    }
    with pytest.raises(SeedValidationError, match="capability"):
        await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.API)


@pytest.mark.anyio
async def test_apply_seed_data_rejects_unknown_provider_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    payload = {
        "capabilities": [{"name": "embedding.demo", "class": "embedding"}],
        "providers": [
            {
                "name": "demo",
                "capability": "embedding.demo",
                "provider_type": "not_a_real_type",
                "gpu_class": "NVIDIA L4",
            }
        ],
    }
    with pytest.raises(SeedValidationError, match="must be one of"):
        await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.API)


def test_load_seed_documents_yaml_error_never_quotes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "seed.yaml"
    path.write_text(
        "\n\nproviders:\n  - token: hunter2\n    api_key: sk-SECRET123: x\n", encoding="utf-8"
    )
    with pytest.raises(SeedValidationError) as caught:
        load_seed_documents([path])
    message = str(caught.value)
    assert "seed.yaml" in message
    assert "line 5, column 26" in message
    assert "sk-SECRET123" not in message
    assert "hunter2" not in message
    assert caught.value.__cause__ is None


@pytest.mark.anyio
async def test_apply_seed_data_capability_validation_never_echoes_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    payload = {
        "capabilities": [
            {
                "name": "embedding.demo",
                "class": "embedding",
                "defaults": {"API_KEY": "sk-SECRET123"},
            }
        ]
    }
    with pytest.raises(SeedValidationError) as caught:
        await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.API)
    message = str(caught.value)
    assert "capability 'embedding.demo': API_KEY: Extra inputs are not permitted" in message
    assert "sk-SECRET123" not in message
    assert "input_value" not in message
    assert caught.value.__cause__ is None


@pytest.mark.anyio
async def test_apply_seed_data_provider_validation_never_echoes_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    provider = {**_SEED_PAYLOAD["providers"][0], "priority": -1}  # type: ignore[index]  # reason: the fixture is a loosely typed literal
    payload = {**_SEED_PAYLOAD, "providers": [provider]}
    with pytest.raises(SeedValidationError) as caught:
        await apply_seed_data(payload, pool=MagicMock(), source=CapabilitySource.API)
    message = str(caught.value)
    assert "provider 'demo-runpod-lb': priority: Input should be greater than or equal to 0" in (
        message
    )
    assert "input_value" not in message
    assert caught.value.__cause__ is None
