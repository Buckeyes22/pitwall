"""Cost screen and state sources for the Textual console."""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

import asyncpg
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from pitwall.core.cost_reporting import recent_workloads_read
from pitwall.cost.budget_gate import MONTH_TO_DATE_WHERE, month_to_date_spend
from pitwall.cost.read_models import RecentWorkloadsRead, WorkloadCostRecord
from pitwall.cost.simulator import WhatIfBatchProjection
from pitwall.cost.sub_budgets import (
    ChargebackLineItem,
    ChargebackReport,
    SubBudgetConfig,
    generate_chargeback_report,
)
from pitwall.db.quota_repository import QuotaRepository
from pitwall.finops.burn_rate import BurnRateRead, read_burn_rate, read_configured_burn_rate
from pitwall.routing.quota import QuotaRecord
from pitwall.tui.errors import source_failure_message
from pitwall.tui.overview import as_utc, format_usd, utc_now

PoolFactory = Callable[[], Awaitable[asyncpg.Pool]]
QuotaRepositoryFactory = Callable[[asyncpg.Pool], QuotaRepository]
_DAY_QUANTUM = Decimal("0.1")
_PERCENT_QUANTUM = Decimal("0.1")
_USD_QUANTUM = Decimal("0.000001")
_UNITS_MILLION = Decimal("1000000")
_COST_TABLE_HEADER = ("Tag", "Allocation", "Spend", "Remaining", "Used")
_BURN_DOWN_HEADER = ("Pool", "Used/Budget", "Reset")


class CostSource(Protocol):
    """Async provider for a single Cost refresh."""

    async def load_cost(self) -> CostSnapshot:
        """Return the current cost analytics snapshot."""


@dataclass(frozen=True, slots=True)
class FreeBurnDownRow:
    """Read-only row rendered in the free-tier burn-down panel."""

    pool_key: str
    provider_id: str
    used: Decimal
    budget: Decimal | None
    reset_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class CostSnapshot:
    """Read-only state rendered by the Cost screen."""

    runway: BurnRateRead
    chargeback: ChargebackReport
    what_if: WhatIfBatchProjection
    refreshed_at: dt.datetime
    recent_workloads: RecentWorkloadsRead = field(
        default_factory=lambda: RecentWorkloadsRead(workloads=())
    )
    free_burn_down: tuple[FreeBurnDownRow, ...] = ()

    @property
    def runway_summary(self) -> str:
        return (
            f"Burn: {format_usd(self.runway.daily_rate_usd)}/day"
            f" | Remaining: {format_usd(self.runway.remaining_budget_usd)}"
            f" | Forecast: {format_optional_usd(self.runway.forecast_total_usd)}"
            f" | Projected breach: {format_breach(self.runway)}"
            f" | Breach ETA: {format_days(self.runway.projected_breach_eta_days)}"
            f" | Trend: {self.runway.trend}"
            f" | Confidence: {format_percent(self.runway.confidence, Decimal('1'))}"
            f" | Data: {format_data_state(self.runway)}"
        )

    @property
    def sub_budget_summary(self) -> str:
        count = len(self.chargeback.line_items)
        noun = "tag" if count == 1 else "tags"
        return (
            f"Sub-budgets: {format_usd(self.chargeback.total_spend_usd)} spend "
            f"across {count} {noun} | "
            f"{format_usd(self.chargeback.unallocated_spend_usd)} unallocated"
        )

    @property
    def what_if_summary(self) -> str:
        return (
            f"What-if: reserves {format_usd(self.what_if.total_reserved_usd)}"
            f" | projected spend {format_usd(self.what_if.projected_spend_usd)}"
            f" | headroom {format_optional_usd(self.what_if.budget_headroom_usd)}"
            f" | {format_budget_status(self.what_if.would_exceed_budget)}"
        )

    @property
    def refreshed_label(self) -> str:
        refreshed = as_utc(self.refreshed_at)
        return refreshed.strftime("%Y-%m-%d %H:%M UTC")


class StaticCostSource:
    """Hermetic source used by tests and local demos."""

    def __init__(self, snapshot: CostSnapshot) -> None:
        self._snapshot = snapshot
        self.load_count = 0

    async def load_cost(self) -> CostSnapshot:
        self.load_count += 1
        return self._snapshot


