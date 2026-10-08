"""Pods / leases screen and state source for the Textual console."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, cast

import asyncpg
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Label, ListItem, ListView, Static

from pitwall.leases.state import TERMINAL_LEASE_STATES
from pitwall.tui.errors import source_failure_message

_CENT = Decimal("0.01")
_READY_KEYS = frozenset(
    {
        "runtime_seen_at",
        "port_mappings_seen_at",
        "probe_passed_at",
    }
)


class LeasesSource(Protocol):
    """Async provider for a single Pods / Leases refresh."""

    async def load_leases(self) -> LeasesSnapshot:
        """Return active pod lease rows for the operator screen."""


@dataclass(frozen=True)
class LeaseDisplayRow:
    """Read-only lease row rendered in the Pods / Leases table."""

    lease_id: str
    provider_id: str
    pod_id: str
    served_model: str
    engine: str
    variant: str
    state: str
    readiness: str
    expires_at: dt.datetime
    external_resource_id: str | None = None
    cost_accrued_usd: Decimal | None = None
    last_traffic_at: dt.datetime | None = None
    idle_timeout_min: int | None = None
    renewal_policy: str = "manual"
    max_usd_per_hour: Decimal | None = None

    @property
    def state_label(self) -> str:
        return normalized_status(self.state)

    @property
    def resource_id(self) -> str:
        return self.external_resource_id or self.pod_id

    @property
    def resource_label(self) -> str:
        if not self.external_resource_id or self.external_resource_id == self.pod_id:
            return self.pod_id
        return f"{self.external_resource_id} · pod {self.pod_id}"

    @property
    def expires_label(self) -> str:
        return format_utc(self.expires_at)

    @property
    def cost_label(self) -> str:
        return format_optional_usd(self.cost_accrued_usd)

    @property
    def last_traffic_label(self) -> str:
        return format_utc(self.last_traffic_at) if self.last_traffic_at is not None else "—"

    @property
    def idle_timeout_label(self) -> str:
        return f"{self.idle_timeout_min}m" if self.idle_timeout_min is not None else "—"

    @property
    def max_usd_per_hour_label(self) -> str:
        if self.max_usd_per_hour is None:
            return "—"
        return f"${self.max_usd_per_hour:.4f}"

    def to_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "provider_id": self.provider_id,
            "external_resource_id": self.resource_id,
            "pod_id": self.pod_id,
            "served_model": self.served_model,
            "engine": self.engine,
            "variant": self.variant,
            "state": self.state_label,
            "readiness": self.readiness,
            "last_traffic_at": (
                self.last_traffic_at.isoformat() if self.last_traffic_at is not None else None
            ),
            "idle_timeout_min": self.idle_timeout_min,
            "renewal_policy": self.renewal_policy,
            "max_usd_per_hour": (
                str(self.max_usd_per_hour) if self.max_usd_per_hour is not None else None
            ),
            "expires_at": self.expires_at.isoformat(),
            "cost_accrued_usd": (
                str(self.cost_accrued_usd) if self.cost_accrued_usd is not None else None
            ),
        }


@dataclass(frozen=True)
class LeasesSnapshot:
    """Read-only state rendered by the Pods / Leases screen."""

    rows: tuple[LeaseDisplayRow, ...]
    refreshed_at: dt.datetime

    @property
    def active_count(self) -> int:
        return len(self.rows)

    @property
    def refreshed_label(self) -> str:
        return format_utc(self.refreshed_at)


class StaticLeasesSource:
    """Hermetic source used by tests and local demos."""

    def __init__(self, snapshot: LeasesSnapshot) -> None:
        self._snapshot = snapshot
        self.load_count = 0

    async def load_leases(self) -> LeasesSnapshot:
        self.load_count += 1
        return self._snapshot


class PostgresLeasesSource:
    """Read active pod lease rows from Pitwall's Postgres-backed lease table."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        now: Callable[[], dt.datetime] | None = None,
        limit: int = 200,
    ) -> None:
        self._pool = pool
        self._now = now or utc_now
        self._limit = limit

    async def load_leases(self) -> LeasesSnapshot:
        terminal_states = tuple(state.value for state in TERMINAL_LEASE_STATES)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT l.id, l.provider_id, l.external_resource_id, l.runpod_pod_id,
                       l.state, l.expires_at,
                       l.readiness, l.cost_accrued_usd, l.last_traffic_at,
                       l.idle_timeout_min, l.renewal_policy, l.max_usd_per_hour,
                       COALESCE(c.served_model_id, '') AS served_model,
                       COALESCE(p.config->>'engine', '') AS engine,
                       COALESCE(p.config->>'variant', '') AS variant
                FROM pitwall.leases AS l
                JOIN pitwall.providers AS p ON p.id = l.provider_id
                JOIN pitwall.capabilities AS c ON c.id = p.capability_id
                WHERE l.state <> ALL($1::text[])
                ORDER BY l.expires_at ASC, l.created_at ASC
                LIMIT $2
                """,
                terminal_states,
                self._limit,
            )

        return LeasesSnapshot(
            rows=tuple(_display_row_from_record(row) for row in rows),
            refreshed_at=as_utc(self._now()),
        )


class LeasesScreen(Screen[None]):
    """Read-only Pods / Leases screen in the operator console."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("o", "show_overview", "Overview"),
        Binding("l", "show_leases", "Leases", show=False),
    ]

    def __init__(self, source: LeasesSource) -> None:
        super().__init__(name="leases")
        self._source = source
        self._snapshot: LeasesSnapshot | None = None
        self._filter_query = ""
        self._stale = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with Vertical(id="nav-panel"):
                yield Label("Pitwall", id="nav-title")
                yield ListView(
                    ListItem(Label("Overview"), id="nav-overview"),
                    ListItem(Label("Leases"), id="nav-leases"),
                    id="shell-nav",
                )
            with Vertical(id="leases-panel"):
                yield Static("Pods / Leases", id="leases-title")
                yield Static("Loading pod leases", id="leases-summary", classes="summary")
                table: DataTable[str] = DataTable(id="leases-table")
                table.cursor_type = "row"
                table.zebra_stripes = True
                yield table
                yield Static("", id="leases-empty", classes="summary")
                yield Static("", id="leases-refreshed", classes="summary")
                yield Static("", id="leases-error", classes="error")
        yield Footer()

    async def on_mount(self) -> None:
        table = self._table()
        table.add_columns(
            "Lease",
            "Resource",
            "Provider",
            "Model",
            "Engine",
            "Variant",
            "State",
            "Ready",
            "Last traffic",
            "Idle",
            "Policy",
            "Max $/h",
            "Expires",
            "Cost",
        )
        await self._refresh()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def action_show_leases(self) -> None:
        await self._refresh()

    async def action_show_overview(self) -> None:
        await self.app.switch_screen("overview")

    def apply_filter(self, query: str) -> None:
        """Render lease rows matching a transient case-insensitive query."""
        self._filter_query = query.casefold().strip()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    async def _refresh(self) -> None:
        self.query_one("#leases-error", Static).update("")
        self.query_one("#leases-empty", Static).update("")
        try:
            snapshot = await self._source.load_leases()
        except (
            Exception
        ) as exc:  # reason: TUI refresh must degrade to an inline error, never crash the app
            failure = source_failure_message("Pods / leases unavailable", exc)
            if self._snapshot is not None:
                # The count is unknown, not zero: keep the last snapshot and mark it stale
                # until a refresh succeeds, however many times it is re-rendered meanwhile.
                self._stale = True
                self._render_snapshot(self._snapshot)
            else:
                self._table().clear()
                self.query_one("#leases-summary", Static).update("Unavailable")
                self.query_one("#leases-refreshed", Static).update("")
            self.query_one("#leases-error", Static).update(failure)
            return
        self._stale = False
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: LeasesSnapshot) -> None:
        table = self._table()
        self._snapshot = snapshot
        table.clear()
        rows = tuple(
            row
            for row in snapshot.rows
            if not self._filter_query
            or self._filter_query
            in " ".join(
                (
                    row.lease_id,
                    row.resource_label,
                    row.pod_id,
                    row.provider_id,
                    row.served_model,
                    row.engine,
                    row.variant,
                    row.state_label,
                    row.readiness,
                    row.last_traffic_label,
                    row.idle_timeout_label,
                    row.renewal_policy,
                    row.max_usd_per_hour_label,
                )
            ).casefold()
        )
        for row in rows:
            table.add_row(
                row.lease_id,
                row.resource_label,
                row.provider_id,
                row.served_model,
                row.engine,
                row.variant,
                row.state_label,
                row.readiness,
                row.last_traffic_label,
                row.idle_timeout_label,
                row.renewal_policy,
                row.max_usd_per_hour_label,
                row.expires_label,
                row.cost_label,
                key=row.lease_id,
            )

        stale_label = " (stale)" if self._stale else ""
        self.query_one("#leases-summary", Static).update(
            f"{snapshot.active_count} active pod leases{stale_label}"
        )
        empty_message = ""
        if not rows:
            empty_message = (
                "No matching pod leases" if self._filter_query else "No active pod leases"
            )
        self.query_one("#leases-empty", Static).update(empty_message)
        self.query_one("#leases-refreshed", Static).update(
            f"Last refreshed: {snapshot.refreshed_label}"
        )

    def _table(self) -> DataTable[str]:
        return cast(DataTable[str], self.query_one("#leases-table", DataTable))


