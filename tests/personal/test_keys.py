from __future__ import annotations

import os
from pathlib import Path

from pitwall.personal.keys import (
    ENDPOINT_KEY_ENV,
    config_file_is_shared,
    ensure_endpoint_key,
    read_endpoint_key,
    resolve_runpod_api_key,
)


def test_endpoint_key_is_created_once_and_owner_only(tmp_path: Path) -> None:
    first = ensure_endpoint_key(tmp_path)
    second = ensure_endpoint_key(tmp_path)

    assert first == second == tmp_path / "endpoint.key"
    assert oct(first.stat().st_mode & 0o777) == "0o600"
    value = read_endpoint_key(tmp_path)
    assert value is not None and len(value) >= 32
    assert ENDPOINT_KEY_ENV == "PITWALL_ENDPOINT_KEY"


def test_env_key_wins_over_runpodctl(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "from-file"\n')

    assert resolve_runpod_api_key({"RUNPOD_API_KEY": "from-env"}, config) == ("from-env", "env")
    assert resolve_runpod_api_key({}, config) == ("from-file", "runpodctl")
    assert resolve_runpod_api_key({}, tmp_path / "missing.toml") == (None, "none")


def test_shared_config_detection(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('apikey = "x"\n')
    os.chmod(config, 0o644)
    assert config_file_is_shared(config) is True
    os.chmod(config, 0o600)
    assert config_file_is_shared(config) is False
