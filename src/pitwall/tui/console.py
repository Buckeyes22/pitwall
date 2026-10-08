"""Input overlays for the console command palette and list search."""

from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class CommandScreen(ModalScreen[str | None]):
    """Accept a named console view or action without a mouse interaction."""

    BINDINGS = [Binding("escape", "dismiss_command", "Close")]

    def __init__(self) -> None:
        super().__init__(name="console-command")

    def compose(self) -> ComposeResult:
        with Vertical(id="console-command-dialog"):
            yield Static("Command", id="console-command-title")
            yield Input(placeholder="overview, models, refresh, help", id="console-command-input")

    def on_mount(self) -> None:
        self.query_one("#console-command-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_dismiss_command(self) -> None:
        self.dismiss(None)


class SearchScreen(ModalScreen[None]):
    """Apply a transient query; Enter retains it and Escape clears it."""

    BINDINGS = [Binding("escape", "dismiss_search", "Close")]

    def __init__(
        self,
        apply_query: Callable[[str], None],
        show_filter: Callable[[str], None],
    ) -> None:
        super().__init__(name="console-search")
        self._apply_query = apply_query
        self._show_filter = show_filter

    def compose(self) -> ComposeResult:
        with Vertical(id="console-search-dialog"):
            yield Static("Search", id="console-search-title")
            yield Input(placeholder="Filter current list", id="console-search-input")

    def on_mount(self) -> None:
        self.query_one("#console-search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._apply_query(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._show_filter(event.value)
        self.dismiss()

    def action_dismiss_search(self) -> None:
        self._apply_query("")
        self._show_filter("")
        self.dismiss()


__all__ = ["CommandScreen", "SearchScreen"]
