"""Tests for the neutral RunPod credential resolver shared by `personal` and
`runpod_client` so a saved `runpodctl` credential works identically everywhere.
"""

from __future__ import annotations

from pathlib import Path

from pitwall.runpod_credentials import (
    MISSING_CREDENTIAL_MESSAGE,
    resolve_runpod_api_key,
    runpodctl_config_path,
)


def test_env_var_wins_when_set(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "from-file"\n')

    key, source = resolve_runpod_api_key({"RUNPOD_API_KEY": "from-env"}, config)

    assert (key, source) == ("from-env", "env")


def test_runpodctl_config_used_when_env_absent(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "from-file"\n')

    key, source = resolve_runpod_api_key({}, config)

    assert (key, source) == ("from-file", "runpodctl")


def test_env_var_wins_even_when_config_missing(tmp_path: Path) -> None:
    key, source = resolve_runpod_api_key({"RUNPOD_API_KEY": "from-env"}, tmp_path / "missing.toml")

    assert (key, source) == ("from-env", "env")


def test_no_credential_when_neither_source_exists(tmp_path: Path) -> None:
    key, source = resolve_runpod_api_key({}, tmp_path / "missing.toml")

    assert (key, source) == (None, "none")


def test_blank_env_var_falls_back_to_runpodctl_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "from-file"\n')

    key, source = resolve_runpod_api_key({"RUNPOD_API_KEY": "   "}, config)

    assert (key, source) == ("from-file", "runpodctl")


def test_blank_or_missing_apikey_in_config_is_treated_as_absent(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = ""\n')

    assert resolve_runpod_api_key({}, config) == (None, "none")

    other = tmp_path / "no_key.toml"
    other.write_text('apiurl = "https://api.runpod.io/graphql"\n')

    assert resolve_runpod_api_key({}, other) == (None, "none")


def test_malformed_config_file_is_treated_as_absent_not_an_error(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("not valid toml [[[")

    assert resolve_runpod_api_key({}, config) == (None, "none")


def test_runpodctl_config_path_defaults_to_environ_home() -> None:
    assert runpodctl_config_path({"HOME": "/home/example"}) == Path(
        "/home/example/.runpod/config.toml"
    )


def test_missing_credential_message_names_both_remediations() -> None:
    """The error message must point to both fixes, and never leak a key value."""

    assert "RUNPOD_API_KEY" in MISSING_CREDENTIAL_MESSAGE
    assert "export RUNPOD_API_KEY" in MISSING_CREDENTIAL_MESSAGE
    assert "runpodctl" in MISSING_CREDENTIAL_MESSAGE
