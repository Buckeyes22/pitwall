"""Production planner and job lifecycle CLI command group."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from typing import Any

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.config import get_settings
from pitwall.core.models import Workload
from pitwall.cost import BudgetRejected
from pitwall.db import get_pool
from pitwall.routing.production import (
    ProductionRoutingService,
    RouteBudgetQuote,
    RouteGuardrailRejected,
    RoutePlanningError,
    RoutingOperation,
)

ServiceFactory = Callable[[Any], ProductionRoutingService]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pitwall routing",
        description="Preview deterministic routes and operate provider-neutral async jobs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="Preview a no-egress production route.")
    _request_args(plan)
    plan.add_argument(
        "--operation",
        choices=[item.value for item in RoutingOperation],
        default=RoutingOperation.SYNC_INFERENCE.value,
    )
    add_json_argument(plan)

    submit = commands.add_parser("submit", help="Submit an asynchronous job.")
    _request_args(submit)
    submit.add_argument("--idempotency-key")
    submit.add_argument("--webhook-url")
    submit.add_argument("--dry-run", action="store_true")
    submit.add_argument(
        "--confirm",
        help="Capability name/ID required for a spend-bearing submission.",
    )
    add_json_argument(submit)

    for command, help_text in (
        ("status", "Read persisted job status."),
        ("result", "Read a persisted job result."),
        ("follow", "Poll a job for a bounded number of attempts."),
    ):
        child = commands.add_parser(command, help=help_text)
        child.add_argument("workload_id")
        if command == "follow":
            child.add_argument("--max-polls", type=int, choices=range(1, 101), default=20)
            child.add_argument("--interval", type=float, default=1.0)
        add_json_argument(child)

    cancel = commands.add_parser("cancel", help="Cancel a queued or running job.")
    cancel.add_argument("workload_id")
    cancel.add_argument("--confirm", help="Workload ID required for cancellation.")
    add_json_argument(cancel)
    return parser


def _request_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("capability_id")
    parser.add_argument("--provider-id")
    parser.add_argument(
        "--input-json",
        default="{}",
        help="JSON object passed to the capability; values are never shown in a plan.",
    )


def cmd_routing(
    argv: list[str],
    *,
    service_factory: ServiceFactory | None = None,
) -> int:
    """Run planner preview, submission, or bounded job lifecycle commands."""

    args = _parser().parse_args(argv)
    output = Output(json_mode(args))
    try:
        result = asyncio.run(_run(args, service_factory=service_factory))
        _render(output, args.command, result)
    except ValueError as exc:
        output.print_error(str(exc))
        output.emit()
        return 2
    except RouteGuardrailRejected:
        output.print_error("pre_spend_payload_rejected")
        output.emit()
        return 2
    except (LookupError, RoutePlanningError, BudgetRejected) as exc:
        output.print_error(str(exc))
        output.emit()
        return 1
    except Exception as exc:  # reason: stable boundary must not emit provider detail
        from pitwall.cli.runtime_errors import report_failure, runtime_reason

        if runtime_reason(exc):
            report_failure(output, "routing_operation_failed", exc)
        else:
            output.print_error(f"routing operation failed ({exc.__class__.__name__})")
        output.emit()
        return 1
    output.emit()
    return 0


async def _run(
    args: argparse.Namespace,
    *,
    service_factory: ServiceFactory | None,
) -> dict[str, object]:
    pool = await get_pool()
    service = (
        service_factory(pool)
        if service_factory is not None
        else ProductionRoutingService(pool, settings=get_settings())
    )
    if args.command in {"plan", "submit"}:
        payload = _json_object(args.input_json)
        if args.command == "plan":
            return (
                await service.preview(
                    capability_id=args.capability_id,
                    payload=payload,
                    operation=RoutingOperation(args.operation),
                    provider_id=args.provider_id,
                )
            ).to_dict()
        async_payload = payload
        if args.dry_run:
            plan = await service.preview(
                capability_id=args.capability_id,
                payload=async_payload,
                operation=RoutingOperation.ASYNC_INFERENCE,
                provider_id=args.provider_id,
            )
            return {
                "dry_run": True,
                "workload_id": f"dry_run_job_{plan.plan_id.removeprefix('plan_')[:16]}",
                "state": "queued",
                "provider_id": plan.selected_provider_id,
                "plan": plan.to_dict(),
                "cost": RouteBudgetQuote(plan, fallback_spend=False).to_serializable_dict(),
            }
        if args.confirm != args.capability_id:
            raise ValueError("--confirm must exactly match capability_id before submission")
        submitted_workload = await service.submit_job(
            capability_id=args.capability_id,
            payload=async_payload,
            provider_id=args.provider_id,
            idempotency_key=args.idempotency_key,
            webhook_url=args.webhook_url,
        )
        return _workload_dict(submitted_workload)

    if args.command == "cancel":
        if args.confirm != args.workload_id:
            raise ValueError("--confirm must exactly match workload_id before cancellation")
        return _workload_dict(await service.cancel_job(args.workload_id))
    if args.command == "follow":
        if not 0 <= args.interval <= 60:
            raise ValueError("--interval must be between 0 and 60 seconds")
        return await _follow(
            service,
            args.workload_id,
            max_polls=args.max_polls,
            interval=args.interval,
        )
    if args.command == "result":
        return (await service.job_result(args.workload_id)).to_dict()
    workload = await service.get_job(args.workload_id)
    return _workload_dict(workload)


async def _follow(
    service: ProductionRoutingService,
    workload_id: str,
    *,
    max_polls: int,
    interval: float,
) -> dict[str, object]:
    terminal = {"completed", "failed", "cancelled", "timed_out"}
    workload: Workload | None = None
    for poll in range(1, max_polls + 1):
        workload = await service.get_job(workload_id)
        if _value(workload.state) in terminal:
            data = _workload_dict(workload)
            data["polls"] = poll
            data["follow_complete"] = True
            return data
        if poll < max_polls:
            await asyncio.sleep(interval)
    assert workload is not None
    data = _workload_dict(workload)
    data["polls"] = max_polls
    data["follow_complete"] = False
    return data


def _workload_dict(workload: Workload) -> dict[str, object]:
    return {
        "workload_id": workload.id,
        "state": _value(workload.state),
        "provider_id": workload.provider_id,
        "external_job_id": workload.external_job_id,
        "plan_id": workload.route_plan_id,
        "plan": workload.route_plan,
        "cost": workload.cost_quote,
        "result": workload.result,
        "error": workload.error,
    }


def _render(output: Output, command: str, result: dict[str, object]) -> None:
    if output.json_mode:
        output.set_json(result)
        return
    if command == "plan":
        fallback_chain = result["fallback_chain"]
        if not isinstance(fallback_chain, list):
            raise RuntimeError("route plan fallback_chain is invalid")
        output.print_table(
            "Production route",
            ["Plan", "Capability", "Provider", "Mode", "Fallbacks"],
            [
                [
                    result["plan_id"],
                    result["capability_name"],
                    result["selected_provider_id"],
                    result["mode"],
                    " -> ".join(str(item) for item in fallback_chain),
                ]
            ],
        )
        return
    plan = result.get("plan")
    nested_plan_id = plan.get("plan_id", "") if isinstance(plan, dict) else ""
    output.print_table(
        "Job",
        ["Workload", "State", "Provider", "Plan"],
        [
            [
                result.get("workload_id", ""),
                result.get("state", ""),
                result.get("provider_id", ""),
                result.get("plan_id") or nested_plan_id,
            ]
        ],
    )


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("--input-json must be valid JSON") from exc
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ValueError("--input-json must be a JSON object")
    return parsed


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = ["cmd_routing"]