class PostgresCostSource:
    """Read Cost view state from Pitwall's Postgres-backed cost layers."""

    def __init__(
        self,
        *,
        pool_factory: PoolFactory,
        now: Callable[[], dt.datetime] | None = None,
        monthly_budget_usd: Decimal | str | int | None = None,
        sub_budget_config: SubBudgetConfig | None = None,
        what_if_projection: WhatIfBatchProjection | None = None,
        window_days: int = 30,
        quota_repository_factory: QuotaRepositoryFactory | None = None,
    ) -> None:
        self._pool_factory = pool_factory
        self._now = now or utc_now
        self._monthly_budget_usd = _optional_non_negative_usd(monthly_budget_usd)
        self._sub_budget_config = sub_budget_config
        self._what_if_projection = what_if_projection
        self._window_days = window_days
        self._quota_repository_factory = quota_repository_factory

    async def load_cost(self) -> CostSnapshot:
        pool = await self._pool_factory()
        observed_at = as_utc(self._now())
        runway = (
            await read_burn_rate(
                pool,
                budget_usd=self._monthly_budget_usd,
                now=observed_at,
                window_days=self._window_days,
            )
            if self._monthly_budget_usd is not None
            else await read_configured_burn_rate(
                pool,
                now=observed_at,
                window_days=self._window_days,
            )
        )
        workloads = await _fetch_month_workload_costs(pool)
        recent = await recent_workloads_read(pool, limit=10)
        if self._sub_budget_config is not None:
            chargeback = generate_chargeback_report(
                self._sub_budget_config,
                workloads,
                tag_resolver=resolve_workload_tag,
            )
        elif runway.budget_usd > 0:
            chargeback = generate_chargeback_report(
                SubBudgetConfig(total_budget_usd=runway.budget_usd),
                workloads,
                tag_resolver=resolve_workload_tag,
            )
        else:
            # Sub-budget allocation is undefined at a zero hard budget, but the
            # read-only screen must still show aggregate unallocated spend.
            chargeback = generate_chargeback_report(
                SubBudgetConfig(total_budget_usd=_USD_QUANTUM),
                workloads,
            )
        what_if = self._what_if_projection or empty_what_if_projection(
            starting_spend_usd=await _gate_month_to_date_spend(pool),
            budget_usd=runway.budget_usd,
        )

        free_burn_down = await _fetch_free_burn_down(
            pool, observed_at, self._quota_repository_factory
        )

        return CostSnapshot(
            runway=runway,
            chargeback=chargeback,
            what_if=what_if,
            refreshed_at=observed_at,
            recent_workloads=recent,
            free_burn_down=free_burn_down,
        )


