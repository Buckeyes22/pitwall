"""Enforce an allowlisted wheel/sdist content policy."""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_NAME = "pitwall"
# A checkout path, a file URL, or private evidence text must never ship in an artifact.
PRIVATE_TEXT = re.compile(rb"(?:/home/[^/\s]+|file:///|PRIVATE_EVIDENCE)")

FORBIDDEN_PARTS = {
    ".git",
    ".remember",
    ".code-intel-eval",
    ".mcp.json",
    ".secrets.baseline",
    ".serena",
    "tests",
    "artifacts",
    "agent-routing",
    "model_routing",
    "packages",
}
FORBIDDEN_AGENT_ENTRIES = (
    b"pitwall-agent-routing =",
    b"model-routing =",
    b"model_routing.cli:",
)
SDIST_ROOT_FILES = {
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "pyproject.toml",
    "PKG-INFO",
    ".gitignore",  # hatchling includes the VCS ignore file in source archives
}
# The wheel force-includes these (pyproject [tool.hatch.build.targets.wheel.force-include]),
# so a wheel built from the sdist needs them in the sdist too. Exact paths, not directories.
SDIST_PACKAGED_DATA_FILES = {
    "config/gateway-catalog.json",
    "config/gateway-catalog.lock.json",
    "config/gateway-routes.json",
    "seed/gateway-capabilities.yaml",
    "seed/gateway-providers.yaml",
}


def _project() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project: dict[str, Any] = tomllib.load(handle)["project"]
    return project


def _names(requirements: Iterable[object]) -> set[str]:
    return {
        re.split(r"[\s;<>=!~\[(]", str(item), maxsplit=1)[0].lower().replace("_", "-")
        for item in requirements
    }


def _metadata_errors(metadata_bytes: bytes, artifact: str) -> list[str]:
    """The package metadata must be the one project: name, version, license, Python."""
    project = _project()
    metadata = BytesParser(policy=default).parsebytes(metadata_bytes)
    errors: list[str] = []
    expected = {
        "Name": EXPECTED_NAME,
        "Version": str(project["version"]),
        "License-Expression": str(project["license"]),
        "Requires-Python": str(project["requires-python"]),
    }
    for field, value in expected.items():
        actual = metadata.get(field)
        if field == "Requires-Python" and isinstance(actual, str):
            same = {c.strip() for c in actual.split(",")} == {c.strip() for c in value.split(",")}
        else:
            same = actual == value
        if not same:
            errors.append(f"{artifact}: {field} must be {value!r}")
    declared = _names(project.get("dependencies", []))
    packaged = _names(
        item for item in metadata.get_all("Requires-Dist") or [] if "extra ==" not in str(item)
    )
    if declared != packaged:
        errors.append(f"{artifact}: runtime dependencies differ from pyproject.toml")
    return errors


def _safe(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not (set(path.parts) & FORBIDDEN_PARTS)
        and not any(part.endswith("_EVIDENCE_PITWALL.md") for part in path.parts)
    )


