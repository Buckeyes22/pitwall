#!/usr/bin/env python3
"""Generate host-specific route catalogs from the canonical harness registry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGEN_COMMAND = "uv run --frozen python tools/agents/sync_routes.py"
sys.path.insert(0, str(ROOT / "src"))

from pitwall.agents import (  # noqa: E402  # reason: sys.path bootstrap
    route_assets,
)
from pitwall.agents.registry import (  # noqa: E402  # reason: sys.path bootstrap
    RegistryError,
    load_registry,
)
from pitwall.agents.route_assets import (  # noqa: E402  # reason: sys.path bootstrap
    _display_routes,  # noqa: F401  # reason: re-exported for the generator tests
)


def generated_files(
    registry: dict[str, Any],
    catalog: dict[str, Any] | None = None,
    *,
    root: Path = ROOT,
) -> dict[Path, str]:
    return route_assets.generated_files(registry, catalog, root=root)


def synchronize(*, check: bool) -> list[Path]:
    registry = load_registry()
    changed: list[Path] = []
    for path, content in generated_files(registry).items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        changed.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing stale outputs"
    )
    args = parser.parse_args(argv)
    try:
        changed = synchronize(check=args.check)
    except RegistryError as exc:
        print(f"cannot generate routes: {exc}", file=sys.stderr)
        return 1
    if changed:
        if args.check:
            for path in changed:
                print(f"stale generated route asset: {path.relative_to(ROOT)}", file=sys.stderr)
            print(
                f"fix: run `{REGEN_COMMAND}` (or `make regen`) and commit the result",
                file=sys.stderr,
            )
            return 1
        for path in changed:
            print(f"generated {path.relative_to(ROOT)}")
    else:
        print("generated route assets are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
