"""``pitwall leases``."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from pitwall.cli.args import guard_cli_pre_spend
from pitwall.cli.output import Output, add_json_argument, safe_json
from pitwall.cli.output import json_mode as _json_mode


def _parse_leases_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="pitwall leases")
    subcommands = parser.add_subparsers(dest="command", required=True)
    list_parser = subcommands.add_parser("list", help="List active pod leases.")
    add_json_argument(list_parser)

    stop_parser = subcommands.add_parser("stop", help="Stop a pod lease and terminate its pod.")
    stop_parser.add_argument("lease_id", help="Lease ID to stop.")
    stop_parser.add_argument(
        "--reason",
        default="operator",
        help="Termination reason recorded on the lease (default: operator).",
    )
    stop_parser.add_argument("--json", action="store_true", help="Emit JSON.")

    renew_parser = subcommands.add_parser("renew", help="Extend a pod lease.")
    renew_parser.add_argument("lease_id", help="Lease ID to renew.")
    renew_parser.add_argument(
        "--extends-minutes",
        type=int,
        default=60,
        help="Minutes to extend the lease by (1-43200, default: 60).",
    )
    renew_parser.add_argument("--json", action="store_true", help="Emit JSON.")
    return parser.parse_args(argv)


async def _leases_source() -> Any:
    from pitwall.db import get_pool
    from pitwall.tui.leases import PostgresLeasesSource

    return PostgresLeasesSource(await get_pool())


async def _leases_list_async(args: argparse.Namespace, out: Output) -> int:
    source = await _leases_source()
    snapshot = await source.load_leases()
    if out.json_mode:
        out.set_json(
            {
                "items": [row.to_dict() for row in snapshot.rows],
                "total": snapshot.active_count,
                "refreshed_at": snapshot.refreshed_at.isoformat(),
            }
        )
    else:
        out.print("Columns: Last traffic | Idle | Policy | Max $/h")
        out.print_table(
            "Active leases",
            ["Lease", "State", "Last traffic", "Idle", "Policy", "Max $/h", "Expires"],
            [
                [
                    row.lease_id,
                    row.state_label,
                    row.last_traffic_label,
                    row.idle_timeout_label,
                    row.renewal_policy,
                    row.max_usd_per_hour_label,
                    row.expires_label,
                ]
                for row in snapshot.rows
            ],
            keep_whole=("Lease",),
        )
    out.emit()
    return 0


async def _leases_stop_async(args: argparse.Namespace, out: Output) -> int:
    from pitwall.api.leases import run_teardown
    from pitwall.db import get_pool
    from pitwall.redis_env import optional_redis_from_env

    guard_cli_pre_spend({"lease_id": args.lease_id, "reason": args.reason})
    async with optional_redis_from_env() as redis_client:
        result = await run_teardown(
            args.lease_id,
            pool=await get_pool(),
            redis_client=redis_client,
            reason="operator",
            terminated_reason=args.reason,
        )
    lease = result.lease
    if out.json_mode:
        out.set_json(safe_json(lease))
    else:
        out.print(f"Lease {lease.id}: {lease.state}")
    out.emit()
    return 0


async def _leases_renew_async(args: argparse.Namespace, out: Output) -> int:
    from pitwall.db import get_pool
    from pitwall.redis_env import optional_redis_from_env
    from pitwall.registry_admin import renew_operator_lease

    guard_cli_pre_spend({"lease_id": args.lease_id, "extends_minutes": args.extends_minutes})
    pool = await get_pool()
    async with optional_redis_from_env() as redis_client:
        lease = await renew_operator_lease(pool, redis_client, args.lease_id, args.extends_minutes)
    if out.json_mode:
        out.set_json(safe_json(lease))
    else:
        out.print(f"Lease {lease.id}: {lease.state}")
    out.emit()
    return 0


def cmd_leases(argv: list[str]) -> int:
    args = _parse_leases_args(argv)
    out = Output(_json_mode(args))
    try:
        if args.command == "list":
            return asyncio.run(_leases_list_async(args, out))
        if args.command == "stop":
            return asyncio.run(_leases_stop_async(args, out))
        if args.command == "renew":
            return asyncio.run(_leases_renew_async(args, out))
        raise AssertionError(f"unhandled leases command: {args.command}")
    except Exception as exc:  # reason: CLI lease errors must never reflect request/provider text.
        code = _lease_error_code(exc)
        from pitwall.cli.runtime_errors import report_failure

        report_failure(out, code, exc, fallback=f"error: lease operation failed: {code}")
        out.emit()
        return 1


def _lease_error_code(exc: Exception) -> str:
    """The stable code REST and MCP report for a lease lifecycle error, else a generic one."""
    from pitwall.api.exceptions import LeaseNotFound, LeaseStateConflict
    from pitwall.api.leases.teardown import TeardownFailed
    from pitwall.leases.mutations import (
        LeaseMutationConflict,
        LeaseMutationExpiryLimitExceeded,
        LeaseMutationIdempotencyConflict,
        LeaseMutationNotFound,
    )

    if isinstance(exc, LeaseNotFound | LeaseMutationNotFound):
        return "lease_not_found"
    if isinstance(exc, LeaseStateConflict | LeaseMutationConflict):
        return "lease_state_conflict"
    if isinstance(exc, LeaseMutationExpiryLimitExceeded):
        return "lease_expiry_limit_exceeded"
    if isinstance(exc, LeaseMutationIdempotencyConflict):
        return "idempotency_conflict"
    if isinstance(exc, TeardownFailed):
        return "teardown_failed"
    # A renewal refused by the budget, or with no budget configured, keeps the gate's code.
    budget_code = getattr(type(exc), "error_code", None)
    if budget_code in {"budget_rejected", "budget_not_configured"}:
        return str(budget_code)
    return "lease_operation_failed"
