"""Release wheel URLs in install instructions track the release version."""

from __future__ import annotations

import importlib.util
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.release
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def _validator() -> ModuleType:
    path = ROOT / "scripts/release/validate_candidate.py"
    spec = importlib.util.spec_from_file_location("validate_candidate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def test_wheel_url_must_match_candidate_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _validator()
    stale = (
        "https://github.com/Buckeyes22/pitwall/releases/download/v0.2.0a1/"
        "pitwall-0.2.0a1-py3-none-any.whl\n"
    )
    monkeypatch.setattr(
        module,
        "ROOT",
        _repo(tmp_path, {"README.md": stale, "docs/releases/v0.2.0a1.md": stale}),
    )
    assert module._install_reference_errors(VERSION) == [
        f"README.md:1: release wheel URL names 0.2.0a1, expected {VERSION}"
    ]


def test_tracked_tree_has_only_current_wheel_urls() -> None:
    assert _validator()._install_reference_errors(VERSION) == []


def test_outside_a_git_checkout_is_an_error_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _validator()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module._install_reference_errors(VERSION) == [
        f"{tmp_path}: install references can only be checked in a git checkout"
    ]