def _display_row_from_record(row: asyncpg.Record) -> LeaseDisplayRow:
    raw_readiness = row.get("readiness")
    readiness = raw_readiness if isinstance(raw_readiness, Mapping) else None
    return LeaseDisplayRow(
        lease_id=str(row["id"]),
        provider_id=str(row["provider_id"]),
        pod_id=display_fact(row.get("runpod_pod_id")),
        external_resource_id=_optional_display_fact(row.get("external_resource_id")),
        served_model=display_fact(row.get("served_model")),
        engine=display_fact(row.get("engine")),
        variant=display_fact(row.get("variant")),
        state=normalized_status(str(row["state"])),
        readiness=readiness_label(readiness),
        expires_at=_datetime_from_value(row["expires_at"], field_name="expires_at"),
        cost_accrued_usd=decimal_or_none(row.get("cost_accrued_usd")),
        last_traffic_at=_datetime_or_none(row.get("last_traffic_at")),
        idle_timeout_min=_int_or_none(row.get("idle_timeout_min")),
        renewal_policy=_renewal_policy(row.get("renewal_policy")),
        max_usd_per_hour=decimal_or_none(row.get("max_usd_per_hour")),
    )


def display_fact(value: object) -> str:
    """Return persisted display facts or a visible unavailable marker."""

    return value.strip() if isinstance(value, str) and value.strip() else "—"


