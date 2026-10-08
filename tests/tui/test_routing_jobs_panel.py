"""Hermetic unit and Pilot coverage for the Operations routing/job workflow."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Static

from pitwall.config import RoutingWeights
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
    WorkloadState,
)
from pitwall.core.models import Capability, Provider, Workload
from pitwall.providers.registry import get_default_registry
from pitwall.routing.production import (
    JobEventPage,
    JobResultPage,
    ProductionRoutePlan,
    RoutingOperation,
    build_production_plan,
)
from pitwall.tui.routing_jobs import (
    ConfirmRoutingJobModal,
    ProductionRoutingJobsSource,
    RoutingJobAction,
    RoutingJobCommand,
    RoutingJobHistoryView,
    RoutingJobResult,
    RoutingJobResultView,
    RoutingJobsError,
    RoutingJobsPanel,
    RoutingJobView,
    RoutingPlanView,
    StaticRoutingJobsSource,
)
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 1, 17, 15, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_chat",
        name="llm.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_TOKEN,
        source=CapabilitySource.API,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider() -> Provider:
    return Provider(
        id="provider_runpod",
        capability_id="cap_chat",
        name="RunPod queue",
        adapter_id=ProviderAdapterId.RUNPOD,
        provider_type=ProviderType.SERVERLESS_QUEUE,
        config={
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "2",
            },
            "expected_latency_ms": 125,
        },
        priority=1,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


def _plan() -> ProductionRoutePlan:
    return build_production_plan(
        capability=_capability(),
        providers=[_provider()],
        payload={"messages": [], "max_output_tokens": 64},
        operation=RoutingOperation.ASYNC_INFERENCE,
        registry=get_default_registry(),
        now=_NOW,
        mode="weighted",
        weights=RoutingWeights(cost="2", latency="0.5"),
        max_attempts=1,
    )


def _workload(
    *,
    state: WorkloadState = WorkloadState.QUEUED,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> Workload:
    plan = _plan()
    completed_at = _NOW if state in {WorkloadState.COMPLETED, WorkloadState.FAILED} else None
    return Workload(
        id="wkl_route_tui",
        capability_id="cap_chat",
        provider_id="provider_runpod",
        type="inference_async",
        state=state,
        external_job_id="external-job-1",
        input={"messages": []},
        result=result,
        error=error,
        submitted_at=_NOW,
        completed_at=completed_at,
        route_plan_id=plan.plan_id,
        route_plan=plan.to_dict(),
    )


class _FakeRoutingService:
    def __init__(self, *, jobs: list[Workload] | None = None) -> None:
        self.jobs = jobs or [_workload()]
        self.calls: list[tuple[str, object]] = []
        self.get_index = 0

    async def preview(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        operation: RoutingOperation = RoutingOperation.SYNC_INFERENCE,
        provider_id: str | None = None,
    ) -> ProductionRoutePlan:
        self.calls.append(("preview", (capability_id, payload, operation, provider_id)))
        return _plan()

    async def submit_job(self, **kwargs: Any) -> Workload:
        self.calls.append(("submit", kwargs))
        return self.jobs[-1]

    async def get_job(self, workload_id: str) -> Workload:
        self.calls.append(("get", workload_id))
        job = self.jobs[min(self.get_index, len(self.jobs) - 1)]
        self.get_index += 1
        return job

    async def job_result(self, workload_id: str) -> JobResultPage:
        self.calls.append(("result", workload_id))
        job = self.jobs[-1]
        return JobResultPage(
            workload_id=workload_id,
            plan_id=job.route_plan_id,
            state=str(getattr(job.state, "value", job.state)),
            provider_id=job.provider_id,
            plan=job.route_plan,
            result=job.result,
            available=job.result is not None,
            unavailable_reason=None if job.result is not None else "provider_result_unavailable",
        )

    async def cancel_job(self, workload_id: str) -> Workload:
        self.calls.append(("cancel", workload_id))
        return self.jobs[-1].model_copy(update={"state": WorkloadState.CANCELLED})

    async def job_events(self, workload_id: str, *, limit: int | None = None) -> JobEventPage:
        self.calls.append(("events", (workload_id, limit)))
        return JobEventPage(
            workload_id=workload_id,
            plan_id=_plan().plan_id,
            events=(
                {"event": "submitted", "state": "queued", "at": _NOW.isoformat()},
                {"event": "terminal", "state": "completed", "at": _NOW.isoformat()},
            )[:limit],
        )


async def test_production_source_requires_exact_confirmation_before_service_factory() -> None:
    service = _FakeRoutingService()
    factory_calls = 0

    async def factory() -> _FakeRoutingService:
        nonlocal factory_calls
        factory_calls += 1
        return service

    source = ProductionRoutingJobsSource(factory)
    submit = RoutingJobCommand(
        action=RoutingJobAction.SUBMIT,
        capability_id="llm.chat",
        payload={"messages": []},
        confirmation="wrong",
    )
    with pytest.raises(RoutingJobsError, match="confirmation mismatch"):
        await source.execute_routing_job(submit)

    cancel = RoutingJobCommand(
        action=RoutingJobAction.CANCEL,
        workload_id="wkl_route_tui",
        confirmation=None,
    )
    with pytest.raises(RoutingJobsError, match="confirmation mismatch"):
        await source.execute_routing_job(cancel)

    assert factory_calls == 0
    assert service.calls == []


async def test_production_source_delegates_plan_submit_and_cancel() -> None:
    service = _FakeRoutingService()

    async def factory() -> _FakeRoutingService:
        return service

    source = ProductionRoutingJobsSource(factory)
    preview = await source.execute_routing_job(
        RoutingJobCommand(
            action=RoutingJobAction.PLAN,
            capability_id="llm.chat",
            payload={"messages": []},
        )
    )
    submitted = await source.execute_routing_job(
        RoutingJobCommand(
            action=RoutingJobAction.SUBMIT,
            capability_id="llm.chat",
            payload={"messages": []},
            confirmation="llm.chat",
        )
    )
    cancelled = await source.execute_routing_job(
        RoutingJobCommand(
            action=RoutingJobAction.CANCEL,
            workload_id="wkl_route_tui",
            confirmation="wkl_route_tui",
        )
    )

    assert preview.plan is not None
    assert preview.plan.objective == str(_plan().selected.objective)
    assert preview.plan.cost_component == str(_plan().selected.cost_component)
    assert preview.plan.latency_component == str(_plan().selected.latency_component)
    assert submitted.job is not None
    assert submitted.job.plan_id == _plan().plan_id
    assert cancelled.job is not None
    assert cancelled.job.state == "cancelled"
    assert [call[0] for call in service.calls] == ["preview", "submit", "cancel"]


async def test_unknown_capability_maps_to_route_not_found() -> None:
    from pitwall.resolver.exceptions import CapabilityNotFoundError

    class MissingCapabilityService(_FakeRoutingService):
        async def preview(
            self,
            *,
            capability_id: str,
            payload: Mapping[str, Any],
            operation: RoutingOperation = RoutingOperation.SYNC_INFERENCE,
            provider_id: str | None = None,
        ) -> ProductionRoutePlan:
            raise CapabilityNotFoundError("llm.nope")

    async def factory() -> MissingCapabilityService:
        return MissingCapabilityService()

    source = ProductionRoutingJobsSource(factory)
    plan = RoutingJobCommand(
        action=RoutingJobAction.PLAN,
        capability_id="llm.nope",
        payload={"messages": []},
    )
    with pytest.raises(RoutingJobsError) as exc_info:
        await source.execute_routing_job(plan)
    assert exc_info.value.code == "route_not_found"


async def test_production_source_follow_is_bounded_and_includes_history() -> None:
    service = _FakeRoutingService(
        jobs=[
            _workload(state=WorkloadState.QUEUED),
            _workload(state=WorkloadState.RUNNING),
            _workload(state=WorkloadState.COMPLETED, result={"answer": "done"}),
        ]
    )
    sleeps: list[float] = []

    async def factory() -> _FakeRoutingService:
        return service

    async def sleep(interval: float) -> None:
        sleeps.append(interval)

    source = ProductionRoutingJobsSource(factory, sleep=sleep)
    result = await source.execute_routing_job(
        RoutingJobCommand(
            action=RoutingJobAction.FOLLOW,
            workload_id="wkl_route_tui",
            max_polls=3,
            interval_seconds=0.25,
            event_limit=1,
        )
    )

    assert result.polls == 3
    assert result.follow_complete is True
    assert result.job is not None and result.job.state == "completed"
    assert result.history is not None and len(result.history.events) == 1
    assert result.history.streaming_supported is False
    assert result.history.unavailable_reason == "provider_event_stream_unavailable"
    assert sleeps == [0.25, 0.25]
    assert service.calls[-1] == ("events", ("wkl_route_tui", 1))


async def test_production_source_follow_stops_at_requested_poll_bound() -> None:
    service = _FakeRoutingService(jobs=[_workload(state=WorkloadState.RUNNING)])
    sleeps: list[float] = []

    async def factory() -> _FakeRoutingService:
        return service

    async def sleep(interval: float) -> None:
        sleeps.append(interval)

    source = ProductionRoutingJobsSource(factory, sleep=sleep)
    result = await source.execute_routing_job(
        RoutingJobCommand(
            action=RoutingJobAction.FOLLOW,
            workload_id="wkl_route_tui",
            max_polls=2,
            interval_seconds=0,
            event_limit=1,
        )
    )

    assert result.polls == 2
    assert result.follow_complete is False
    assert result.job is not None and result.job.state == "running"
    assert sleeps == [0]
    assert [call[0] for call in service.calls].count("get") == 2


async def test_production_source_reads_status_result_and_history() -> None:
    completed = _workload(
        state=WorkloadState.COMPLETED,
        result={"answer": "bounded result"},
    )
    service = _FakeRoutingService(jobs=[completed])

    async def factory() -> _FakeRoutingService:
        return service

    source = ProductionRoutingJobsSource(factory)
    status = await source.execute_routing_job(
        RoutingJobCommand(action=RoutingJobAction.STATUS, workload_id=completed.id)
    )
    result = await source.execute_routing_job(
        RoutingJobCommand(action=RoutingJobAction.RESULT, workload_id=completed.id)
    )
    history = await source.execute_routing_job(
        RoutingJobCommand(
            action=RoutingJobAction.HISTORY,
            workload_id=completed.id,
            event_limit=1,
        )
    )

    assert status.job is not None and status.job.state == "completed"
    assert result.result_page is not None
    assert result.result_page.result_summary == '{"answer":"bounded result"}'
    assert history.history is not None and len(history.history.events) == 1
    assert [call[0] for call in service.calls] == ["get", "result", "events"]


async def test_production_source_rejects_unbounded_follow_and_history() -> None:
    factory_called = False

    async def factory() -> _FakeRoutingService:
        nonlocal factory_called
        factory_called = True
        return _FakeRoutingService()

    source = ProductionRoutingJobsSource(factory)
    with pytest.raises(RoutingJobsError, match="invalid follow bounds"):
        await source.execute_routing_job(
            RoutingJobCommand(
                action=RoutingJobAction.FOLLOW,
                workload_id="wkl_route_tui",
                max_polls=101,
            )
        )
    with pytest.raises(RoutingJobsError, match="invalid history bounds"):
        await source.execute_routing_job(
            RoutingJobCommand(
                action=RoutingJobAction.HISTORY,
                workload_id="wkl_route_tui",
                event_limit=0,
            )
        )
    assert factory_called is False


def test_job_view_redacts_and_bounds_results_and_provider_failure_detail() -> None:
    secret = "provider-secret-canary"
    workload = _workload(
        state=WorkloadState.FAILED,
        result={
            "token": secret,
            "output": "x" * 3_000,
            "diagnostic": "Bearer opaque-provider-credential-12345",
        },
        error={
            "code": "provider_attempts_failed",
            "message": secret,
            "attempts": [
                {
                    "provider_id": "provider-runpod-private",
                    "code": "provider_call_failed",
                    "type": "TimeoutError",
                    "message": secret,
                }
            ],
        },
    )

    view = RoutingJobView.from_workload(workload)
    rendered = view.render(include_result=True)

    assert len(view.result_summary) <= 2_048
    assert "***REDACTED***" in view.result_summary
    assert secret not in rendered
    assert "opaque-provider-credential-12345" not in rendered
    assert "provider-runpod-private" not in view.failure_summary
    assert view.failure_summary == ("provider_attempts_failed (provider_call_failed/TimeoutError)")


class _PanelApp(App[None]):
    def __init__(self, panel: RoutingJobsPanel) -> None:
        super().__init__()
        self.panel = panel

    def compose(self) -> ComposeResult:
        yield self.panel


def _scripted_results() -> dict[RoutingJobAction, RoutingJobResult]:
    plan = RoutingPlanView.from_plan(_plan())
    queued = RoutingJobView.from_workload(_workload())
    cancelled = RoutingJobView.from_workload(
        _workload().model_copy(update={"state": WorkloadState.CANCELLED})
    )
    completed = RoutingJobView.from_workload(
        _workload(state=WorkloadState.COMPLETED, result={"answer": "bounded result"})
    )
    result_page = RoutingJobResultView.from_page(
        JobResultPage(
            workload_id=queued.workload_id,
            plan_id=queued.plan_id,
            state="completed",
            provider_id=queued.provider_id,
            plan=_plan().to_dict(),
            result={"answer": "bounded result"},
            available=True,
        )
    )
    history = RoutingJobHistoryView.from_page(
        JobEventPage(
            workload_id=queued.workload_id,
            plan_id=queued.plan_id,
            events=({"event": "submitted", "state": "queued", "at": _NOW.isoformat()},),
        )
    )
    return {
        RoutingJobAction.PLAN: RoutingJobResult(action=RoutingJobAction.PLAN, plan=plan),
        RoutingJobAction.SUBMIT: RoutingJobResult(action=RoutingJobAction.SUBMIT, job=queued),
        RoutingJobAction.CANCEL: RoutingJobResult(action=RoutingJobAction.CANCEL, job=cancelled),
        RoutingJobAction.STATUS: RoutingJobResult(action=RoutingJobAction.STATUS, job=queued),
        RoutingJobAction.RESULT: RoutingJobResult(
            action=RoutingJobAction.RESULT, result_page=result_page
        ),
        RoutingJobAction.FOLLOW: RoutingJobResult(
            action=RoutingJobAction.FOLLOW,
            job=completed,
            history=history,
            polls=2,
            follow_complete=True,
        ),
        RoutingJobAction.HISTORY: RoutingJobResult(
            action=RoutingJobAction.HISTORY,
            history=history,
        ),
    }


async def test_panel_renders_plan_score_components_and_bounded_history() -> None:
    source = StaticRoutingJobsSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-capability", Input).value = "llm.chat"
        panel.query_one("#routing-payload", Input).value = '{"messages":[]}'
        panel.query_one("#routing-plan", Button).press()
        await pilot.pause()

        rendered = str(panel.query_one("#routing-action-result", Static).content)
        expected = _plan().selected
        assert f"Objective: {expected.objective}" in rendered
        assert f"Score: cost {expected.cost_component}" in rendered
        assert f"latency {expected.latency_component}" in rendered
        assert "Selected provider: provider_runpod" in rendered

        panel.query_one("#routing-workload-id", Input).value = "wkl_route_tui"
        panel.query_one("#routing-history", Button).press()
        await pilot.pause()
        history = str(panel.query_one("#routing-history-result", Static).content)
        assert "History: wkl_route_tui | 1 events" in history
        assert "provider_event_stream_unavailable" in history

    assert [call.action for call in source.calls] == [
        RoutingJobAction.PLAN,
        RoutingJobAction.HISTORY,
    ]


async def test_panel_submit_and_cancel_require_exact_typed_confirmation() -> None:
    source = StaticRoutingJobsSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-capability", Input).value = "llm.chat"
        panel.query_one("#routing-payload", Input).value = '{"messages":[]}'
        panel.query_one("#routing-submit", Button).press()
        await pilot.pause()
        confirm = app.screen.query_one("#routing-confirm-text", Input)
        confirm.value = "wrong"
        await pilot.pause()
        assert app.screen.query_one("#routing-confirm-apply", Button).disabled
        confirm.value = "llm.chat"
        await pilot.pause()
        app.screen.query_one("#routing-confirm-apply", Button).press()
        await pilot.pause()

        assert panel.query_one("#routing-workload-id", Input).value == "wkl_route_tui"
        panel.query_one("#routing-cancel", Button).press()
        await pilot.pause()
        cancel_confirm = app.screen.query_one("#routing-confirm-text", Input)
        cancel_confirm.value = "wkl_route_tui"
        await pilot.pause()
        app.screen.query_one("#routing-confirm-apply", Button).press()
        await pilot.pause()

        rendered = str(panel.query_one("#routing-action-result", Static).content)
        assert "Action: cancel" in rendered
        assert "State: cancelled" in rendered

    assert [call.action for call in source.calls] == [
        RoutingJobAction.SUBMIT,
        RoutingJobAction.CANCEL,
    ]
    assert source.calls[0].confirmation == "llm.chat"
    assert source.calls[1].confirmation == "wkl_route_tui"


async def test_panel_status_result_and_follow_render_bounded_lifecycle() -> None:
    source = StaticRoutingJobsSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-workload-id", Input).value = "wkl_route_tui"
        panel.query_one("#routing-status", Button).press()
        await pilot.pause()
        assert "State: queued" in str(panel.query_one("#routing-action-result", Static).content)

        panel.query_one("#routing-result", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#routing-action-result", Static).content)
        assert 'Result: {"answer":"bounded result"}' in rendered
        assert "Job result: wkl_route_tui" in rendered
        assert "provider: provider_runpod" in rendered
        assert "Stored score: objective" in rendered

        panel.query_one("#routing-max-polls", Input).value = "2"
        panel.query_one("#routing-interval", Input).value = "0"
        panel.query_one("#routing-event-limit", Input).value = "1"
        panel.query_one("#routing-follow", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#routing-action-result", Static).content)
        assert "Follow: 2 polls | terminal" in rendered
        assert "History: wkl_route_tui | 1 events" in str(
            panel.query_one("#routing-history-result", Static).content
        )

    assert [call.action for call in source.calls] == [
        RoutingJobAction.STATUS,
        RoutingJobAction.RESULT,
        RoutingJobAction.FOLLOW,
    ]


async def test_panel_failure_is_stable_and_does_not_render_provider_detail() -> None:
    source = StaticRoutingJobsSource(failure=RoutingJobsError("routing_operation_failed"))
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-workload-id", Input).value = "wkl_route_tui"
        panel.query_one("#routing-status", Button).press()
        await pilot.pause()

        rendered = str(panel.query_one("#routing-action-error", Static).content)
        assert rendered == "Routing status failed safely: routing_operation_failed."


async def test_panel_submit_modal_cancel_and_escape_never_execute() -> None:
    source = StaticRoutingJobsSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-capability", Input).value = "llm.chat"
        panel.query_one("#routing-payload", Input).value = '{"messages":[]}'
        panel.query_one("#routing-submit", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRoutingJobModal)
        await pilot.click("#routing-confirm-cancel")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmRoutingJobModal)
        assert source.calls == []
        assert "Running routing" not in str(
            panel.query_one("#routing-action-result", Static).content
        )
        assert panel.query_one("#routing-stop", Button).disabled

        panel.query_one("#routing-submit", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmRoutingJobModal)
        confirm = app.screen.query_one("#routing-confirm-text", Input)
        confirm.value = "llm.chat"
        await pilot.pause()
        assert not app.screen.query_one("#routing-confirm-apply", Button).disabled
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmRoutingJobModal)
        assert source.calls == []
        assert "Running routing" not in str(
            panel.query_one("#routing-action-result", Static).content
        )
        assert panel.query_one("#routing-stop", Button).disabled

        panel.query_one("#routing-plan", Button).press()
        await pilot.pause()
        assert "Action: plan" in str(panel.query_one("#routing-action-result", Static).content)

    assert [call.action for call in source.calls] == [RoutingJobAction.PLAN]


async def test_panel_invalid_json_reports_safe_inline_error_with_zero_source_calls() -> None:
    source = StaticRoutingJobsSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-capability", Input).value = "llm.chat"
        panel.query_one("#routing-payload", Input).value = "{not valid json"
        panel.query_one("#routing-plan", Button).press()
        await pilot.pause()
        assert str(panel.query_one("#routing-action-error", Static).content) == (
            "Routing request is invalid; check identifiers, JSON, and bounds."
        )
        assert panel.query_one("#routing-stop", Button).disabled

        panel.query_one("#routing-payload", Input).value = '{"messages":[]}'
        panel.query_one("#routing-plan", Button).press()
        await pilot.pause()
        assert "Action: plan" in str(panel.query_one("#routing-action-result", Static).content)
        assert str(panel.query_one("#routing-action-error", Static).content) == ""

    assert [call.action for call in source.calls] == [RoutingJobAction.PLAN]


async def test_panel_generic_exception_is_safe_inline_without_provider_canary() -> None:
    canary = "provider-secret-canary-8f31"

    class FailingOnceSource(StaticRoutingJobsSource):
        async def execute_routing_job(self, command: RoutingJobCommand) -> RoutingJobResult:
            self.calls.append(command)
            if len(self.calls) == 1:
                raise RuntimeError(f"provider transport failed: Bearer {canary}")
            return self._results[command.action][-1]

    source = FailingOnceSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-workload-id", Input).value = "wkl_route_tui"
        panel.query_one("#routing-status", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#routing-action-error", Static).content)
        assert rendered == "Routing status is unavailable."
        assert canary not in rendered
        assert "RuntimeError" not in rendered
        assert panel.query_one("#routing-stop", Button).disabled

        panel.query_one("#routing-capability", Input).value = "llm.chat"
        panel.query_one("#routing-plan", Button).press()
        await pilot.pause()
        assert "Action: plan" in str(panel.query_one("#routing-action-result", Static).content)
        assert str(panel.query_one("#routing-action-error", Static).content) == ""

    assert [call.action for call in source.calls] == [
        RoutingJobAction.STATUS,
        RoutingJobAction.PLAN,
    ]


async def test_panel_stop_wait_cancels_blocked_job_and_remains_usable() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    release = asyncio.Event()

    class BlockingSource(StaticRoutingJobsSource):
        async def execute_routing_job(self, command: RoutingJobCommand) -> RoutingJobResult:
            self.calls.append(command)
            if command.action is RoutingJobAction.FOLLOW:
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return self._results[command.action][-1]

    source = BlockingSource(_scripted_results())
    panel = RoutingJobsPanel(source)
    app = _PanelApp(panel)

    async with app.run_test(size=(150, 48)) as pilot:
        panel.query_one("#routing-workload-id", Input).value = "wkl_route_tui"
        panel.query_one("#routing-follow", Button).press()
        await pilot.pause()
        assert "Running routing follow…" in str(
            panel.query_one("#routing-action-result", Static).content
        )
        assert not panel.query_one("#routing-stop", Button).disabled
        await asyncio.wait_for(started.wait(), timeout=HANG_GUARD_SECS)

        panel.query_one("#routing-stop", Button).press()
        await asyncio.wait_for(cancelled.wait(), timeout=HANG_GUARD_SECS)
        await pilot.pause()
        assert "Wait stopped." in str(panel.query_one("#routing-action-result", Static).content)
        assert panel.query_one("#routing-stop", Button).disabled
        assert str(panel.query_one("#routing-action-error", Static).content) == ""

        release.set()
        await pilot.pause()
        rendered = str(panel.query_one("#routing-action-result", Static).content)
        assert "Wait stopped." in rendered
        assert "Follow:" not in rendered
        assert not str(panel.query_one("#routing-history-result", Static).content)

        panel.query_one("#routing-status", Button).press()
        await pilot.pause()
        assert "Action: status" in str(panel.query_one("#routing-action-result", Static).content)

    assert [call.action for call in source.calls] == [
        RoutingJobAction.FOLLOW,
        RoutingJobAction.STATUS,
    ]
