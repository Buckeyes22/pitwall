"""Every RunPod client resolves its key through resolve_runpod_api_key."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from pitwall import runpod_files
from pitwall.runpod_client import graphql, mounts, pods, registry, serverless, templates
from pitwall.runpod_credentials import DEFAULT_RUNPOD_REST_URL

_SAVED_KEY = "rpa_saved_by_runpodctl_0123456789"  # pragma: allowlist secret
_SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"


@pytest.fixture
def saved_runpodctl_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    config = tmp_path / ".runpod" / "config.toml"
    config.parent.mkdir()
    config.write_text(f'apikey = "{_SAVED_KEY}"\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    return _SAVED_KEY


def _mounts_key() -> str:
    return mounts.NetworkVolumeClient()._api_key


def _templates_key() -> str:
    return templates._graphql_api_key(None)


def _graphql_key() -> str:
    return graphql.RunpodGraphQLClient()._api_key


def _files_key() -> str:
    import os

    service = runpod_files.build_configured_volume_file_service(environ=dict(os.environ))
    return str(service._pod_logs._api_key)  # type: ignore[attr-defined]  # reason: test reads the private _api_key that the public type does not declare


def _pods_key() -> str:
    return pods._require_api_key()


def _serverless_key() -> str:
    return serverless._rest_api_key()


def _registry_key() -> str:
    return registry._require_registry_api_key()


@pytest.mark.parametrize(
    "read_key",
    [
        pytest.param(_mounts_key, id="mounts"),
        pytest.param(_templates_key, id="templates"),
        pytest.param(_graphql_key, id="graphql"),
        pytest.param(_files_key, id="files"),
        pytest.param(_pods_key, id="pods"),
        pytest.param(_serverless_key, id="serverless"),
        pytest.param(_registry_key, id="registry"),
    ],
)
def test_saved_runpodctl_key_used_by_every_client(
    saved_runpodctl_key: str, read_key: Callable[[], str]
) -> None:
    assert read_key() == saved_runpodctl_key


def test_only_the_resolver_reads_the_api_key_variable() -> None:
    offenders: list[str] = []
    for path in (
        *sorted((_SRC / "runpod_client").glob("*.py")),
        _SRC / "runpod_files.py",
        _SRC / "runpod_control_plane.py",
        _SRC / "providers" / "runpod.py",
    ):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and node.value == "RUNPOD_API_KEY":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_rest_base_url_default_is_defined_once() -> None:
    pattern = re.compile(r"https://api\.runpod\.io/v2")
    holders = [
        path.name
        for path in (
            *sorted((_SRC / "runpod_client").glob("*.py")),
            _SRC / "runpod_files.py",
            _SRC / "runpod_credentials.py",
        )
        if any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and pattern.fullmatch(node.value)
            for node in ast.walk(ast.parse(path.read_text()))
        )
    ]
    assert holders == ["runpod_credentials.py"]
    assert DEFAULT_RUNPOD_REST_URL == "https://api.runpod.io/v2"
