"""Workload lifecycle management for pass-through requests.

Inserts and updates workload rows to track the lifecycle of OpenAI-compatible
pass-through requests through Pitwall. The workload row is created as 'queued'
when a request arrives, transitioned to 'running' once the upstream is called,
and completed or failed based on the outcome. The external OpenAI response is
never modified.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from pitwall.core.enums import WorkloadState
from pitwall.core.ids import ulid_new
from pitwall.core.models import Workload
from pitwall.db.repository import WorkloadRepository


def generate_workload_id() -> str:
    return f"wkl_{ulid_new()}"


async def transition_to_running(
    repo: WorkloadRepository,
    workload_id: str,
    *,
    provider_id: str | None = None,
    fallback_chain: list[str] | None = None,
) -> Workload | None:
    del provider_id  # retained for lifecycle API compatibility; winner persists at completion
    now = dt.datetime.now(dt.UTC)
    patch: dict[str, Any] = {"started_at": now}
    if fallback_chain is not None:
        patch["fallback_chain"] = fallback_chain if fallback_chain else None
    return await repo.guarded_transition(
        workload_id,
        from_states={WorkloadState.QUEUED},
        to_state=WorkloadState.RUNNING,
        patch=patch,
    )


async def transition_to_completed(
    repo: WorkloadRepository,
    workload_id: str,
    *,
    execution_ms: int | None = None,
    output_bytes: int | None = None,
    result: dict[str, Any] | None = None,
    provider_id: str | None = None,
    cost_ceiling_usd: Decimal | None = None,
    cost_quote: dict[str, object] | None = None,
    cost_actual_usd: Decimal | None = None,
    cost_actual_provenance: str | None = None,
    fallback_chain: list[str] | None = None,
    langfuse_trace_id: str | None = None,
) -> Workload | None:
    now = dt.datetime.now(dt.UTC)
    patch: dict[str, Any] = {
        "completed_at": now,
        "execution_ms": execution_ms,
        "output_bytes": output_bytes,
        "result": result,
        "fallback_chain": fallback_chain if fallback_chain else None,
        "langfuse_trace_id": langfuse_trace_id,
    }
    if provider_id is not None:
        patch["provider_id"] = provider_id
    if (cost_ceiling_usd is None) != (cost_quote is None):
        raise ValueError("cost_ceiling_usd and cost_quote must be provided together")
    if cost_ceiling_usd is not None:
        patch["cost_ceiling_usd"] = cost_ceiling_usd
        patch["cost_quote"] = cost_quote
    if cost_actual_usd is not None:
        if cost_actual_provenance is None:
            raise ValueError("cost_actual_provenance is required with cost_actual_usd")
        patch["cost_actual_usd"] = cost_actual_usd
        patch["cost_actual_provenance"] = cost_actual_provenance
        patch["cost_reconciled_at"] = now
    return await repo.guarded_transition(
        workload_id,
        from_states={WorkloadState.RUNNING},
        to_state=WorkloadState.COMPLETED,
        patch=patch,
    )


async def transition_to_failed(
    repo: WorkloadRepository,
    workload_id: str,
    *,
    execution_ms: int | None = None,
    output_bytes: int | None = None,
    provider_id: str | None = None,
    error: dict[str, Any] | None = None,
    fallback_chain: list[str] | None = None,
    langfuse_trace_id: str | None = None,
    cost_ceiling_usd: Decimal | None = None,
    cost_quote: dict[str, object] | None = None,
    cost_actual_usd: Decimal | None = None,
    cost_actual_provenance: str | None = None,
    allow_queued: bool = False,
) -> Workload | None:
    now = dt.datetime.now(dt.UTC)
    patch: dict[str, Any] = {
        "completed_at": now,
        "execution_ms": execution_ms,
        "error": error,
        "fallback_chain": fallback_chain if fallback_chain else None,
        "langfuse_trace_id": langfuse_trace_id,
    }
    if output_bytes is not None:
        patch["output_bytes"] = output_bytes
    if provider_id is not None:
        patch["provider_id"] = provider_id
    if (cost_ceiling_usd is None) != (cost_quote is None):
        raise ValueError("cost_ceiling_usd and cost_quote must be provided together")
    if cost_ceiling_usd is not None:
        patch["cost_ceiling_usd"] = cost_ceiling_usd
        patch["cost_quote"] = cost_quote
    if cost_actual_usd is not None:
        if cost_actual_provenance is None:
            raise ValueError("cost_actual_provenance is required with cost_actual_usd")
        patch["cost_actual_usd"] = cost_actual_usd
        patch["cost_actual_provenance"] = cost_actual_provenance
        patch["cost_reconciled_at"] = now
    return await repo.guarded_transition(
        workload_id,
        from_states=(
            {WorkloadState.QUEUED, WorkloadState.RUNNING}
            if allow_queued
            else {WorkloadState.RUNNING}
        ),
        to_state=WorkloadState.FAILED,
        patch=patch,
    )


__all__ = [
    "generate_workload_id",
    "transition_to_completed",
    "transition_to_failed",
    "transition_to_running",
]
