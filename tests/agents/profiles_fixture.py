"""Write and read the ``[agents.profiles]`` tables of a test pitwall.toml."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pitwall.agents import profiles_toml


def write_profiles(path: Path, document: Mapping[str, Any]) -> Path:
    """Write ``document`` (the profiles shape, e.g. ``{"models": {...}}``) to ``path`` as TOML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"defaults": {}, **document}
    path.write_text(profiles_toml.render(body), encoding="utf-8")
    return path


def read_profiles(path: Path) -> dict[str, Any]:
    """The parsed ``[agents.profiles]`` table of ``path``."""
    profiles: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))["agents"]["profiles"]
    return profiles
