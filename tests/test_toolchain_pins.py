"""One Python and one uv across workflows, Dockerfiles, and the release smoke script.

``.python-version`` is the only place the interpreter is chosen: every ``actions/setup-python`` step
reads it (``tools/ci/check_workflows.py``), and the Dockerfile base-image tags and the Compose smoke
script must agree with it. The uv version is written in every ``setup-uv`` step and every
Dockerfile ``ARG UV_VERSION``; they must all match, and it must be new enough to download the pinned
CPython (uv 0.11.19 had no CPython 3.14.7, which failed the first v0.3.0a1 tag run).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
DOCKERFILES = sorted((ROOT / "docker").glob("Dockerfile.*"))
SMOKE = ROOT / "scripts" / "release" / "smoke_compose.sh"
# First uv release that can install CPython 3.14.7.
MIN_UV_FOR_PYTHON = (0, 12, 2)


def _python_version() -> str:
    version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f".python-version must be X.Y.Z, got {version!r}"
    )
    return version


def _setup_uv_versions() -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for path in WORKFLOWS:
        document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in (document.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if "astral-sh/setup-uv@" in str(step.get("uses", "")):
                    pinned = str((step.get("with") or {}).get("version", "<unpinned>"))
                    versions.setdefault(pinned, set()).add(f"{path.name}:{job_name}")
    return versions


def _dockerfile_uv_versions() -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for path in DOCKERFILES:
        match = re.search(r"^ARG UV_VERSION=(\S+)$", path.read_text(encoding="utf-8"), re.M)
        versions.setdefault(match.group(1) if match else "<missing>", set()).add(path.name)
    return versions


def test_workflows_and_dockerfiles_pin_one_uv_version() -> None:
    workflow_versions = _setup_uv_versions()
    docker_versions = _dockerfile_uv_versions()
    assert workflow_versions, "no setup-uv steps found"
    assert len(DOCKERFILES) == 5
    assert set(workflow_versions) == set(docker_versions), (
        f"setup-uv steps pin {workflow_versions}; Dockerfiles pin {docker_versions}. "
        "Use one uv version in both places."
    )
    assert len(workflow_versions) == 1, workflow_versions


def test_the_pinned_uv_can_install_the_pinned_python() -> None:
    (uv_version,) = _dockerfile_uv_versions()
    parts = tuple(int(part) for part in uv_version.split("."))
    assert parts >= MIN_UV_FOR_PYTHON, (
        f"uv {uv_version} predates CPython {_python_version()} support "
        f"(needs {'.'.join(map(str, MIN_UV_FOR_PYTHON))} or newer)"
    )


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda path: path.name)
def test_dockerfile_base_images_use_the_python_version_file(dockerfile: Path) -> None:
    tags = re.findall(r"^FROM python:(\S+?)-slim@", dockerfile.read_text(encoding="utf-8"), re.M)
    assert tags == [_python_version()] * 2, f"{dockerfile.name}: {tags} vs {_python_version()}"


def test_the_compose_smoke_script_derives_python_from_the_dockerfiles() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    assert "docker/Dockerfile.${service}" in text
    assert "EXPECTED_PYTHON" in text
    assert not re.search(r"\b\d+\.\d+\.\d+\b", text.replace("127.0.0.1", "")), (
        "smoke_compose.sh must not hard-code a Python version"
    )


def test_python_is_provisioned_only_through_the_version_file() -> None:
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"--python\s+3\.\d+", text), path.name
        assert "uv python install" not in text, path.name
        assert not re.search(r"python-version:\s*[\"']?\d", text), path.name


@pytest.mark.skipif(
    os.environ.get("PITWALL_DEPENDENCY_COMPAT") == "1",
    reason="the dependency-compatibility job re-resolves uv.lock on purpose, so the committed "
    "pre-commit pin cannot match it",
)
def test_pre_commit_ruff_matches_the_locked_ruff() -> None:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    ruff = next(repo for repo in config["repos"] if repo["repo"].endswith("/ruff-pre-commit"))
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    locked = re.search(r'^name = "ruff"\nversion = "([^"]+)"', lock, re.M)
    assert locked is not None
    assert ruff["rev"] == f"v{locked.group(1)}", (
        f".pre-commit-config.yaml pins ruff {ruff['rev']} but uv.lock locks {locked.group(1)}; "
        "set rev to the locked version"
    )
