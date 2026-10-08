"""Feature-local CLI adapter for the shared provider-operations service."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.db import get_pool
from pitwall.providers.service import ProviderOperationsService

ServiceFactory = Callable[[], Awaitable[ProviderOperationsService]]


def _provider_id(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise argparse.ArgumentTypeError("provider ID must be a non-empty string")
    return value


def _capability_id(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise argparse.ArgumentTypeError("capability ID must be a non-empty string")
    return value


def _bounded_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer between 1 and 100") from exc
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("limit must be between 1 and 100")
    return parsed


def _parse_provider_ops_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall provider-ops",
        description="Read safe provider descriptors, health, and bounded availability observations.",
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    listing = commands.add_parser("list", help="List safe persisted provider descriptors.")
    listing.add_argument("--capability-id", type=_capability_id)
    listing.add_argument("--enabled-only", action="store_true")
    listing.add_argument("--limit", type=_bounded_limit, default=100)
    add_json_argument(listing)

    describe = commands.add_parser("describe", help="Describe one persisted provider safely.")
    describe.add_argument("provider_id", type=_provider_id)
    add_json_argument(describe)

    availability = commands.add_parser(
        "availability",
        help="Perform one explicit bounded, read-only availability probe.",
    )
    availability.add_argument("provider_id", type=_provider_id)
    availability.add_argument("--limit", type=_bounded_limit, default=100)
    add_json_argument(availability)

    health = commands.add_parser(
        "health",
        help="Read persisted health; --probe makes one explicit live read.",
    )
    health.add_argument("provider_id", type=_provider_id)
    health.add_argument("--probe", action="store_true")
    add_json_argument(health)
    return parser.parse_args(argv)


async def get_provider_operations_service() -> ProviderOperationsService:
    """Build the common service; it resolves credentials only at its boundary."""
    return ProviderOperationsService(await get_pool())


class _ProviderOperationsNotFound(Exception):
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id


def cmd_provider_ops(
    argv: list[str],
    *,
    service_factory: ServiceFactory = get_provider_operations_service,
) -> int:
    """Run the registered provider-ops command group with machine-stable JSON."""
    args = _parse_provider_ops_args(argv)
    output = Output(json_mode(args))
    try:
        result = asyncio.run(_run(args, service_factory))
    except _ProviderOperationsNotFound as exc:
        payload = {"error": "provider_not_found", "id": exc.provider_id}
        output.set_json(payload)
        if not output.json_mode:
            output.print_error("provider_not_found")
        output.emit()
        return 2
    except ValueError as exc:
        del exc
        output.set_json({"error": "provider_operations_invalid_request"})
        if not output.json_mode:
            output.print_error("provider_operations_invalid_request")
        output.emit()
        return 2
    except Exception as exc:  # reason: provider/database exceptions can include secret material
        del exc
        output.set_json({"error": "provider_operations_unavailable"})
        if not output.json_mode:
            output.print_error("provider_operations_unavailable")
        output.emit()
        return 1

    if output.json_mode:
        output.set_json(result)
    else:
        _render_human(args.operation, result, output)
    output.emit()
    return 0


async def _run(
    args: argparse.Namespace,
    service_factory: ServiceFactory,
) -> dict[str, object]:
    service = await service_factory()
    if args.operation == "list":
        descriptors = await service.list_descriptors(
            capability_id=args.capability_id,
            enabled_only=args.enabled_only,
            limit=args.limit,
        )
        items = [descriptor.as_dict() for descriptor in descriptors]
        return {"items": items, "total": len(items)}
    if args.operation == "describe":
        descriptor = await service.describe(args.provider_id)
        if descriptor is None:
            raise _ProviderOperationsNotFound(args.provider_id)
        return descriptor.as_dict()
    if args.operation == "availability":
        availability = await service.availability(args.provider_id, limit=args.limit)
        if availability is None:
            raise _ProviderOperationsNotFound(args.provider_id)
        return availability.as_dict()
    if args.operation == "health":
        health = await service.health(args.provider_id, probe=args.probe)
        if health is None:
            raise _ProviderOperationsNotFound(args.provider_id)
        return health.as_dict()
    raise AssertionError(f"unexpected provider-ops operation: {args.operation!r}")


def _render_human(operation: str, result: dict[str, object], output: Output) -> None:
    if operation == "list":
        items = _items(result)
        output.print_panel(f"{result['total']} provider descriptor(s)", title="Provider operations")
        output.print_table(
            "Providers",
            ["Provider ID", "Adapter", "Persisted health", "Credential set", "Capabilities"],
            [
                [
                    item["provider_id"],
                    item["adapter_id"],
                    item["persisted_health"],
                    "yes" if item["credential_configured"] else "no",
                    ", ".join(_strings(item.get("capabilities"))),
                ]
                for item in items
            ],
        )
        return
    if operation == "describe":
        output.print_panel(
            "\n".join(
                (
                    f"Provider: {result['provider_id']}",
                    f"Adapter: {result['adapter_id']}",
                    f"Persisted health: {result['persisted_health']}",
                    f"Credential configured: {'yes' if result['credential_configured'] else 'no'}",
                    f"Capabilities: {', '.join(_strings(result.get('capabilities')))}",
                )
            ),
            title="Provider descriptor",
        )
        return
    if operation == "availability":
        output.print_panel(
            "\n".join(
                (
                    f"Provider: {result['provider_id']}",
                    f"Status: {result['status']}",
                    f"Observed at: {result['observed_at']}",
                    f"Source contract: {result['source_contract'] or 'unavailable'}",
                    f"Error code: {result['error_code'] or 'none'}",
                )
            ),
            title="Provider availability",
            border_style="green" if result["status"] in {"available", "empty"} else "yellow",
        )
        output.print_table(
            "Availability",
            ["Resource", "Kind", "Available", "Region", "Accelerator", "Pricing"],
            [
                [
                    item["resource_id"],
                    item["kind"],
                    item["available"],
                    item["region"] or "",
                    item["accelerator"] or "",
                    ", ".join(
                        f"{name}={value}" for name, value in _mapping(item.get("pricing")).items()
                    ),
                ]
                for item in _items(result)
            ],
        )
        return
    if operation == "health":
        output.print_panel(
            "\n".join(
                (
                    f"Provider: {result['provider_id']}",
                    f"Persisted health: {result['persisted_health']}",
                    f"Live status: {result['live_status']}",
                    f"Availability sample: {result['availability_count'] if result['availability_count'] is not None else 'not probed'}",
                    f"Error code: {result['error_code'] or 'none'}",
                )
            ),
            title="Provider health",
            border_style="green"
            if result["live_status"] in {"healthy", "not_probed"}
            else "yellow",
        )
        return
    raise AssertionError(f"unexpected provider-ops operation: {operation!r}")


def _items(result: dict[str, object]) -> list[dict[str, Any]]:
    value = result.get("items", [])
    return value if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


__all__ = ["cmd_provider_ops", "get_provider_operations_service"]
