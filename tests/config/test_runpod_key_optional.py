"""A-24: the API boots without RUNPOD_API_KEY; the key is needed at first RunPod use."""

from __future__ import annotations

import pytest

from pitwall.config import required_runtime_env_vars
from pitwall.runpod_client.pods import RunPodError, _require_api_key
from tests.conftest import _env_for_app, _import_app


def test_api_boots_without_runpod_key(
    clear_app_module: None, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    env = _env_for_app()
    del env["RUNPOD_API_KEY"]
    mod = _import_app(env)
    assert mod.app is not None
    assert "RUNPOD_API_KEY" not in required_runtime_env_vars("api")
    assert "DATABASE_URL" in required_runtime_env_vars("api")


def test_runpod_operation_without_key_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    with pytest.raises(RunPodError, match="RUNPOD_API_KEY"):
        _require_api_key()
