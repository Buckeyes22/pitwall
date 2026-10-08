"""RunPod Resources view backed only by the shared control-plane service."""

from __future__ import annotations

import json
import uuid
from typing import cast

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static

from pitwall.runpod_control_plane import (
    MutationResult,
    RunPodControlPlaneError,
)
from pitwall.tui.errors import source_failure_message
from pitwall.tui.onboarding import OnboardingSource, RunPodOnboardingPanel
from pitwall.tui.resources_model import (
    RegistryAuthEntry,
    ResourceEndpointEntry,
    ResourceHubTemplateEntry,
    ResourceMutationDraft,
    ResourceMutationOperation,
    ResourcePodEntry,
    ResourceQueryOperation,
    ResourcesSnapshot,
    ResourcesSource,
    ResourceTemplateEntry,
    ResourceVolumeEntry,
    StaticResourcesSource,
    display_text,
    filtered_snapshot,
    format_endpoint_table,
    format_hub_template_table,
    format_pod_table,
    format_registry_table,
    format_template_table,
    format_volume_table,
)
from pitwall.tui.resources_source import RunPodResourcesSource
from pitwall.tui.runpod_market import RunpodMarketPanel, RunpodMarketSource

_MUTATION_OPTIONS: tuple[tuple[str, ResourceMutationOperation], ...] = (
    ("Pod · create", "pod.create"),
    ("Pod · update", "pod.update"),
    ("Pod · start/stop/restart/reset", "pod.action"),
    ("Pod · terminate", "pod.terminate"),
    ("Endpoint · create", "endpoint.create"),
    ("Endpoint · update workers/scaling/GPU pools", "endpoint.update"),
    ("Endpoint · delete", "endpoint.delete"),
    ("Account template · create", "template.create"),
    ("Account template · update", "template.update"),
    ("Account template · delete", "template.delete"),
    ("Volume · create", "volume.create"),
    ("Volume · grow", "volume.grow"),
    ("Volume · delete", "volume.delete"),
    ("Registry auth · create", "registry_auth.create"),
    ("Registry auth · replace (delete/recreate)", "registry_auth.replace"),
    ("Registry auth · delete", "registry_auth.delete"),
)
_QUERY_OPTIONS: tuple[tuple[str, ResourceQueryOperation], ...] = (
    ("Pod · get", "pod.get"),
    ("Endpoint · get", "endpoint.get"),
    ("Account template · get", "template.get"),
    ("Volume · get", "volume.get"),
    ("Registry auth · get", "registry_auth.get"),
    ("Hub template · get", "hub_template.get"),
    ("Hub templates · search", "hub_template.search"),
)
_MAX_TUI_JSON_BYTES = 65_536


