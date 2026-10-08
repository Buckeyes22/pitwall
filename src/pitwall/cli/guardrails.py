"""CLI adapter for pre-spend guardrail status and bounded preview."""

from __future__ import annotations

import argparse

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.security.pre_spend import (
    PreSpendInspectionService,
    get_pre_spend_inspection_service,
    parse_pre_spend_json,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall guardrails",
        description="Inspect pre-spend guardrail status or preview a JSON payload.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    status = subcommands.add_parser(
        "status",
        help="Show configured rules, limits, counters, and last-decision metadata.",
    )
    add_json_argument(status)

    preview = subcommands.add_parser(
        "preview",
        help="Inspect one JSON payload without provider, database, audit, or counter writes.",
    )
    preview.add_argument("--payload", required=True, help="JSON value to inspect.")
    add_json_argument(preview)
    return parser.parse_args(argv)


def cmd_guardrails(
    argv: list[str],
    *,
    service: PreSpendInspectionService | None = None,
) -> int:
    """Run the guardrails command group."""
    args = _parse_args(argv)
    output = Output(json_mode(args))
    inspection = service or get_pre_spend_inspection_service()

    if args.command == "status":
        status = inspection.status().to_dict()
        if output.json_mode:
            output.set_json(status)
        else:
            counters = status["counters"]
            output.print(
                f"Guardrails: {status['mode']} | inspected {counters['total']} | "
                f"allow {counters['allow']} | redact {counters['redact']} | "
                f"block {counters['block']}"
            )
            output.print_table(
                "Guardrail rules",
                ["Rule", "Kind", "Balanced action", "Description"],
                [
                    [
                        rule["rule_id"],
                        rule["kind"],
                        rule["balanced_action"],
                        rule["description"],
                    ]
                    for rule in status["rules"]
                ],
            )
        output.emit()
        return 0

    try:
        payload = parse_pre_spend_json(
            args.payload,
            max_input_bytes=inspection.status().limits.max_input_bytes,
        )
    except ValueError as exc:
        output.print_error(str(exc))
        output.emit()
        return 2

    result = inspection.preview(payload).semantic_dict()
    if output.json_mode:
        output.set_json(result)
    else:
        output.print(
            f"Guardrail preview: {result['decision']} | "
            f"findings {len(result['findings'])} | "
            f"inspected {result['inspected_bytes']} bytes"
        )
        output.print_table(
            "Guardrail findings",
            ["Rule", "Kind", "Path", "Action", "Fingerprint"],
            [
                [
                    finding["rule"],
                    finding["kind"],
                    finding["path"],
                    finding["action"],
                    finding["fingerprint_sha256"],
                ]
                for finding in result["findings"]
            ],
        )
    output.emit()
    return 0


__all__ = ["cmd_guardrails"]
