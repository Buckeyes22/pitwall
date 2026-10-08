"""Tests for the settings-field and ``.env.example`` configuration inventory.

The tests prove discovery is grounded in ``src/pitwall/config.py`` and the
literal env-example key names: adding or removing a field, or changing a
default, constraint, or alias changes the rows and contract digests; comments
and string literals never become fields; example values are never recorded;
malformed or missing sources fail clearly; and no ambient environment value is
ever read. Nothing imports, instantiates, or introspects ``pitwall``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.release_acceptance import config_inventory

MINIMAL = """class _StrictBase:
    pass


class R2Config(_StrictBase):
    endpoint: str = Field(default="", description="R2 endpoint")
    ttl_s: int = Field(default=60, ge=1, le=600)


class PitwallSettings(BaseSettings):
    admin_secret: str = Field(default="", validation_alias="PITWALL_ADMIN_SECRET")
    extra: R2Config = Field(default_factory=R2Config)
"""

ENV_EXAMPLE = "PITWALL_ADMIN_SECRET=\n"


_ENV_GUARD = """\
import os


class _NoReadEnviron:
    def _no(self, *args, **kwargs):
        raise AssertionError("ambient environment read")

    get = __getitem__ = __contains__ = __iter__ = _no
    keys = values = items = copy = setdefault = _no


os.environ = _NoReadEnviron()
from tools.release_acceptance import config_inventory

