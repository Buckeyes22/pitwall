"""Routing-jobs sources and command validation: the production service and a static double."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from pitwall.config import PitwallSettings
from pitwall.core.models import Workload
from pitwall.cost import BudgetRejected
from pitwall.resolver.exceptions import CapabilityNotFoundError
from pitwall.routing.production import (
    JobEventPage,
    JobResultPage,
    ProductionRoutePlan,
    ProductionRoutingService,
    RouteGuardrailRejected,
    RoutePlanningError,
    RoutingOperation,
)
from pitwall.tui.routing_jobs_views import (
    RoutingJobAction,
    RoutingJobCommand,
    RoutingJobHistoryView,
    RoutingJobResult,
    RoutingJobResultView,
    RoutingJobView,
    RoutingPlanView,
    enum_text,
)

MAX_IDENTIFIER_LENGTH = 255
MAX_WEBHOOK_LENGTH = 2_048
MAX_PAYLOAD_INPUT_BYTES = 65_536
TERMINAL_STATES = frozenset({"cancelled", "completed", "failed", "timed_out"})


class RoutingJobsSource(Protocol):
    """Typed async boundary between Textual widgets and routing services."""

    async def execute_routing_job(self, command: RoutingJobCommand) -> RoutingJobResult: ...


class _ProductionRoutingServiceLike(Protocol):
    async def preview(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        operation: RoutingOperation = RoutingOperation.SYNC_INFERENCE,
        provider_id: str | None = None,
    ) -> ProductionRoutePlan: ...

    async def submit_job(
        self,
        *,
        capability_id: str,
        payload: Mapping[str, Any],
        provider_id: str | None = None,
        idempotency_key: str | None = None,
        webhook_url: str | None = None,
    ) -> Workload: ...

    async def get_job(self, workload_id: str) -> Workload: ...

    async def job_result(self, workload_id: str) -> JobResultPage: ...

    async def cancel_job(self, workload_id: str) -> Workload: ...

    async def job_events(self, workload_id: str, *, limit: int | None = None) -> JobEventPage: ...


RoutingServiceFactory = Callable[[], Awaitable[_ProductionRoutingServiceLike]]
Sleeper = Callable[[float], Awaitable[None]]


class RoutingJobsError(RuntimeError):
    """Stable non-provider-specific error rendered by the TUI."""

    def __init__(self, code: str) -> None:
        normalized = (
            code
            if 1 <= len(code) <= 64
            and code == code.lower()
            and all(character.isalnum() or character == "_" for character in code)
            else "routing_operation_failed"
        )
        self.code = normalized
        super().__init__(normalized.replace("_", " "))


class ProductionRoutingJobsSource:
    """Delegate every plan and lifecycle action to ProductionRoutingService."""

    def __init__(
        self,
        service_factory: RoutingServiceFactory,
        *,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self._service_factory = service_factory
        self._sleep = sleep

    async def execute_routing_job(self, command: RoutingJobCommand) -> RoutingJobResult:
        validate_command(command)
        try:
            service = await self._service_factory()
            return await self._execute(service, command)
        except RoutingJobsError:
            raise
        except BudgetRejected:
            raise RoutingJobsError("budget_rejected") from None
        except RouteGuardrailRejected:
            raise RoutingJobsError("pre_spend_payload_rejected") from None
        except CapabilityNotFoundError:
            raise RoutingJobsError("route_not_found") from None
        except LookupError:
            code = (
                "route_not_found"
                if command.action in {RoutingJobAction.PLAN, RoutingJobAction.SUBMIT}
                else "job_not_found"
            )
            raise RoutingJobsError(code) from None
        except RoutePlanningError:
            raise RoutingJobsError("routing_unavailable") from None
        except TypeError, ValueError:
            raise RoutingJobsError("invalid_request") from None
        except Exception:  # reason: TUI boundary maps heterogeneous service failures safely
            raise RoutingJobsError("routing_operation_failed") from None

    async def _execute(
        self,
        service: _ProductionRoutingServiceLike,
        command: RoutingJobCommand,
    ) -> RoutingJobResult:
        if command.action is RoutingJobAction.PLAN:
            plan = await service.preview(
                capability_id=required_text(command.capability_id),
                payload=command.payload,
                operation=RoutingOperation.ASYNC_INFERENCE,
                provider_id=command.provider_id,
            )
            return RoutingJobResult(action=command.action, plan=RoutingPlanView.from_plan(plan))
        if command.action is RoutingJobAction.SUBMIT:
            workload = await service.submit_job(
                capability_id=required_text(command.capability_id),
                payload=command.payload,
                provider_id=command.provider_id,
                idempotency_key=command.idempotency_key,
                webhook_url=command.webhook_url,
            )
            return RoutingJobResult(
                action=command.action,
                job=RoutingJobView.from_workload(workload),
            )

        workload_id = required_text(command.workload_id)
        if command.action is RoutingJobAction.CANCEL:
            workload = await service.cancel_job(workload_id)
            return RoutingJobResult(
                action=command.action,
                job=RoutingJobView.from_workload(workload),
            )
        if command.action is RoutingJobAction.HISTORY:
            event_page = await service.job_events(workload_id, limit=command.event_limit)
            return RoutingJobResult(
                action=command.action,
                history=RoutingJobHistoryView.from_page(event_page),
            )
        if command.action is RoutingJobAction.RESULT:
            result_page = await service.job_result(workload_id)
            return RoutingJobResult(
                action=command.action,
                result_page=RoutingJobResultView.from_page(result_page),
            )
        if command.action is RoutingJobAction.FOLLOW:
            return await self._follow(service, command, workload_id)

        workload = await service.get_job(workload_id)
        return RoutingJobResult(
            action=command.action,
            job=RoutingJobView.from_workload(workload),
        )

    async def _follow(
        self,
        service: _ProductionRoutingServiceLike,
        command: RoutingJobCommand,
        workload_id: str,
    ) -> RoutingJobResult:
        workload: Workload | None = None
        polls = 0
        for polls in range(1, command.max_polls + 1):
            workload = await service.get_job(workload_id)
            if enum_text(workload.state) in TERMINAL_STATES:
                break
            if polls < command.max_polls:
                await self._sleep(command.interval_seconds)
        if workload is None:  # pragma: no cover - validation guarantees at least one poll
            raise RoutingJobsError("invalid_request")
        complete = enum_text(workload.state) in TERMINAL_STATES
        page = await service.job_events(workload_id, limit=command.event_limit)
        return RoutingJobResult(
            action=command.action,
            job=RoutingJobView.from_workload(workload),
            history=RoutingJobHistoryView.from_page(page),
            polls=polls,
            follow_complete=complete,
        )


class StaticRoutingJobsSource:
    """Scripted hermetic routing source for Pilot tests."""

    def __init__(
        self,
        results: Mapping[RoutingJobAction, RoutingJobResult | Sequence[RoutingJobResult]]
        | None = None,
        *,
        failure: RoutingJobsError | None = None,
    ) -> None:
        self._results: dict[RoutingJobAction, list[RoutingJobResult]] = {}
        for action, value in (results or {}).items():
            self._results[action] = list(value) if isinstance(value, Sequence) else [value]
        self.failure = failure
        self.calls: list[RoutingJobCommand] = []

    async def execute_routing_job(self, command: RoutingJobCommand) -> RoutingJobResult:
        validate_command(command)
        self.calls.append(command)
        if self.failure is not None:
            raise self.failure
        scripted = self._results.get(command.action, [])
        if not scripted:
            raise RoutingJobsError("routing_operation_unavailable")
        return scripted[0] if len(scripted) == 1 else scripted.pop(0)


def validate_command(
    command: RoutingJobCommand,
    *,
    require_confirmation: bool = True,
) -> None:
    if command.action in {RoutingJobAction.PLAN, RoutingJobAction.SUBMIT}:
        _validated_identifier(command.capability_id)
    else:
        _validated_identifier(command.workload_id)
    if command.provider_id is not None:
        _validated_identifier(command.provider_id)
    if command.idempotency_key is not None:
        _validated_identifier(command.idempotency_key)
    if not isinstance(command.payload, Mapping) or not all(
        isinstance(key, str) for key in command.payload
    ):
        raise RoutingJobsError("invalid_request")
    try:
        payload_size = len(
            json.dumps(command.payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except TypeError, ValueError:
        raise RoutingJobsError("invalid_request") from None
    if payload_size > MAX_PAYLOAD_INPUT_BYTES:
        raise RoutingJobsError("payload_too_large")
    if command.webhook_url is not None and (
        command.webhook_url != command.webhook_url.strip()
        or not 1 <= len(command.webhook_url) <= MAX_WEBHOOK_LENGTH
    ):
        raise RoutingJobsError("invalid_webhook")
    if isinstance(command.max_polls, bool) or not 1 <= command.max_polls <= 100:
        raise RoutingJobsError("invalid_follow_bounds")
    if (
        isinstance(command.interval_seconds, bool)
        or not math.isfinite(command.interval_seconds)
        or not 0 <= command.interval_seconds <= 60
    ):
        raise RoutingJobsError("invalid_follow_bounds")
    if isinstance(command.event_limit, bool) or not 1 <= command.event_limit <= 100:
        raise RoutingJobsError("invalid_history_bounds")
    if not require_confirmation:
        return
    if command.action is RoutingJobAction.SUBMIT and command.confirmation != command.capability_id:
        raise RoutingJobsError("confirmation_mismatch")
    if command.action is RoutingJobAction.CANCEL and command.confirmation != command.workload_id:
        raise RoutingJobsError("confirmation_mismatch")


def _validated_identifier(value: str | None) -> str:
    if value is None or value != value.strip() or not 1 <= len(value) <= MAX_IDENTIFIER_LENGTH:
        raise RoutingJobsError("invalid_identifier")
    return value


def required_text(value: str | None) -> str:
    if value is None:  # pragma: no cover - command validation owns the public failure
        raise RoutingJobsError("invalid_identifier")
    return value


def production_routing_service_factory(
    pool_factory: Callable[[], Awaitable[Any]],
    *,
    settings: PitwallSettings,
) -> RoutingServiceFactory:
    """Build the production TUI service lazily from the app-owned pool."""

    async def factory() -> ProductionRoutingService:
        return ProductionRoutingService(await pool_factory(), settings=settings)

    return factory
