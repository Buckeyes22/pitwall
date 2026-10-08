"""Cost reporting queries over pitwall.workloads and pitwall.cost_daily.

This module provides read-only queries into the persisted cost data.
No cost estimation happens here — only reading already-persisted values
from the service layer (workloads and cost_daily tables).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import asyncpg

from pitwall.cost.read_models import (
    CostSummaryEntry,
    CostSummaryRead,
    RecentWorkloadsRead,
    WorkloadCostRead,
    WorkloadCostRecord,
)


async def cost_summary_read(
    pool: asyncpg.Pool,
    *,
    capability_class: str | None = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> CostSummaryRead:
    """Return aggregated cost summary from pitwall.cost_daily.

    Args:
        pool: asyncpg connection pool.
        capability_class: Optional filter for capability class (e.g., 'embedding').
        since: Optional start date (inclusive).
        until: Optional end date (inclusive).

    Returns:
        A Decimal-authoritative aggregate read model. Compatibility conversion
        to JSON numbers happens only in :func:`cost_summary`.
    """
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if capability_class is not None:
        conditions.append(f"capability_class = ${idx}")
        params.append(capability_class)
        idx += 1

    if since is not None:
        conditions.append(f"day >= ${idx}")
        params.append(since)
        idx += 1

    if until is not None:
        conditions.append(f"day <= ${idx}")
        params.append(until)
        idx += 1

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    query = f"""
        SELECT
            day,
            capability_class,
            provider_type,
            workload_count,
            cost_usd
        FROM pitwall.cost_daily
        WHERE {where_clause}
        ORDER BY day DESC, capability_class, provider_type
    """

    total_query = f"""
        SELECT COALESCE(SUM(cost_usd), 0)
        FROM pitwall.cost_daily
        WHERE {where_clause}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
        total_row = await conn.fetchrow(total_query, *params)

    total_usd = _decimal_value(total_row[0], "total_usd") if total_row else Decimal("0")

    entries: list[CostSummaryEntry] = []
    for row in rows:
        entries.append(
            CostSummaryEntry(
                day=row["day"],
                capability_class=row["capability_class"],
                provider_type=row["provider_type"],
                workload_count=row["workload_count"],
                cost_usd=_decimal_value(row["cost_usd"], "cost_usd"),
            )
        )

    return CostSummaryRead(total_usd=total_usd, entries=tuple(entries))


async def cost_summary(
    pool: asyncpg.Pool,
    *,
    capability_class: str | None = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
) -> dict[str, Any]:
    """Return the established JSON-number response from the Decimal read model."""

    result = await cost_summary_read(
        pool,
        capability_class=capability_class,
        since=since,
        until=until,
    )
    return result.to_legacy_serializable_dict()


_RECENT_WORKLOADS_SELECT = """
        SELECT
            w.id,
            w.capability_id,
            w.provider_id,
            w.type,
            w.state,
            w.external_job_id,
            w.runpod_job_id,
            w.idempotency_key,
            w.submitted_at,
            w.started_at,
            w.completed_at,
            w.execution_ms,
            w.queue_ms,
            w.cold_start_ms,
            w.input_bytes,
            w.output_bytes,
            w.cost_estimate_usd,
            w.cost_ceiling_usd,
            w.cost_quote,
            w.cost_actual_usd,
            w.cost_actual_provenance,
            w.cost_reconciled_at,
            w.error,
            w.langfuse_trace_id,
            p.provider_type
        FROM pitwall.workloads w
        LEFT JOIN pitwall.providers p ON p.id = w.provider_id
"""


def _recent_workloads_read_query(
    *,
    capability_id: str | None,
    provider_id: str | None,
    provider_type: str | None,
    state: str | None,
    since: dt.datetime | None,
    until: dt.datetime | None,
    limit: int,
) -> tuple[str, list[Any]]:
    """Build the parameterised query: filters become numbered placeholders, never text."""
    filters: list[tuple[str, Any]] = [
        ("w.capability_id = ", capability_id),
        ("w.provider_id = ", provider_id),
        ("p.provider_type = ", provider_type),
        ("w.state = ", state),
        ("w.submitted_at >= ", since),
        ("w.submitted_at <= ", until),
    ]
    conditions: list[str] = []
    params: list[Any] = []
    for clause, value in filters:
        if value is not None:
            params.append(value)
            conditions.append(f"{clause}${len(params)}")
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)
    query = f"""{_RECENT_WORKLOADS_SELECT}
        WHERE {where_clause}
        ORDER BY w.submitted_at DESC
        LIMIT ${len(params)}
    """
    return query, params


