"""The catalog sync locates its checkout when it runs, not when the module is imported."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pitwall.gateway_catalog import sync


def test_importing_sync_does_not_resolve_a_repo_root() -> None:
    probe = (
        "import pitwall.gateway_catalog.sync as s; "
        "assert not hasattr(s, 'REPO_ROOT'); "
        "assert not hasattr(s, 'DEFAULT_SEED_PATH')"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)


def test_default_repo_root_follows_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "gateway-providers.yaml").write_text("providers: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert sync.default_repo_root() == tmp_path


def test_write_artifacts_defaults_to_the_root_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "seed").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "seed" / "gateway-providers.yaml").write_text("providers: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    artifacts = sync.transform([], curated_at="2026-09-03")
    sync.write_artifacts(
        artifacts,
        version="3.8.51",
        tarball_sha256="0" * 64,
        registry_covered=0,
        known_unreachable=(),
    )
    assert (tmp_path / "config" / "gateway-catalog.lock.json").is_file()
    assert (tmp_path / "seed" / "gateway-capabilities.yaml").is_file()
