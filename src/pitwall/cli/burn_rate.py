"""``pitwall burn-rate``: CLI adapter for the persisted burn-rate read model."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from decimal import Decimal

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.db import get_pool
from pitwall.finops.burn_rate import BurnRateRead, read_configured_burn_rate

_BURN_RATE_UNAVAILABLE = "burn_rate_unavailable"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _window_days(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 366:
        raise argparse.ArgumentTypeError("window days must be between 1 and 366")
    return parsed


def _parse_burn_rate_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pitwall burn-rate",
        description="Read the monthly budget burn-rate forecast from persisted UTC daily rollups.",
    )
    parser.add_argument(
        "--window-days",
        type=_window_days,
        default=30,
        help="UTC daily-rollup observation window (1-366; default: 30).",
    )
    add_json_argument(parser)
    return parser.parse_args(argv)


async def _read_burn_rate(args: argparse.Namespace) -> BurnRateRead:
    pool = await get_pool()
    return await read_configured_burn_rate(
        pool,
        now=_utc_now(),
        window_days=args.window_days,
    )


def _money(value: Decimal | None) -> str:
    return f"${value:.2f}" if value is not None else "unavailable"


def _datetime_label(value: dt.datetime | None) -> str:
    return value.isoformat().replace("+00:00", "Z") if value is not None else "not projected"


def _days_label(value: Decimal | None) -> str:
    return f"{value:.1f} days" if value is not None else "unavailable"


def _render_human(result: BurnRateRead, out: Output) -> None:
    percent = f"{result.percent_consumed:.1f}%" if result.percent_consumed is not None else "n/a"
    freshness = "stale" if result.stale else "fresh"
    last_rollup = result.last_rollup_day.isoformat() if result.last_rollup_day else "none"
    out.print_panel(
        "\n".join(
            (
                f"As of: {result.now.isoformat().replace('+00:00', 'Z')}",
                (
                    "Observation: "
                    f"{result.observation_window_start.isoformat()} through "
                    f"{result.observation_window_end.isoformat()} "
                    f"({result.observed_day_count}/{result.observation_window_days} days)"
                ),
                f"Spend to date: {_money(result.spend_to_date_usd)} of {_money(result.budget_usd)} ({percent})",
                f"Daily rate: {_money(result.daily_rate_usd)}/day",
                f"Forecast month-end: {_money(result.forecast_total_usd)}",
                f"Projected breach: {_datetime_label(result.projected_breach_at)}",
                f"Projected breach ETA: {_days_label(result.projected_breach_eta_days)}",
                f"Confidence: {result.confidence:.1%} | Trend: {result.trend}",
                (f"Data: {result.data_sufficiency}, {freshness}, last rollup {last_rollup}"),
            )
        ),
        title="Burn-rate forecast",
        border_style="yellow" if result.stale else "green",
    )


def cmd_burn_rate(argv: list[str]) -> int:
    """Run ``pitwall burn-rate``; ``pitwall.cli`` dispatches ``burn-rate`` here.

    The JSON is ``BurnRateRead.to_dict()``, the same schema REST and MCP return.
    """
    args = _parse_burn_rate_args(argv)
    out = Output(json_mode(args))
    try:
        result = asyncio.run(_read_burn_rate(args))
    except Exception as exc:  # reason: persistence/config failures can contain secret material
        del exc
        out.set_json({"error": _BURN_RATE_UNAVAILABLE})
        if not out.json_mode:
            out.print_error(_BURN_RATE_UNAVAILABLE)
        out.emit()
        return 1

    if out.json_mode:
        out.set_json(result.to_dict())
    else:
        _render_human(result, out)
    out.emit()
    return 0


__all__ = ["cmd_burn_rate"]