def _recent_workloads_read_record(row: Any) -> WorkloadCostRecord:
    cost = WorkloadCostRead.from_persisted(
        cost_estimate_usd=_optional_decimal_value(row["cost_estimate_usd"], "cost_estimate_usd"),
        cost_ceiling_usd=_optional_decimal_value(
            _row_optional(row, "cost_ceiling_usd"), "cost_ceiling_usd"
        ),
        cost_quote=_optional_mapping_value(_row_optional(row, "cost_quote"), "cost_quote"),
        cost_actual_usd=_optional_decimal_value(row["cost_actual_usd"], "cost_actual_usd"),
        cost_actual_provenance=_optional_string_value(
            _row_optional(row, "cost_actual_provenance"),
            "cost_actual_provenance",
        ),
        cost_reconciled_at=_optional_datetime_value(
            _row_optional(row, "cost_reconciled_at"),
            "cost_reconciled_at",
        ),
    )
    return WorkloadCostRecord(
        fields={
            "id": row["id"],
            "capability_id": row["capability_id"],
            "provider_id": row["provider_id"],
            "provider_type": row["provider_type"],
            "type": row["type"],
            "state": row["state"],
            "external_job_id": row["external_job_id"],
            "runpod_job_id": row["runpod_job_id"],
            "idempotency_key": row["idempotency_key"],
            "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
            "execution_ms": row["execution_ms"],
            "queue_ms": row["queue_ms"],
            "cold_start_ms": row["cold_start_ms"],
            "input_bytes": row["input_bytes"],
            "output_bytes": row["output_bytes"],
            "error": row["error"],
            "langfuse_trace_id": row["langfuse_trace_id"],
        },
        cost=cost,
    )


async def recent_workloads_read(
    pool: asyncpg.Pool,
    *,
    capability_id: str | None = None,
    provider_id: str | None = None,
    provider_type: str | None = None,
    state: str | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    limit: int = 20,
) -> RecentWorkloadsRead:
    """Return recent workloads from pitwall.workloads with optional filters.

    Args:
        pool: asyncpg connection pool.
        capability_id: Optional filter for capability ID.
        provider_id: Optional filter for provider ID.
        provider_type: Optional filter for provider type (e.g., 'serverless_lb').
        state: Optional filter for workload state.
        since: Optional start datetime (inclusive).
        until: Optional end datetime (inclusive).
        limit: Maximum number of workloads to return (default 20).

    Returns:
        A Decimal-authoritative workload read model. Compatibility conversion
        happens only in :func:`recent_workloads`.
    """
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise TypeError("limit must be int")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    query, params = _recent_workloads_read_query(
        capability_id=capability_id,
        provider_id=provider_id,
        provider_type=provider_type,
        state=state,
        since=since,
        until=until,
        limit=limit,
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return RecentWorkloadsRead(workloads=tuple(_recent_workloads_read_record(row) for row in rows))


async def recent_workloads(
    pool: asyncpg.Pool,
    *,
    capability_id: str | None = None,
    provider_id: str | None = None,
    provider_type: str | None = None,
    state: str | None = None,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return the established response plus the shared exact cost breakdown."""

    result = await recent_workloads_read(
        pool,
        capability_id=capability_id,
        provider_id=provider_id,
        provider_type=provider_type,
        state=state,
        since=since,
        until=until,
        limit=limit,
    )
    return result.to_legacy_serializable_dict()


def _optional_decimal_value(value: object, name: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal_value(value, name)


def _row_optional(row: Any, name: str) -> object:
    try:
        return row[name]
    except IndexError, KeyError:
        return None


def _optional_mapping_value(value: object, name: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _optional_string_value(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    return value


def _optional_datetime_value(value: object, name: str) -> dt.datetime | None:
    if value is None:
        return None
    if not isinstance(value, dt.datetime):
        raise TypeError(f"{name} must be datetime.datetime")
    return value


def _decimal_value(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal")
    return value


__all__ = [
    "cost_summary",
    "cost_summary_read",
    "recent_workloads",
    "recent_workloads_read",
]
