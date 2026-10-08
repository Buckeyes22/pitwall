"""Providers/Resources attachable panel for shared RunPod onboarding."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol

from pydantic import ValidationError
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from pitwall.onboarding import (
    OnboardingAction,
    OnboardingCommand,
    OnboardingError,
    OnboardingResult,
    RunPodOnboardingRequest,
    RunPodOnboardingService,
)

ServiceFactory = Callable[[], Awaitable[RunPodOnboardingService]]


class OnboardingSource(Protocol):
    async def execute(self, command: OnboardingCommand) -> OnboardingResult: ...


class ServiceOnboardingSource:
    """Construct, use, and close the common service for one TUI action."""

    def __init__(self, service_factory: ServiceFactory) -> None:
        self._service_factory = service_factory

    async def execute(self, command: OnboardingCommand) -> OnboardingResult:
        service = await self._service_factory()
        try:
            return await service.execute(command)
        finally:
            await service.aclose()


class StaticOnboardingSource:
    """Hermetic source for Pilot tests and local screenshots."""

    def __init__(
        self,
        results: dict[OnboardingAction, OnboardingResult],
        *,
        failure: OnboardingError | None = None,
        wait: asyncio.Event | None = None,
    ) -> None:
        self.results = results
        self.failure = failure
        self.wait = wait
        self.calls: list[OnboardingCommand] = []

    async def execute(self, command: OnboardingCommand) -> OnboardingResult:
        self.calls.append(command)
        if self.wait is not None:
            await self.wait.wait()
        if self.failure is not None:
            raise self.failure
        return self.results[command.action]


class ConfirmOnboardingModal(ModalScreen[bool]):
    """Require the exact immutable plan id before apply or resume."""

    def __init__(
        self,
        *,
        result: OnboardingResult,
        request: RunPodOnboardingRequest,
        action: OnboardingAction,
    ) -> None:
        super().__init__()
        self._result = result
        self._request = request
        self._action = action

    def compose(self) -> ComposeResult:
        with Vertical(id="onboarding-confirm-modal"):
            yield Static(f"Confirm {self._action.value}: {self._result.plan_id}")
            yield Static(
                f"Target: {self._request.name} ({self._result.topology.value})",
                id="onboarding-confirm-target",
            )
            yield Static(
                f"Provider: RunPod ({self._request.provider_name})",
                id="onboarding-confirm-provider",
            )
            yield Static(
                "Provider resources: " + _inventory(self._result.provider_resources),
                id="onboarding-confirm-effect",
            )
            yield Static(
                "Database mutations: " + _inventory(self._result.database_mutations),
                id="onboarding-confirm-database",
            )
            yield Static(
                "Existing resource reuse: " + _inventory(self._result.existing_resource_reuse),
                id="onboarding-confirm-reuse",
            )
            yield Static(
                f"Estimated ceiling: {self._result.estimated_ceiling_usd} USD",
                id="onboarding-confirm-ceiling",
            )
            yield Static(
                "Rollback guidance: " + _inventory(self._result.rollback_guidance),
                id="onboarding-confirm-rollback",
            )
            yield Static(
                "Irreversible consequence: provider resources may be retained when safe "
                "compensation cannot be proven.",
                id="onboarding-confirm-consequences",
            )
            yield Static("Type the exact plan id. Re-plan after changing the request.")
            yield Input(id="onboarding-confirm-text")
            with Horizontal():
                yield Button("Cancel", id="onboarding-confirm-cancel")
                yield Button(
                    self._action.value.title(),
                    id="onboarding-confirm-apply",
                    disabled=True,
                    variant="error",
                )

    async def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#onboarding-confirm-apply", Button).disabled = (
            event.value != self._result.plan_id
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "onboarding-confirm-cancel":
            self.dismiss(False)
        elif event.button.id == "onboarding-confirm-apply" and not event.button.disabled:
            self.dismiss(True)


class RunPodOnboardingPanel(VerticalScroll):
    """Plan-first onboarding controls suitable for Providers or Resources."""

    DEFAULT_CSS = """
    RunPodOnboardingPanel {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    #onboarding-actions { height: auto; }
    #onboarding-result { height: auto; margin-top: 1; }
    #onboarding-error { color: $error; }
    """

    def __init__(self, source: OnboardingSource) -> None:
        super().__init__(id="runpod-onboarding")
        self._source = source
        self._active_task: asyncio.Task[None] | None = None
        self._request: RunPodOnboardingRequest | None = None
        self._last_result: OnboardingResult | None = None

    def compose(self) -> ComposeResult:
        yield Static("RunPod onboarding", id="onboarding-title")
        yield Static(
            "Plan is the default. Apply and resume require the exact previewed plan id.",
            id="onboarding-summary",
        )
        yield Input(placeholder="Secret-free onboarding request JSON", id="onboarding-request")
        with Horizontal(id="onboarding-actions"):
            yield Button("Plan", id="onboarding-plan", variant="primary")
            yield Button("Apply", id="onboarding-apply", disabled=True, variant="error")
            yield Button("Status", id="onboarding-status")
            yield Button("Resume", id="onboarding-resume", disabled=True, variant="warning")
            yield Button("Rollback guidance", id="onboarding-rollback")
            yield Button("Cancel", id="onboarding-cancel", disabled=True)
        yield Static("Ready to plan", id="onboarding-result")
        yield Static("", id="onboarding-error")

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "onboarding-request":
            return
        self._request = None
        self._last_result = None
        self.query_one("#onboarding-apply", Button).disabled = True
        self.query_one("#onboarding-resume", Button).disabled = True

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "onboarding-cancel":
            self._cancel()
        elif action == "onboarding-plan":
            self._start(self._run(OnboardingAction.PLAN))
        elif action == "onboarding-status":
            self._start(self._run(OnboardingAction.STATUS))
        elif action == "onboarding-rollback":
            self._start(self._run(OnboardingAction.ROLLBACK))
        elif action == "onboarding-apply":
            self._confirm(OnboardingAction.APPLY)
        elif action == "onboarding-resume":
            self._confirm(OnboardingAction.RESUME)

    def _start(self, operation: Coroutine[object, object, None]) -> None:
        self._cancel()
        self.query_one("#onboarding-error", Static).update("")
        self.query_one("#onboarding-result", Static).update("Loading onboarding state…")
        self.query_one("#onboarding-cancel", Button).disabled = False
        self._active_task = asyncio.create_task(operation)

    async def _run(
        self,
        action: OnboardingAction,
        *,
        confirmed_plan_id: str | None = None,
    ) -> None:
        try:
            request = self._request or self._parse_request()
            result = await self._source.execute(
                OnboardingCommand(
                    action=action,
                    request=request,
                    confirmed_plan_id=confirmed_plan_id,
                )
            )
        except asyncio.CancelledError:
            if self._active_task is asyncio.current_task():
                self.query_one("#onboarding-result", Static).update(
                    "Onboarding action cancelled. Inspect status before resuming; "
                    "a confirmed write may already have completed."
                )
            self._finish()
            return
        except json.JSONDecodeError, ValidationError, ValueError:
            self.query_one("#onboarding-result", Static).update(
                "No onboarding result: invalid request."
            )
            self.query_one("#onboarding-error", Static).update(
                "Onboarding request JSON is invalid."
            )
            self._finish()
            return
        except OnboardingError as exc:
            if exc.result is not None:
                self._last_result = exc.result
                self._request = request
                self._render_result(exc.result)
                self.query_one("#onboarding-resume", Button).disabled = not exc.result.can_resume
            else:
                self.query_one("#onboarding-result", Static).update(
                    "No onboarding result available. Inspect status before retrying."
                )
            self.query_one("#onboarding-error", Static).update(
                "RunPod onboarding failed safely; inspect status or resume the unchanged plan."
            )
            self._finish()
            return
        except Exception:  # reason: provider/persistence detail must not reach the TUI
            self.query_one("#onboarding-result", Static).update(
                "No onboarding result available. Inspect status before retrying."
            )
            self.query_one("#onboarding-error", Static).update("RunPod onboarding is unavailable.")
            self._finish()
            return
        self._request = request
        self._last_result = result
        self._render_result(result)
        self.query_one("#onboarding-apply", Button).disabled = action != OnboardingAction.PLAN
        self.query_one("#onboarding-resume", Button).disabled = not result.can_resume
        self._finish()

    def _parse_request(self) -> RunPodOnboardingRequest:
        raw = self.query_one("#onboarding-request", Input).value
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request root must be an object")
        return RunPodOnboardingRequest.model_validate(payload)

    def _confirm(self, action: OnboardingAction) -> None:
        result = self._last_result
        if result is None or self._request is None:
            return
        self.app.push_screen(
            ConfirmOnboardingModal(result=result, request=self._request, action=action),
            callback=lambda confirmed: self._after_confirmation(action, bool(confirmed)),
        )

    def _after_confirmation(self, action: OnboardingAction, confirmed: bool) -> None:
        if not confirmed or self._last_result is None:
            return
        self._start(self._run(action, confirmed_plan_id=self._last_result.plan_id))

    def _render_result(self, result: OnboardingResult) -> None:
        lines = [
            f"{result.action.value.title()} | {result.status.value}",
            f"Plan: {result.plan_id}",
            f"Zero write: {'yes' if result.zero_write else 'no'}",
            f"Estimated ceiling: {result.estimated_ceiling_usd} USD",
            "Endpoint hourly range: "
            f"{result.cost_impact.endpoint_minimum_hourly_usd}-"
            f"{result.cost_impact.endpoint_maximum_hourly_usd} USD",
            f"Volume pricing: {result.cost_impact.network_volume_pricing}",
            "Provider resources: " + _inventory(result.provider_resources),
            "Database mutations: " + _inventory(result.database_mutations),
            "Existing resource reuse: " + _inventory(result.existing_resource_reuse),
            "Rollback guidance: " + _inventory(result.rollback_guidance),
            f"Next: {result.next_step or 'none'}",
        ]
        lines.extend(
            (
                f"{step.order}. {step.key}: {step.state.value} | "
                f"Resource: {_step_resource(step.resource_type, step.resource_id)} | "
                f"Effect: {step.effect} | Writes: {_inventory(step.writes)} | "
                f"Reused: {'yes' if step.reused else 'no'} | Rollback: {step.rollback}"
            )
            for step in result.steps
        )
        if result.retained_resources:
            lines.append("Retained: " + ", ".join(result.retained_resources))
        self.query_one("#onboarding-result", Static).update("\n".join(lines))

    def _finish(self) -> None:
        if self._active_task is not asyncio.current_task():
            return
        self._active_task = None
        self.query_one("#onboarding-cancel", Button).disabled = True

    def _cancel(self) -> None:
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()


def _inventory(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "none"


def _step_resource(resource_type: str | None, resource_id: str | None) -> str:
    if resource_type is None:
        return "none"
    return f"{resource_type}:{resource_id}" if resource_id is not None else resource_type


__all__ = [
    "ConfirmOnboardingModal",
    "OnboardingSource",
    "RunPodOnboardingPanel",
    "ServiceOnboardingSource",
    "StaticOnboardingSource",
]
