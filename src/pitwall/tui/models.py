"""Model catalogue screens and sources for the Textual operator console."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, cast

from markdown_it import MarkdownIt
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Markdown,
    MarkdownViewer,
    Static,
)
from textual.widgets._markdown import MarkdownTableOfContents

from pitwall.models import Catalogue, ModelDossier, UnknownModel, load_catalogue, reload
from pitwall.models.schema import Variant
from pitwall.tui.errors import source_failure_message
from pitwall.tui.hardware_fit import HardwareFitScreen, HardwareFitSource


class ModelCatalogueSource(Protocol):
    """Async provider for model catalogue list and dossier detail state."""

    async def load_models(self) -> ModelsSnapshot:
        """Return the catalogue rows for the model list."""

    async def load_model(self, model_id: str) -> ModelDossier:
        """Return one dossier by its catalogue model id."""

    async def refresh(self) -> ModelsSnapshot:
        """Reload the catalogue and return current list rows."""


@dataclass(frozen=True, slots=True)
class ModelDisplayRow:
    """Read-only catalogue row rendered in the models table."""

    model_id: str
    model: str
    vendor: str
    family: str
    variants: str
    default: str
    chat: str
    best_single_gpu: str = ""


@dataclass(frozen=True, slots=True)
class ModelsSnapshot:
    """Read-only model catalogue state rendered by the list screen."""

    rows: tuple[ModelDisplayRow, ...]


class StaticModelCatalogueSource:
    """Hermetic model catalogue source used by tests and local demos."""

    def __init__(
        self,
        snapshot: ModelsSnapshot,
        *,
        dossiers: dict[str, ModelDossier] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._dossiers = dossiers or {}
        self.load_count = 0
        self.detail_load_count = 0
        self.refresh_count = 0

    async def load_models(self) -> ModelsSnapshot:
        self.load_count += 1
        return self._snapshot

    async def load_model(self, model_id: str) -> ModelDossier:
        self.detail_load_count += 1
        try:
            return self._dossiers[model_id]
        except KeyError as exc:
            raise UnknownModel(model_id) from exc

    async def refresh(self) -> ModelsSnapshot:
        self.refresh_count += 1
        return self._snapshot


class LocalModelCatalogueSource:
    """Read packaged or locally configured dossiers without network access."""

    async def load_models(self) -> ModelsSnapshot:
        return _snapshot_from_catalogue(load_catalogue())

    async def load_model(self, model_id: str) -> ModelDossier:
        dossier = load_catalogue().get(model_id)
        if dossier is None:
            raise UnknownModel(model_id)
        return dossier

    async def refresh(self) -> ModelsSnapshot:
        reload()
        return await self.load_models()


class ModelsScreen(Screen[None]):
    """Read-only model catalogue list in the operator console."""

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("o", "show_overview", "Overview"),
        Binding("m", "show_models", "Models", show=False),
        Binding("v", "toggle_details", "Details"),
    ]

    def __init__(
        self, source: ModelCatalogueSource, hardware_fit_source: HardwareFitSource
    ) -> None:
        super().__init__(name="models")
        self._source = source
        self._hardware_fit_source = hardware_fit_source
        self._snapshot: ModelsSnapshot | None = None
        self._details_visible = False
        self._selected_model_id: str | None = None
        self._filter_query = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with Vertical(id="nav-panel"):
                yield Label("Pitwall", id="nav-title")
                yield ListView(
                    ListItem(Label("Overview"), id="nav-overview"),
                    ListItem(Label("Models"), id="nav-models"),
                    id="shell-nav",
                )
            with Vertical(id="models-panel"):
                yield Static("Models", id="models-title")
                table: DataTable[str] = DataTable(id="models-table")
                table.cursor_type = "row"
                table.zebra_stripes = True
                yield table
                yield Static("", id="models-empty", classes="summary")
                yield Static("", id="models-refreshed", classes="summary")
                yield Static("", id="models-details", classes="summary")
                yield Static("", id="models-error", classes="error")
        yield Footer()

    async def on_mount(self) -> None:
        self._configure_table()
        self._table().focus()
        await self._load()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def action_show_overview(self) -> None:
        await self.app.switch_screen("overview")

    async def action_show_models(self) -> None:
        await self._refresh()

    def action_toggle_details(self) -> None:
        if self._is_wide:
            return
        self._details_visible = not self._details_visible
        self._update_details()

    def apply_filter(self, query: str) -> None:
        """Render catalogue rows matching a transient case-insensitive query."""
        self._filter_query = query.casefold().strip()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def on_resize(self) -> None:
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._selected_model_id = str(event.row_key.value)
        self._update_details()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        await self._load_detail(str(event.row_key.value))

    async def _load_detail(self, model_id: str) -> None:
        self.query_one("#models-error", Static).update("")
        try:
            dossier = await self._source.load_model(model_id)
        except (
            Exception
        ) as exc:  # reason: an unavailable dossier detail must stay inline, never crash the app
            self.query_one("#models-error", Static).update(
                source_failure_message("Model detail unavailable", exc)
            )
            return
        await self.app.push_screen(ModelDetailScreen(dossier, self._hardware_fit_source))

    async def _load(self) -> None:
        await self._request(self._source.load_models)

    async def _refresh(self) -> None:
        await self._request(self._source.refresh)

    async def _request(self, request: Callable[[], Awaitable[ModelsSnapshot]]) -> None:
        self.query_one("#models-error", Static).update("")
        self.query_one("#models-empty", Static).update("")
        try:
            snapshot = await request()
        except (
            Exception
        ) as exc:  # reason: TUI refresh must degrade to an inline error, never crash the app
            self._snapshot = None
            self._selected_model_id = None
            self._table().clear()
            self._update_details()
            self.query_one("#models-refreshed", Static).update("")
            self.query_one("#models-error", Static).update(
                source_failure_message("Model catalogue unavailable", exc)
            )
            return
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: ModelsSnapshot) -> None:
        table = self._table()
        self._snapshot = snapshot
        self._configure_table()
        visible_rows = self._filtered_rows(snapshot.rows)
        for row in visible_rows:
            values = (
                (row.model, row.vendor, row.default, row.best_single_gpu)
                if not self._is_wide
                else (
                    row.model,
                    row.vendor,
                    row.default,
                    row.best_single_gpu,
                    row.variants,
                    row.family,
                    row.chat,
                )
            )
            table.add_row(*values, key=row.model_id)
        self.query_one("#models-empty", Static).update(
            "No models available" if not visible_rows else ""
        )
        self._selected_model_id = visible_rows[0].model_id if visible_rows else None
        hint = "" if self._is_wide else "→ more columns at ≥140 cols; press v for details"
        self.query_one("#models-refreshed", Static).update(hint or "Catalogue loaded")
        self._update_details()

    def _filtered_rows(self, rows: tuple[ModelDisplayRow, ...]) -> tuple[ModelDisplayRow, ...]:
        if not self._filter_query:
            return rows
        return tuple(
            row
            for row in rows
            if self._filter_query
            in " ".join(
                (row.model_id, row.model, row.vendor, row.family, row.default, row.chat)
            ).casefold()
        )

    @property
    def _is_wide(self) -> bool:
        return self.app.size.width >= 140

    def _configure_table(self) -> None:
        table = self._table()
        table.clear(columns=True)
        if self._is_wide:
            table.add_columns(
                "Model", "Vendor", "Default", "Best 1-GPU", "Variants", "Family", "Chat"
            )
        else:
            table.add_columns("Model", "Vendor", "Default", "Best 1-GPU")

    def _update_details(self) -> None:
        details = self.query_one("#models-details", Static)
        details.update("")
        if self._is_wide or not self._details_visible or self._snapshot is None:
            return
        row = next(
            (item for item in self._snapshot.rows if item.model_id == self._selected_model_id),
            None,
        )
        if row is not None:
            details.update(f"Variants: {row.variants} · Family: {row.family} · Chat: {row.chat}")

    def _table(self) -> DataTable[str]:
        return cast(DataTable[str], self.query_one("#models-table", DataTable))


class ModelDetailScreen(Screen[None]):
    """Markdown dossier detail screen for one catalogue model."""

    BINDINGS = [Binding("g", "show_fit", "Hardware fit")]

    def __init__(self, dossier: ModelDossier, hardware_fit_source: HardwareFitSource) -> None:
        super().__init__(name="model-detail")
        self._dossier = dossier
        self._hardware_fit_source = hardware_fit_source

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DossierMarkdownViewer(dossier_markdown(self._dossier), id="model-dossier")
        yield Footer()

    async def action_show_fit(self) -> None:
        default_index = next(
            index for index, variant in enumerate(self._dossier.variants) if variant.default
        )
        await self.app.push_screen(
            HardwareFitScreen(
                self._dossier,
                self._hardware_fit_source,
                variant_index=default_index,
                ttl_minutes=120,
                cloud="secure",
            )
        )


class _DossierMarkdown(Markdown):
    """Markdown document that exposes its source in diagnostic string form."""

    def __init__(
        self,
        source: str,
        *,
        parser_factory: Callable[[], MarkdownIt] | None,
        open_links: bool,
    ) -> None:
        super().__init__(source, parser_factory=parser_factory, open_links=open_links)
        self._source = source

    def __str__(self) -> str:
        return self._source


class DossierMarkdownViewer(MarkdownViewer):
    """Markdown viewer retaining dossier source for diagnostics and Pilot assertions."""

    def compose(self) -> ComposeResult:
        markdown = _DossierMarkdown(
            self._markdown or "",
            parser_factory=self._parser_factory,
            open_links=self._open_links,
        )
        markdown.can_focus = True
        yield markdown
        yield MarkdownTableOfContents(markdown)


def _companion_summary(variant: Variant) -> str:
    return (
        "<br>".join(
            f"{companion.kind}: {companion.repo}/{companion.file}"
            for companion in variant.companions
        )
        or "none"
    )


def dossier_markdown(dossier: ModelDossier) -> str:
    """Build Markdown detail while retaining the dossier's authored research body."""

    badge = "> **not servable via the OpenAI proxy**\n\n" if not dossier.openai_chat else ""
    rows = [
        "| Variant | Engine | Format | VRAM floor | Context | Confidence | Evidence | Companions |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    rows.extend(
        f"| {variant.id} | {variant.engine} | {variant.format} | {variant.min_vram_gb} | "
        f"{variant.context} | {variant.confidence} | "
        f"{variant.evidence.kind if variant.evidence is not None else 'none'} | "
        f"{_companion_summary(variant)} |"
        for variant in dossier.variants
    )
    return f"# {dossier.model_id}\n\n{badge}" + "\n".join(rows) + "\n\n" + dossier.body


