"""pitwall budget: show and change runtime budget limits without a restart."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.cli.runtime_errors import report_failure
from pitwall.cost.budget_limits import BudgetLimitsError, budget_status, set_limits
from pitwall.db import get_pool

_BUDGET_UNAVAILABLE = "budget_unavailable"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall budget", description="Show or change the runtime budget limits."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    show = subcommands.add_parser(
        "show", help="Show effective limits, month-to-date spend, and remaining budget."
    )
    add_json_argument(show)
    change = subcommands.add_parser(
        "set", help="Change the monthly budget and/or per-request cap (audited)."
    )
    change.add_argument("--monthly", dest="monthly", help="Monthly budget in USD")
    change.add_argument("--per-request", dest="per_request", help="Per-request cap in USD")
    change.add_argument(
        "--reason", required=True, help="Why the limit changes (recorded in config_audit)"
    )
    add_json_argument(change)
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    pool = await get_pool()
    if args.command == "set":
        await set_limits(
            pool,
            monthly_budget_usd=args.monthly,
            per_request_max_usd=args.per_request,
            reason=args.reason,
            actor="cli",
        )
    return await budget_status(pool)


def cmd_budget(argv: list[str]) -> int:
    args = _parse_args(argv)
    output = Output(json_mode(args))
    try:
        status = asyncio.run(_run(args))
    except BudgetLimitsError as exc:
        output.print(f"pitwall budget: {exc}")
        output.emit()
        return 2
    except Exception as exc:  # reason: CLI boundary: one line naming the fix, never a traceback
        report_failure(output, _BUDGET_UNAVAILABLE, exc)
        output.emit()
        return 1
    if output.json_mode:
        output.set_json(status)
    else:
        output.print(
            f"Budget ({status['source']}): monthly {status['monthly_budget_usd']} USD, per-request cap "
            f"{status['per_request_max_usd']} USD, spent {status['mtd_spend_usd']} USD, remaining {status['budget_remaining_usd']} USD"
        )
    output.emit()
    return 0


__all__ = ["cmd_budget"]
