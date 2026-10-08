"""``pitwall doctor``: print the installation readiness report."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Iterable
from dataclasses import replace

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.doctor import (
    DEFAULT_TIMEOUT_S,
    DoctorCheck,
    run_doctor,
    run_registered_sections,
)


def _parse_doctor_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall doctor",
        description="Check install, configuration, services, and spend controls; exit 1 on any failure.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument(
        "--api-url", help="API base URL (default: PITWALL_API_URL or http://127.0.0.1:8080)."
    )
    parser.add_argument(
        "--canary",
        metavar="CAPABILITY",
        help="Also send a dry-run inference to this embedding capability.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="Per-probe timeout in seconds (default: 5).",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


def _print_checks(out: Output, checks: Iterable[DoctorCheck]) -> None:
    for check in checks:
        out.print(f"{f'[{check.status}]':<6} {check.id}: {check.detail}", soft_wrap=True)
        if check.next_step:
            out.print(f"       next: {check.next_step}", soft_wrap=True)


def cmd_doctor(argv: list[str]) -> int:
    args = _parse_doctor_args(argv)
    out = Output(json_mode(args))
    report = asyncio.run(
        run_doctor(
            environ=os.environ, api_url=args.api_url, canary=args.canary, timeout_s=args.timeout
        )
    )
    report = replace(report, sections=run_registered_sections())
    if out.json_mode:
        out.set_json(report.to_dict())
    else:
        # One unwrapped line per check: agents read this through 80-column pipes, and
        # the check ids are the contract docs/agents/install.md points them at.
        _print_checks(out, report.checks)
        for section in report.sections:
            out.print(f"== {section.name}: {section.status}", soft_wrap=True)
            _print_checks(out, section.checks)
        summary = report.to_dict()["summary"]
        counts = ", ".join(f"{summary[name]} {name}" for name in ("ok", "warn", "fail", "skip"))
        out.print(
            f"doctor: {report.status} ({counts}), mode {report.mode}, pitwall {report.version}",
            soft_wrap=True,
        )
    out.emit()
    return report.exit_code(strict=args.strict)
