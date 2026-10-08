"""Binding-derived help overlay for the operator console."""

from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Static


class HelpScreen(ModalScreen[None]):
    """Display the global and active-screen bindings without duplicating them."""

    BINDINGS = [Binding("escape", "dismiss_help", "Close")]

    def __init__(self, active_screen: Screen[object]) -> None:
        super().__init__()
        self._active_screen = active_screen

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static("Keyboard help", id="help-title")
            yield Static(_binding_text("Global", self.app.BINDINGS), id="help-global")
            yield Static(
                _binding_text("This screen", self._active_screen.BINDINGS),
                id="help-screen",
            )
            yield Static(
                "Command and search overlays keep focus in the current console view.",
                id="help-overlays",
            )
            yield Static("Escape: close", id="help-close")

    def action_dismiss_help(self) -> None:
        self.dismiss()


def _binding_text(title: str, bindings: Iterable[BindingType]) -> str:
    """Render binding metadata directly from the binding declarations."""
    entries = [
        f"{binding.key}: {binding.description}" for binding in Binding.make_bindings(bindings)
    ]
    return f"{title}\n" + "\n".join(entries)


__all__ = ["HelpScreen"]