class CostScreen(Screen[None]):
    """Read-only Cost screen in the operator console."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, source: CostSource) -> None:
        super().__init__(name="cost")
        self._source = source

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with Vertical(id="nav-panel"):
                yield Label("Pitwall", id="nav-title")
                yield ListView(
                    ListItem(Label("Overview"), id="nav-overview"),
                    ListItem(Label("Providers"), id="nav-providers"),
                    ListItem(Label("Cost"), id="nav-cost"),
                    id="shell-nav",
                )
            with Vertical(id="cost-panel"):
                yield Static("Cost", id="cost-title")
                yield Static("Loading runway", id="runway-summary", classes="summary")
                yield Static("", id="sub-budget-summary", classes="summary")
                yield Static("Loading sub-budgets", id="sub-budget-table")
                yield Static("Recent workload costs", classes="summary")
                yield Static("Loading workload costs", id="workload-cost-table")
                yield Static("free-tier burn-down", classes="summary")
                yield Static("Loading free-tier burn-down", id="cost-free-burn-down")
                yield Static("", id="what-if-summary", classes="summary")
                yield Static("", id="cost-refreshed", classes="summary")
                yield Static("", id="cost-error", classes="error")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        self.query_one("#cost-error", Static).update("")
        try:
            snapshot = await self._source.load_cost()
        except (
            Exception
        ) as exc:  # reason: TUI refresh must degrade to an inline error, never crash the app
            for widget_id in (
                "runway-summary",
                "sub-budget-table",
                "workload-cost-table",
                "cost-free-burn-down",
            ):
                widget = self.query_one(f"#{widget_id}", Static)
                if str(widget.content).startswith("Loading "):
                    widget.update("Unavailable")
            self.query_one("#cost-error", Static).update(
                source_failure_message("Cost unavailable", exc)
            )
            return
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: CostSnapshot) -> None:
        self.query_one("#runway-summary", Static).update(snapshot.runway_summary)
        self.query_one("#sub-budget-summary", Static).update(snapshot.sub_budget_summary)
        self.query_one("#sub-budget-table", Static).update(
            format_cost_table(snapshot.chargeback.line_items)
        )
        self.query_one("#workload-cost-table", Static).update(
            format_workload_cost_table(snapshot.recent_workloads.workloads)
        )
        self.query_one("#cost-free-burn-down", Static).update(
            format_free_burn_down_panel(snapshot.free_burn_down)
        )
        self.query_one("#what-if-summary", Static).update(snapshot.what_if_summary)
        self.query_one("#cost-refreshed", Static).update(
            f"Last refreshed: {snapshot.refreshed_label}"
        )


def format_cost_table(entries: tuple[ChargebackLineItem, ...]) -> str:
    """Render a stable fixed-width sub-budget table."""

    rows: list[tuple[str, str, str, str, str]] = [_COST_TABLE_HEADER]
    rows.extend(_chargeback_row(entry) for entry in entries)
    widths = [max(len(row[column]) for row in rows) for column in range(5)]

    rendered: list[str] = []
    for index, row in enumerate(rows):
        rendered.append(_format_row(row, widths))
        if index == 0:
            rendered.append("  ".join("-" * width for width in widths))
    return "\n".join(rendered)


def format_workload_cost_table(entries: tuple[WorkloadCostRecord, ...]) -> str:
    """Render the shared estimate/ceiling/actual model without semantic collapse."""

    header = ("Workload", "Estimate", "Ceiling", "Confidence", "Actual", "Kind", "State")
    rows = [header]
    rows.extend(
        (
            str(entry.fields.get("id", "")),
            _cost_money(entry.cost.estimate),
            _cost_money(entry.cost.ceiling),
            entry.cost.confidence,
            _cost_money(entry.cost.actual),
            entry.cost.actual_kind,
            entry.cost.reconciliation_status,
        )
        for entry in entries
    )
    widths = [max(len(row[column]) for row in rows) for column in range(len(header))]
    rendered = [
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows
    ]
    rendered.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(rendered)


def format_free_burn_down_panel(
    rows: tuple[FreeBurnDownRow, ...],
) -> str:
    """Render the free-tier burn-down panel with one row per pool."""

    if not rows:
        return "free-tier burn-down: no free pools configured"
    lines = ["free-tier burn-down", format_free_burn_down_table(rows)]
    return "\n".join(lines)


def format_free_burn_down_table(rows: tuple[FreeBurnDownRow, ...]) -> str:
    """Render the fixed-width burn-down table without the panel header."""

    table_rows: list[tuple[str, str, str]] = [_BURN_DOWN_HEADER]
    table_rows.extend(_burn_down_row(row) for row in rows)
    widths = [
        max(len(row[column]) for row in table_rows) for column in range(len(_BURN_DOWN_HEADER))
    ]
    rendered: list[str] = []
    for index, row in enumerate(table_rows):
        rendered.append(_format_row_3(row, widths))
        if index == 0:
            rendered.append("  ".join("-" * width for width in widths))
    return "\n".join(rendered)


def format_units_compact(value: Decimal | None) -> str:
    """Render a token count compactly as ``X.YM`` when it reaches one million."""

    if value is None:
        return "—"
    scaled = float(value / _UNITS_MILLION)
    return f"{scaled:.1f}M"


def format_reset_date(reset_at: dt.datetime | None) -> str:
    """Render a UTC reset date as ``resets YYYY-MM-DD`` or a placeholder."""

    if reset_at is None:
        return "no reset"
    return f"resets {as_utc(reset_at).date().isoformat()}"


def _cost_money(value: Decimal | None) -> str:
    return "-" if value is None else str(value)


def format_days(value: Decimal | None) -> str:
    """Format an optional runway duration."""

    if value is None:
        return "unavailable"
    rounded = value.quantize(_DAY_QUANTUM, rounding=ROUND_HALF_UP)
    noun = "day" if rounded == Decimal("1.0") else "days"
    return f"{rounded:.1f} {noun}"


def format_breach(runway: BurnRateRead) -> str:
    """Render projected UTC breach or its explicit unavailable state."""
    if runway.projected_breach_at is None:
        return "not projected"
    return runway.projected_breach_at.strftime("%Y-%m-%d %H:%M UTC")


def format_data_state(runway: BurnRateRead) -> str:
    """Render no-data, sparse, fresh, and stale states without raw DB errors."""
    last_rollup = runway.last_rollup_day.isoformat() if runway.last_rollup_day else "unknown"
    if runway.data_sufficiency == "no_data":
        return f"no data; stale (last rollup {last_rollup})" if runway.stale else "no data"
    if runway.stale:
        return f"stale (last rollup {last_rollup})"
    return runway.data_sufficiency


def format_percent(numerator: Decimal, denominator: Decimal) -> str:
    """Format a bounded percentage for operator summaries."""

    if denominator <= 0:
        return "n/a"
    percent = (numerator / denominator * Decimal("100")).quantize(
        _PERCENT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if percent < 0:
        percent = Decimal("0.0")
    elif percent > 100:
        percent = Decimal("100.0")
    return f"{percent:.1f}%"


def format_optional_usd(value: Decimal | None) -> str:
    """Format an optional USD value."""

    if value is None:
        return "unavailable"
    return format_usd(value)


def format_budget_status(value: bool | None) -> str:
    """Format optional what-if budget status."""

    if value is None:
        return "budget unknown"
    if value:
        return "over budget"
    return "within budget"


def empty_what_if_projection(
    *,
    starting_spend_usd: Decimal,
    budget_usd: Decimal | None,
) -> WhatIfBatchProjection:
    """Return a read-only empty simulator summary when no what-if inputs are configured."""

    headroom = None if budget_usd is None else budget_usd - starting_spend_usd
    return WhatIfBatchProjection(
        projections=(),
        total_reserved_usd=Decimal("0.000000"),
        starting_spend_usd=starting_spend_usd,
        projected_spend_usd=starting_spend_usd,
        budget_usd=budget_usd,
        budget_headroom_usd=headroom,
        would_exceed_budget=None if headroom is None else headroom < 0,
    )


def resolve_workload_tag(workload: Mapping[str, Any]) -> str | None:
    """Resolve a sub-budget tag from a workload row mapping."""

    direct = _first_text_value(workload, ("budget_tag", "tag", "team"))
    if direct is not None:
        return direct

    raw_input = workload.get("input")
    if isinstance(raw_input, Mapping):
        return _first_text_value(raw_input, ("budget_tag", "tag", "team"))
    return None


async def _gate_month_to_date_spend(pool: asyncpg.Pool) -> Decimal:
    """The month-to-date spend the budget gate enforces (not the burn-rate rollup's)."""
    async with pool.acquire() as conn:
        return await month_to_date_spend(conn)


async def _fetch_month_workload_costs(pool: asyncpg.Pool) -> tuple[Mapping[str, Any], ...]:
    async with pool.acquire() as conn:
        # Every state counts, as in the budget gate: failed and cancelled work still bills.
        rows = await conn.fetch(
            f"""SELECT input, cost_actual_usd, cost_ceiling_usd, cost_estimate_usd
                FROM pitwall.workloads
                WHERE {MONTH_TO_DATE_WHERE}
                ORDER BY submitted_at ASC, id ASC"""
        )
    return tuple(
        {
            "input": row["input"],
            "cost_actual_usd": row["cost_actual_usd"],
            "cost_ceiling_usd": row["cost_ceiling_usd"],
            "cost_estimate_usd": row["cost_estimate_usd"],
        }
        for row in rows
    )


def _chargeback_row(entry: ChargebackLineItem) -> tuple[str, str, str, str, str]:
    return (
        entry.tag,
        format_usd(entry.allocation_usd),
        format_usd(entry.spend_usd),
        format_usd(entry.remaining_usd),
        format_percent(entry.spend_usd, entry.allocation_usd),
    )


def _burn_down_row(row: FreeBurnDownRow) -> tuple[str, str, str]:
    used_budget = f"{format_units_compact(row.used)}/{format_units_compact(row.budget)}"
    return (
        row.pool_key,
        used_budget,
        format_reset_date(row.reset_at),
    )


def _format_row(row: tuple[str, str, str, str, str], widths: list[int]) -> str:
    return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))


