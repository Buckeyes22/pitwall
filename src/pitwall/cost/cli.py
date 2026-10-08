"""The ``pitwall cost`` command group: ``summary`` and ``workloads`` reads of persisted cost.

Owns its argument parsing and the ``cmd_cost`` entry point that ``pitwall.cli`` dispatches to.
It reads through the shared cost read models; money is printed and serialised as Decimal
strings. A failed read prints a fixed message, never the exception text.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from typing import Any

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.core.cost_reporting import cost_summary_read, recent_workloads_read
from pitwall.db import get_pool

_COST_UNAVAILABLE = "cost_unavailable"


async def show_cost_summary(
    pool: Any,
    output: Output,
    *,
    capability_class: str | None = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> None:
    """Load and render the shared aggregate cost read model."""

    result = await cost_summary_read(
        pool,
        capability_class=capability_class,
        since=since,
        until=until,
    )
    if output.json_mode:
        output.set_json(result.to_legacy_serializable_dict())
        return

    output.print_table(
        "Cost Summary",
        ["Day", "Capability", "Provider type", "Workloads", "Cost (USD)"],
        [
            [
                entry.day.isoformat(),
                entry.capability_class,
                entry.provider_type,
                entry.workload_count,
                str(entry.cost_usd),
            ]
            for entry in result.entries
        ],
        caption=f"Total USD: {result.total_usd}",
    )


async def show_recent_workload_costs(
    pool: Any,
    output: Output,
    *,
    capability_id: str | None = None,
    provider_id: str | None = None,
    provider_type: str | None = None,
    state: str | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    limit: int = 20,
) -> None:
    """Load and render one shared estimate/actual cost model per workload."""

    result = await recent_workloads_read(
        pool,
        capability_id=capability_id,
        provider_id=provider_id,
        provider_type=provider_type,
        state=state,
        since=since,
        until=until,
        limit=limit,
    )
    if output.json_mode:
        output.set_json(result.to_legacy_serializable_dict())
        return

    output.print_table(
        "Recent Workload Costs",
        ["Workload", "Estimate", "Ceiling", "Confidence", "Actual", "Reconciliation"],
        [
            [
                item.fields.get("id", ""),
                _money(item.cost.estimate),
                _money(item.cost.ceiling),
                item.cost.confidence,
                _money(item.cost.actual),
                item.cost.reconciliation_status,
            ]
            for item in result.workloads
        ],
    )


def _money(value: object) -> str:
    return "-" if value is None else str(value)


def _iso_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO date (YYYY-MM-DD)") from exc


def _iso_datetime(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a UTC offset")
    return parsed.astimezone(dt.UTC)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall cost",
        description="Read Decimal-authoritative persisted cost data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    summary = commands.add_parser("summary", help="Show daily aggregate cost.")
    summary.add_argument("--capability-class")
    summary.add_argument("--since", type=_iso_date)
    summary.add_argument("--until", type=_iso_date)
    add_json_argument(summary)

    workloads = commands.add_parser(
        "workloads",
        help="Show workload estimate, ceiling, actual, and reconciliation state.",
    )
    workloads.add_argument("--capability-id")
    workloads.add_argument("--provider-id")
    workloads.add_argument("--provider-type")
    workloads.add_argument("--state")
    workloads.add_argument("--since", type=_iso_datetime)
    workloads.add_argument("--until", type=_iso_datetime)
    workloads.add_argument("--limit", type=int, choices=range(1, 101), default=20)
    add_json_argument(workloads)
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace, output: Output) -> None:
    pool = await get_pool()
    if args.command == "summary":
        await show_cost_summary(
            pool,
            output,
            capability_class=args.capability_class,
            since=args.since,
            until=args.until,
        )
        return
    await show_recent_workload_costs(
        pool,
        output,
        capability_id=args.capability_id,
        provider_id=args.provider_id,
        provider_type=args.provider_type,
        state=args.state,
        since=args.since,
        until=args.until,
        limit=args.limit,
    )


def cmd_cost(argv: list[str]) -> int:
    """Run the shared cost summary/workload command group."""
    args = _parse_args(argv)
    output = Output(json_mode(args))
    try:
        asyncio.run(_run(args, output))
    except Exception as exc:  # reason: persistence/config failures can contain secret material
        del exc
        output.set_json({"error": _COST_UNAVAILABLE})
        if not output.json_mode:
            output.print_error(_COST_UNAVAILABLE)
        output.emit()
        return 1
    output.emit()
    return 0


__all__ = ["cmd_cost", "show_cost_summary", "show_recent_workload_costs"]