def inspect_wheel(path: Path) -> list[str]:
    errors: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            errors.append(f"{path.name}: duplicate archive paths are forbidden")
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            errors.append(f"{path.name}: exactly one METADATA file is required")
        else:
            errors.extend(_metadata_errors(archive.read(metadata_names[0]), path.name))
        entry_names = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if len(entry_names) != 1:
            errors.append(f"{path.name}: exactly one entry_points.txt is required")
        else:
            entry_points = archive.read(entry_names[0])
            if b"pitwall = pitwall.cli:main" not in entry_points:
                errors.append(f"{path.name}: missing the pitwall console entry")
            if any(marker in entry_points for marker in FORBIDDEN_AGENT_ENTRIES):
                errors.append(f"{path.name}: Agent Routing command entry is forbidden")
        for licence in ("LICENSE", "NOTICE"):
            packaged = [n for n in names if n.endswith(f".dist-info/licenses/{licence}")]
            if len(packaged) != 1:
                errors.append(f"{path.name}: exactly one packaged {licence} is required")
            elif archive.read(packaged[0]) != (ROOT / licence).read_bytes():
                errors.append(f"{path.name}: packaged {licence} differs from the repository's")
        for name in names:
            if name.endswith("/") or name.endswith(".dist-info/RECORD"):
                continue
            if PRIVATE_TEXT.search(archive.read(name)):
                errors.append(f"{path.name}: {name} contains a checkout or private path")
    for name in names:
        if not _safe(name):
            errors.append(f"{path.name}: forbidden or unsafe path {name}")
            continue
        first = PurePosixPath(name).parts[0]
        if first != "pitwall" and not first.endswith(".dist-info"):
            errors.append(f"{path.name}: unexpected top-level path {name}")
    required = {
        "pitwall/py.typed",
        "pitwall/db/migrations/0001_capabilities.sql",
        "pitwall/agents/__init__.py",
        "pitwall/agents/__main__.py",
        "pitwall/agents/resources/config/harness-registry.json",
        "pitwall/agents/resources/config/harness-installers.json",
        "pitwall/agents/resources/config/model-catalog.json",
        "pitwall/agents/resources/schemas/routes.schema.json",
        "pitwall/agents/resources/schemas/model-studio.schema.json",
        "pitwall/providers/model_studio/catalog.json",
    }
    for name in sorted(required - set(names)):
        errors.append(f"{path.name}: missing {name}")
    sql_count = sum(
        name.startswith("pitwall/db/migrations/") and name.endswith(".sql") for name in names
    )
    if sql_count != len(list((Path("db/migrations")).glob("*.sql"))):
        errors.append(f"{path.name}: packaged migration count {sql_count} does not match source")
    return errors


def inspect_sdist(path: Path) -> list[str]:
    errors: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            errors.append(f"{path.name}: duplicate archive paths are forbidden")
        if len({PurePosixPath(name).parts[0] for name in names if PurePosixPath(name).parts}) != 1:
            errors.append(f"{path.name}: exactly one sdist root is required")
        metadata = next(
            (m for m in members if PurePosixPath(m.name).parts[1:] == ("PKG-INFO",)), None
        )
        extracted = archive.extractfile(metadata) if metadata is not None else None
        if extracted is None:
            errors.append(f"{path.name}: PKG-INFO is required")
        else:
            errors.extend(_metadata_errors(extracted.read(), path.name))
        for member in members:
            if not member.isfile() or not _safe(member.name):
                continue
            body = archive.extractfile(member)
            if body is not None and PRIVATE_TEXT.search(body.read()):
                errors.append(f"{path.name}: {member.name} contains a checkout or private path")
    for member in members:
        if member.issym() or member.islnk():
            errors.append(f"{path.name}: links are not permitted: {member.name}")
        if not _safe(member.name):
            errors.append(f"{path.name}: forbidden or unsafe path {member.name}")
            continue
        parts = PurePosixPath(member.name).parts
        if len(parts) < 2 or member.isdir():
            continue
        relative = PurePosixPath(*parts[1:])
        allowed = (
            relative.parts[0] in {"src", "db", "plugins"}
            or relative.parts[:2] == ("docs", "models")
            or str(relative) in SDIST_ROOT_FILES
            or str(relative) in SDIST_PACKAGED_DATA_FILES
        )
        if not allowed:
            errors.append(f"{path.name}: unexpected sdist path {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("*.whl"))
    sdists = sorted(args.directory.glob("*.tar.gz"))
    errors: list[str] = []
    if len(wheels) != 1 or len(sdists) != 1:
        errors.append("exactly one wheel and one sdist are required")
    for artifact in wheels:
        errors.extend(inspect_wheel(artifact))
    for artifact in sdists:
        errors.extend(inspect_sdist(artifact))
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print(f"artifact policy passed: {wheels[0].name}, {sdists[0].name}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
