"""Structured dispatch lifecycle events."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .run_store import RunStore, append_jsonl, utc_now

EventCallback = Callable[[dict[str, Any], RunStore], None]


class EventEmitter:
    def __init__(
        self,
        store: RunStore,
        *,
        harness: str,
        model: str,
        workflow_id: str | None = None,
        task_id: str | None = None,
        callback: EventCallback | None = None,
    ) -> None:
        self.store = store
        self.harness = harness
        self.model = model
        self.workflow_id = workflow_id
        self.task_id = task_id
        self.callback = callback

    def emit(self, event: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        envelope = {
            "schemaVersion": 1,
            "eventId": str(uuid.uuid4()),
            "event": event,
            "timestamp": utc_now(),
            "dispatchId": self.store.dispatch_id,
            "workflowId": self.workflow_id,
            "taskId": self.task_id,
            "provider": self.harness,
            "model": self.model,
            "data": data or {},
        }
        append_jsonl(self.store.artifact("events.jsonl"), envelope)
        append_jsonl(self.store.state_root / "events.jsonl", envelope)
        if self.callback is not None:
            self.callback(envelope, self.store)
        return envelope

    def emit_dispatch_paused(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record a non-terminal pause: the dispatch waits on an unresolved ask."""
        return self.emit("dispatch.paused", data)

    def emit_ask_resolved(
        self, ask_id: str, resolved_by: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Record an ask resolution; ``resolved_by`` is mandatory provenance."""
        if not resolved_by:
            raise ValueError("ask.resolved requires a resolved_by source")
        return self.emit(
            "ask.resolved", {"askId": ask_id, "resolvedBy": resolved_by, **(data or {})}
        )

    def emit_steer_acked(self, steer_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record a steering acknowledgement."""
        return self.emit("steer.acked", {"steerId": steer_id, **(data or {})})
