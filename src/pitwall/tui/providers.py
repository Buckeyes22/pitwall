"""Providers screen and registry-backed state source for the Textual console."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import asyncpg
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from pitwall.core.models import default_credential_reference
from pitwall.db.quota_repository import QuotaRepository
from pitwall.providers.registry import ProviderRegistry, get_default_registry
from pitwall.providers.service import (
    ProviderAvailabilityRead,
    ProviderDescriptor,
    ProviderHealthRead,
    ProviderOperationsService,
)
from pitwall.routing.quota import QuotaRecord
from pitwall.tui.onboarding import OnboardingSource, RunPodOnboardingPanel

log = logging.getLogger(__name__)

RegistryFactory = Callable[[], ProviderRegistry]
PoolFactory = Callable[[], Awaitable[asyncpg.Pool]]
ProviderOperationsFactory = Callable[[asyncpg.Pool], ProviderOperationsService]
QuotaRepositoryFactory = Callable[[asyncpg.Pool], QuotaRepository]

_PROVIDER_TABLE_HEADER = (
    "Provider ID",
    "Adapter",
    "Credential ref",
    "Status",
    "Pricing model",
    "Credential set",
    "Capabilities",
    "Armed",
    "Active pod",
    "HEADROOM",
    "RESET",
    "TOS",
)
_PROVIDER_TABLE_COLUMNS = 12
_AVAILABILITY_LIMIT = 25
_HEADROOM_BAR_WIDTH = 10


def format_headroom(headroom: float | None) -> str:
    """Render a 10-cell headroom bar plus a percentage for a 0..1 fraction."""

    if headroom is None:
        return "—"
    bounded = max(0.0, min(1.0, float(headroom)))
    filled = int(round(bounded * _HEADROOM_BAR_WIDTH))
    if filled < 0:
        filled = 0
    elif filled > _HEADROOM_BAR_WIDTH:
        filled = _HEADROOM_BAR_WIDTH
    bar = "[" + ("#" * filled) + ("-" * (_HEADROOM_BAR_WIDTH - filled)) + "]"
    percent = int(round(bounded * 100))
    return f"{bar} {percent}%"


def format_reset_in(
    reset_at: dt.datetime | None,
    *,
    now: dt.datetime | None = None,
) -> str:
    """Render a compact days+hours countdown to a quota reset window."""

    if reset_at is None:
        return "—"
    current = now if now is not None else dt.datetime.now(dt.UTC)
    if reset_at.tzinfo is None or current.tzinfo is None:
        delta = reset_at - current
    else:
        delta = reset_at.astimezone(current.tzinfo) - current
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "now"
    days, remainder = divmod(total_seconds, 86400)
    hours = remainder // 3600
    if days > 0:
        return f"{days}d {hours}h"
    minutes = (remainder % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def format_tos_label(verdict: str | None) -> str:
    """Render a TOS verdict, mapping absent values to a visible placeholder."""

    if not verdict:
        return "—"
    return verdict


class ProvidersSource(Protocol):
    """Async provider for a single Providers refresh."""

    async def load_providers(self) -> ProvidersSnapshot:
        """Return the current registered provider plugin snapshot."""

    async def probe_provider(self, provider_id: str) -> ProviderHealthRead | None:
        """Perform one explicit bounded live probe, or return no result safely."""

    async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
        """Perform one explicit bounded availability/pricing read."""


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    """Read-only provider plugin row rendered by the Providers screen."""

    provider_id: str
    status: str
    pricing_model: str
    armed: bool
    active_pod_id: str | None
    adapter_id: str = "runpod"
    credential_ref: str = "RUNPOD_API_KEY"
    credential_configured: bool | None = None
    capabilities: tuple[str, ...] = ()
    quota_headroom: float | None = None
    reset_in: str | None = None
    tos: str | None = None

    def as_row(
        self,
    ) -> tuple[str, str, str, str, str, str, str, str, str, str, str, str]:
        """Return table cell values in display order."""

        return (
            self.provider_id,
            self.adapter_id,
            self.credential_ref,
            self.status,
            self.pricing_model,
            (
                "yes"
                if self.credential_configured is True
                else "no"
                if self.credential_configured is False
                else "unknown"
            ),
            ", ".join(self.capabilities) or "—",
            "yes" if self.armed else "no",
            self.active_pod_id or "—",
            format_headroom(self.quota_headroom),
            self.reset_in if self.reset_in is not None else "—",
            format_tos_label(self.tos),
        )


@dataclass(frozen=True, slots=True)
class ProvidersSnapshot:
    """Read-only state rendered by the Providers screen."""

    entries: tuple[ProviderEntry, ...]

    @property
    def summary(self) -> str:
        noun = "provider" if len(self.entries) == 1 else "providers"
        return f"{len(self.entries)} registered {noun}"


class StaticProvidersSource:
    """Hermetic source used by tests and local demos."""

    def __init__(
        self,
        snapshot: ProvidersSnapshot,
        *,
        probe_results: Mapping[str, ProviderHealthRead] | None = None,
        availability_results: Mapping[str, ProviderAvailabilityRead] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._probe_results = dict(probe_results or {})
        self._availability_results = dict(availability_results or {})
        self.load_count = 0
        self.probe_count = 0
        self.availability_count = 0

    async def load_providers(self) -> ProvidersSnapshot:
        self.load_count += 1
        return self._snapshot

    async def probe_provider(self, provider_id: str) -> ProviderHealthRead | None:
        self.probe_count += 1
        return self._probe_results.get(provider_id)

    async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
        self.availability_count += 1
        return self._availability_results.get(provider_id)


class RegistryProvidersSource:
    """Read provider plugin metadata from the process provider registry."""

    def __init__(
        self,
        *,
        registry_factory: RegistryFactory = get_default_registry,
    ) -> None:
        self._registry_factory = registry_factory

    async def load_providers(self) -> ProvidersSnapshot:
        registry = self._registry_factory()
        entries = tuple(_provider_entry(registry, provider_id) for provider_id in registry.ids)
        return ProvidersSnapshot(entries=entries)

    async def probe_provider(self, provider_id: str) -> ProviderHealthRead | None:
        del provider_id
        return None

    async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
        del provider_id
        return None


class PostgresProvidersSource:
    """Read persisted provider health and active served-pod facts."""

    def __init__(
        self,
        *,
        pool_factory: PoolFactory,
        limit: int = 200,
        quota_repository_factory: QuotaRepositoryFactory | None = None,
    ) -> None:
        self._pool_factory = pool_factory
        self._limit = limit
        self._quota_repository_factory = quota_repository_factory

    async def load_providers(self) -> ProvidersSnapshot:
        pool = await self._pool_factory()
        quota_lookup: dict[tuple[str, str], QuotaRecord] = {}
        if self._quota_repository_factory is not None:
            quota_records = await self._quota_repository_factory(pool).list_all()
            quota_lookup = {(r.provider_id, r.pool_key): r for r in quota_records}
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name, adapter_id, credential_ref, health_status, config
                FROM pitwall.providers
                ORDER BY priority ASC, name ASC, id ASC
                LIMIT $1
                """,
                self._limit,
            )
        return ProvidersSnapshot(
            entries=tuple(_provider_entry_from_record(row, quota_lookup) for row in rows)
        )

    async def probe_provider(self, provider_id: str) -> ProviderHealthRead | None:
        del provider_id
        return None

    async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
        del provider_id
        return None


