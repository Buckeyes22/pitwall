"""Guarded dry-run preview and launch actions for the Textual console."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Static

from pitwall.config import PitwallSettings
from pitwall.models.fit import FitOption
from pitwall.models.lookup import CatalogueLookup
from pitwall.models.schema import ModelDossier, Variant
from pitwall.serve import ServeRequest, ServeResult, serve_model
from pitwall.tui.errors import source_failure_message

_REDACTED = "***REDACTED***"
_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "authorization")


class ServeActionSource(Protocol):
    """Async boundary between console widgets and the serve-model service."""

    async def preview(self, request: ServeRequest) -> ServeResult:
        """Return the mandatory dry-run launch preview."""

    async def launch(self, request: ServeRequest) -> ServeResult:
        """Launch after the operator completes confirmation."""


class PitwallServeActionSource:
    """Production adapter for the shared serve-model service."""

    def __init__(
        self,
        pool: Any,
        *,
        base_url: str,
        settings: PitwallSettings,
        catalogue: CatalogueLookup | None,
    ) -> None:
        self._pool = pool
        self._base_url = base_url
        self._settings = settings
        self._catalogue = catalogue

    async def preview(self, request: ServeRequest) -> ServeResult:
        return await serve_model(
            self._pool,
            request.model_copy(update={"dry_run": True}),
            base_url=self._base_url,
            settings=self._settings,
            catalogue=self._catalogue,
        )

    async def launch(self, request: ServeRequest) -> ServeResult:
        return await serve_model(
            self._pool,
            request.model_copy(update={"dry_run": False}),
            base_url=self._base_url,
            settings=self._settings,
            catalogue=self._catalogue,
        )


class ScriptedServeActionSource:
    """Hermetic serve source with scripted results and exact call recording."""

    def __init__(
        self,
        *,
        preview_result: ServeResult | None = None,
        launch_result: ServeResult | None = None,
        preview_error: Exception | None = None,
        launch_error: Exception | None = None,
    ) -> None:
        self._preview_result = preview_result
        self._launch_result = launch_result
        self._preview_error = preview_error
        self._launch_error = launch_error
        self.calls: list[tuple[str, bool]] = []

    async def preview(self, request: ServeRequest) -> ServeResult:
        self.calls.append(("preview", request.dry_run))
        if self._preview_error is not None:
            raise self._preview_error
        if self._preview_result is None:
            raise RuntimeError("no scripted preview result")
        return self._preview_result

    async def launch(self, request: ServeRequest) -> ServeResult:
        self.calls.append(("launch", request.dry_run))
        if self._launch_error is not None:
            raise self._launch_error
        if self._launch_result is None:
            raise RuntimeError("no scripted launch result")
        return self._launch_result


def redacted_preview(value: object) -> object:
    """Recursively redact values stored under secret-looking mapping keys."""
    if isinstance(value, Mapping):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            normalized_key = str(key).casefold()
            redacted[key] = (
                _REDACTED
                if any(part in normalized_key for part in _SECRET_KEY_PARTS)
                else redacted_preview(item)
            )
        return redacted
    if isinstance(value, list):
        return [redacted_preview(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redacted_preview(item) for item in value)
    return value


class ConfirmLaunchModal(ModalScreen[bool]):
    """Require the exact capability name before allowing a paid launch."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    CSS = """
    ConfirmLaunchModal {
        align: center middle;
    }

    #confirm-panel {
        width: 74;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }

    #confirm-actions {
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        dossier: ModelDossier,
        variant: Variant,
        option: FitOption,
        *,
        ttl_minutes: int,
    ) -> None:
        super().__init__()
        self._dossier = dossier
        self._variant = variant
        self._option = option
        self._ttl_minutes = ttl_minutes

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-panel"):
            yield Static("Confirm model launch", id="confirm-title")
            yield Static(
                "\n".join(
                    (
                        f"Capability: {self._dossier.pitwall.capability_name}",
                        f"Model / variant: {self._dossier.model_id} / {self._variant.id}",
                        f"GPU: {self._option.gpu_class} × {self._option.gpu_count}",
                        f"Fit verdict: {self._option.fit}",
                        f"TTL ({self._ttl_minutes}m): {_money(self._option.cost_for_ttl)}",
                    )
                ),
                id="confirm-summary",
            )
            yield Static(
                f"Type {self._dossier.pitwall.capability_name} exactly to launch.",
                id="confirm-instruction",
            )
            yield Input(id="confirm-text")
            with Horizontal(id="confirm-actions"):
                yield Button("Cancel", id="confirm-cancel")
                yield Button("Launch", id="confirm-launch", variant="error", disabled=True)

    @on(Input.Changed, "#confirm-text")
    def enable_exact_confirmation(self, event: Input.Changed) -> None:
        self.query_one("#confirm-launch", Button).disabled = (
            event.value != self._dossier.pitwall.capability_name
        )

    @on(Button.Pressed, "#confirm-cancel")
    def cancel_button(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#confirm-launch")
    def confirm_button(self) -> None:
        button = self.query_one("#confirm-launch", Button)
        if not button.disabled:
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ServePreviewScreen(Screen[None]):
    """Mandatory dry-run preview followed by type-to-confirm launch."""

    BINDINGS = [Binding("enter", "confirm", "Confirm launch")]

    def __init__(
        self,
        dossier: ModelDossier,
        variant: Variant,
        option: FitOption,
        request: ServeRequest,
        source: ServeActionSource,
    ) -> None:
        super().__init__(name="serve-preview")
        self._dossier = dossier
        self._variant = variant
        self._option = option
        self._request = request
        self._source = source
        self._preview_complete = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="serve-panel"):
            yield Static(
                f"Fit verdict: {self._option.fit} · confidence: {self._variant.confidence}",
                id="serve-fit",
            )
            yield Static(
                f"TTL estimate: {_money(self._option.cost_for_ttl)}",
                id="serve-cost",
            )
            yield Static("Running mandatory dry-run preview…", id="serve-preview-content")
            yield Static("", id="serve-status")
            yield Static("", id="serve-error", classes="error")
        yield Footer()

    async def on_mount(self) -> None:
        try:
            result = await self._source.preview(self._request)
        except Exception as exc:  # reason: preview failures must remain inline in the console
            self.query_one("#serve-preview-content", Static).update("")
            self.query_one("#serve-error", Static).update(
                source_failure_message("Preview failed", exc)
            )
            return
        content = json.dumps(redacted_preview(result.to_dict()), indent=2, sort_keys=True)
        self.query_one("#serve-preview-content", Static).update(content)
        self._preview_complete = True

    @work(exclusive=True, group="serve-confirm")
    async def action_confirm(self) -> None:
        if not self._preview_complete:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmLaunchModal(
                self._dossier,
                self._variant,
                self._option,
                ttl_minutes=self._request.ttl_minutes,
            )
        )
        if not confirmed:
            return
        self.query_one("#serve-error", Static).update("")
        try:
            result = await self._source.launch(self._request.model_copy(update={"dry_run": False}))
        except Exception as exc:  # reason: launch failures must remain inline in the console
            self.query_one("#serve-error", Static).update(
                source_failure_message("Launch failed", exc)
            )
            return
        self.query_one("#serve-status", Static).update(
            f"Launch succeeded · {result.proxy_base_url} · expires {result.expires_at}"
        )


def _money(value: Decimal | None) -> str:
    return "unpriced" if value is None else f"${value:.2f}"


__all__ = [
    "ConfirmLaunchModal",
    "PitwallServeActionSource",
    "ScriptedServeActionSource",
    "ServeActionSource",
    "ServePreviewScreen",
    "redacted_preview",
]
