"""Install instructions never name the unrelated PyPI `pitwall` and track the release version."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.release
# Built from parts so this file's own text never trips the guard it tests.
PACKAGE = "pit" + "wall"


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


@pytest.mark.parametrize(
    "line",
    [
        f"uv tool install {PACKAGE}",
        f"pip install {PACKAGE}",
        f"pipx install {PACKAGE}`",
        f"uv tool install --python 3.14.7 {PACKAGE}",
        f"install {PACKAGE}[email] to enable email",
    ],
)
def test_bare_package_name_install_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    module = _validator()
    monkeypatch.setattr(module, "ROOT", _repo(tmp_path, {"docs/a.md": line + "\n"}))
    assert module._install_reference_errors("0.3.0a1") == [
        "docs/a.md:1: bare `pitwall` package install; PyPI's pitwall is an unrelated project"
    ]


@pytest.mark.parametrize(
    "line",
    [
        f"claude plugin install {PACKAGE}@pitwall-local --scope user",
        "copilot plugin install pitwall-copilot@pitwall-local",
        f"uv tool uninstall {PACKAGE}",
        "uv tool install --python 3.14.7 .",
        f"uv tool install {PACKAGE}[email] @ https://example.invalid/pitwall-0.3.0a1-py3-none-any.whl",
    ],
)
def test_non_package_installs_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    module = _validator()
    monkeypatch.setattr(module, "ROOT", _repo(tmp_path, {"docs/a.md": line + "\n"}))
    assert module._install_reference_errors("0.3.0a1") == []


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
    assert module._install_reference_errors("0.3.0a1") == [
        "README.md:1: release wheel URL names 0.2.0a1, expected 0.3.0a1"
    ]


def test_tracked_tree_has_no_bare_install_and_current_wheel_urls() -> None:
    assert _validator()._install_reference_errors("0.3.0a1") == []


def test_outside_a_git_checkout_is_an_error_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _validator()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module._install_reference_errors("0.3.0a1") == [
        f"{tmp_path}: install references can only be checked in a git checkout"
    ]