def _snapshot_from_catalogue(catalogue: Catalogue) -> ModelsSnapshot:
    return ModelsSnapshot(rows=tuple(_display_row(dossier) for dossier in catalogue.models()))


def _display_row(dossier: ModelDossier) -> ModelDisplayRow:
    default_variant = dossier.resolve_variant(None)
    return ModelDisplayRow(
        model_id=dossier.model_id,
        model=dossier.model_id,
        vendor=dossier.vendor,
        family=dossier.family,
        variants=str(len(dossier.variants)),
        default=default_variant.id if default_variant is not None else "",
        chat="yes" if dossier.openai_chat else "no",
        best_single_gpu=_best_single_gpu(default_variant),
    )


def _best_single_gpu(variant: Variant | None) -> str:
    """Return the catalogue's preferred single-GPU label for a variant."""
    if variant is None:
        return ""
    if variant.recommended_gpu_classes:
        return variant.recommended_gpu_classes[0]
    return f"≥{variant.min_vram_gb} GB"


__all__ = [
    "LocalModelCatalogueSource",
    "ModelCatalogueSource",
    "ModelDetailScreen",
    "ModelDisplayRow",
    "ModelsScreen",
    "ModelsSnapshot",
    "StaticModelCatalogueSource",
    "dossier_markdown",
]
