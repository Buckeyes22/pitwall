"""``pitwall config``."""

from __future__ import annotations

import argparse
import os

from pitwall.cli.output import Output, add_json_argument, safe_json
from pitwall.cli.output import json_mode as _json_mode


def _parse_config_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall config",
        description="Inspect and validate Pitwall runtime configuration.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser(
        "check",
        help="Validate boot-time configuration for a service.",
    )
    check.add_argument(
        "service",
        nargs="?",
        default="api",
        help="Service name to validate (default: api).",
    )
    add_json_argument(check)
    return parser.parse_args(argv)


def cmd_config(argv: list[str]) -> int:
    from pitwall.config import (
        check_domain_config,
        format_config_check_result,
        format_settings_load_error,
    )

    args = _parse_config_args(argv)
    out = Output(_json_mode(args))
    if args.command != "check":
        return 1
    try:
        result = check_domain_config(args.service)
    except ValueError as exc:  # ConfigFileError, including settings that fail validation
        err_msg = format_settings_load_error(exc)
        out.print_error(err_msg)
        out.add_json("error", err_msg)
        out.emit()
        return os.EX_CONFIG

    report = format_config_check_result(result)
    if result.errors:
        out.print_error(report)
        out.add_json("errors", [safe_json(e) for e in result.errors])
        out.emit()
        return os.EX_CONFIG
    out.print_success(report)
    out.add_json("service", result.service)
    out.add_json("status", "ok")
    out.emit()
    return 0
