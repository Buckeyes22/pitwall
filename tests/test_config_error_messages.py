"""Config parse and validation errors never echo file content (Task 14 addendum 2).

PyYAML's messages quote source lines, tag and alias names, and escape characters; pydantic's
include input values; tomllib's include key names. Each site reports only a fixed description
and a position.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pitwall.agents import profiles_toml, yaml_channel
from pitwall.agents.mcp_registration import RegistrationError, plan_registration
from pitwall.api.provider_schemas import validate_provider_registration_config
from pitwall.config import (
    ConfigFileError,
    agents_settings_from_toml,
    get_settings,
    load_settings_from_env,
)
from pitwall.gitops.schema import GitOpsConfigError, load_desired_state
from pitwall.mcp_install import McpInstallError, _plan_codex
from pitwall.models.catalogue import load_catalogue, write_evidence
from pitwall.models.errors import CatalogueError
from pitwall.models.inventory import load_inventory
from pitwall.personal.backend import _configured_backend
from pitwall.policy.loader import PolicyLoadError, load_policy_file
from pitwall.seed import SeedValidationError, load_seed_documents
from pitwall.workbench.native_profile import NativeProfileError, parse_frontmatter
from tools.release_acceptance import ops_inventory

SECRETS = ("hunter2pass", "sk-SECRET123")
#: Fragments of PyYAML problem text that would quote the file.
PROBLEM_TEXT = (
    "could not determine",
    "undefined alias",
    "escape character",
    "mapping values",
    "month must be",
    "not 13",
)

#: (body, error class, file line of the error or None when the error has no position) with the
#: secret on the error line. PyYAML's timestamp constructor raises a plain ValueError.
YAML_PAYLOADS: dict[str, tuple[str, str, int | None]] = {
    "timestamp": ("a: 1\nAPI_KEY: 2001-13-45\n", "ValueError", None),
    "tag": ("a: 1\nAPI_KEY: !hunter2pass\n", "ConstructorError", 2),
    "alias": ("a: 1\nAPI_KEY: *hunter2pass\n", "ComposerError", 2),
    "escape": ('a: 1\nAPI_KEY: "abc\\hunter2pass"\n', "ScannerError", 2),
    "snippet": ("a: 1\nTOKEN: hunter2pass\nAPI_KEY: sk-SECRET123: x\n", "ScannerError", 3),
}


def _message(error: type[BaseException], load: Callable[[], object]) -> str:
    with pytest.raises(error) as caught:
        load()
    assert caught.value.__cause__ is None
    return str(caught.value)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _dossier(root: Path, body: str) -> None:
    _write(root / "org--broken.md", f"---\n{body}---\nbody\n")


def _evidence(root: Path) -> object:
    return write_evidence(
        root,
        model_id="org/broken",
        variant_id="v",
        gpu_class="a100",
        observed_vram_gb=1.0,
        observed_startup_s=1.0,
        date="2026-10-06",
    )


#: site -> (error type, loader taking (tmp_path, body), lines before the body in the file)
YAML_SITES: dict[str, tuple[type[BaseException], Callable[[Path, str], object], int]] = {
    "yaml_channel": (
        yaml_channel.YamlChannelError,
        lambda _root, body: yaml_channel.read_entry(body, "mcp_servers", "pitwall-channel"),
        0,
    ),
    "policy": (
        PolicyLoadError,
        lambda root, body: load_policy_file(_write(root / "policy.yaml", body)),
        0,
    ),
    "catalogue_load": (
        CatalogueError,
        lambda root, body: (_dossier(root, body), load_catalogue(root)),
        1,
    ),
    "catalogue_write_evidence": (
        CatalogueError,
        lambda root, body: (_dossier(root, body), _evidence(root)),
        1,
    ),
    "native_profile": (
        NativeProfileError,
        lambda _root, body: parse_frontmatter(f"---\n{body}---\nbody\n"),
        1,
    ),
    "seed": (
        SeedValidationError,
        lambda root, body: load_seed_documents([_write(root / "seed.yaml", body)]),
        0,
    ),
    "inventory": (
        ValueError,
        lambda root, body: load_inventory(_write(root / "inventory.yaml", body)),
        0,
    ),
    "ops_inventory": (
        ops_inventory.OpsInventoryError,
        lambda root, body: ops_inventory._load_compose(
            _write(root / "compose.yaml", body).parent, "compose.yaml"
        ),
        0,
    ),
}


@pytest.mark.parametrize("payload", sorted(YAML_PAYLOADS))
@pytest.mark.parametrize("site", sorted(YAML_SITES))
def test_yaml_errors_name_class_and_line_only(tmp_path: Path, site: str, payload: str) -> None:
    error, load, offset = YAML_SITES[site]
    body, name, line = YAML_PAYLOADS[payload]
    message = _message(error, lambda: load(tmp_path, body))
    if line is None:
        assert message.endswith(f"invalid YAML ({name})")
    else:
        assert f"invalid YAML ({name}) at line {line + offset}, column " in message
    for text in (*SECRETS, *PROBLEM_TEXT, "'h'"):
        assert text not in message


def test_seed_position_counts_stripped_indentation(tmp_path: Path) -> None:
    path = _write(tmp_path / "seed.yaml", "\n\n  API_KEY: sk-SECRET123: x\n")
    message = _message(SeedValidationError, lambda: load_seed_documents([path]))
    assert "invalid YAML (ScannerError) at line 3, column 24" in message
    assert "sk-SECRET123" not in message


def test_yaml_reader_error_reports_a_character_position(tmp_path: Path) -> None:
    message = _message(
        yaml_channel.YamlChannelError,
        lambda: yaml_channel.read_entry("a: hunter2pass\x01\n", "s", "n"),
    )
    assert message == "invalid YAML (ReaderError) at character 15"


#: site -> (error type, loader taking (tmp_path,)) for a document with a secret-valued extra field.
VALIDATION_SITES: dict[str, tuple[type[BaseException], Callable[[Path], object]]] = {
    "policy": (
        PolicyLoadError,
        lambda root: load_policy_file(
            _write(root / "policy.yaml", "version: 1\npolicies: []\nAPI_KEY: sk-SECRET123\n")
        ),
    ),
    "catalogue": (
        CatalogueError,
        lambda root: (
            _dossier(root, "model_id: org/broken\nAPI_KEY: sk-SECRET123\n"),
            load_catalogue(root),
        ),
    ),
    "catalogue_write_evidence": (
        CatalogueError,
        lambda root: (
            _dossier(root, "model_id: org/broken\nAPI_KEY: sk-SECRET123\nvariants:\n  - id: v\n"),
            _evidence(root),
        ),
    ),
    "gitops": (
        GitOpsConfigError,
        lambda root: load_desired_state(
            [_write(root / "state.yaml", "apiVersion: pitwall.dev/v1\nAPI_KEY: sk-SECRET123\n")]
        ),
    ),
}


@pytest.mark.parametrize("site", sorted(VALIDATION_SITES))
def test_validation_errors_never_echo_input_values(tmp_path: Path, site: str) -> None:
    error, load = VALIDATION_SITES[site]
    message = _message(error, lambda: load(tmp_path))
    assert "API_KEY: Extra inputs are not permitted" in message
    assert "sk-SECRET123" not in message
    assert "input_value" not in message


_GPU = "    - name: x\n      count: 1\n      vram_gb: 24\n      arch: sm_86\n      nvlink: false\n"


@pytest.mark.parametrize(
    ("field", "message_part"),
    [
        ("count", "gpus.0.count: Input should be a valid integer"),
        ("arch", "gpus.0.arch: Value error, arch must look like sm_86"),
    ],
)
def test_inventory_validation_error_never_echoes_values(
    tmp_path: Path, field: str, message_part: str
) -> None:
    gpu = _GPU.replace(f"{field}: {'1' if field == 'count' else 'sm_86'}", f"{field}: sk-SECRET123")
    path = _write(tmp_path / "inventory.yaml", f"inventory:\n  gpus:\n{gpu}")
    message = _message(ValueError, lambda: load_inventory(path))
    assert message_part in message
    assert "sk-SECRET123" not in message
    assert "input_value" not in message
    assert "errors.pydantic.dev" not in message


def test_self_hosted_provider_config_error_never_echoes_values() -> None:
    message = _message(
        ValueError,
        lambda: validate_provider_registration_config(
            provider_type=None,
            endpoint_id=None,
            cloud_type=None,
            config={"self_hosted": {"readiness": "sk-SECRET123"}},
        ),
    )
    assert "config.self_hosted is invalid: readiness: Input should be" in message
    assert "sk-SECRET123" not in message
    assert "input_value" not in message


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.usefixtures("fresh_settings")
def test_get_settings_never_echoes_a_rejected_config_file_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "pitwall.toml", 'not_a_setting = "sk-SECRET123"\n')
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))
    message = _message(ConfigFileError, get_settings)
    assert "Pitwall settings could not be parsed:" in message
    assert "not_a_setting: Extra inputs are not permitted" in message
    assert "sk-SECRET123" not in message
    assert "input_value" not in message


@pytest.mark.usefixtures("fresh_settings")
def test_get_settings_names_a_rejected_environment_variable_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_PRE_SPEND_MODE", "sk-SECRET123")
    message = _message(ConfigFileError, get_settings)
    assert "PITWALL_PRE_SPEND_MODE" in message
    assert "sk-SECRET123" not in message


def test_agents_table_error_has_no_cause() -> None:
    message = _message(
        ConfigFileError, lambda: agents_settings_from_toml({"not_a_key": "sk-SECRET123"})
    )
    assert "agents.not_a_key: Extra inputs are not permitted" in message
    assert "sk-SECRET123" not in message


def test_unreadable_config_file_names_the_error_class_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pitwall.toml"
    path.write_bytes(b'note = "\xffsk-SECRET123"\n')
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))
    message = _message(ConfigFileError, load_settings_from_env)
    assert message == f"could not read Pitwall config file {path}: UnicodeDecodeError"


#: Each tomllib message here quotes a key from the file.
TOML_PAYLOADS = {
    "table": ("[hunter2pass]\n[hunter2pass]\n", 2),
    "inline": ("a = {hunter2pass = 1, hunter2pass = 2}\n", 1),
}


def _codex_config(root: Path, body: str) -> dict[str, str]:
    _write(root / ".codex" / "config.toml", body)
    return {"HOME": str(root)}


#: site -> (error type, loader taking (tmp_path, body))
TOML_SITES: dict[str, tuple[type[BaseException], Callable[[Path, str], object]]] = {
    "profiles_toml_parse": (profiles_toml.ProfilesTomlError, lambda _r, b: profiles_toml.parse(b)),
    "profiles_toml_replace": (
        profiles_toml.ProfilesTomlError,
        lambda _r, b: profiles_toml.replace_profiles(b, {}),
    ),
    "mcp_registration": (
        RegistrationError,
        lambda root, body: plan_registration(
            "codex", _codex_config(root, body), root, command="/opt/pitwall"
        ),
    ),
    "mcp_install": (
        McpInstallError,
        lambda root, body: _plan_codex(
            "user", _write(root / "config.toml", body), ["pitwall"], remove=False
        ),
    ),
    "personal_backend": (
        ConfigFileError,
        lambda root, body: _configured_backend(
            {"PITWALL_CONFIG_FILE": str(_write(root / "pitwall.toml", body))}
        ),
    ),
}


@pytest.mark.parametrize("payload", sorted(TOML_PAYLOADS))
@pytest.mark.parametrize("site", sorted(TOML_SITES))
def test_toml_errors_report_position_only(tmp_path: Path, site: str, payload: str) -> None:
    error, load = TOML_SITES[site]
    body, line = TOML_PAYLOADS[payload]
    message = _message(error, lambda: load(tmp_path, body))
    assert f"invalid TOML at line {line}, column " in message
    assert "hunter2pass" not in message


@pytest.mark.parametrize("payload", sorted(TOML_PAYLOADS))
def test_pitwall_config_toml_error_reports_position_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    body, line = TOML_PAYLOADS[payload]
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(_write(tmp_path / "pitwall.toml", body)))
    message = _message(ConfigFileError, load_settings_from_env)
    assert f"invalid TOML at line {line}, column " in message
    assert "hunter2pass" not in message