def _optional_display_fact(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def readiness_label(readiness: Mapping[str, object] | None) -> str:
    """Return a compact readiness label for a persisted readiness payload."""

    if readiness is None:
        return "pending"
    populated = {key for key in _READY_KEYS if bool(readiness.get(key))}
    if populated == _READY_KEYS:
        return "ready"
    if populated:
        return "partial"
    return "pending"


def normalized_status(value: str | None) -> str:
    """Return a display-safe status key."""

    if value is None:
        return "unknown"
    stripped = value.strip().lower()
    return stripped or "unknown"


def format_optional_usd(amount: Decimal | None) -> str:
    """Format an optional USD Decimal with cent rounding."""

    if amount is None:
        return "pending"
    rounded = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    return f"${rounded:,.2f}"


def decimal_or_none(value: object) -> Decimal | None:
    """Convert optional numeric DB payloads to Decimal."""

    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float | str):
        return Decimal(str(value))
    return None


def _datetime_or_none(value: object) -> dt.datetime | None:
    return value if isinstance(value, dt.datetime) else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _renewal_policy(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "manual"


def format_utc(value: dt.datetime) -> str:
    """Render a datetime as an operator-facing UTC label."""

    return as_utc(value).strftime("%Y-%m-%d %H:%M UTC")


def as_utc(value: dt.datetime) -> dt.datetime:
    """Normalize a datetime for operator display."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def utc_now() -> dt.datetime:
    """Return current UTC time."""

    return dt.datetime.now(dt.UTC)


def _datetime_from_value(value: object, *, field_name: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    raise TypeError(f"{field_name} must be a datetime")


__all__ = [
    "LeaseDisplayRow",
    "LeasesScreen",
    "LeasesSnapshot",
    "LeasesSource",
    "PostgresLeasesSource",
    "StaticLeasesSource",
    "format_optional_usd",
    "normalized_status",
    "readiness_label",
]
