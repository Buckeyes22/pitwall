"""Helpers shared by the lease-backed compute adapters (Vast.ai and Lambda Cloud).

Both adapters admit a budget, persist a lease row, compensate a failed provision, and
normalize provider payloads the same way; this module is the one place that logic lives.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Lease
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import TaggedPricingModel
from pitwall.db.repository import LeaseRepository
from pitwall.providers.interface import ProvisionRequest
from pitwall.providers.provisioning import (
    ProvisionAdmission,
    admit_provision,
    lease_reservation,
    mark_provision_failed,
)

_HOUR_SECONDS = Decimal(3600)
_TERMINAL_LEASE_STATE_VALUES = (
    LeaseState.STOPPED.value,
    LeaseState.FAILED.value,
    LeaseState.EXPIRED.value,
)


class IntervalGate:
    """Serialize calls so consecutive ones start at least ``interval_s`` apart."""

    def __init__(
        self,
        interval_s: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._interval_s = interval_s
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._lock = asyncio.Lock()
        self._last_started: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            now = self._monotonic()
            if self._last_started is not None:
                remaining = self._interval_s - (now - self._last_started)
                if remaining > 0:
                    await self._sleeper(remaining)
            self._last_started = self._monotonic()


async def compensate_failed_provision(
    *,
    pool: Any,
    now: dt.datetime | None,
    workload_id: str | None,
    external_id: str | None,
    lease_id: str | None,
    dispatch_started: bool,
    delete_external: Callable[[str], Awaitable[None]],
    compensated_reason: str,
    failed_reason: str,
    label: str,
    log: logging.Logger,
) -> None:
    """Undo a failed provision: delete the external resource, close its lease, fail the row.

    Cleanup is best effort and catches ``BaseException`` on purpose: a cancellation must not
    leave a paid resource behind, and the caller re-raises the original failure afterwards.
    """

    cleaned_up = False
    if external_id is not None:
        try:
            await delete_external(external_id)
            cleaned_up = True
            if lease_id is not None:
                await close_lease(
                    pool,
                    lease_id=lease_id,
                    terminal_state=LeaseState.FAILED,
                    reason=compensated_reason,
                    now=now,
                    failed_reason=failed_reason,
                )
        except BaseException:  # cleanup is best effort; preserve the original failure
            log.warning(
                "%s provisioning compensation failed for external resource %s", label, external_id
            )
    await mark_provision_failed(
        pool,
        workload_id=workload_id,
        now=now,
        reservation_released=(not dispatch_started or cleaned_up),
    )


async def close_lease(
    pool: Any,
    *,
    lease_id: str,
    terminal_state: LeaseState | str,
    reason: str | None,
    now: dt.datetime | None,
    failed_reason: str,
) -> int:
    if not _has_acquire(pool):
        return 0
    state = _lease_state_value(terminal_state)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pitwall.leases
            SET state = $1,
                terminated_at = $2,
                terminated_reason = $3
            WHERE id = $4
              AND state <> ALL($5::text[])
            """,
            state,
            _utc_now(now),
            _teardown_reason(reason, state, failed_reason),
            lease_id,
            list(_TERMINAL_LEASE_STATE_VALUES),
        )
    return _rows_affected(result)


def _teardown_reason(reason: str | None, state: str, failed_reason: str) -> str:
    if reason is not None and reason.strip():
        return reason.strip()
    if state == LeaseState.EXPIRED.value:
        return "lease_expired"
    if state == LeaseState.FAILED.value:
        return failed_reason
    return "operator_stop"


async def _admit_budget(
    request: ProvisionRequest,
    pricing: TaggedPricingModel,
) -> ProvisionAdmission | None:
    if request.budget_gate is None:
        return None
    # A lease bills until teardown: reserve its rate for the whole lease TTL.
    reservation = lease_reservation(
        pricing,
        request.capability,
        request.payload,
        ttl_ms=_lease_ttl_ms(request.provider_record),
    )
    return await admit_provision(request, estimate_usd=reservation)


async def _persist_created_lease(
    pool: Any,
    *,
    provider_record: ProviderRecord,
    external_id: str | None,
    now: dt.datetime | None,
    workload_id: str | None = None,
) -> str | None:
    """Record the lease, linked to its admission workload so teardown settles that workload."""
    if external_id is None or not _has_acquire(pool):
        return None
    created_at = _utc_now(now)
    lease_id = _lease_id_for_provider(provider_record.id)
    lease = Lease(
        id=lease_id,
        provider_id=provider_record.id,
        workload_id=workload_id,
        external_resource_id=external_id,
        state=LeaseState.CREATING,
        created_at=created_at,
        expires_at=_expiry_for_lease(provider_record, created_at),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        auto_teardown_on_expiry=True,
    )
    await LeaseRepository(pool).create(lease)
    return lease_id


