"""Shared durable admission state for provider compute provisioning."""

from __future__ import annotations

import datetime as dt
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pitwall.core.enums import WorkloadState
from pitwall.core.models import Capability
from pitwall.cost.estimator import (
    CostQuote,
    GpuHourPricing,
    PerSecondPricing,
    PerVmSecondPricing,
)
from pitwall.providers.interface import ProvisionRequest


@dataclass(frozen=True, slots=True)
class ProvisionAdmission:
    """One budget reservation and whether this caller owns provider dispatch."""

    workload_id: str
    is_new: bool


@dataclass(frozen=True, slots=True)
class ProvisionReplay:
    """A completed idempotent provisioning result loaded without provider egress."""

    external_id: str
    lease_id: str | None


class _ProvisionReplayError(RuntimeError):
    """A same-key provisioning request that cannot replay; names the key's workload."""

    def __init__(self, message: str, *, workload_id: str) -> None:
        super().__init__(message)
        self.workload_id = workload_id


class ProvisionReplayInProgress(_ProvisionReplayError):
    """An idempotent provisioning request is already being dispatched."""


class ProvisionReplayFailed(_ProvisionReplayError):
    """An idempotent provisioning request previously failed."""


class ProvisionReplayConflict(_ProvisionReplayError):
    """The idempotency key belongs to a different provisioning request."""


#: Workload ``input`` key holding the SHA-256 of the request a keyed admission admitted. The
#: lease launch path (``pitwall.api.leases.launch.LAUNCH_REQUEST_DIGEST_KEY``) uses the same key.
LAUNCH_REQUEST_DIGEST_KEY = "launch_request_sha256"


#: Reservation and settlement rate for a provider lease whose adapter states no per-second
#: rate (the same conservative default ``estimate_raw_pod_lease_cost`` uses for raw pods).
FALLBACK_LEASE_USD_PER_HOUR = Decimal("0.50")
_HOUR_SECONDS = Decimal(3600)


def lease_rate_per_second(pricing: object) -> Decimal | None:
    """The per-second rate a provider lease bills at, or ``None`` when the pricing has none.

    A per-VM-second rate is the rate. A per-second rate with a bid is the higher of the two
    (a Vast create sends the bid as its price), so the budget never counts less than the VM
    can bill.
    """
    if isinstance(pricing, PerVmSecondPricing):
        return pricing.rate_per_second
    if isinstance(pricing, PerSecondPricing):
        bid = pricing.bid_rate_per_second
        return max(pricing.rate_per_second, bid) if bid is not None else pricing.rate_per_second
    if isinstance(pricing, GpuHourPricing):
        return pricing.per_second_active
    return None


def fallback_lease_rate_per_second() -> Decimal:
    return FALLBACK_LEASE_USD_PER_HOUR / _HOUR_SECONDS


def lease_reservation(
    pricing: Any,
    capability: Capability,
    payload: Mapping[str, Any],
    *,
    ttl_ms: int,
) -> CostQuote | Decimal:
    """What a provider lease admission reserves: its billing rate for the whole lease TTL.

    The quote prices the lease TTL, not the capability's execution timeout. A pricing with no
    per-second rate reserves ``FALLBACK_LEASE_USD_PER_HOUR`` for the TTL instead, never $0.
    """
    if lease_rate_per_second(pricing) is None:
        hours = Decimal(ttl_ms) / Decimal(3_600_000)
        return (FALLBACK_LEASE_USD_PER_HOUR * hours).quantize(Decimal("0.000001"))
    lease_window = capability.model_copy(
        update={"defaults": capability.defaults.model_copy(update={"execution_timeout_ms": ttl_ms})}
    )
    return CostQuote(pricing=pricing, capability=lease_window, payload=dict(payload))


async def admit_provision(
    request: ProvisionRequest,
    *,
    estimate_usd: object,
) -> ProvisionAdmission | None:
    """Reserve spend and retain ownership information when the gate provides it."""

    gate = request.budget_gate
    if gate is None:
        return None
    kwargs: dict[str, Any] = {
        "capability_id": request.capability.id,
        "provider_id": request.provider_record.id,
        "estimate_usd": estimate_usd,
        "workload_type": "vm_lease",
        "idempotency_key": request.idempotency_key,
    }
    admission_method = getattr(gate, "try_launch_admission", None)
    if callable(admission_method):
        if request.idempotency_key is not None and request.request_fingerprint is not None:
            # Record the request digest in the admission transaction, so a same-key request
            # that waited on the budget lock can replay (or refuse) by it.
            document = json.dumps({LAUNCH_REQUEST_DIGEST_KEY: request.request_fingerprint})

            async def record_request_digest(conn: Any, workload_id: str) -> None:
                await conn.execute(
                    "UPDATE pitwall.workloads SET input = $2::jsonb WHERE id = $1",
                    workload_id,
                    document,
                )

            kwargs["after_new_admission"] = record_request_digest
        admitted = await admission_method(**kwargs)
        return ProvisionAdmission(
            workload_id=str(admitted.workload_id),
            is_new=bool(admitted.is_new),
        )
    workload_id = await gate.try_launch(**kwargs)
    return ProvisionAdmission(workload_id=str(workload_id), is_new=True)


