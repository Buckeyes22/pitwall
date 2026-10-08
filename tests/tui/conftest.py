"""TUI tests run on the registry backend unless a test opts into personal mode."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _registry_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend is chosen by `[personal] backend`, never by DATABASE_URL alone."""
    config = tmp_path / "registry-backend.toml"
    config.write_text('[personal]\nbackend = "registry"\n', encoding="utf-8")
    monkeypatch.setenv("PITWALL_CONFIG_FILE", str(config))
