from __future__ import annotations

import asyncio
import json

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Static

from pitwall.onboarding import (
    OnboardingAction,
    OnboardingCommand,
    OnboardingError,
    OnboardingResult,
    OnboardingStatus,
)
from pitwall.tui.onboarding import (
    ConfirmOnboardingModal,
    RunPodOnboardingPanel,
    StaticOnboardingSource,
)
from tests.hang_guard import HANG_GUARD_SECS
from tests.onboarding.test_service import _endpoint_request, _service

pytestmark = pytest.mark.anyio


class PanelApp(App[None]):
    def __init__(self, panel: RunPodOnboardingPanel) -> None:
        super().__init__()
        self.panel = panel

    def compose(self) -> ComposeResult:
        yield self.panel


async def _results() -> dict[OnboardingAction, OnboardingResult]:
    service, _, _, _ = _service()
    request = _endpoint_request()
    plan = await service.plan(request)
    applied = await service.apply(request, confirmed_plan_id=plan.plan_id)
    return {
        OnboardingAction.PLAN: plan,
        OnboardingAction.APPLY: applied,
        OnboardingAction.STATUS: applied.model_copy(update={"action": OnboardingAction.STATUS}),
        OnboardingAction.RESUME: applied.model_copy(update={"action": OnboardingAction.RESUME}),
        OnboardingAction.ROLLBACK: plan.model_copy(update={"action": OnboardingAction.ROLLBACK}),
    }


def _set_request(panel: RunPodOnboardingPanel) -> None:
    panel.query_one("#onboarding-request", Input).value = json.dumps(
        _endpoint_request().model_dump(mode="json")
    )


async def test_panel_plan_then_exact_confirmation_applies_shared_plan() -> None:
    source = StaticOnboardingSource(await _results())
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#onboarding-result", Static).content)
        assert "Plan | planned" in rendered
        plan = source.results[OnboardingAction.PLAN]
        assert plan.provider_resources[0] in rendered
        assert plan.database_mutations[0] in rendered
        assert "Existing resource reuse: none" in rendered
        assert plan.rollback_guidance[0] in rendered
        assert "Writes: pitwall.config_audit" in rendered
        assert not panel.query_one("#onboarding-apply", Button).disabled

        panel.query_one("#onboarding-apply", Button).press()
        await pilot.pause()
        exact = plan.plan_id
        assert "demo-onboarding" in str(
            app.screen.query_one("#onboarding-confirm-target", Static).content
        )
        assert "RunPod" in str(app.screen.query_one("#onboarding-confirm-provider", Static).content)
        assert plan.provider_resources[0] in str(
            app.screen.query_one("#onboarding-confirm-effect", Static).content
        )
        assert plan.database_mutations[0] in str(
            app.screen.query_one("#onboarding-confirm-database", Static).content
        )
        assert "none" in str(app.screen.query_one("#onboarding-confirm-reuse", Static).content)
        assert plan.rollback_guidance[0] in str(
            app.screen.query_one("#onboarding-confirm-rollback", Static).content
        )
        assert "USD" in str(app.screen.query_one("#onboarding-confirm-ceiling", Static).content)
        assert "retained" in str(
            app.screen.query_one("#onboarding-confirm-consequences", Static).content
        )
        await pilot.click("#onboarding-confirm-text")
        await pilot.press(*list(exact))
        await pilot.click("#onboarding-confirm-apply")
        await pilot.pause()

        assert "Apply | complete" in str(panel.query_one("#onboarding-result", Static).content)

    assert [call.action for call in source.calls] == [
        OnboardingAction.PLAN,
        OnboardingAction.APPLY,
    ]
    assert source.calls[1].confirmed_plan_id == source.results[OnboardingAction.PLAN].plan_id


async def test_panel_wrong_confirmation_and_modal_cancel_are_zero_write() -> None:
    source = StaticOnboardingSource(await _results())
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        panel.query_one("#onboarding-apply", Button).press()
        await pilot.pause()
        await pilot.click("#onboarding-confirm-text")
        await pilot.press(*list("wrong"))
        assert app.screen.query_one("#onboarding-confirm-apply", Button).disabled
        await pilot.click("#onboarding-confirm-cancel")
        await pilot.pause()

    assert [call.action for call in source.calls] == [OnboardingAction.PLAN]


