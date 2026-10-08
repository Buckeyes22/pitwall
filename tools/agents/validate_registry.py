#!/usr/bin/env python3
"""Validate the canonical harness registry without third-party dependencies."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pitwall.agents.catalog import (  # noqa: E402  # reason: module setup (ROOT or sys.path) must run before the package imports
    CatalogError,
    load_catalog,
)
from pitwall.agents.registry import (  # noqa: E402  # reason: module setup (ROOT or sys.path) must run before the package imports
    RegistryError,
    load_registry,
    validate_source_layout,
)


def main() -> int:
    try:
        registry = load_registry()
        validate_source_layout(registry, repo_root=ROOT)
    except RegistryError as exc:
        print(f"harness registry invalid: {exc}", file=sys.stderr)
        return 1
    try:
        catalog = load_catalog(registry=registry)
    except CatalogError as exc:
        print(f"model catalog invalid: {exc}", file=sys.stderr)
        return 1
    print(
        f"harness registry valid: {len(registry['harnesses'])} harnesses, "
        f"{len(registry['hosts'])} hosts, {len(catalog['models'])} catalog models"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