class ServiceProvidersSource:
    """Providers source backed exclusively by ``ProviderOperationsService``.

    Descriptors are persisted reads.  A live provider call can occur only from
    ``probe_provider`` after the operator explicitly chooses a provider ID.
    """

    def __init__(
        self,
        *,
        pool_factory: PoolFactory,
        service_factory: ProviderOperationsFactory = ProviderOperationsService,
        limit: int = 100,
    ) -> None:
        if not 1 <= limit <= 100:
            raise ValueError("provider descriptor limit must be between 1 and 100")
        self._pool_factory = pool_factory
        self._service_factory = service_factory
        self._limit = limit

    async def load_providers(self) -> ProvidersSnapshot:
        service = await self._service()
        descriptors = await service.list_descriptors(enabled_only=False, limit=self._limit)
        return ProvidersSnapshot(
            entries=tuple(_provider_entry_from_descriptor(descriptor) for descriptor in descriptors)
        )

    async def probe_provider(self, provider_id: str) -> ProviderHealthRead | None:
        service = await self._service()
        return await service.health(provider_id, probe=True)

    async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
        service = await self._service()
        return await service.availability(provider_id, limit=_AVAILABILITY_LIMIT)

    async def _service(self) -> ProviderOperationsService:
        return self._service_factory(await self._pool_factory())


