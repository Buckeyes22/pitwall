"""Bounded, redacted views of routing plans, jobs, events, and results for the TUI."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pitwall.core.models import Workload
from pitwall.routing.production import (
    JobEventPage,
    JobResultPage,
    ProductionRoutePlan,
)
from pitwall.security.redaction import redact_text
from pitwall.tui.serve import redacted_preview

_MAX_RESULT_CHARACTERS = 2_048
_MAX_EVENT_CHARACTERS = 256


class RoutingJobAction(StrEnum):
    """Operations exposed by the provider-neutral TUI source."""

    PLAN = "plan"
    SUBMIT = "submit"
    STATUS = "status"
    RESULT = "result"
    CANCEL = "cancel"
    FOLLOW = "follow"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class RoutingJobCommand:
    """Bounded, typed command passed from the widget to its source."""

    action: RoutingJobAction
    capability_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    provider_id: str | None = None
    idempotency_key: str | None = None
    webhook_url: str | None = None
    workload_id: str | None = None
    confirmation: str | None = None
    max_polls: int = 20
    interval_seconds: float = 1.0
    event_limit: int = 25


@dataclass(frozen=True, slots=True)
class RoutingPlanView:
    """Payload-free route-plan fields safe for operator rendering."""

    plan_id: str
    capability_id: str
    capability_name: str
    selected_provider_id: str
    fallback_chain: tuple[str, ...]
    mode: str
    objective: str
    cost_component: str
    latency_component: str
    latency_ms: str
    latency_signal: str
    candidate_count: int
    eliminated_count: int

    @classmethod
    def from_plan(cls, plan: ProductionRoutePlan) -> RoutingPlanView:
        selected = plan.selected
        return cls(
            plan_id=plan.plan_id,
            capability_id=plan.capability_id,
            capability_name=plan.capability_name,
            selected_provider_id=plan.selected_provider_id,
            fallback_chain=plan.fallback_chain,
            mode=plan.mode,
            objective=str(selected.objective),
            cost_component=str(selected.cost_component),
            latency_component=str(selected.latency_component),
            latency_ms=str(selected.latency_ms),
            latency_signal=selected.latency_signal,
            candidate_count=len(plan.ranked_candidates),
            eliminated_count=len(plan.eliminated),
        )

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> RoutingPlanView | None:
        """Read only the stable semantic fields from an append-only stored plan."""

        ranked = document.get("ranked_candidates")
        selected_provider = _optional_text(document.get("selected_provider_id"))
        selected: Mapping[str, object] | None = None
        if isinstance(ranked, list):
            for candidate in ranked:
                if (
                    isinstance(candidate, Mapping)
                    and _optional_text(candidate.get("provider_id")) == selected_provider
                ):
                    selected = candidate
                    break
        if (
            selected is None
            and isinstance(ranked, list)
            and ranked
            and isinstance(ranked[0], Mapping)
        ):
            selected = ranked[0]
        if selected is None or selected_provider is None:
            return None
        components = selected.get("score_components")
        component_map = components if isinstance(components, Mapping) else {}
        plan_id = _optional_text(document.get("plan_id"))
        capability_id = _optional_text(document.get("capability_id"))
        capability_name = _optional_text(document.get("capability_name"))
        if plan_id is None or capability_id is None or capability_name is None:
            return None
        fallback = document.get("fallback_chain")
        fallback_chain = (
            tuple(
                _bounded_text(item, fallback="unknown")
                for value in fallback
                if (item := _optional_text(value)) is not None
            )
            if isinstance(fallback, list)
            else (selected_provider,)
        )
        eliminated = document.get("eliminated")
        return cls(
            plan_id=_bounded_text(plan_id, fallback="unknown"),
            capability_id=_bounded_text(capability_id, fallback="unknown"),
            capability_name=_bounded_text(capability_name, fallback="unknown"),
            selected_provider_id=_bounded_text(selected_provider, fallback="unknown"),
            fallback_chain=fallback_chain,
            mode=_bounded_text(document.get("mode"), fallback="unknown"),
            objective=_bounded_text(selected.get("objective"), fallback="unknown"),
            cost_component=_bounded_text(component_map.get("cost"), fallback="unknown"),
            latency_component=_bounded_text(component_map.get("latency"), fallback="unknown"),
            latency_ms=_bounded_text(component_map.get("latency_ms"), fallback="unknown"),
            latency_signal=_bounded_text(component_map.get("latency_signal"), fallback="unknown"),
            candidate_count=len(ranked) if isinstance(ranked, list) else 0,
            eliminated_count=len(eliminated) if isinstance(eliminated, list) else 0,
        )

    @property
    def fallback_label(self) -> str:
        fallbacks = tuple(
            provider_id
            for provider_id in self.fallback_chain
            if provider_id != self.selected_provider_id
        )
        return "none" if not fallbacks else " -> ".join(fallbacks)

    def render(self) -> str:
        return "\n".join(
            (
                f"Plan: {self.plan_id}",
                f"Capability: {self.capability_name} ({self.capability_id})",
                f"Selected provider: {self.selected_provider_id}",
                f"Mode: {self.mode} | fallbacks: {self.fallback_label}",
                f"Objective: {self.objective}",
                f"Score: cost {self.cost_component} | latency {self.latency_component}",
                f"Latency: {self.latency_ms} ms ({self.latency_signal})",
                f"Candidates: {self.candidate_count} | eliminated: {self.eliminated_count}",
            )
        )


@dataclass(frozen=True, slots=True)
class RoutingJobView:
    """Provider-neutral persisted job status with bounded safe result fields."""

    workload_id: str
    state: str
    provider_id: str
    plan_id: str | None
    external_job_id: str | None
    plan: RoutingPlanView | None
    result_summary: str
    failure_summary: str

    @classmethod
    def from_workload(cls, workload: Workload) -> RoutingJobView:
        plan = (
            RoutingPlanView.from_document(workload.route_plan)
            if workload.route_plan is not None
            else None
        )
        return cls(
            workload_id=_bounded_text(workload.id, fallback="unknown"),
            state=enum_text(workload.state),
            provider_id=_bounded_text(workload.provider_id, fallback="unknown"),
            plan_id=workload.route_plan_id,
            external_job_id=(
                None
                if workload.external_job_id is None
                else _bounded_text(workload.external_job_id, fallback="unknown")
            ),
            plan=plan,
            result_summary=_bounded_json(workload.result, fallback="not available"),
            failure_summary=_failure_summary(workload.error),
        )

    def render(self, *, include_result: bool) -> str:
        lines = [
            f"Job: {self.workload_id}",
            f"State: {self.state} | provider: {self.provider_id}",
            f"Plan: {self.plan_id or 'none'}",
            f"External job: {self.external_job_id or 'none'}",
        ]
        if self.failure_summary != "none":
            lines.append(f"Failure: {self.failure_summary}")
        if include_result:
            lines.append(f"Result: {self.result_summary}")
        if self.plan is not None:
            lines.append(
                "Stored score: "
                f"objective {self.plan.objective} | cost {self.plan.cost_component} | "
                f"latency {self.plan.latency_component}"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RoutingJobEventView:
    """One bounded lifecycle event without provider payloads."""

    event: str
    state: str
    observed_at: str

    @classmethod
    def from_mapping(cls, event: Mapping[str, object]) -> RoutingJobEventView:
        return cls(
            event=_bounded_text(event.get("event"), fallback="event"),
            state=_bounded_text(event.get("state"), fallback="unknown"),
            observed_at=_bounded_text(event.get("at"), fallback="unknown"),
        )


@dataclass(frozen=True, slots=True)
class RoutingJobHistoryView:
    """Bounded persisted history and explicit stream capability."""

    workload_id: str
    plan_id: str | None
    events: tuple[RoutingJobEventView, ...]
    streaming_supported: bool
    unavailable_reason: str | None

    @classmethod
    def from_page(cls, page: JobEventPage) -> RoutingJobHistoryView:
        return cls(
            workload_id=_bounded_text(page.workload_id, fallback="unknown"),
            plan_id=page.plan_id,
            events=tuple(RoutingJobEventView.from_mapping(event) for event in page.events),
            streaming_supported=page.streaming_supported,
            unavailable_reason=(
                None
                if page.streaming_supported
                else _bounded_text(page.unavailable_reason, fallback="unsupported")
            ),
        )

    def render(self) -> str:
        lines = [
            f"History: {self.workload_id} | {len(self.events)} events",
            (
                "Provider stream: supported"
                if self.streaming_supported
                else f"Provider stream: unavailable ({self.unavailable_reason or 'unsupported'})"
            ),
        ]
        lines.extend(
            f"{index}. {event.event} | {event.state} | {event.observed_at}"
            for index, event in enumerate(self.events, start=1)
        )
        if not self.events:
            lines.append("No persisted lifecycle events")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RoutingJobResultView:
    """Bounded presentation of the shared provider-neutral result page."""

    workload_id: str
    plan_id: str | None
    state: str
    provider_id: str
    plan: RoutingPlanView | None
    available: bool
    unavailable_reason: str | None
    result_summary: str

    @classmethod
    def from_page(cls, page: JobResultPage) -> RoutingJobResultView:
        return cls(
            workload_id=_bounded_text(page.workload_id, fallback="unknown"),
            plan_id=page.plan_id,
            state=_bounded_text(page.state, fallback="unknown"),
            provider_id=_bounded_text(page.provider_id, fallback="unknown"),
            plan=RoutingPlanView.from_document(page.plan) if page.plan is not None else None,
            available=page.available,
            unavailable_reason=(
                None
                if page.unavailable_reason is None
                else _bounded_text(page.unavailable_reason, fallback="unavailable")
            ),
            result_summary=_bounded_json(page.result, fallback="not available"),
        )

    def render(self) -> str:
        lines = [
            f"Job result: {self.workload_id}",
            f"State: {self.state} | provider: {self.provider_id}",
            f"Plan: {self.plan_id or 'none'}",
        ]
        if self.available:
            lines.append(f"Result: {self.result_summary}")
        else:
            lines.append(f"Result unavailable: {self.unavailable_reason or 'unavailable'}")
        if self.plan is not None:
            lines.append(
                "Stored score: "
                f"objective {self.plan.objective} | cost {self.plan.cost_component} | "
                f"latency {self.plan.latency_component}"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RoutingJobResult:
    """Single action result consumed by the Operations routing panel."""

    action: RoutingJobAction
    plan: RoutingPlanView | None = None
    job: RoutingJobView | None = None
    result_page: RoutingJobResultView | None = None
    history: RoutingJobHistoryView | None = None
    polls: int | None = None
    follow_complete: bool | None = None


def enum_text(value: object) -> str:
    return str(getattr(value, "value", value))


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bounded_text(value: object, *, fallback: str) -> str:
    text = _optional_text(value) or fallback
    text = " ".join(text.split())
    return text if len(text) <= _MAX_EVENT_CHARACTERS else text[: _MAX_EVENT_CHARACTERS - 3] + "..."


def _bounded_json(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    bounded = _bound_json_value(redacted_preview(value), depth=0)
    rendered = redact_text(
        json.dumps(
            bounded,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
    )
    if len(rendered) <= _MAX_RESULT_CHARACTERS:
        return rendered
    return rendered[: _MAX_RESULT_CHARACTERS - 3] + "..."


def _bound_json_value(value: object, *, depth: int) -> object:
    if depth >= 8:
        return "..."
    if isinstance(value, Mapping):
        mapping_items = list(value.items())
        bounded = {
            str(key): _bound_json_value(item, depth=depth + 1) for key, item in mapping_items[:32]
        }
        if len(mapping_items) > 32:
            bounded["..."] = f"{len(mapping_items) - 32} more fields"
        return bounded
    if isinstance(value, (list, tuple)):
        sequence_items = [_bound_json_value(item, depth=depth + 1) for item in value[:32]]
        if len(value) > 32:
            sequence_items.append(f"... {len(value) - 32} more items")
        return sequence_items
    if isinstance(value, str) and len(value) > 512:
        return value[:509] + "..."
    return value


def _failure_summary(error: Mapping[str, Any] | None) -> str:
    if not error:
        return "none"
    code = _safe_failure_token(error.get("code"), fallback="workload_failed")
    attempts = error.get("attempts")
    attempt_labels: list[str] = []
    if isinstance(attempts, list):
        for attempt in attempts[:10]:
            if not isinstance(attempt, Mapping):
                continue
            attempt_code = _safe_failure_token(attempt.get("code"), fallback="provider_call_failed")
            attempt_type = _safe_failure_token(attempt.get("type"), fallback="provider_error")
            attempt_labels.append(f"{attempt_code}/{attempt_type}")
    return code if not attempt_labels else f"{code} ({', '.join(attempt_labels)})"


def _safe_failure_token(value: object, *, fallback: str) -> str:
    text = _optional_text(value)
    if text is None or len(text) > 64:
        return fallback
    if not all(character.isalnum() or character in {"_", "-", "."} for character in text):
        return fallback
    return text