rows = config_inventory.discover(config_inventory.ROOT)
report = config_inventory.discover_with_issues(config_inventory.ROOT)
assert report["surfaces"] == rows
print("ENV_GUARD_OK", len(rows))
"""


def _root(tmp_path: Path, body: str = MINIMAL, env: str | None = ENV_EXAMPLE) -> Path:
    module = tmp_path / config_inventory.CONFIG_MODULE
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(body, encoding="utf-8")
    if env is not None:
        (tmp_path / config_inventory.ENV_EXAMPLE).write_text(env, encoding="utf-8")
    return tmp_path


def _ids(rows: list[dict]) -> list[str]:
    return [row["surface_id"] for row in rows]


def _row(rows: list[dict], surface_id: str) -> dict:
    return next(row for row in rows if row["surface_id"] == surface_id)


def _contracts(rows: list[dict]) -> dict[str, str]:
    return {row["surface_id"]: row["metadata"]["contract_digest"] for row in rows}


def test_real_repo_fields_and_env_keys_are_discovered() -> None:
    rows = config_inventory.discover(config_inventory.ROOT)
    ids = _ids(rows)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert all(row["kind"] == "config" and row["operation"] == "setting" for row in rows)
    for surface_id in (
        "config:field:PitwallSettings:runpod_api_key",
        "config:field:RoutingWeights:cost",
        "config:field:PitwallSettings:runpod_rest_api_url",
        "config:field:PitwallSettings:r2_endpoint",
        "config:env:PITWALL_ADMIN_SECRET",
        "config:env:PITWALL_MONTHLY_BUDGET_USD",
    ):
        assert surface_id in ids
    fields = [row for row in rows if row["metadata"]["category"] == "field"]
    env_keys = [row for row in rows if row["metadata"]["category"] == "env_example"]
    assert len(fields) >= 78
    assert len(env_keys) >= 90


def test_field_rows_carry_model_scoped_ids_sources_and_digests() -> None:
    rows = config_inventory.discover(config_inventory.ROOT)
    for row in rows:
        metadata = row["metadata"]
        assert re.fullmatch(r"[0-9a-f]{64}", metadata["source_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", metadata["contract_digest"])
        if metadata["category"] == "field":
            assert row["surface_id"] == f"config:field:{metadata['model']}:{metadata['field']}"
            assert re.fullmatch(r"src/pitwall/config\.py:\d+", row["source"])
        else:
            assert row["surface_id"] == f"config:env:{metadata['env_name']}"
            assert re.fullmatch(r"\.env\.example:\d+", row["source"])


def test_literal_field_contract_metadata() -> None:
    rows = config_inventory.discover(config_inventory.ROOT)
    embedding = _row(rows, "config:field:PitwallSettings:pitwall_embedding_via_pitwall")
    assert embedding["metadata"]["alias"] == "PITWALL_EMBEDDING_VIA_PITWALL"
    assert embedding["metadata"]["alias_unresolved"] is False
    assert embedding["metadata"]["default_expression"] == "False"
    assert embedding["metadata"]["required"] is False
    cache = _row(rows, "config:field:PitwallSettings:runpod_market_cache_ttl_s")
    assert cache["metadata"]["type_expression"] == "float"
    assert cache["metadata"]["constraints"] == {"ge": 0, "allow_inf_nan": False}
    weights = _row(rows, "config:field:PitwallSettings:pitwall_routing_weights")
    assert weights["metadata"]["constraints"]["max_length"] == 1000
    type_expression = weights["metadata"]["type_expression"]
    assert type_expression.startswith("Annotated[") and "dict[" in type_expression


def test_real_repo_dynamic_defaults_are_flagged_not_executed() -> None:
    rows = config_inventory.discover(config_inventory.ROOT)
    ttl = _row(rows, "config:field:PitwallSettings:r2_temp_credential_ttl_s")
    assert ttl["metadata"]["default_expression"] == "DEFAULT_R2_TEMP_CREDENTIAL_TTL_S"
    assert ttl["metadata"]["default_unresolved"] is True
    weights = _row(rows, "config:field:PitwallSettings:pitwall_routing_weights")
    assert weights["metadata"]["default_unresolved"] is True
    report = config_inventory.discover_with_issues(config_inventory.ROOT)
    dynamic = [issue for issue in report["issues"] if issue["topic"] == "dynamic_default"]
    assert dynamic
    assert all(issue["status"] == "unresolved" for issue in dynamic)
    assert all("never executed" in issue["reason"] for issue in dynamic)


def test_a_field_without_a_default_is_required(tmp_path: Path) -> None:
    body = (
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        '    api_key: str = Field(description="control-plane key")\n'
    )
    rows = config_inventory.discover(_root(tmp_path, body, env=None))
    api_key = _row(rows, "config:field:PitwallSettings:api_key")
    assert api_key["metadata"]["required"] is True
    assert api_key["metadata"]["default_expression"] is None


def test_dynamic_default_and_alias_stay_source_expressions(tmp_path: Path) -> None:
    body = (
        "ALIAS_NAME = None\n\n\n"
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        "    ttl_s: int = Field(default=DEFAULT_TTL)\n"
        '    admin_secret: str = Field(default="", validation_alias=ALIAS_NAME)\n'
    )
    root = _root(tmp_path, body, env=None)
    rows = config_inventory.discover(root)
    ttl = _row(rows, "config:field:PitwallSettings:ttl_s")
    assert ttl["metadata"]["default_expression"] == "DEFAULT_TTL"
    assert ttl["metadata"]["default_unresolved"] is True
    assert "dynamic_default" in ttl["metadata"]["unresolved"]
    secret = _row(rows, "config:field:PitwallSettings:admin_secret")
    assert secret["metadata"]["alias"] is None
    assert secret["metadata"]["alias_expression"] == "ALIAS_NAME"
    assert secret["metadata"]["alias_unresolved"] is True
    assert secret["metadata"]["alias"] is None
    ttl_line = next(n for n, text in enumerate(body.splitlines(), 1) if "ttl_s" in text)
    report = config_inventory.discover_with_issues(root)
    topics = {(issue["topic"], issue.get("source")) for issue in report["issues"]}
    assert ("dynamic_default", f"{config_inventory.CONFIG_MODULE}:{ttl_line}") in topics
    assert "dynamic_alias" in {issue["topic"] for issue in report["issues"]}


def test_added_and_removed_field_changes_discovery(tmp_path: Path) -> None:
    before = config_inventory.discover(_root(tmp_path))
    assert "config:field:PitwallSettings:admin_secret" in _ids(before)
    added_body = MINIMAL.replace(
        "    extra: R2Config", "    ghost: int = Field(default=0)\n    extra: R2Config"
    )
    added = config_inventory.discover(_root(tmp_path, added_body))
    assert "config:field:PitwallSettings:ghost" in _ids(added)
    removed_body = MINIMAL.replace(
        '    admin_secret: str = Field(default="", validation_alias="PITWALL_ADMIN_SECRET")\n',
        "",
    )
    removed = config_inventory.discover(_root(tmp_path, removed_body))
    assert "config:field:PitwallSettings:admin_secret" not in _ids(removed)
    assert _contracts(before) != _contracts(removed)


def test_alias_default_and_constraint_change_contract_digest(tmp_path: Path) -> None:
    baseline = _contracts(config_inventory.discover(_root(tmp_path)))
    endpoint_id = "config:field:R2Config:endpoint"
    ttl_id = "config:field:R2Config:ttl_s"
    secret_id = "config:field:PitwallSettings:admin_secret"

    default_changed = config_inventory.discover(
        _root(
            tmp_path,
            MINIMAL.replace(
                'default="", description="R2 endpoint"', 'default="X", description="R2 endpoint"'
            ),
        )
    )
    assert _contracts(default_changed)[endpoint_id] != baseline[endpoint_id]

    constraint_changed = config_inventory.discover(
        _root(tmp_path, MINIMAL.replace("ge=1, le=600", "ge=1, le=601"))
    )
    assert _contracts(constraint_changed)[ttl_id] != baseline[ttl_id]

    alias_changed = config_inventory.discover(
        _root(
            tmp_path,
            MINIMAL.replace(
                'validation_alias="PITWALL_ADMIN_SECRET"',
                'validation_alias="PITWALL_ADMIN_SECRET_V2"',
            ),
        )
    )
    assert _contracts(alias_changed)[secret_id] != baseline[secret_id]
    assert _row(alias_changed, secret_id)["metadata"]["alias"] == "PITWALL_ADMIN_SECRET_V2"


def test_comments_and_strings_are_never_fields(tmp_path: Path) -> None:
    root = _root(tmp_path)
    baseline = _contracts(config_inventory.discover(root))
    module = root / config_inventory.CONFIG_MODULE
    commented = (
        MINIMAL.replace(
            "class PitwallSettings(BaseSettings):",
            "class PitwallSettings(BaseSettings):\n"
            "    # ghost: int = Field(default=0)\n"
            '    """admin_secret: str = Field(default="x")"""',
        )
        + "\n# trailing: int = Field(default=0)\n"
    )
    module.write_text(commented, encoding="utf-8")
    rows = config_inventory.discover(root)
    assert _contracts(rows) == baseline
    assert "config:field:PitwallSettings:ghost" not in _ids(rows)
    assert "config:field:PitwallSettings:trailing" not in _ids(rows)


def test_example_values_are_never_recorded(tmp_path: Path) -> None:
    secret = "opaque-example-secret-4f1c"
    root = _root(tmp_path, MINIMAL, env=f"PITWALL_ADMIN_SECRET={secret}\n# PITWALL_HIDDEN=hidden\n")
    rows = config_inventory.discover(root)
    dumped = json.dumps(rows)
    assert secret not in dumped
    assert "hidden" not in dumped
    assert "PITWALL_HIDDEN" not in dumped
    row = _row(rows, "config:env:PITWALL_ADMIN_SECRET")
    assert row["metadata"]["env_name"] == "PITWALL_ADMIN_SECRET"
    assert row["metadata"]["example_value_redacted"] is True
    assert row["source"] == f"{config_inventory.ENV_EXAMPLE}:1"


def test_added_and_removed_env_key_changes_discovery(tmp_path: Path) -> None:
    without = config_inventory.discover(
        _root(tmp_path, MINIMAL, env="PITWALL_API_TOKEN=\nPITWALL_ADMIN_SECRET=\n")
    )
    assert "config:env:PITWALL_ADMIN_SECRET" in _ids(without)
    added = config_inventory.discover(_root(tmp_path, MINIMAL, env="PITWALL_API_TOKEN=\n"))
    assert "config:env:PITWALL_ADMIN_SECRET" not in _ids(added)
    assert "config:env:PITWALL_API_TOKEN" in _ids(added)


def test_same_field_name_in_two_models_never_collides(tmp_path: Path) -> None:
    body = (
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class DatabaseConfig(_StrictBase):\n"
        '    url: str = Field(default="")\n\n\n'
        "class RedisConfig(_StrictBase):\n"
        '    url: str = Field(default="")\n\n\n'
        "class PitwallSettings(BaseSettings):\n"
        '    database_url: str = Field(default="")\n'
    )
    rows = config_inventory.discover(_root(tmp_path, body, env=None))
    ids = _ids(rows)
    assert "config:field:DatabaseConfig:url" in ids
    assert "config:field:RedisConfig:url" in ids
    assert "config:field:PitwallSettings:database_url" in ids
    assert len(ids) == 3
    assert len(set(_contracts(rows).values())) == 3


def test_discovery_never_reads_ambient_environment() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _ENV_GUARD],
        cwd=config_inventory.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ENV_GUARD_OK")


def test_ambient_setting_values_never_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "ambient-secret-3c9f1b"
    monkeypatch.setenv("PITWALL_ADMIN_SECRET", sentinel)
    dumped = json.dumps(config_inventory.discover_with_issues(config_inventory.ROOT))
    assert sentinel not in dumped


def test_malformed_or_missing_module_fails_clearly(tmp_path: Path) -> None:
    malformed = _root(tmp_path, "class PitwallSettings(BaseSettings)\n    x: int =\n")
    with pytest.raises(ValueError, match="invalid Python"):
        config_inventory.discover(malformed)
    no_settings = _root(tmp_path, "class Other(BaseSettings):\n    pass\n")
    with pytest.raises(ValueError, match="PitwallSettings"):
        config_inventory.discover(no_settings)
    with pytest.raises(ValueError, match="cannot read"):
        config_inventory.discover(tmp_path / "absent")


def test_issues_state_unresolved_domains_explicitly() -> None:
    report = config_inventory.discover_with_issues(config_inventory.ROOT)
    assert report["schema_version"] == config_inventory.SCHEMA_VERSION
    assert report["surfaces"] == config_inventory.discover(config_inventory.ROOT)
    issues = {issue["topic"]: issue for issue in report["issues"]}
    for topic in ("runtime_env_binding", "config_files", "other_projects"):
        assert issues[topic]["status"] == "unresolved"
        assert "UNRESOLVED" in issues[topic]["reason"]
    assert issues["scope"]["status"] == "partial"


def test_field_bare_ellipsis_and_keyword_mean_required(tmp_path: Path) -> None:
    body = (
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        "    bare: str\n"
        "    empty: str = Field()\n"
        "    ellipsis: str = Field(...)\n"
        "    keyword: str = Field(default=...)\n"
        "    keyword_value: str = Field(default='x')\n"
        "    factory: list[str] = Field(default_factory=list)\n"
    )
    rows = config_inventory.discover(_root(tmp_path, body, env=None))
    required = {
        field: _row(rows, f"config:field:PitwallSettings:{field}")["metadata"]["required"]
        for field in ("bare", "empty", "ellipsis", "keyword", "keyword_value", "factory")
    }
    assert required == {
        "bare": True,
        "empty": True,
        "ellipsis": True,
        "keyword": True,
        "keyword_value": False,
        "factory": False,
    }
    for field in ("bare", "empty", "ellipsis", "keyword"):
        metadata = _row(rows, f"config:field:PitwallSettings:{field}")["metadata"]
        assert metadata["default_expression"] is None
        assert metadata["default_unresolved"] is False
    factory = _row(rows, "config:field:PitwallSettings:factory")["metadata"]
    assert factory["default_expression"] == "list"
    assert factory["default_unresolved"] is True
    assert "dynamic_default" in factory["unresolved"]
    same_name = _root(
        tmp_path,
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        "    x: str = Field(...)\n",
        env=None,
    )
    empty_same_name = _root(
        tmp_path,
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        "    x: str = Field()\n",
        env=None,
    )
    ellipsis_id = "config:field:PitwallSettings:x"
    assert (
        _contracts(config_inventory.discover(same_name))[ellipsis_id]
        == _contracts(config_inventory.discover(empty_same_name))[ellipsis_id]
    )


def test_required_change_changes_contract_digest(tmp_path: Path) -> None:
    baseline = _contracts(config_inventory.discover(_root(tmp_path)))
    field_id = "config:field:R2Config:endpoint"
    changed = config_inventory.discover(
        _root(
            tmp_path,
            MINIMAL.replace(
                'endpoint: str = Field(default="", description="R2 endpoint")',
                'endpoint: str = Field(description="R2 endpoint")',
            ),
        )
    )
    assert _row(changed, field_id)["metadata"]["required"] is True
    assert _contracts(changed)[field_id] != baseline[field_id]


def test_dynamic_alias_expression_change_changes_contract_digest(tmp_path: Path) -> None:
    first = (
        "ALIAS_ONE = None\n\n\n"
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        "    admin_secret: str = Field(default='', validation_alias=ALIAS_ONE)\n"
    )
    second = first.replace("validation_alias=ALIAS_ONE", "validation_alias=ALIAS_TWO")
    baseline = config_inventory.discover(_root(tmp_path, first, env=None))
    changed = config_inventory.discover(_root(tmp_path, second, env=None))
    field_id = "config:field:PitwallSettings:admin_secret"
    before = _row(baseline, field_id)["metadata"]
    after = _row(changed, field_id)["metadata"]
    assert before["alias"] is None and after["alias"] is None
    assert before["alias_expression"] == "ALIAS_ONE"
    assert after["alias_expression"] == "ALIAS_TWO"
    assert before["contract_digest"] != after["contract_digest"]


def test_duplicate_field_declaration_is_explicit(tmp_path: Path) -> None:
    body = (
        "class _StrictBase:\n"
        "    pass\n\n\n"
        "class PitwallSettings(BaseSettings):\n"
        "    admin_secret: str = Field(default='first')\n"
        "    admin_secret: str = Field(default='second')\n"
    )
    root = _root(tmp_path, body, env=None)
    rows = config_inventory.discover(root)
    ids = _ids(rows)
    assert ids.count("config:field:PitwallSettings:admin_secret") == 1
    assert (
        _row(rows, "config:field:PitwallSettings:admin_secret")["metadata"]["default_expression"]
        == "'first'"
    )
    first_line = next(n for n, text in enumerate(body.splitlines(), 1) if "first" in text)
    second_line = next(n for n, text in enumerate(body.splitlines(), 1) if "second" in text)
    report = config_inventory.discover_with_issues(root)
    duplicate = [
        issue for issue in report["issues"] if issue["topic"] == "duplicate_field_declaration"
    ]
    assert len(duplicate) == 1
    assert duplicate[0]["status"] == "unresolved"
    assert duplicate[0]["duplicate_sources"] == [
        f"{config_inventory.CONFIG_MODULE}:{first_line}",
        f"{config_inventory.CONFIG_MODULE}:{second_line}",
    ]
    assert "2 times" in duplicate[0]["reason"]


def test_duplicate_env_declaration_is_explicit(tmp_path: Path) -> None:
    env = "PITWALL_API_TOKEN=first\nPITWALL_OTHER=ok\nPITWALL_API_TOKEN=second\n"
    root = _root(tmp_path, MINIMAL, env=env)
    rows = config_inventory.discover(root)
    ids = _ids(rows)
    assert ids.count("config:env:PITWALL_API_TOKEN") == 1
    row = _row(rows, "config:env:PITWALL_API_TOKEN")
    assert row["source"] == f"{config_inventory.ENV_EXAMPLE}:1"
    report = config_inventory.discover_with_issues(root)
    duplicate = [
        issue for issue in report["issues"] if issue["topic"] == "duplicate_env_declaration"
    ]
    assert len(duplicate) == 1
    assert duplicate[0]["duplicate_sources"] == [
        f"{config_inventory.ENV_EXAMPLE}:1",
        f"{config_inventory.ENV_EXAMPLE}:3",
    ]
    dumped = json.dumps(duplicate)
    assert "PITWALL_API_TOKEN=first" not in dumped
    assert "PITWALL_API_TOKEN=second" not in dumped


def test_env_line_change_drifts_source_hash_without_leaking_value(tmp_path: Path) -> None:
    first_secret = "opaque-first-8a1e"
    second_secret = "opaque-second-5d3b"
    root = _root(tmp_path, MINIMAL, env=f"PITWALL_ADMIN_SECRET={first_secret}\n")
    first = _row(config_inventory.discover(root), "config:env:PITWALL_ADMIN_SECRET")
    root = _root(tmp_path, MINIMAL, env=f"PITWALL_ADMIN_SECRET={second_secret}\n")
    second = _row(config_inventory.discover(root), "config:env:PITWALL_ADMIN_SECRET")
    assert first["metadata"]["source_sha256"] != second["metadata"]["source_sha256"]
    assert first["metadata"]["contract_digest"] == second["metadata"]["contract_digest"]
    same_value_moved = _root(
        tmp_path, MINIMAL, env=f"# comment\nPITWALL_ADMIN_SECRET={first_secret}\n"
    )
    moved = _row(config_inventory.discover(same_value_moved), "config:env:PITWALL_ADMIN_SECRET")
    assert moved["metadata"]["source_sha256"] == first["metadata"]["source_sha256"]
    dumped = json.dumps([first, second, moved])
    assert first_secret not in dumped
    assert second_secret not in dumped


def test_output_is_deterministic_sorted_and_unique(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = config_inventory.discover(root)
    assert first == config_inventory.discover(root)
    ids = _ids(first)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert config_inventory.discover_with_issues(root)["surfaces"] == first
