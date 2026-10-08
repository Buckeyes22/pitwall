"""Pydantic response models for the quota and gateway-model REST surfaces (Task 10).

The schemas are intentionally minimal: they describe the JSON shape returned
by ``/v1/quotas`` and ``/v1/gateway/models`` so the OpenAPI document carries
the new endpoints and clients can introspect them. The Python route handlers
build these models from ``QuotaRecord`` and ``ModelIdMapping`` rows that the
``QuotaRepository`` returns, never directly from SQL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pitwall.routing.quota import QuotaRecord


class QuotaLockout(BaseModel):
    """Reason a quota gate eliminated a provider from the routing pool."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=64)


class QuotaRow(BaseModel):
    """One row of the ``GET /v1/quotas`` response.

    Mirrors ``QuotaRecord.to_dict()`` and adds the Stage-2-derived ``headroom``
    (0..1) and ``lockout`` (None unless the quota gate would reject).
    """

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    pool_key: str
    free_type: str
    window_start: str | None
    reset_at: str | None
    budget_units: str | None
    used_units: str
    tos_verdict: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    headroom: float = Field(ge=0.0, le=1.0)
    lockout: QuotaLockout | None = None

    @classmethod
    def from_record(
        cls,
        record: QuotaRecord,
        *,
        headroom: float,
        lockout_reason: str | None,
    ) -> QuotaRow:
        return cls(
            provider_id=record.provider_id,
            pool_key=record.pool_key,
            free_type=record.free_type,
            window_start=record.window_start.isoformat() if record.window_start else None,
            reset_at=record.reset_at.isoformat() if record.reset_at else None,
            budget_units=str(record.budget_units) if record.budget_units is not None else None,
            used_units=str(record.used_units),
            tos_verdict=record.tos_verdict,
            evidence=dict(record.evidence),
            headroom=headroom,
            lockout=QuotaLockout(reason=lockout_reason) if lockout_reason else None,
        )


class QuotaList(BaseModel):
    """Body of ``GET /v1/quotas``."""

    model_config = ConfigDict(extra="forbid")

    quotas: list[QuotaRow] = Field(default_factory=list)


class QuotaRefreshResponse(BaseModel):
    """Body of ``POST /v1/admin/quotas/refresh``."""

    model_config = ConfigDict(extra="forbid")

    refreshed: int = Field(ge=0)


class GatewayModelRow(BaseModel):
    """One row of the ``GET /v1/gateway/models`` response."""

    model_config = ConfigDict(extra="forbid")

    id: str
    capability: str
    provider: str
    trains_on_prompts: bool = False
    tos: str


class GatewayModelList(BaseModel):
    """Body of ``GET /v1/gateway/models``."""

    model_config = ConfigDict(extra="forbid")

    object: str = "list"
    data: list[GatewayModelRow] = Field(default_factory=list)


def _coerce_evidence(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if key}


def compute_headroom(record: QuotaRecord) -> float:
    """Return the share of the window still available (0..1).

    Unbudgeted (keyless) pools report ``1.0`` — they never exhaust.
    """

    if record.budget_units is None or record.budget_units <= Decimal("0"):
        return 1.0
    used = min(record.used_units, record.budget_units)
    remaining = max(record.budget_units - used, Decimal("0"))
    return max(0.0, min(1.0, float(remaining / record.budget_units)))


__all__ = [
    "GatewayModelList",
    "GatewayModelRow",
    "QuotaList",
    "QuotaLockout",
    "QuotaRefreshResponse",
    "QuotaRow",
    "compute_headroom",
]