def _format_row_3(
    row: tuple[str, str, str],
    widths: list[int],
) -> str:
    return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))


async def _fetch_free_burn_down(
    pool: asyncpg.Pool,
    observed_at: dt.datetime,
    quota_repository_factory: QuotaRepositoryFactory | None,
) -> tuple[FreeBurnDownRow, ...]:
    """Read every free pool as a burn-down row when a repository is configured."""

    if quota_repository_factory is None:
        return ()
    repo = quota_repository_factory(pool)
    records = await repo.list_all()
    return tuple(_burn_down_row_from_record(record) for record in records)


def _burn_down_row_from_record(record: QuotaRecord) -> FreeBurnDownRow:
    return FreeBurnDownRow(
        pool_key=record.pool_key,
        provider_id=record.provider_id,
        used=record.used_units,
        budget=record.budget_units,
        reset_at=record.reset_at,
    )


def _first_text_value(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        raw_value = mapping.get(key)
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if value:
                return value
    return None


def _optional_non_negative_usd(value: Decimal | str | int | None) -> Decimal | None:
    if value is None:
        return None
    parsed = _usd(value)
    if parsed < 0:
        raise ValueError("monthly_budget_usd must be non-negative")
    return parsed


def _usd(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("USD value must be decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("USD value must be decimal") from exc
    if not parsed.is_finite():
        raise ValueError("USD value must be finite")
    return parsed.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)


__all__ = [
    "CostScreen",
    "CostSnapshot",
    "CostSource",
    "FreeBurnDownRow",
    "PostgresCostSource",
    "StaticCostSource",
    "empty_what_if_projection",
    "format_breach",
    "format_budget_status",
    "format_cost_table",
    "format_free_burn_down_panel",
    "format_free_burn_down_table",
    "format_workload_cost_table",
    "format_data_state",
    "format_days",
    "format_optional_usd",
    "format_percent",
    "format_reset_date",
    "format_units_compact",
    "resolve_workload_tag",
]
