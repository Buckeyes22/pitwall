"""Feature-local CLI renderer for the shared RunPod market read."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.runpod_market import (
    RunpodMarketRead,
    RunpodMarketService,
    build_configured_runpod_market_service,
)

MarketServiceFactory = Callable[[], RunpodMarketService]


def add_runpod_catalogue_parser(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the catalogue leaf in the shared ``runpod`` command tree."""

    parser = commands.add_parser(
        "catalogue",
        help="Read catalogue, availability, prices, balance, and billing support.",
        description="Read RunPod catalogue, availability, prices, balance, and billing support.",
    )
    _add_runpod_catalogue_arguments(parser)
    return parser


def _add_runpod_catalogue_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--refresh", action="store_true", help="Force one provider refresh.")
    add_json_argument(parser)


def cmd_runpod_catalogue(
    argv: list[str],
    *,
    service_factory: MarketServiceFactory = build_configured_runpod_market_service,
) -> int:
    """Run one cached/forced market read and close the local clients."""

    parser = argparse.ArgumentParser(
        prog="pitwall runpod catalogue",
        description="Read RunPod catalogue, availability, prices, balance, and billing support.",
    )
    _add_runpod_catalogue_arguments(parser)
    args = parser.parse_args(argv)
    output = Output(json_mode(args))

    async def run() -> int:
        service = service_factory()
        try:
            return await run_runpod_catalogue_command(
                service,
                force_refresh=args.refresh,
                output=output,
            )
        finally:
            await service.aclose()

    return asyncio.run(run())


async def run_runpod_catalogue_command(
    service: RunpodMarketService,
    *,
    force_refresh: bool,
    output: Output,
) -> int:
    """Render one cached or forced catalogue read and return a stable exit code."""

    snapshot = await service.read(force_refresh=force_refresh)
    if output.json_mode:
        output.set_json(snapshot.to_serializable_dict())
        output.emit()
        return 0
    _render_human(snapshot, output=output)
    return 0


def _render_human(snapshot: RunpodMarketRead, *, output: Output) -> None:
    output.print(
        "RunPod catalogue: "
        f"{snapshot.state} | age {snapshot.age_seconds}s | "
        f"{'cache hit' if snapshot.cache_hit else 'refreshed'}"
    )
    output.print_table(
        "RunPod GPUs",
        ["GPU", "Secure $/GPU/hr", "Community $/GPU/hr", "Minimum bid", "Stock"],
        [
            [
                row.gpu_type_id,
                _price(row.graphql.secure_price if row.graphql else None),
                _price(row.graphql.community_price if row.graphql else None),
                _price(row.graphql.lowest_bid_price if row.graphql else None),
                row.graphql.stock_status if row.graphql else "unavailable",
            ]
            for row in snapshot.gpus
        ],
        caption="Live values are cached; REST v2 catalogue fields remain separately sourced.",
    )
    output.print_table(
        "RunPod Datacenters",
        ["Datacenter", "Location", "GPU types"],
        [
            [item.datacenter_id, item.location or "", len(item.gpu_types)]
            for item in snapshot.datacenters
        ],
    )
    if snapshot.balance is None:
        output.print_warning("RunPod credit balance unavailable")
    else:
        output.print_panel(
            f"Balance: ${snapshot.balance.client_balance_usd} USD\n"
            f"Current spend: {_price(snapshot.balance.current_spend_per_hr_usd)}/hr\n"
            f"Under balance: {'yes' if snapshot.balance.under_balance else 'no'}",
            title="RunPod credit",
        )
    output.print_table(
        "RunPod Billing Categories",
        ["Category", "History", "Workload actual", "State"],
        [
            [
                item.category,
                "supported" if item.history_read_supported else "unavailable",
                "supported" if item.workload_actual_supported else "unavailable",
                item.actual_state,
            ]
            for item in snapshot.billing_categories
        ],
    )


def _price(value: object | None) -> str:
    return str(value) if value is not None else "unavailable"


__all__ = [
    "add_runpod_catalogue_parser",
    "cmd_runpod_catalogue",
    "run_runpod_catalogue_command",
]