async def test_panel_active_cancel_and_error_are_safe_and_non_disclosing() -> None:
    wait = asyncio.Event()
    slow_source = StaticOnboardingSource(await _results(), wait=wait)
    slow_panel = RunPodOnboardingPanel(slow_source)
    slow_app = PanelApp(slow_panel)
    async with slow_app.run_test(size=(160, 45)) as pilot:
        _set_request(slow_panel)
        slow_panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        slow_panel.query_one("#onboarding-cancel", Button).press()
        await pilot.pause()
        assert (
            "cancelled" in str(slow_panel.query_one("#onboarding-result", Static).content).lower()
        )

    failure = OnboardingError(
        "provider_failed",
        "Authorization: Bearer tui-secret-canary",
        result=(await _results())[OnboardingAction.STATUS].model_copy(
            update={
                "status": OnboardingStatus.FAILED,
                "can_resume": True,
            }
        ),
    )
    failed_source = StaticOnboardingSource(await _results(), failure=failure)
    failed_panel = RunPodOnboardingPanel(failed_source)
    failed_app = PanelApp(failed_panel)
    async with failed_app.run_test(size=(160, 45)) as pilot:
        _set_request(failed_panel)
        failed_panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        rendered = str(failed_panel.query_one("#onboarding-error", Static).content)
        assert "failed safely" in rendered
        assert "tui-secret-canary" not in rendered
        assert not failed_panel.query_one("#onboarding-resume", Button).disabled


async def test_starting_status_while_plan_runs_does_not_show_cancelled_text() -> None:
    wait = asyncio.Event()
    slow_source = StaticOnboardingSource(await _results(), wait=wait)
    panel = RunPodOnboardingPanel(slow_source)
    app = PanelApp(panel)
    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        panel.query_one("#onboarding-status", Button).press()
        await pilot.pause()
        await pilot.pause()

        rendered = str(panel.query_one("#onboarding-result", Static).content).lower()
        assert "cancelled" not in rendered
        assert panel.query_one("#onboarding-cancel", Button).disabled is False
        wait.set()


async def test_panel_status_resume_and_rollback_use_shared_actions() -> None:
    source = StaticOnboardingSource(await _results())
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-status", Button).press()
        await pilot.pause()
        panel.query_one("#onboarding-rollback", Button).press()
        await pilot.pause()

    assert [call.action for call in source.calls] == [
        OnboardingAction.STATUS,
        OnboardingAction.ROLLBACK,
    ]


class _ScriptedFailureSource(StaticOnboardingSource):
    """Raise each queued failure once, then serve scripted results."""

    def __init__(
        self,
        results: dict[OnboardingAction, OnboardingResult],
        failures: list[BaseException],
    ) -> None:
        super().__init__(results)
        self._failures = failures

    async def execute(self, command: OnboardingCommand) -> OnboardingResult:
        self.calls.append(command)
        if self._failures:
            raise self._failures.pop(0)
        return self.results[command.action]


class _CancelSignallingSource(StaticOnboardingSource):
    """Signal when an in-flight action observes cancellation."""

    def __init__(self, results: dict[OnboardingAction, OnboardingResult]) -> None:
        super().__init__(results, wait=asyncio.Event())
        self.cancelled = asyncio.Event()

    async def execute(self, command: OnboardingCommand) -> OnboardingResult:
        self.calls.append(command)
        assert self.wait is not None
        try:
            await self.wait.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return self.results[command.action]


async def test_panel_invalid_json_is_recoverable_inline_with_zero_source_calls() -> None:
    source = StaticOnboardingSource(await _results())
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        panel.query_one("#onboarding-request", Input).value = "{not valid json"
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert str(panel.query_one("#onboarding-error", Static).content) == (
            "Onboarding request JSON is invalid."
        )
        assert panel.query_one("#onboarding-apply", Button).disabled
        assert panel.query_one("#onboarding-resume", Button).disabled
        assert panel.query_one("#onboarding-cancel", Button).disabled

        panel.query_one("#onboarding-request", Input).value = "[1, 2]"
        panel.query_one("#onboarding-status", Button).press()
        await pilot.pause()
        assert str(panel.query_one("#onboarding-error", Static).content) == (
            "Onboarding request JSON is invalid."
        )
        assert source.calls == []
        assert "Loading" not in str(panel.query_one("#onboarding-result", Static).content)

        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert "Plan | planned" in str(panel.query_one("#onboarding-result", Static).content)
        assert not panel.query_one("#onboarding-apply", Button).disabled
        assert str(panel.query_one("#onboarding-error", Static).content) == ""

    assert [call.action for call in source.calls] == [OnboardingAction.PLAN]