async def _external_id_for_lease(pool: Any, lease_id: str) -> str | None:
    if not _has_acquire(pool):
        return None
    async with pool.acquire() as conn:
        record = await conn.fetchrow(
            "SELECT external_resource_id, runpod_pod_id FROM pitwall.leases WHERE id = $1",
            lease_id,
        )
    # An asyncpg Record is not a Mapping; read it through its keys.
    row = dict(record.items()) if hasattr(record, "items") else None
    if not isinstance(row, Mapping):
        return None
    return _optional_string(row.get("external_resource_id")) or _optional_string(
        row.get("runpod_pod_id")
    )


def _cost_mapping(provider_record: ProviderRecord) -> Mapping[str, Any]:
    config = _config_mapping(provider_record)
    cost = config.get("cost")
    if isinstance(cost, Mapping):
        return cost
    return config


def _config_mapping(provider_record: ProviderRecord) -> Mapping[str, Any]:
    config = provider_record.config
    return config if isinstance(config, Mapping) else {}


def _response_payload(response: httpx.Response) -> object:
    if not response.content:
        return {}
    loaded: object = json.loads(response.text, parse_float=Decimal)
    return loaded


def _raw_object(payload: object) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        return dict(payload)
    return {"data": payload}


def _json_dumps(value: Mapping[str, Any]) -> str:
    return _json_value(value)


def _json_value(value: object) -> str:
    if isinstance(value, Decimal):
        return format(_finite_decimal(value, "json decimal"), "f")
    if isinstance(value, Mapping):
        items = (
            f"{json.dumps(str(key), ensure_ascii=True)}:{_json_value(item)}"
            for key, item in value.items()
        )
        return "{" + ",".join(items) + "}"
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return "[" + ",".join(_json_value(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _json_safe(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_json_safe(item) for item in value]
    return value


def _hourly_usd_to_per_second(raw_value: object, name: str) -> Decimal:
    return _non_negative_decimal(raw_value, name) / _HOUR_SECONDS


def _non_negative_decimal(raw_value: object, name: str) -> Decimal:
    if isinstance(raw_value, bool):
        raise ValueError(f"{name} must be a decimal value")
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal value") from exc
    return _finite_decimal(value, name)


def _finite_decimal(value: Decimal, name: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _first_present(mapping: Mapping[str, Any], *keys: str) -> object | None:
    for key in keys:
        value: object = mapping.get(key)
        if value is not None:
            return value
    return None


def _mapping_value(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return None


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, Decimal) and value == value.to_integral_value() and value >= 0:
        return int(value)
    return None


def _normalized_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip().lower()
    return ""


def _lease_id_for_provider(provider_id: str) -> str:
    safe_provider_id = provider_id.replace("-", "_")
    return f"lease_{safe_provider_id}_{uuid.uuid4().hex[:12]}"


def _lease_ttl_ms(provider_record: ProviderRecord) -> int:
    config = _config_mapping(provider_record)
    raw_ttl_ms = config.get("lease_ttl_ms", config.get("ttl_ms", 7_200_000))
    return _positive_int(raw_ttl_ms, "lease_ttl_ms")


def _expiry_for_lease(provider_record: ProviderRecord, created_at: dt.datetime) -> dt.datetime:
    return created_at + dt.timedelta(milliseconds=_lease_ttl_ms(provider_record))


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
    else:
        raise ValueError(f"{name} must be a positive integer")
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _utc_now(value: dt.datetime | None) -> dt.datetime:
    observed = dt.datetime.now(dt.UTC) if value is None else value
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("provider operation time must include timezone information")
    return observed.astimezone(dt.UTC)


def _lease_state_value(state: LeaseState | str) -> str:
    value = state.value if isinstance(state, LeaseState) else str(state)
    LeaseState(value)
    return value


def _rows_affected(result: object) -> int:
    if not isinstance(result, str):
        return 0
    parts = result.strip().split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def _has_acquire(pool: Any) -> bool:
    acquire = getattr(pool, "acquire", None)
    return callable(acquire)
