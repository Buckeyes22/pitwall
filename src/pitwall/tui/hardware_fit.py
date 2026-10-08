"""Hardware-fit source adapters and screen for catalogue variants."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from pitwall.api.provider_schemas import warm_cache_matches
from pitwall.config import PitwallSettings, load_settings_from_env
from pitwall.models.fit import CacheState, Cloud, FitOption, fit_options
from pitwall.models.prices import GpuPriceSnapshot, gpu_price_freshness, load_gpu_price_snapshot
from pitwall.models.schema import ModelDossier, Variant
from pitwall.runpod_market import RunpodMarketService
from pitwall.serve import ServeRequest
from pitwall.tui.errors import source_failure_message
from pitwall.tui.serve import ServeActionSource, ServePreviewScreen

_CACHE_LOOKUP_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class HardwareFitSnapshot:
    """Fit options plus the provenance of the GPU-price snapshot."""

    options: tuple[FitOption, ...]
    checked_at: datetime
    price_source: Literal["live", "fallback"]
    price_max_age_s: int = 3600

    @classmethod
    def from_price_snapshot(
        cls,
        variant: Variant,
        snapshot: GpuPriceSnapshot,
        *,
        ttl_minutes: int,
        cloud: Cloud,
        price_max_age_s: int = 3600,
        cache_state: CacheState = "not_checked",
    ) -> HardwareFitSnapshot:
        """Build an immutable fit display snapshot from GPU price data."""
        return cls(
            options=tuple(
                fit_options(
                    variant,
                    gpu_types=snapshot.gpu_types,
                    ttl_minutes=ttl_minutes,
                    cloud=cloud,
                    warm_cache=cache_state == "warm",
                    cache_state=cache_state,
                )
            ),
            checked_at=snapshot.checked_at,
            price_source=snapshot.source,
            price_max_age_s=price_max_age_s,
        )

    def price_state(self, now: datetime | None = None, max_age_s: int | None = None) -> str:
        """Return the user-facing price freshness state."""
        if self.price_source == "fallback":
            return "unpriced"
        current = now or datetime.now(UTC)
        freshness = gpu_price_freshness(
            GpuPriceSnapshot(gpu_types=(), checked_at=self.checked_at, source=self.price_source),
            max_age_s=self.price_max_age_s if max_age_s is None else max_age_s,
            now=current,
        )
        age_seconds = freshness.age_seconds
        age_minutes = age_seconds // 60
        if freshness.stale:
            return f"stale ({age_minutes // 60}h {age_minutes % 60}m old)"
        return f"live ({age_minutes}m old)"


class HardwareFitSource(Protocol):
    """Async provider for hardware fit state."""

    async def load_fit(
        self,
        variant: Variant,
        *,
        capability_name: str,
        ttl_minutes: int,
        cloud: Cloud,
    ) -> HardwareFitSnapshot:
        """Return GPU fit options for one variant."""


class StaticHardwareFitSource:
    """Scripted hardware-fit source for tests and local demos."""

    def __init__(self, snapshot: HardwareFitSnapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[tuple[str, int, Cloud]] = []

    async def load_fit(
        self,
        variant: Variant,
        *,
        capability_name: str,
        ttl_minutes: int,
        cloud: Cloud,
    ) -> HardwareFitSnapshot:
        del capability_name
        self.calls.append((variant.id, ttl_minutes, cloud))
        return self._snapshot


class LiveHardwareFitSource:
    """Fit source backed by the cached RunPod GPU-price snapshot."""

    def __init__(
        self,
        settings: PitwallSettings | None = None,
        *,
        market_service: RunpodMarketService | None = None,
    ) -> None:
        self._settings = settings or load_settings_from_env()
        self._market_service = market_service

    async def load_fit(
        self,
        variant: Variant,
        *,
        capability_name: str,
        ttl_minutes: int,
        cloud: Cloud,
    ) -> HardwareFitSnapshot:
        snapshot = (
            await load_gpu_price_snapshot(
                cloud=cloud,
                settings=self._settings,
                market_service=self._market_service,
            )
            if self._market_service is not None
            else await load_gpu_price_snapshot(cloud=cloud)
        )
        cache_state = await self._cache_state(variant, capability_name=capability_name)
        return HardwareFitSnapshot.from_price_snapshot(
            variant,
            snapshot,
            ttl_minutes=ttl_minutes,
            cloud=cloud,
            price_max_age_s=self._settings.pitwall_price_max_age_s or 3600,
            cache_state=cache_state,
        )

    async def _cache_state(self, variant: Variant, *, capability_name: str) -> CacheState:
        if not self._settings.database_url.strip():
            return "not_checked"
        try:
            from pitwall.db import get_pool
            from pitwall.db.repository import ProviderRepository

            async def lookup() -> CacheState:
                # The shared pool factory: one pool for the process, not one per lookup.
                pool = await get_pool(self._settings.database_url, min_size=1, max_size=1)
                provider = await ProviderRepository(pool).get_by_name(f"serve-{capability_name}")
                if provider is None:
                    return "cold"
                return "warm" if warm_cache_matches(provider.config, variant=variant.id) else "cold"

            return await asyncio.wait_for(lookup(), timeout=_CACHE_LOOKUP_TIMEOUT_S)
        except Exception:  # reason: an unreachable optional registry is an explicit unknown state
            return "not_checked"


def _companion_summary(variant: Variant) -> str:
    return (
        ", ".join(
            f"{companion.kind}: {companion.repo}/{companion.file}"
            for companion in variant.companions
        )
        or "none"
    )


class HardwareFitScreen(Screen[None]):
    """Read-only hardware fit table for a selected model variant."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("c", "toggle_cloud", "Cloud"),
        Binding("v", "toggle_details", "Details"),
        Binding("shift+v", "cycle_variant", "Variant"),
    ]

    def __init__(
        self,
        dossier: ModelDossier,
        source: HardwareFitSource,
        *,
        variant_index: int = 0,
        ttl_minutes: int = 120,
        cloud: Cloud = "secure",
    ) -> None:
        super().__init__(name="hardware-fit")
        self._dossier = dossier
        self._source = source
        self._variant_index = variant_index
        self._ttl_minutes = ttl_minutes
        self._cloud = cloud
        self._options: tuple[FitOption, ...] = ()
        self._snapshot: HardwareFitSnapshot | None = None
        self._details_visible = False
        self._selected_gpu_class: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="fit-panel"):
            yield Static("", id="fit-summary")
            yield Static("", id="fit-price-state", classes="summary")
            yield Static("", id="fit-details", classes="summary")
            table: DataTable[str] = DataTable(id="fit-table")
            table.cursor_type = "row"
            table.zebra_stripes = True
            yield table
            yield Static("", id="fit-error", classes="error")
        yield Footer()

    async def on_mount(self) -> None:
        self._configure_table()
        self._table().focus()
        await self._load()

    async def action_refresh(self) -> None:
        await self._load()

    async def action_toggle_cloud(self) -> None:
        self._cloud = "community" if self._cloud == "secure" else "secure"
        await self._load()

    async def action_cycle_variant(self) -> None:
        self._variant_index = (self._variant_index + 1) % len(self._dossier.variants)
        await self._load()

    def action_toggle_details(self) -> None:
        if self._is_wide:
            return
        self._details_visible = not self._details_visible
        self._update_details()

    def on_resize(self) -> None:
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._selected_gpu_class = str(event.row_key.value)
        self._update_details()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        raw_source = getattr(self.app, "serve_action_source", None)
        if raw_source is None:
            self.query_one("#fit-error", Static).update("Model launch is unavailable")
            return
        source = cast(ServeActionSource, raw_source)
        option = next(
            (item for item in self._options if item.gpu_class == str(event.row_key.value)),
            None,
        )
        if option is None:
            return
        rate_per_second = (
            option.price_per_hour / Decimal(3600) if option.price_per_hour is not None else None
        )
        request = ServeRequest(
            capability_name=self._dossier.pitwall.capability_name,
            model=self._dossier.model_id,
            served_model_name=self._dossier.pitwall.served_model_name,
            variant=self._variant.id,
            engine=self._variant.engine,
            gpu_class=option.gpu_class,
            gpu_count=option.gpu_count,
            ttl_minutes=self._ttl_minutes,
            rate_per_second=rate_per_second,
            dry_run=True,
        )
        await self.app.push_screen(
            ServePreviewScreen(self._dossier, self._variant, option, request, source)
        )

    async def _load(self) -> None:
        self.query_one("#fit-error", Static).update("")
        try:
            snapshot = await self._source.load_fit(
                self._variant,
                capability_name=self._dossier.pitwall.capability_name,
                ttl_minutes=self._ttl_minutes,
                cloud=self._cloud,
            )
        except Exception as exc:  # reason: TUI fit loading must show inline failures
            self._snapshot = None
            self._selected_gpu_class = None
            self._table().clear()
            self._options = ()
            self._update_details()
            self.query_one("#fit-price-state", Static).update("")
            self.query_one("#fit-error", Static).update(
                source_failure_message("Hardware fit unavailable", exc)
            )
            return
        self._render_snapshot(snapshot)

    @property
    def _variant(self) -> Variant:
        return self._dossier.variants[self._variant_index]

    def _render_snapshot(self, snapshot: HardwareFitSnapshot) -> None:
        self._snapshot = snapshot
        self._configure_table()
        self._options = snapshot.options
        self._selected_gpu_class = snapshot.options[0].gpu_class if snapshot.options else None
        for option in snapshot.options:
            values = (
                (
                    option.gpu_class,
                    str(option.gpu_count),
                    option.fit,
                    _money(option.price_per_hour),
                    _money(option.cost_for_ttl),
                )
                if not self._is_wide
                else (
                    option.gpu_class,
                    str(option.gpu_count),
                    f"{option.vram_gb} GB",
                    "unknown" if option.headroom_gb is None else f"{option.headroom_gb} GB",
                    option.fit,
                    option.cache_state.replace("_", " "),
                    _money(option.price_per_hour),
                    _money(option.cost_for_ttl),
                    str(option.max_count),
                )
            )
            self._table().add_row(*values, key=option.gpu_class)
        evidence_kind = (
            self._variant.evidence.kind if self._variant.evidence is not None else "none"
        )
        badge = " · not servable via the OpenAI proxy" if not self._dossier.openai_chat else ""
        self.query_one("#fit-summary", Static).update(
            f"{self._dossier.model_id} · {self._variant.id} · confidence: "
            f"{self._variant.confidence} · evidence: {evidence_kind} · companions: "
            f"{_companion_summary(self._variant)}{badge} · TTL: {self._ttl_minutes}m · "
            f"cloud: {self._cloud} · cache: "
            f"{(snapshot.options[0].cache_state if snapshot.options else 'not_checked').replace('_', ' ')}"
        )
        hint = "" if self._is_wide else "→ more columns at ≥140 cols; press v for details"
        self.query_one("#fit-price-state", Static).update(hint or snapshot.price_state())
        self._update_details()

    @property
    def _is_wide(self) -> bool:
        return self.app.size.width >= 140

    def _configure_table(self) -> None:
        table = self._table()
        table.clear(columns=True)
        if self._is_wide:
            table.add_columns(
                "GPU", "Count", "VRAM", "Headroom", "Fit", "Cache", "$/hr", "TTL Cost", "Max"
            )
        else:
            table.add_columns("GPU", "Count", "Fit", "$/hr", "TTL Cost")

    def _update_details(self) -> None:
        details = self.query_one("#fit-details", Static)
        details.update("")
        if self._is_wide or not self._details_visible:
            return
        option = next(
            (item for item in self._options if item.gpu_class == self._selected_gpu_class),
            None,
        )
        if option is not None:
            headroom = "unknown" if option.headroom_gb is None else f"{option.headroom_gb} GB"
            snapshot_age = self._snapshot.price_state() if self._snapshot is not None else "unknown"
            details.update(
                f"VRAM: {option.vram_gb} GB · Headroom: {headroom} · Cache: {option.cache_state.replace('_', ' ')} · Max: {option.max_count} · confidence: {self._variant.confidence} · snapshot: {snapshot_age}"
            )

    def _table(self) -> DataTable[str]:
        return cast(DataTable[str], self.query_one("#fit-table", DataTable))


def _money(value: Decimal | None) -> str:
    return "unpriced" if value is None else f"${value:.2f}"


__all__ = [
    "HardwareFitScreen",
    "HardwareFitSnapshot",
    "HardwareFitSource",
    "LiveHardwareFitSource",
    "StaticHardwareFitSource",
]