class ResourceMutationEditorModal(ModalScreen[ResourceMutationDraft | None]):
    """Collect one explicitly selected, strict mutation payload."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    CSS = """
    ResourceMutationEditorModal {
        align: center middle;
    }

    #resource-mutation-editor {
        width: 90;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }

    #resource-mutation-editor Horizontal {
        height: auto;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-mutation-editor"):
            yield Static("Prepare RunPod mutation", id="resource-mutation-title")
            yield Select(_MUTATION_OPTIONS, id="resource-mutation-operation", allow_blank=False)
            yield Input(
                placeholder='Strict JSON, e.g. {"resource_id":"pod_123","action":"stop"}',
                id="resource-mutation-json",
            )
            yield Static(
                "Preview performs no provider write. Live apply requires the exact resource id or create name.",
                id="resource-mutation-help",
            )
            yield Static("", id="resource-mutation-error", classes="error")
            with Horizontal():
                yield Button("Cancel", id="resource-mutation-cancel")
                yield Button("Preview", id="resource-mutation-preview", variant="primary")

    @on(Button.Pressed, "#resource-mutation-cancel")
    def cancel_button(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#resource-mutation-preview")
    def preview_button(self) -> None:
        try:
            operation = cast(ResourceMutationOperation, self.query_one(Select).value)
            payload = _parse_payload(self.query_one("#resource-mutation-json", Input).value)
            draft = ResourceMutationDraft(
                operation=operation,
                payload=payload,
                idempotency_key=f"tui-{uuid.uuid4().hex}",
            )
            _ = draft.confirmation_target
        except (ValueError, TypeError) as exc:
            self.query_one("#resource-mutation-error", Static).update(str(exc))
            return
        self.dismiss(draft)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ResourceMutationConfirmModal(ModalScreen[bool]):
    """Show the mandatory preview and require the exact typed target."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    CSS = """
    ResourceMutationConfirmModal {
        align: center middle;
    }

    #resource-mutation-confirm {
        width: 82;
        height: auto;
        padding: 1 2;
        border: thick $error;
        background: $surface;
    }

    #resource-mutation-confirm Horizontal {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, draft: ResourceMutationDraft, preview: MutationResult) -> None:
        super().__init__()
        self._draft = draft
        self._preview = preview

    def compose(self) -> ComposeResult:
        target = self._draft.confirmation_target
        consequences = (
            "irreversible" if self._preview.irreversible else "reversible/provider-defined"
        )
        with Vertical(id="resource-mutation-confirm"):
            yield Static("Confirm RunPod mutation", id="resource-confirm-title")
            yield Static(
                "\n".join(
                    (
                        "Provider: runpod",
                        f"Operation: {self._preview.operation}",
                        f"Resource: {self._preview.resource_id or target}",
                        f"Effect: {self._preview.effect}",
                        f"Estimated ceiling: {self._preview.estimated_ceiling or 'unavailable'}",
                        f"Consequences: {consequences}",
                    )
                ),
                id="resource-confirm-summary",
            )
            yield Static(f"Type {target} exactly to apply.", id="resource-confirm-instruction")
            yield Input(id="resource-confirm-text")
            with Horizontal():
                yield Button("Cancel", id="resource-confirm-cancel")
                yield Button("Apply", id="resource-confirm-apply", variant="error", disabled=True)

    @on(Input.Changed, "#resource-confirm-text")
    def enable_exact_confirmation(self, event: Input.Changed) -> None:
        self.query_one("#resource-confirm-apply", Button).disabled = (
            event.value != self._draft.confirmation_target
        )

    @on(Button.Pressed, "#resource-confirm-cancel")
    def cancel_button(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#resource-confirm-apply")
    def apply_button(self) -> None:
        button = self.query_one("#resource-confirm-apply", Button)
        if not button.disabled:
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ResourceQueryModal(ModalScreen[tuple[ResourceQueryOperation, str] | None]):
    """Collect one explicit resource get or Hub search."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    CSS = """
    ResourceQueryModal {
        align: center middle;
    }

    #resource-query-dialog {
        width: 72;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $surface;
    }

    #resource-query-dialog Horizontal {
        height: auto;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-query-dialog"):
            yield Static("RunPod lookup / Hub search")
            yield Select(_QUERY_OPTIONS, id="resource-query-operation", allow_blank=False)
            yield Input(placeholder="Resource id or Hub search text", id="resource-query-value")
            yield Static("", id="resource-query-error", classes="error")
            with Horizontal():
                yield Button("Cancel", id="resource-query-cancel")
                yield Button("Run read", id="resource-query-run", variant="primary")

    @on(Button.Pressed, "#resource-query-cancel")
    def cancel_button(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#resource-query-run")
    def query_button(self) -> None:
        value = self.query_one("#resource-query-value", Input).value.strip()
        if not value:
            self.query_one("#resource-query-error", Static).update(
                "a resource id or query is required"
            )
            return
        operation = cast(ResourceQueryOperation, self.query_one(Select).value)
        self.dismiss((operation, value))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ResourcesScreen(Screen[None]):
    """One RunPod account resource screen with read-only default behavior."""

    BINDINGS = [Binding("r", "refresh", "Refresh")]

    def __init__(
        self,
        source: ResourcesSource,
        runpod_market_source: RunpodMarketSource | None = None,
        onboarding_source: OnboardingSource | None = None,
    ) -> None:
        super().__init__(name="resources")
        self._source = source
        self._runpod_market_source = runpod_market_source
        self._onboarding_source = onboarding_source
        self._snapshot: ResourcesSnapshot | None = None
        self._filter = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="shell"):
            with Vertical(id="nav-panel"):
                yield Label("Pitwall", id="nav-title")
                yield ListView(
                    ListItem(Label("Overview"), id="nav-overview"),
                    ListItem(Label("Providers"), id="nav-providers"),
                    ListItem(Label("Leases"), id="nav-leases"),
                    ListItem(Label("Cost"), id="nav-cost"),
                    ListItem(Label("Resources"), id="nav-resources"),
                    id="shell-nav",
                )
            with VerticalScroll(id="resources-panel"):
                yield Static("Resources", id="resources-title")
                yield Static("Loading resources", id="resources-summary", classes="summary")
                with Horizontal(id="resource-actions"):
                    yield Button("Lookup / Hub search", id="resources-query")
                    yield Button("Prepare mutation", id="resources-mutate", variant="warning")
                yield Static("", id="resources-filter", classes="summary", markup=False)
                yield Static("Pods", classes="resource-section")
                yield Static(
                    "Loading pods", id="pods-table", classes="resource-table", markup=False
                )
                yield Static("Endpoints", classes="resource-section")
                yield Static(
                    "Loading endpoints",
                    id="endpoints-table",
                    classes="resource-table",
                    markup=False,
                )
                yield Static("Account templates", classes="resource-section")
                yield Static(
                    "Loading templates",
                    id="templates-table",
                    classes="resource-table",
                    markup=False,
                )
                yield Static("Hub templates · read-only", classes="resource-section")
                yield Static(
                    "Loading Hub templates",
                    id="hub-templates-table",
                    classes="resource-table",
                    markup=False,
                )
                yield Static("Volumes", classes="resource-section")
                yield Static(
                    "Loading volumes",
                    id="volumes-table",
                    classes="resource-table",
                    markup=False,
                )
                yield Static("Registry auths", classes="resource-section")
                yield Static(
                    "Loading registry auths",
                    id="registry-table",
                    classes="resource-table",
                    markup=False,
                )
                yield Static("", id="resources-operation-result", markup=False)
                yield Static("", id="resources-refreshed", classes="summary")
                yield Static("", id="resources-error", classes="error", markup=False)
                if self._runpod_market_source is not None:
                    yield RunpodMarketPanel(
                        self._runpod_market_source,
                        id="runpod-market-panel",
                    )
                if self._onboarding_source is not None:
                    yield RunPodOnboardingPanel(self._onboarding_source)
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def action_refresh(self) -> None:
        await self._refresh()
        panels = self.query(RunpodMarketPanel)
        if panels:
            await panels.first().load_snapshot(force_refresh=True)

    def apply_filter(self, query: str) -> None:
        self._filter = query.strip().casefold()
        self.query_one("#resources-filter", Static).update(
            f"Filter: {query.strip()}" if query.strip() else ""
        )
        if self._snapshot is not None:
            self._render_snapshot(filtered_snapshot(self._snapshot, self._filter))

    async def _refresh(self) -> None:
        self.query_one("#resources-summary", Static).update("Loading resources")
        self.query_one("#resources-error", Static).update("")
        try:
            snapshot = await self._source.load_resources()
        except Exception as exc:  # reason: degrade inline without leaking transport state
            failure = source_failure_message("Resources unavailable", exc)
            if self._snapshot is not None:
                # A failed refresh keeps the last good snapshot instead of blanking it.
                self._render_snapshot(filtered_snapshot(self._snapshot, self._filter))
                self.query_one("#resources-error", Static).update(failure)
                return
            for widget_id in (
                "resources-summary",
                "pods-table",
                "endpoints-table",
                "templates-table",
                "hub-templates-table",
                "volumes-table",
                "registry-table",
            ):
                widget = self.query_one(f"#{widget_id}", Static)
                if str(widget.content).startswith("Loading "):
                    widget.update("Unavailable")
            self.query_one("#resources-error", Static).update(failure)
            return
        self._snapshot = snapshot
        self._render_snapshot(filtered_snapshot(snapshot, self._filter))

    def _render_snapshot(self, snapshot: ResourcesSnapshot) -> None:
        self.query_one("#resources-summary", Static).update(snapshot.summary)
        self.query_one("#pods-table", Static).update(format_pod_table(snapshot.pods))
        self.query_one("#endpoints-table", Static).update(format_endpoint_table(snapshot.endpoints))
        self.query_one("#templates-table", Static).update(format_template_table(snapshot.templates))
        self.query_one("#hub-templates-table", Static).update(
            format_hub_template_table(snapshot.hub_templates)
        )
        self.query_one("#volumes-table", Static).update(format_volume_table(snapshot.volumes))
        self.query_one("#registry-table", Static).update(
            format_registry_table(snapshot.registry_auths)
        )
        self.query_one("#resources-refreshed", Static).update(
            f"Last refreshed: {snapshot.refreshed_label}"
        )
        if snapshot.unavailable_sections:
            errors = snapshot.unavailable_errors or ("unknown",) * len(
                snapshot.unavailable_sections
            )
            labelled = ", ".join(
                f"{name} ({error})"
                for name, error in zip(snapshot.unavailable_sections, errors, strict=False)
            )
            self.query_one("#resources-error", Static).update(
                "Provider error · unavailable: " + labelled
            )

    @work(exclusive=True, group="runpod-resource-query")
    async def _open_query(self) -> None:
        request = await self.app.push_screen_wait(ResourceQueryModal())
        if request is None:
            return
        operation, value = request
        self.query_one("#resources-error", Static).update("")
        try:
            result = await self._source.query_resource(operation, value)
        except Exception as exc:  # reason: provider read failures remain inline and safe
            self.query_one("#resources-operation-result", Static).update("")
            self.query_one("#resources-error", Static).update(
                source_failure_message("Resource read unavailable", exc)
            )
            return
        self.query_one("#resources-operation-result", Static).update(
            json.dumps(result, indent=2, sort_keys=True)
        )

    @work(exclusive=True, group="runpod-resource-mutation")
    async def _open_mutation(self) -> None:
        draft = await self.app.push_screen_wait(ResourceMutationEditorModal())
        if draft is None:
            return
        self.query_one("#resources-error", Static).update("")
        try:
            preview = await self._source.preview_mutation(draft)
        except Exception as exc:  # reason: preview failures stay inline and perform no write
            self.query_one("#resources-operation-result", Static).update("")
            self.query_one("#resources-error", Static).update(
                source_failure_message("Mutation preview failed", exc)
            )
            return
        confirmed = await self.app.push_screen_wait(ResourceMutationConfirmModal(draft, preview))
        if not confirmed:
            return
        try:
            result = await self._source.apply_mutation(draft)
        except Exception as exc:  # reason: failures remain inline and preserve the shell
            message = source_failure_message("Mutation failed", exc)
            self.query_one("#resources-operation-result", Static).update("")
            if isinstance(exc, RunPodControlPlaneError) and exc.changed:
                await self._refresh()
            self.query_one("#resources-error", Static).update(message)
            return
        self.query_one("#resources-operation-result", Static).update(
            json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True)
        )
        await self._refresh()

    @on(Button.Pressed, "#resources-query")
    def query_button(self) -> None:
        self._open_query()

    @on(Button.Pressed, "#resources-mutate")
    def mutation_button(self) -> None:
        self._open_mutation()


def _parse_payload(value: str) -> dict[str, object]:
    if len(value.encode("utf-8")) > _MAX_TUI_JSON_BYTES:
        raise ValueError("mutation JSON exceeds 65536 bytes")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("mutation payload must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("mutation payload must be a JSON object")
    if "intent" in parsed or "idempotency_key" in parsed:
        raise ValueError("intent and idempotency_key are controlled by the preview workflow")
    return cast(dict[str, object], parsed)


__all__ = [
    "RegistryAuthEntry",
    "ResourceEndpointEntry",
    "ResourceHubTemplateEntry",
    "ResourceMutationConfirmModal",
    "ResourceMutationDraft",
    "ResourceMutationEditorModal",
    "ResourcePodEntry",
    "ResourceTemplateEntry",
    "ResourceVolumeEntry",
    "ResourcesScreen",
    "ResourcesSnapshot",
    "ResourcesSource",
    "RunPodResourcesSource",
    "StaticResourcesSource",
    "display_text",
    "format_endpoint_table",
    "format_hub_template_table",
    "format_pod_table",
    "format_registry_table",
    "format_template_table",
    "format_volume_table",
]
