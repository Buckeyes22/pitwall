"""CLI test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def registry_config_file(tmp_path: Path) -> Path:
    """A pitwall.toml that picks the registry backend: DATABASE_URL alone never does."""
    config = tmp_path / "registry-backend.toml"
    config.write_text('[personal]\nbackend = "registry"\n', encoding="utf-8")
    return config


@pytest.fixture
def registry_backend(registry_config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(registry_config_file))