async def test_panel_generic_exception_is_safe_and_hides_bearer_canary() -> None:
    canary = "synthetic-bearer-canary-7c19"
    source = _ScriptedFailureSource(await _results(), [RuntimeError(f"Bearer {canary}")])
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#onboarding-error", Static).content)
        assert rendered == "RunPod onboarding is unavailable."
        assert canary not in rendered
        assert "RuntimeError" not in rendered
        assert "Loading" not in str(panel.query_one("#onboarding-result", Static).content)
        assert panel.query_one("#onboarding-apply", Button).disabled
        assert panel.query_one("#onboarding-cancel", Button).disabled

        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert "Plan | planned" in str(panel.query_one("#onboarding-result", Static).content)
        assert str(panel.query_one("#onboarding-error", Static).content) == ""

    assert [call.action for call in source.calls] == [
        OnboardingAction.PLAN,
        OnboardingAction.PLAN,
    ]


async def test_panel_onboarding_error_without_result_is_safe_and_recoverable() -> None:
    failure = OnboardingError("provider_failed", "provider detail withheld")
    source = _ScriptedFailureSource(await _results(), [failure])
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert "failed safely" in str(panel.query_one("#onboarding-error", Static).content)
        assert panel.query_one("#onboarding-apply", Button).disabled
        assert panel.query_one("#onboarding-resume", Button).disabled
        assert "Loading" not in str(panel.query_one("#onboarding-result", Static).content)
        assert "Plan |" not in str(panel.query_one("#onboarding-result", Static).content)

        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert "Plan | planned" in str(panel.query_one("#onboarding-result", Static).content)
        assert not panel.query_one("#onboarding-apply", Button).disabled
        assert str(panel.query_one("#onboarding-error", Static).content) == ""

    assert [call.action for call in source.calls] == [
        OnboardingAction.PLAN,
        OnboardingAction.PLAN,
    ]


async def test_editing_request_after_plan_disables_stale_apply_and_resume() -> None:
    results = await _results()
    results[OnboardingAction.PLAN] = results[OnboardingAction.PLAN].model_copy(
        update={"can_resume": True}
    )
    source = StaticOnboardingSource(results)
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert not panel.query_one("#onboarding-apply", Button).disabled
        assert not panel.query_one("#onboarding-resume", Button).disabled

        panel.query_one("#onboarding-request", Input).value = json.dumps(
            _endpoint_request(name="renamed-onboarding").model_dump(mode="json")
        )
        await pilot.pause()
        assert panel.query_one("#onboarding-apply", Button).disabled
        assert panel.query_one("#onboarding-resume", Button).disabled

        panel.query_one("#onboarding-apply", Button).press()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmOnboardingModal)

        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert not panel.query_one("#onboarding-apply", Button).disabled

    assert [call.action for call in source.calls] == [
        OnboardingAction.PLAN,
        OnboardingAction.PLAN,
    ]


async def test_panel_loading_and_cancel_states_are_race_free() -> None:
    source = _CancelSignallingSource(await _results())
    panel = RunPodOnboardingPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(160, 45)) as pilot:
        _set_request(panel)
        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert "Loading onboarding state…" in str(
            panel.query_one("#onboarding-result", Static).content
        )
        assert not panel.query_one("#onboarding-cancel", Button).disabled
        assert panel.query_one("#onboarding-apply", Button).disabled
        assert [call.action for call in source.calls] == [OnboardingAction.PLAN]

        panel.query_one("#onboarding-cancel", Button).press()
        await asyncio.wait_for(source.cancelled.wait(), timeout=HANG_GUARD_SECS)
        await pilot.pause()
        cancelled_text = str(panel.query_one("#onboarding-result", Static).content)
        assert "cancelled" in cancelled_text.lower()
        assert panel.query_one("#onboarding-cancel", Button).disabled

        assert source.wait is not None
        source.wait.set()
        await pilot.pause()
        assert panel.query_one("#onboarding-cancel", Button).disabled
        assert str(panel.query_one("#onboarding-result", Static).content) == cancelled_text
        assert [call.action for call in source.calls] == [OnboardingAction.PLAN]

        panel.query_one("#onboarding-plan", Button).press()
        await pilot.pause()
        assert "Plan | planned" in str(panel.query_one("#onboarding-result", Static).content)
        assert panel.query_one("#onboarding-cancel", Button).disabled

    assert [call.action for call in source.calls] == [
        OnboardingAction.PLAN,
        OnboardingAction.PLAN,
    ]