async def load_provision_replay(
    pool: Any,
    workload_id: str,
    *,
    capability_id: str,
    provider_id: str,
    request_fingerprint: str | None,
) -> ProvisionReplay:
    """Load a completed replay or fail closed without performing provider egress.

    The key's workload is returned only for the same request: a provisioning workload for the
    same capability and provider whose recorded request digest equals ``request_fingerprint``.
    A workload admitted before digests existed (no stored digest) replays for any caller.
    """

    if not _has_acquire(pool):
        raise ProvisionReplayInProgress(
            f"provisioning request {workload_id!r} is already reserved", workload_id=workload_id
        )
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT state, result, capability_id, provider_id, type, input"
            " FROM pitwall.workloads WHERE id = $1",
            workload_id,
        )
    # An asyncpg Record is not a Mapping; read it through its keys.
    row = dict(record.items()) if hasattr(record, "items") else None
    if not isinstance(row, Mapping):
        raise ProvisionReplayInProgress(
            f"provisioning request {workload_id!r} is already reserved", workload_id=workload_id
        )
    if (row.get("capability_id"), row.get("provider_id"), row.get("type")) != (
        capability_id,
        provider_id,
        "vm_lease",
    ):
        raise ProvisionReplayConflict(
            f"idempotency key was already used for a different request ({workload_id!r})",
            workload_id=workload_id,
        )
    stored_input = row.get("input")
    recorded = (
        stored_input.get(LAUNCH_REQUEST_DIGEST_KEY) if isinstance(stored_input, Mapping) else None
    )
    recorded_digest = recorded if isinstance(recorded, str) else None
    if recorded_digest is not None and (
        request_fingerprint is None or not hmac.compare_digest(recorded_digest, request_fingerprint)
    ):
        raise ProvisionReplayConflict(
            f"idempotency key was already used for a different request ({workload_id!r})",
            workload_id=workload_id,
        )
    state = str(row.get("state", ""))
    if state == WorkloadState.FAILED.value:
        raise ProvisionReplayFailed(
            f"provisioning request {workload_id!r} previously failed", workload_id=workload_id
        )
    result = row.get("result")
    if state != WorkloadState.COMPLETED.value or not isinstance(result, Mapping):
        raise ProvisionReplayInProgress(
            f"provisioning request {workload_id!r} is in progress", workload_id=workload_id
        )
    external_id = _optional_string(result.get("external_id"))
    if external_id is None:
        raise ProvisionReplayInProgress(
            f"provisioning request {workload_id!r} has no completed external id",
            workload_id=workload_id,
        )
    return ProvisionReplay(
        external_id=external_id,
        lease_id=_optional_string(result.get("lease_id")),
    )


async def mark_provision_completed(
    pool: Any,
    *,
    workload_id: str | None,
    external_id: str,
    lease_id: str | None,
    now: dt.datetime | None,
) -> None:
    """Persist the exact replay outcome after provider and lease writes succeed."""

    if workload_id is None or not _has_acquire(pool):
        return
    completed_at = _utc_now(now)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pitwall.workloads
            SET state = 'completed',
                completed_at = $2,
                result = $3::jsonb
            WHERE id = $1
              AND state IN ('queued', 'running')
            """,
            workload_id,
            completed_at,
            {"external_id": external_id, "lease_id": lease_id},
        )


async def mark_provision_failed(
    pool: Any,
    *,
    workload_id: str | None,
    now: dt.datetime | None,
    reservation_released: bool,
) -> None:
    """Fail the operation and release spend only when absence is proven.

    Proven absence (never dispatched, or the created resource was deleted) also clears the
    workload's idempotency key in the same statement, as the RunPod paths do, so a same-key
    retry admits and provisions again. An unknown outcome keeps the key bound.
    """

    if workload_id is None or not _has_acquire(pool):
        return
    completed_at = _utc_now(now)
    async with pool.acquire() as conn:
        error = {"type": "ProviderProvisionError", "message": "provider provisioning failed"}
        if reservation_released:
            await conn.execute(
                """
                UPDATE pitwall.workloads
                SET state = 'failed',
                    completed_at = $2,
                    cost_actual_usd = $3,
                    cost_actual_provenance = 'broker_zero_provision_failed',
                    cost_reconciled_at = $2,
                    error = $4::jsonb,
                    idempotency_key = NULL
                WHERE id = $1
                  AND state IN ('queued', 'running')
                """,
                workload_id,
                completed_at,
                Decimal("0"),
                error,
            )
        else:
            await conn.execute(
                """
                UPDATE pitwall.workloads
                SET state = 'failed',
                    completed_at = $2,
                    error = $3::jsonb
                WHERE id = $1
                  AND state IN ('queued', 'running')
                """,
                workload_id,
                completed_at,
                error,
            )


def _utc_now(value: dt.datetime | None) -> dt.datetime:
    result = value or dt.datetime.now(dt.UTC)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("provider operation time must be timezone-aware")
    return result.astimezone(dt.UTC)


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _has_acquire(pool: Any) -> bool:
    return callable(getattr(pool, "acquire", None))


__all__ = [
    "FALLBACK_LEASE_USD_PER_HOUR",
    "LAUNCH_REQUEST_DIGEST_KEY",
    "ProvisionAdmission",
    "ProvisionReplay",
    "ProvisionReplayConflict",
    "ProvisionReplayFailed",
    "ProvisionReplayInProgress",
    "admit_provision",
    "fallback_lease_rate_per_second",
    "lease_rate_per_second",
    "lease_reservation",
    "load_provision_replay",
    "mark_provision_completed",
    "mark_provision_failed",
]
