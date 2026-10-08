"""Typed production-routing and job-lifecycle surface for the Operations TUI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from pitwall.tui.routing_jobs_source import (
    MAX_IDENTIFIER_LENGTH,
    MAX_PAYLOAD_INPUT_BYTES,
    MAX_WEBHOOK_LENGTH,
    ProductionRoutingJobsSource,
    RoutingJobsError,
    RoutingJobsSource,
    StaticRoutingJobsSource,
    production_routing_service_factory,
    required_text,
    validate_command,
)
from pitwall.tui.routing_jobs_views import (
    RoutingJobAction,
    RoutingJobCommand,
    RoutingJobEventView,
    RoutingJobHistoryView,
    RoutingJobResult,
    RoutingJobResultView,
    RoutingJobView,
    RoutingPlanView,
)


class ConfirmRoutingJobModal(ModalScreen[bool]):
    """Require an exact capability or workload identifier before mutation."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, action: RoutingJobAction, expected: str) -> None:
        super().__init__()
        self._action = action
        self._expected = expected

    def compose(self) -> ComposeResult:
        with Vertical(id="routing-confirm-modal"):
            yield Static(f"Confirm routing {self._action.value}", id="routing-confirm-title")
            yield Static(
                f"Type {self._expected} exactly. This action may change paid provider state.",
                id="routing-confirm-instruction",
            )
            yield Input(id="routing-confirm-text")
            with Horizontal(id="routing-confirm-actions"):
                yield Button("Back", id="routing-confirm-cancel")
                yield Button(
                    self._action.value.title(),
                    id="routing-confirm-apply",
                    disabled=True,
                    variant="error",
                )

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "routing-confirm-text":
            self.query_one("#routing-confirm-apply", Button).disabled = (
                event.value != self._expected
            )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "routing-confirm-cancel":
            self.dismiss(False)
        elif event.button.id == "routing-confirm-apply" and not event.button.disabled:
            self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RoutingJobsPanel(VerticalScroll):
    """Operations-attached workflow for deterministic plans and async jobs."""

    DEFAULT_CSS = """
    RoutingJobsPanel {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    #routing-request-actions, #routing-job-actions, #routing-follow-controls {
        height: auto;
    }
    #routing-action-result, #routing-history-result, #routing-action-error {
        height: auto;
        margin-top: 1;
    }
    #routing-action-error { color: $error; }
    """

    def __init__(self, source: RoutingJobsSource) -> None:
        super().__init__(id="routing-jobs")
        self._source = source
        self._active_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Static("Production routing and jobs", id="routing-jobs-title")
        yield Static(
            "Preview is no-egress. Submit and cancel require exact typed confirmation.",
            id="routing-jobs-summary",
        )
        yield Input(placeholder="Capability name or ID", id="routing-capability")
        yield Input(placeholder="Optional explicit provider ID", id="routing-provider")
        yield Input(placeholder="JSON object payload", value="{}", id="routing-payload")
        yield Input(placeholder="Optional idempotency key", id="routing-idempotency-key")
        yield Input(placeholder="Optional webhook URL", id="routing-webhook-url")
        with Horizontal(id="routing-request-actions"):
            yield Button("Plan async route", id="routing-plan", variant="primary")
            yield Button("Submit job", id="routing-submit", variant="error")
        yield Input(placeholder="Workload ID", id="routing-workload-id")
        with Horizontal(id="routing-follow-controls"):
            yield Input(value="20", placeholder="Follow polls (1-100)", id="routing-max-polls")
            yield Input(
                value="1", placeholder="Poll interval seconds (0-60)", id="routing-interval"
            )
            yield Input(value="25", placeholder="History events (1-100)", id="routing-event-limit")
        with Horizontal(id="routing-job-actions"):
            yield Button("Status", id="routing-status")
            yield Button("Result", id="routing-result")
            yield Button("History", id="routing-history")
            yield Button("Follow", id="routing-follow")
            yield Button("Cancel job", id="routing-cancel", variant="error")
            yield Button("Stop wait", id="routing-stop", disabled=True)
        yield Static("Ready", id="routing-action-result")
        yield Static("", id="routing-history-result")
        yield Static("", id="routing-action-error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        action_by_id = {
            "routing-plan": RoutingJobAction.PLAN,
            "routing-submit": RoutingJobAction.SUBMIT,
            "routing-status": RoutingJobAction.STATUS,
            "routing-result": RoutingJobAction.RESULT,
            "routing-history": RoutingJobAction.HISTORY,
            "routing-follow": RoutingJobAction.FOLLOW,
            "routing-cancel": RoutingJobAction.CANCEL,
        }
        if event.button.id == "routing-stop":
            self._cancel_active()
            return
        action = action_by_id.get(event.button.id or "")
        if action is None:
            return
        try:
            command = self._command(action)
        except RoutingJobsError, ValueError:
            self.query_one("#routing-action-error", Static).update(
                "Routing request is invalid; check identifiers, JSON, and bounds."
            )
            return
        if action in {RoutingJobAction.SUBMIT, RoutingJobAction.CANCEL}:
            expected = (
                required_text(command.capability_id)
                if action is RoutingJobAction.SUBMIT
                else required_text(command.workload_id)
            )
            self.app.push_screen(
                ConfirmRoutingJobModal(action=action, expected=expected),
                callback=lambda confirmed: self._after_confirmation(command, bool(confirmed)),
            )
            return
        self._start(command)

    def _command(self, action: RoutingJobAction) -> RoutingJobCommand:
        capability_id = _input_value(self, "#routing-capability")
        provider_id = _input_value(self, "#routing-provider")
        workload_id = _input_value(self, "#routing-workload-id")
        idempotency_key = _input_value(self, "#routing-idempotency-key")
        webhook_url = _input_value(self, "#routing-webhook-url")
        raw_payload = self.query_one("#routing-payload", Input).value
        if len(raw_payload.encode("utf-8")) > MAX_PAYLOAD_INPUT_BYTES:
            raise ValueError("payload is too large")
        payload = json.loads(raw_payload or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        command = RoutingJobCommand(
            action=action,
            capability_id=capability_id,
            payload=payload,
            provider_id=provider_id,
            idempotency_key=idempotency_key,
            webhook_url=webhook_url,
            workload_id=workload_id,
            max_polls=_integer_input(self, "#routing-max-polls"),
            interval_seconds=_float_input(self, "#routing-interval"),
            event_limit=_integer_input(self, "#routing-event-limit"),
        )
        validate_command(command, require_confirmation=False)
        return command

    def _after_confirmation(self, command: RoutingJobCommand, confirmed: bool) -> None:
        if not confirmed:
            return
        expected = (
            command.capability_id
            if command.action is RoutingJobAction.SUBMIT
            else command.workload_id
        )
        self._start(replace(command, confirmation=expected))

    def _start(self, command: RoutingJobCommand) -> None:
        self._cancel_active()
        self.query_one("#routing-action-error", Static).update("")
        self.query_one("#routing-action-result", Static).update(
            f"Running routing {command.action.value}…"
        )
        self.query_one("#routing-stop", Button).disabled = False
        self._active_task = asyncio.create_task(self._run(command))

    async def _run(self, command: RoutingJobCommand) -> None:
        try:
            result = await self._source.execute_routing_job(command)
        except asyncio.CancelledError:
            if self._active_task is asyncio.current_task():
                self.query_one("#routing-action-result", Static).update(
                    "Wait stopped. Read status before retrying; "
                    "a confirmed write may have completed."
                )
            self._finish()
            return
        except RoutingJobsError as exc:
            self.query_one("#routing-action-error", Static).update(
                f"Routing {command.action.value} failed safely: {exc.code}."
            )
            self._finish()
            return
        except Exception:  # reason: infrastructure/provider detail must not reach the TUI.
            self.query_one("#routing-action-error", Static).update(
                f"Routing {command.action.value} is unavailable."
            )
            self._finish()
            return
        self._render_result(result)
        self._finish()

    def _render_result(self, result: RoutingJobResult) -> None:
        lines = [f"Action: {result.action.value}"]
        if result.plan is not None:
            lines.append(result.plan.render())
        if result.job is not None:
            lines.append(result.job.render(include_result=False))
            self.query_one("#routing-workload-id", Input).value = result.job.workload_id
        if result.result_page is not None:
            lines.append(result.result_page.render())
            self.query_one("#routing-workload-id", Input).value = result.result_page.workload_id
        if result.polls is not None:
            lines.append(
                f"Follow: {result.polls} polls | "
                f"{'terminal' if result.follow_complete else 'bounded wait exhausted'}"
            )
        self.query_one("#routing-action-result", Static).update("\n".join(lines))
        self.query_one("#routing-history-result", Static).update(
            "" if result.history is None else result.history.render()
        )

    def _finish(self) -> None:
        if self._active_task is not asyncio.current_task():
            return
        self._active_task = None
        self.query_one("#routing-stop", Button).disabled = True

    def _cancel_active(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    def on_unmount(self) -> None:
        self._cancel_active()


def _input_value(panel: RoutingJobsPanel, selector: str) -> str | None:
    value = panel.query_one(selector, Input).value.strip()
    if not value:
        return None
    limit = MAX_WEBHOOK_LENGTH if selector == "#routing-webhook-url" else MAX_IDENTIFIER_LENGTH
    if len(value) > limit:
        raise ValueError("identifier is too long")
    return value


def _integer_input(panel: RoutingJobsPanel, selector: str) -> int:
    return int(panel.query_one(selector, Input).value)


def _float_input(panel: RoutingJobsPanel, selector: str) -> float:
    return float(panel.query_one(selector, Input).value)


__all__ = [
    "ConfirmRoutingJobModal",
    "ProductionRoutingJobsSource",
    "RoutingJobAction",
    "RoutingJobCommand",
    "RoutingJobEventView",
    "RoutingJobHistoryView",
    "RoutingJobResult",
    "RoutingJobResultView",
    "RoutingJobView",
    "RoutingJobsError",
    "RoutingJobsPanel",
    "RoutingJobsSource",
    "RoutingPlanView",
    "StaticRoutingJobsSource",
    "production_routing_service_factory",
]