class ProvidersScreen(Screen[None]):
    """Read-only Providers screen in the operator console."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("g", "probe", "Probe provider"),
        Binding("v", "availability", "Availability/pricing"),
    ]

    def __init__(
        self,
        source: ProvidersSource,
        onboarding_source: OnboardingSource | None = None,
    ) -> None:
        super().__init__(name="providers")
        self._source = source
        self._onboarding_source = onboarding_source
        self._snapshot: ProvidersSnapshot | None = None
        self._filter_query = ""
        self._availability_snapshots: dict[str, ProviderAvailabilityRead] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with Vertical(id="nav-panel"):
                yield Label("Pitwall", id="nav-title")
                yield ListView(
                    ListItem(Label("Overview"), id="nav-overview"),
                    ListItem(Label("Providers"), id="nav-providers"),
                    id="shell-nav",
                )
            with Vertical(id="providers-panel"):
                yield Static("Providers", id="providers-title")
                yield Static("", id="providers-summary", classes="summary")
                yield Static("Loading providers", id="providers-table")
                yield Input(
                    placeholder="Provider ID: health (g), availability/pricing (v)",
                    id="providers-probe-id",
                )
                yield Static("", id="providers-probe-result", classes="summary")
                yield Static("", id="providers-error", classes="error")
                if self._onboarding_source is not None:
                    yield RunPodOnboardingPanel(self._onboarding_source)
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def action_probe(self) -> None:
        """Make a single explicit live health read for the entered provider ID."""
        self.query_one("#providers-error", Static).update("")
        provider_id = self.query_one("#providers-probe-id", Input).value.strip()
        result_widget = self.query_one("#providers-probe-result", Static)
        if not provider_id:
            result_widget.update("Enter a provider ID before probing.")
            return
        try:
            health = await self._source.probe_provider(provider_id)
        except Exception:  # reason: TUI must not display provider/database exception detail
            result_widget.update("Provider probe unavailable.")
            return
        if health is None:
            result_widget.update("Provider not found.")
            return
        availability_count = (
            str(health.availability_count)
            if health.availability_count is not None
            else "not probed"
        )
        result_widget.update(
            " | ".join(
                (
                    f"{health.provider_id}",
                    f"persisted: {health.persisted_health}",
                    f"live: {health.live_status}",
                    f"availability sample: {availability_count}",
                    f"code: {health.error_code or 'none'}",
                )
            )
        )

    async def action_availability(self) -> None:
        """Make one explicit bounded availability/pricing read for a provider."""

        self.query_one("#providers-error", Static).update("")
        provider_id = self.query_one("#providers-probe-id", Input).value.strip()
        result_widget = self.query_one("#providers-probe-result", Static)
        if not provider_id:
            result_widget.update("Enter a provider ID before reading availability.")
            return
        result_widget.update("Loading provider availability/pricing.")
        try:
            snapshot = await self._source.provider_availability(provider_id)
        except Exception:  # reason: TUI must not display provider/database exception detail
            previous = self._availability_snapshots.get(provider_id)
            if previous is None:
                result_widget.update("Provider availability unavailable.")
            else:
                result_widget.update(
                    self._format_availability(
                        previous,
                        presentation="Availability/pricing stale; refresh unavailable.",
                    )
                )
            return
        if snapshot is None:
            result_widget.update("Provider not found.")
            return
        if snapshot.status in {"unavailable", "error"}:
            previous = self._availability_snapshots.get(provider_id)
            if previous is None:
                result_widget.update("Provider availability unavailable.")
            else:
                result_widget.update(
                    self._format_availability(
                        previous,
                        presentation="Availability/pricing stale; refresh unavailable.",
                    )
                )
            return
        self._availability_snapshots[provider_id] = snapshot
        presentation = (
            "Availability/pricing populated."
            if snapshot.status == "available"
            else "Availability/pricing empty."
        )
        result_widget.update(self._format_availability(snapshot, presentation=presentation))

    @staticmethod
    def _format_availability(
        snapshot: ProviderAvailabilityRead,
        *,
        presentation: str,
    ) -> str:
        lines = [
            presentation,
            " | ".join(
                (
                    snapshot.provider_id,
                    f"status: {snapshot.status}",
                    f"observed: {snapshot.observed_at.isoformat()}",
                    f"source: {snapshot.source_contract or 'unavailable'}",
                    f"items: {len(snapshot.items)}",
                    f"code: {snapshot.error_code or 'none'}",
                )
            ),
        ]
        if not snapshot.items:
            lines.append("No availability/pricing items.")
        lines.extend(
            " | ".join(
                (
                    item.resource_id,
                    item.kind,
                    f"available: {item.available}",
                    f"region: {item.region or 'none'}",
                    f"accelerator: {item.accelerator or 'none'}",
                    "pricing: "
                    + (
                        ", ".join(f"{key}={value}" for key, value in item.pricing.items()) or "none"
                    ),
                )
            )
            for item in snapshot.items
        )
        return "\n".join(lines)

    def apply_filter(self, query: str) -> None:
        """Render provider rows matching a transient case-insensitive query."""
        self._filter_query = query.casefold().strip()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    async def _refresh(self) -> None:
        self.query_one("#providers-error", Static).update("")
        try:
            snapshot = await self._source.load_providers()
        except (
            Exception
        ) as exc:  # reason: service/database details can contain sensitive configuration
            log.warning("providers refresh failed (%s)", type(exc).__name__)
            table = self.query_one("#providers-table", Static)
            if str(table.content) == "Loading providers":
                table.update("Unavailable")
            self.query_one("#providers-error", Static).update(
                f"Providers unavailable ({type(exc).__name__})."
            )
            return
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: ProvidersSnapshot) -> None:
        self._snapshot = snapshot
        entries = tuple(
            entry
            for entry in snapshot.entries
            if not self._filter_query or self._filter_query in " ".join(entry.as_row()).casefold()
        )
        self.query_one("#providers-summary", Static).update(snapshot.summary)
        self.query_one("#providers-table", Static).update(format_providers_table(entries))


def format_providers_table(entries: tuple[ProviderEntry, ...]) -> str:
    """Render a stable fixed-width provider table."""

    rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str, str]] = [
        _PROVIDER_TABLE_HEADER
    ]
    rows.extend(entry.as_row() for entry in entries)
    widths = [max(len(row[column]) for row in rows) for column in range(_PROVIDER_TABLE_COLUMNS)]

    rendered: list[str] = []
    for index, row in enumerate(rows):
        rendered.append(_format_row(row, widths))
        if index == 0:
            rendered.append("  ".join("-" * width for width in widths))
    return "\n".join(rendered)


def _format_row(
    row: tuple[str, str, str, str, str, str, str, str, str, str, str, str],
    widths: list[int],
) -> str:
    return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))


def _provider_entry(registry: ProviderRegistry, provider_id: str) -> ProviderEntry:
    adapter = registry.lookup(provider_id)
    return ProviderEntry(
        provider_id=provider_id,
        status="registered",
        pricing_model="tagged",
        armed=False,
        active_pod_id=None,
        adapter_id=adapter.id,
        credential_ref=default_credential_reference(adapter.id),
    )


def _armed_state(config: Mapping[str, object]) -> tuple[bool, str | None]:
    active_pod_value = config.get("active_pod_id")
    active_pod_id = active_pod_value.strip() if isinstance(active_pod_value, str) else ""
    armed = bool(active_pod_id) and bool(config.get("active_lease_id"))
    return armed, active_pod_id or None


def _provider_entry_from_record(
    row: Mapping[str, object],
    quota_lookup: Mapping[tuple[str, str], QuotaRecord] | None = None,
) -> ProviderEntry:
    config_value = row.get("config")
    config = config_value if isinstance(config_value, Mapping) else {}
    armed, active_pod_id = _armed_state(config)
    cost_value = config.get("cost")
    cost = cost_value if isinstance(cost_value, Mapping) else {}
    mode_value = cost.get("mode")
    pricing_model = (
        mode_value.strip() if isinstance(mode_value, str) and mode_value.strip() else "tagged"
    )
    headroom, reset_in, tos = _quota_fields_for_provider(str(row["id"]), config, quota_lookup)
    return ProviderEntry(
        provider_id=str(row["id"]),
        status=str(row["health_status"]),
        pricing_model=pricing_model,
        armed=armed,
        active_pod_id=active_pod_id,
        adapter_id=str(row.get("adapter_id", "runpod")),
        credential_ref=str(row.get("credential_ref", "RUNPOD_API_KEY")),
        quota_headroom=headroom,
        reset_in=reset_in,
        tos=tos,
    )


def _quota_fields_for_provider(
    provider_id: str,
    config: Mapping[str, object],
    quota_lookup: Mapping[tuple[str, str], QuotaRecord] | None,
) -> tuple[float | None, str | None, str | None]:
    """Resolve quota presentation fields for one persisted provider record."""

    if not quota_lookup:
        return None, None, None
    pool_key = _provider_pool_key(config)
    candidates = [
        record
        for (record_provider_id, record_pool_key), record in quota_lookup.items()
        if record_provider_id == provider_id and (not pool_key or record_pool_key == pool_key)
    ]
    if not candidates:
        return None, None, None
    record = candidates[0]
    headroom = _headroom_from_record(record)
    reset_in = format_reset_in(record.reset_at)
    tos = record.tos_verdict or None
    return headroom, reset_in, tos


def _provider_pool_key(config: Mapping[str, object]) -> str:
    gateway = config.get("gateway")
    if not isinstance(gateway, Mapping):
        return ""
    catalog = gateway.get("catalog")
    if not isinstance(catalog, Mapping):
        return ""
    value = catalog.get("pool_key")
    return str(value).strip() if value is not None else ""


def _headroom_from_record(record: QuotaRecord) -> float | None:
    if record.budget_units is None or record.budget_units <= 0:
        return None
    used = record.used_units
    remaining = record.budget_units - used
    if remaining <= 0:
        return 0.0
    fraction = remaining / record.budget_units
    if fraction < 0:
        return 0.0
    if fraction > 1:
        return 1.0
    return float(fraction)


def _provider_entry_from_descriptor(descriptor: ProviderDescriptor) -> ProviderEntry:
    """Map the safe service model into presentation-only table values."""
    armed, active_pod_id = _armed_state(descriptor.config)
    return ProviderEntry(
        provider_id=descriptor.provider_id,
        status=descriptor.persisted_health,
        pricing_model=descriptor.pricing_kind,
        armed=armed,
        active_pod_id=active_pod_id,
        adapter_id=descriptor.adapter_id,
        credential_ref=descriptor.credential_ref,
        credential_configured=descriptor.credential_configured,
        capabilities=descriptor.capabilities,
    )


__all__ = [
    "ProviderEntry",
    "PostgresProvidersSource",
    "ProvidersScreen",
    "ProvidersSnapshot",
    "ProvidersSource",
    "RegistryProvidersSource",
    "ServiceProvidersSource",
    "StaticProvidersSource",
    "format_headroom",
    "format_providers_table",
    "format_reset_in",
    "format_tos_label",
]
