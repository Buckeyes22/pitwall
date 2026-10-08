"""Feature-local REST reads for free-tier quota state (Task 10).

The router is included from ``pitwall.api.app`` once the schema is registered.
The handler binds the ``QuotaRepository`` either to ``app.state.quota_repository``
(test seam) or constructs one from ``app.state.pool`` (production boot).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, cast

from fastapi import APIRouter, Request
from pydantic import ValidationError

from pitwall.api.schemas.quotas import (
    QuotaList,
    QuotaRefreshResponse,
    QuotaRow,
    compute_headroom,
)
from pitwall.db.quota_repository import QuotaRepository
from pitwall.routing.quota import QuotaRecord, record_ineligibility

log = logging.getLogger("pitwall.api.routes.quotas")

router = APIRouter(tags=["quotas"])


def _resolve_repo(request: Request) -> QuotaRepository | None:
    configured: object = getattr(request.app.state, "quota_repository", None)
    if configured is not None:
        return cast(QuotaRepository, configured)
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return None
    return QuotaRepository(pool)


async def _quota_poll(repository: QuotaRepository, *, now: dt.datetime) -> int:
    """Record a quota sample for every existing provider quota row.

    Returns the number of rows sampled. The handler treats this as the
    "refreshed" count returned to the operator.
    """

    records = await repository.list_all()
    for record in records:
        try:
            await repository.record_sample(
                record.provider_id,
                now,
                record.used_units,
                record.reset_at,
            )
        except Exception:  # reason: quota refresh must never raise to the caller
            log.warning(
                "quota_poll record_sample failed for %s",
                record.provider_id,
                exc_info=True,
            )
    return len(records)


def _row_payload(record: QuotaRecord, *, now: dt.datetime) -> QuotaRow | None:
    try:
        return QuotaRow.from_record(
            record,
            headroom=compute_headroom(record),
            lockout_reason=record_ineligibility(record, now),
        )
    except ValidationError as exc:
        log.warning("quota row validation failed for %s: %s", record.provider_id, exc)
        return None


@router.get("/v1/quotas", response_model=QuotaList)
async def list_quotas(request: Request) -> dict[str, Any]:
    """Return every quota row with its Stage-2 headroom and lockout reason."""

    repository = _resolve_repo(request)
    if repository is None:
        return QuotaList(quotas=[]).model_dump(mode="json")
    records = await repository.list_all()
    now = dt.datetime.now(dt.UTC)
    rows = [row for row in (_row_payload(record, now=now) for record in records) if row is not None]
    return QuotaList(quotas=rows).model_dump(mode="json")


@router.post("/v1/admin/quotas/refresh", response_model=QuotaRefreshResponse)
async def refresh_quotas(request: Request) -> dict[str, Any]:
    """Run ``_quota_poll`` once and return the number of records sampled."""

    repository = _resolve_repo(request)
    if repository is None:
        return QuotaRefreshResponse(refreshed=0).model_dump(mode="json")
    refreshed = await _quota_poll(repository, now=dt.datetime.now(dt.UTC))
    return QuotaRefreshResponse(refreshed=refreshed).model_dump(mode="json")


__all__ = ["list_quotas", "refresh_quotas", "router"]
