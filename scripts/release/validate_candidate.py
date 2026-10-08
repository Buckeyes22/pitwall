"""Validate the immutable source identity used for a release candidate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# Every packaged plugin manifest and marketplace entry carries the package version.
PLUGIN_MANIFESTS = (
    ("plugins/claude/.claude-plugin/plugin.json", "pitwall"),
    ("plugins/codex/.codex-plugin/plugin.json", "pitwall-codex"),
    ("plugins/copilot/plugin.json", "pitwall-copilot"),
)
MARKETPLACES = (
    (".claude-plugin/marketplace.json", "pitwall"),
    (".agents/plugins/marketplace.json", "pitwall-codex"),
    (".github/plugin/marketplace.json", "pitwall-copilot"),
)
BARE_INSTALL = re.compile(
    r"\binstall (?:--\S+ (?:\S+ )?)*pitwall(?:\[[a-z,]+\])?(?=$|[`'\")\];,.]|\s(?!@))"
)
RELEASE_WHEEL = re.compile(r"releases/download/v([^/\s]+)/pitwall-([^-/\s]+)-py3-none-any\.whl")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("must contain an object")
    return value


def _plugin_errors(version: str) -> list[str]:
    errors: list[str] = []
    for relative, name in PLUGIN_MANIFESTS:
        try:
            manifest = _load_object(ROOT / relative)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot load {relative}: {exc}")
            continue
        if manifest.get("name") != name:
            errors.append(f"{relative} must be named {name}")
        if manifest.get("version") != version:
            errors.append(f"{relative} must use version {version}")
    for relative, name in MARKETPLACES:
        try:
            plugins = _load_object(ROOT / relative).get("plugins")
        except (OSError, ValueError) as exc:
            errors.append(f"cannot load {relative}: {exc}")
            continue
        entry = plugins[0] if isinstance(plugins, list) and len(plugins) == 1 else None
        if not isinstance(entry, dict) or entry.get("name") != name:
            errors.append(f"{relative} must contain exactly the plugin {name}")
        elif entry.get("version") != version:
            errors.append(f"{relative} plugin entry must use version {version}")
    return errors


def _version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _install_reference_errors(version: str) -> list[str]:
    """Reject bare PyPI `pitwall` installs and wheel URLs for another version."""

    if not (ROOT / ".git").exists():
        return [f"{ROOT}: install references can only be checked in a git checkout"]
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8")
    errors: list[str] = []
    for relative in filter(None, listed.split("\0")):
        try:
            text = (ROOT / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError, FileNotFoundError:
            continue
        historical = (
            relative.startswith("docs/releases/") and relative != f"docs/releases/v{version}.md"
        )
        for number, line in enumerate(text.splitlines(), start=1):
            if BARE_INSTALL.search(line):
                errors.append(
                    f"{relative}:{number}: bare `pitwall` package install; "
                    "PyPI's pitwall is an unrelated project"
                )
            if historical or relative == "CHANGELOG.md":
                continue
            for tag, wheel in RELEASE_WHEEL.findall(line):
                if tag != version or wheel != version:
                    errors.append(
                        f"{relative}:{number}: release wheel URL names {tag}, expected {version}"
                    )
    return errors


def validate(tag: str, *, allow_dirty: bool = False) -> list[str]:
    errors: list[str] = []
    version = _version()
    if tag != f"v{version}":
        errors.append(f"tag {tag!r} does not match project version v{version}")
    if not re.fullmatch(r"v0\.[1-9][0-9]*\.[0-9]+(?:a[1-9][0-9]*)?", tag):
        errors.append("the public alpha tag must be pre-1.0 and match v0.MINOR.PATCH[aN]")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$", changelog, re.M
    )
    if match is None:
        errors.append(f"CHANGELOG.md needs a dated '## [{version}] - YYYY-MM-DD' entry")
    else:
        release_date = date.fromisoformat(match.group(1))
        if release_date > date.today():
            errors.append("changelog release date cannot be in the future")

    release_notes = ROOT / "docs" / "releases" / f"{tag}.md"
    if not release_notes.is_file() or not release_notes.read_text(encoding="utf-8").strip():
        errors.append(f"{release_notes.relative_to(ROOT)} must contain release notes")

    errors.extend(_plugin_errors(version))
    errors.extend(_install_reference_errors(version))

    if not allow_dirty:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if status:
            errors.append("release source tree is dirty or contains untracked files")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--allow-dirty", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    errors = validate(args.tag, allow_dirty=args.allow_dirty)
    for error in errors:
        print(f"release validation failed: {error}", file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
