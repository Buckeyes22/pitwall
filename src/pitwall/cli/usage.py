"""``pitwall usage``: subscription usage for every routed plan, and ``usage serve``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pitwall.agents import usage
from pitwall.agents.errors import EX_CONFIG
from pitwall.agents.profiles import ProfilesError, load_profiles
from pitwall.agents.registry import RegistryError, load_registry
from pitwall.agents.usage import serve as usage_serve


def _parse_usage_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall usage",
        description="Show subscription usage for every routed plan.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    commands = parser.add_subparsers(dest="usage_command", required=False)
    serve = commands.add_parser(
        "serve", help="Publish usage over HTTP for a desk meter, sampling on an interval."
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8848)
    serve.add_argument("--interval", type=float, default=45.0, metavar="SECONDS")
    return parser.parse_args(argv)


def _home_dir() -> Path:
    """The invoking user's home directory: ``$HOME``, else the account's home."""
    environment: Mapping[str, str] = os.environ
    return Path(environment.get("HOME", "~")).expanduser()


def _routes_context() -> tuple[dict[str, Any], dict[str, Any], Path]:
    registry = load_registry()
    return registry, load_profiles(os.environ, registry=registry), _home_dir()


def _show(json_output: bool) -> int:
    try:
        registry, config, home = _routes_context()
    except (ProfilesError, RegistryError) as exc:
        print(f"pitwall usage: {exc}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    rows = usage.collect(os.environ, registry=registry, routes_config=config, home=home, now=now)
    if json_output:
        print(
            json.dumps(
                {"observed_at": usage.iso_utc(now), "plans": [row.to_dict() for row in rows]},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        sys.stdout.write(usage.render_table(rows, now=now))
    return 0


def _serve(host: str, port: int, interval: float) -> int:
    def collect_rows(now: datetime) -> list[usage.Row]:
        # Routes are read again on every refresh, so a route added later shows up without a restart.
        registry, config, home = _routes_context()
        return usage.collect(
            os.environ, registry=registry, routes_config=config, home=home, now=now
        )

    try:
        usage_serve.run(host, port, interval, os.environ, collect_rows)
    except usage_serve.ServeConfigError as exc:
        print(f"pitwall usage: {exc}", file=sys.stderr)
        return EX_CONFIG
    except OSError as exc:
        print(f"pitwall usage: cannot serve on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_usage(argv: list[str]) -> int:
    args = _parse_usage_args(argv)
    if args.usage_command == "serve":
        return _serve(args.host, args.port, args.interval)
    return _show(args.json_output)
