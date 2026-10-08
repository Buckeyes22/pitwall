"""Artifact build contract: sdist packaging stays consistent with wheel force-includes.

The wheel force-includes ``db/migrations`` and ``docs/models`` from paths outside
``src/pitwall``. Hatchling builds the wheel from the sdist, so every force-include
source must be present in the sdist; the previous blanket ``/docs`` exclusion dropped
``docs/models`` and broke every wheel build. These tests pin the allowlist behavior
of ``inspect_sdist`` and the source-archive contents.
"""

from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.release


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pkg_info() -> bytes:
    """Core metadata matching pyproject.toml, as inspect_sdist requires."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    lines = [
        "Metadata-Version: 2.4",
        f"Name: {project['name']}",
        f"Version: {project['version']}",
        f"License-Expression: {project['license']}",
        f"Requires-Python: {project['requires-python']}",
        *(f"Requires-Dist: {requirement}" for requirement in project["dependencies"]),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_tar(path: Path, members: list[str]) -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    root = f"pitwall-{project['version']}"
    with tarfile.open(path, "w:gz") as archive:
        for member, payload in [("PKG-INFO", _pkg_info()), *((m, None) for m in members)]:
            source = ROOT / member
            if payload is None and source.is_file():
                archive.add(source, arcname=f"{root}/{member}")
                continue
            payload = payload if payload is not None else b"placeholder\n"
            info = tarfile.TarInfo(f"{root}/{member}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return path


def test_inspect_sdist_allows_docs_models(tmp_path: Path) -> None:
    module = _load("inspect_artifacts", ROOT / "scripts/release/inspect_artifacts.py")
    archive = _write_tar(
        tmp_path / "allowed.tar.gz",
        ["docs/models/README.md", "docs/models/qwen--qwen.md"],
    )
    assert module.inspect_sdist(archive) == []


def test_inspect_sdist_rejects_other_docs_paths(tmp_path: Path) -> None:
    module = _load("inspect_artifacts", ROOT / "scripts/release/inspect_artifacts.py")
    archive = _write_tar(
        tmp_path / "rejected.tar.gz",
        ["docs/models/README.md", "docs/private.md", "docs/operator/upgrade-recovery.md"],
    )
    errors = module.inspect_sdist(archive)
    assert any("docs/private.md" in error for error in errors)
    assert any("docs/operator/upgrade-recovery.md" in error for error in errors)
    assert not any("docs/models/README.md" in error for error in errors)


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    uv = shutil.which("uv")
    assert uv is not None, "uv must be on PATH to build release artifacts"
    out = tmp_path_factory.mktemp("artifact-build") / "dist"
    result = subprocess.run(
        [uv, "build", "--out-dir", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"
    return out


def test_every_wheel_forced_include_exists_in_built_sdist(built_dist: Path) -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = config["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    sdists = sorted(built_dist.glob("*.tar.gz"))
    assert len(sdists) == 1, f"expected exactly one sdist, found {sdists}"

    with tarfile.open(sdists[0], "r:gz") as archive:
        files = {member.name for member in archive.getmembers() if member.isfile()}
    roots = {name.split("/", 1)[0] for name in files}
    assert len(roots) == 1, f"expected one sdist root directory, found {roots}"
    root = roots.pop()

    missing: list[str] = []
    for source in force_include:
        source_path = ROOT / source
        if not source_path.is_dir():
            if f"{root}/{source}" not in files:
                missing.append(source)
            continue
        for path in sorted(source_path.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = path.relative_to(ROOT).as_posix()
                if f"{root}/{relative}" not in files:
                    missing.append(relative)
    assert not missing, f"wheel force-include sources missing from sdist: {missing}"


def test_built_sdist_passes_artifact_policy(built_dist: Path) -> None:
    module = _load("inspect_artifacts", ROOT / "scripts/release/inspect_artifacts.py")
    sdists = sorted(built_dist.glob("*.tar.gz"))
    assert len(sdists) == 1
    assert module.inspect_sdist(sdists[0]) == []
