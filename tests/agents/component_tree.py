"""Copy the Agent Routing source subtree of this repository into a synthetic checkout."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Every repository path that the Agent Routing installer, bootstrap, and doctor read
# from a source checkout.
COMPONENT_PATHS = (
    ".gitignore",
    "pyproject.toml",
    "src/pitwall/__init__.py",
    "src/pitwall/agents",
    "tools/__init__.py",
    "tools/agents",
    "plugins",
    "docs/agents",
    "docs/prompting",
    ".claude-plugin/marketplace.json",
    ".agents/plugins/marketplace.json",
    ".github/plugin/marketplace.json",
)
# The copied manifest carries this version so installer and bootstrap tests do not move with
# the product version.
FIXTURE_VERSION = "0.12.0"
_IGNORE = shutil.ignore_patterns(".venv", ".mypy_cache", ".ruff_cache", "__pycache__", "*.pyc")


def copy_component(target: Path) -> Path:
    """Copy ``COMPONENT_PATHS`` from the repository into *target* and return it."""

    for relative in COMPONENT_PATHS:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, ignore=_IGNORE)
        else:
            shutil.copy2(source, destination)
    manifest = target / "pyproject.toml"
    manifest.write_text(
        re.sub(
            r'^version = ".*"$',
            f'version = "{FIXTURE_VERSION}"',
            manifest.read_text(encoding="utf-8"),
            count=1,
            flags=re.M,
        ),
        encoding="utf-8",
    )
    return target
