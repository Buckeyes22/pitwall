"""Single-lease pod teardown service.

This path is deliberately scoped to one persisted lease. Account-wide emergency
termination belongs to the admin kill switch, not this module.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import logging
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol, cast

from redis.exceptions import RedisError

from pitwall.api.exceptions import (
    LeaseNotFound,
    LeaseStateConflict,
    PitwallApiError,
    PreSpendPayloadRejected,
)
from pitwall.core.enums import LeaseState, ProviderAdapterId
from pitwall.core.models import Lease, Provider
from pitwall.cost.estimator import parse_pricing_model
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    insert_audit,
)
from pitwall.leases.events import (
    StopReason,
    build_lease_stopped_event,
    publish_lease_event,
)
from pitwall.leases.state import (
    ACTIVE_LEASE_STATES,
    TERMINAL_LEASE_STATES,
    transition_lease_state,
)
from pitwall.providers.interface import (
    CredentialReference,
    ProviderOperationContext,
    TeardownRequest,
)
from pitwall.providers.provisioning import fallback_lease_rate_per_second, lease_rate_per_second
from pitwall.providers.registry import get_default_registry
from pitwall.runpod_client.pods import _terminate_pod, terminate_pod
from pitwall.security.pre_spend import PreSpendDecision, get_pre_spend_inspection_service

log = logging.getLogger("pitwall.api.leases.teardown")

LEASE_TERMINATED_CHANNEL = "pitwall:lease:terminated"
LEASE_TERMINATED_EVENT_TYPE = "lease.terminated"
_DEFAULT_TERMINATION_REASON = "operator_stop"
_DEFAULT_EXPIRATION_REASON = "lease_expired"
_TEARDOWN_TERMINAL_STATES = frozenset({LeaseState.STOPPED, LeaseState.EXPIRED})
_USD_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class LeaseTeardownResult:
    """Result returned after a scoped lease teardown attempt."""

    lease: Lease
    event: dict[str, str | None] | None
    published_subscribers: int = 0
    #: Audit or disarm failures after the pod was terminated and the lease closed.
    errors: tuple[str, ...] = ()


class TeardownInProgress(Exception):
    """Another teardown of this lease holds its lock (raised only when not waiting)."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(f"teardown of lease {lease_id} is already in progress")
        self.lease_id = lease_id


class TeardownFailed(PitwallApiError):
    """Raised when the single-lease teardown cannot be completed.

    A provider failure leaves the lease ``stopping``; the lease reconciler retries it.
    """

    status_code = 502
    error_code = "teardown_failed"

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "detail": "provider teardown did not complete; the lease stays stopping and is retried",
        }


class LeaseTeardownRepository(Protocol):
    """Lease repository contract required by scoped teardown."""

    async def get(self, lease_id: str) -> Lease | None: ...

    async def capability_name(self, lease_id: str) -> str | None: ...

    async def update_state(self, lease_id: str, state: str) -> Lease | None: ...

    def teardown_lock(self, lease_id: str, *, wait: bool) -> AbstractAsyncContextManager[bool]: ...

    async def close_teardown(
        self,
        lease_id: str,
        *,
        state: str,
        cost_accrued_usd: Any,
        terminated_at: dt.datetime,
        terminated_reason: str,
        closable_states: tuple[str, ...] = ("stopping",),
    ) -> Lease | None: ...


async def run_teardown(
    lease_id: str,
    *,
    pool: Any,
    redis_client: Any | None = None,
    reason: StopReason | str | None = "operator",
    terminated_reason: str | None = None,
    now: dt.datetime | None = None,
    terminal_state: LeaseState | str = LeaseState.STOPPED,
    api_key: str | None = None,
    rest_api_url: str | None = None,
    wait_for_lock: bool = True,
) -> LeaseTeardownResult:
    """Terminate the pod for one lease, close its cost, and publish its event.

    One teardown runs per lease at a time: the caller waits for a teardown in progress
    (then finds the lease closed), or with ``wait_for_lock=False`` raises
    ``TeardownInProgress`` instead, which the reconciler's retry uses to skip a busy lease.
    """

    lease_id, reason, terminated_reason = _guard_teardown_inputs(
        lease_id=lease_id,
        reason=reason,
        terminated_reason=terminated_reason,
    )
    target_state = _teardown_terminal_state(terminal_state)
    lease_repo: LeaseTeardownRepository = LeaseRepository(pool)
    async with lease_repo.teardown_lock(lease_id, wait=wait_for_lock) as acquired:
        if not acquired:
            raise TeardownInProgress(lease_id)
        existing = await lease_repo.get(lease_id)
        if existing is None:
            raise LeaseNotFound(lease_id)

        if _lease_state(existing) in TERMINAL_LEASE_STATES:
            return LeaseTeardownResult(lease=existing, event=None, published_subscribers=0)

        capability_name = await lease_repo.capability_name(lease_id)
        stopping = await _mark_stopping(lease_repo, existing)
        terminated_at = now or dt.datetime.now(dt.UTC)
        legacy_reason = terminated_reason
        if legacy_reason is None and reason != "operator":
            legacy_reason = reason
        termination_reason = _normalize_reason(legacy_reason, terminal_state=target_state)
        provider = await ProviderRepository(pool).get(stopping.provider_id)
        adapter_rate, observed_rate = await _settlement_rate_inputs(pool, stopping, provider)
        await _terminate_resource(
            stopping,
            provider=provider,
            pool=pool,
            terminated_at=terminated_at,
            termination_reason=termination_reason,
            target_state=target_state,
            overrides=_runpod_overrides(api_key=api_key, rest_api_url=rest_api_url),
        )

        cost_accrued_usd = close_lease_cost(
            stopping,
            provider=provider,
            terminated_at=terminated_at,
            raw_pod_usd_per_hour=observed_rate,
            adapter_rate_per_second=adapter_rate,
        )
        closed_state = transition_lease_state(stopping.state, target_state)
        close_kwargs: dict[str, Any] = {}
        if adapter_rate is not None:
            # A non-RunPod adapter's teardown already moved the lease to the terminal state
            # (without a cost); this teardown holds the lock, so it closes and settles it.
            close_kwargs["closable_states"] = ("stopping", closed_state.value)
        closed = await lease_repo.close_teardown(
            stopping.id,
            state=closed_state.value,
            cost_accrued_usd=cost_accrued_usd,
            terminated_at=terminated_at,
            terminated_reason=termination_reason,
            **close_kwargs,
        )
        if closed is None:  # no longer stopping: another teardown closed it first
            current = await lease_repo.get(stopping.id)
            if current is None or _lease_state(current) not in TERMINAL_LEASE_STATES:
                raise LeaseNotFound(stopping.id)
            return LeaseTeardownResult(lease=current, event=None, published_subscribers=0)

        # The pod is terminated and the lease closed: from here on, failures are
        # reported on the result, never raised, so a completed teardown is not
        # reported as a failed one.
        errors = await _finish_after_close(
            pool,
            closed=closed,
            previous=existing,
            provider=provider,
            automated=reason != "operator",
            termination_reason=termination_reason,
        )
        event = lease_terminated_event(closed)
        subscribers = await publish_lease_terminated(redis_client, event)
        await _publish_stopped_event(
            pool,
            redis_client,
            closed=closed,
            provider=provider,
            capability_name=capability_name,
            stop_reason=_normalize_stop_reason(reason, terminal_state=target_state),
        )
        log.info(
            "lease teardown complete: lease=%s resource=%s cost_usd=%s subscribers=%s",
            closed.id,
            closed.external_resource_id or closed.runpod_pod_id,
            cost_accrued_usd,
            subscribers,
        )
        return LeaseTeardownResult(
            lease=closed,
            event=event,
            published_subscribers=subscribers,
            errors=errors,
        )


def _runpod_overrides(*, api_key: str | None, rest_api_url: str | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if api_key is not None:
        overrides["api_key"] = api_key
    if rest_api_url is not None:
        overrides["rest_api_url"] = rest_api_url
    return overrides


def _lease_is_runpod(lease: Lease) -> bool:
    """A RunPod lease records ``runpod_pod_id``; other providers record only the external id."""
    return lease.runpod_pod_id is not None


async def _terminate_resource(
    stopping: Lease,
    *,
    provider: Provider | None,
    pool: Any,
    terminated_at: dt.datetime,
    termination_reason: str,
    target_state: LeaseState,
    overrides: dict[str, str],
) -> None:
    """Terminate the leased resource through its provider; failures stay typed."""
    pod_id = stopping.external_resource_id or stopping.runpod_pod_id
    if pod_id is None:  # Defensive: the Lease model and DB constraint both reject this state.
        raise TeardownFailed("lease has no external resource identifier")
    runpod = (
        provider.adapter_id == ProviderAdapterId.RUNPOD
        if provider is not None
        else _lease_is_runpod(stopping)
    )
    if provider is None and not runpod:
        raise TeardownFailed(
            f"provider record for lease {stopping.id} is missing and the lease is not a RunPod lease"
        )
    if not runpod and overrides:
        raise TeardownFailed("RunPod-specific teardown overrides require a RunPod provider")
    try:
        if runpod and "api_key" not in overrides:
            # Local import: the launch module imports the provider adapter, which imports this one.
            from pitwall.api.leases.launch import provider_runpod_api_key

            provider_key = provider_runpod_api_key(provider)
            if provider_key is not None:
                overrides = {**overrides, "api_key": provider_key}
        if provider is not None and not runpod:
            adapter = get_default_registry().lookup_compute(provider.adapter_id.value)
            await adapter.teardown(
                TeardownRequest(
                    context=ProviderOperationContext(pool=pool, now=terminated_at),
                    provider_record=provider,
                    credentials=CredentialReference(provider.credential_ref),
                    lease_id=stopping.id,
                    reason=termination_reason,
                    terminal_state=target_state,
                )
            )
        elif overrides:
            await _terminate_pod(pod_id, **overrides)
        else:
            await terminate_pod(pod_id)
    except Exception as exc:  # reason: provider detail may carry credentials; keep it typed
        log.warning("lease %s teardown failed (%s); left stopping", stopping.id, type(exc).__name__)
        raise TeardownFailed(f"provider teardown failed for lease {stopping.id}") from exc


async def _finish_after_close(
    pool: Any,
    *,
    closed: Lease,
    previous: Lease,
    provider: Provider | None,
    automated: bool,
    termination_reason: str,
) -> tuple[str, ...]:
    """Audit the stop and disarm the serve provider; return the errors instead of raising."""
    errors: list[str] = []
    try:
        await insert_audit(
            pool,
            actor="system:lease-controller" if automated else "system:lease",
            action="stop",
            entity_type="lease",
            entity_id=closed.id,
            old_value={"state": _state_value(previous.state)},
            new_value={
                "state": _state_value(closed.state),
                "reason": termination_reason,
                "external_resource_id": (closed.external_resource_id or closed.runpod_pod_id),
            },
            change_reason=(
                f"automated lease stop: {termination_reason}"
                if automated
                else "operator lease stop"
            ),
        )
    except Exception as exc:  # reason: cleanup and lifecycle publication must still complete
        errors.append(f"audit: {type(exc).__name__}")
        log.warning("lease teardown audit insert failed", exc_info=True)
    try:
        await disarm_serve_provider(pool, provider=provider, lease_id=closed.id)
    except Exception as exc:  # reason: lifecycle publication must follow irreversible termination
        errors.append(f"disarm: {type(exc).__name__}")
        log.warning("lease teardown provider disarm failed", exc_info=True)
    return tuple(errors)


async def _publish_stopped_event(
    pool: Any,
    redis_client: Any | None,
    *,
    closed: Lease,
    provider: Provider | None,
    capability_name: str | None,
    stop_reason: StopReason,
) -> None:
    if capability_name is None and provider is not None:
        try:
            capability = await CapabilityRepository(pool).get(provider.capability_id)
        except Exception:  # reason: lifecycle delivery must not roll back completed teardown
            log.warning("lease stopped capability lookup failed", exc_info=True)
            capability = None
        if capability is not None:
            capability_name = capability.name
    if capability_name is None:
        log.warning("lease stopped capability identity unavailable: lease=%s", closed.id)
        return
    await publish_lease_event(
        pool,
        redis_client,
        build_lease_stopped_event(
            lease=closed,
            capability_name=capability_name,
            reason=stop_reason,
        ),
    )


async def lock_provider_for_arming(
    conn: Any, provider_id: str
) -> tuple[dict[str, Any], str] | None:
    """Lock one provider row for the rest of the transaction and return its live state.

    ``SELECT ... FOR UPDATE`` makes a concurrent arm or disarm of the same provider wait,
    so each decides on the row as the other left it, never on an earlier snapshot. ``None``
    means the provider no longer exists.
    """
    row = await conn.fetchrow(
        "SELECT config, health_status FROM pitwall.providers WHERE id = $1 FOR UPDATE",
        provider_id,
    )
    if row is None:
        return None
    config = row["config"]
    if isinstance(config, str):
        config = json.loads(config)
    return dict(config or {}), str(row["health_status"])


async def disarm_serve_provider(
    pool: Any,
    *,
    provider: Provider | None,
    lease_id: str,
) -> bool:
    """Drop the pod facts that route the OpenAI proxy to a lease that just closed.

    Only the provider armed by *this* lease is touched; a provider re-armed by a
    newer lease keeps serving. Marking it ``disarmed`` removes it from the proxy
    chain so requests return 503 until a fresh serve-model arms it again. Disarmed is
    not a failure: the probe's failure counters are left untouched.

    The decision is made on the provider row locked inside the write transaction, not on the
    ``provider`` snapshot the teardown read earlier: a lease armed since then keeps the
    provider, and ``False`` is returned with nothing written or audited.
    """

    if provider is None:
        return False
    config = dict(provider.config)
    acquire = getattr(pool, "acquire", None)
    if not callable(acquire) and config.get("active_lease_id") != lease_id:
        # No transaction to lock in (fake pools): the snapshot is all there is.
        return False
    repo = ProviderRepository(pool)
    if callable(acquire):
        async with acquire() as conn, conn.transaction():
            live = await lock_provider_for_arming(conn, provider.id)
            if live is None:
                return False
            config, old_health = live
            if config.get("active_lease_id") != lease_id:
                return False
            new_config = _without_pod_facts(config)
            await repo.patch(provider.id, config=new_config, health_status="disarmed", conn=conn)
            await insert_audit(
                pool,
                actor="system:lease",
                action="lease_closed",
                entity_type="provider",
                entity_id=provider.id,
                old_value={"config": config, "health_status": old_health},
                new_value={"config": new_config, "health_status": "disarmed"},
                change_reason=f"lease {lease_id} closed",
                conn=conn,
            )
    else:
        new_config = _without_pod_facts(config)
        await repo.patch(provider.id, config=new_config, health_status="disarmed")
        await insert_audit(
            pool,
            actor="system:lease",
            action="lease_closed",
            entity_type="provider",
            entity_id=provider.id,
            old_value={"config": config, "health_status": provider.health_status},
            new_value={"config": new_config, "health_status": "disarmed"},
            change_reason=f"lease {lease_id} closed",
        )
    return True


def _without_pod_facts(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config.items()
        if key not in ("active_pod_id", "active_lease_id")
    }


def close_lease_cost(
    lease: Lease,
    *,
    provider: Provider | None,
    terminated_at: dt.datetime,
    raw_pod_usd_per_hour: Decimal | None = None,
    adapter_rate_per_second: Decimal | None = None,
) -> Decimal:
    """Return the final accrued cost for a lease at teardown time.

    A non-RunPod (Lambda Cloud, Vast) lease settles at ``adapter_rate_per_second``, its
    adapter's rate (``_adapter_rate_per_second``), for the time it ran. A RunPod lease keeps
    the provider's ``per_second_active`` rate, else its tagged pricing's rate, else the
    lease's ``max_usd_per_hour`` (the caller's cap). An uncapped provider-less raw-pod lease
    (``runpod_direct``) is charged at *raw_pod_usd_per_hour*, RunPod's price for the pod
    (``raw_pod_observed_usd_per_hour``), else at the rate its budget admission reserved
    (``raw_pod_reservation_usd_per_hour``). Any other lease with no rate is charged at
    ``FALLBACK_LEASE_USD_PER_HOUR``. The charge is never $0.
    """

    rate = _settlement_rate(
        lease,
        provider,
        adapter_rate_per_second=adapter_rate_per_second,
        raw_pod_usd_per_hour=raw_pod_usd_per_hour,
    )
    elapsed = terminated_at - lease.created_at
    elapsed_seconds = Decimal(str(max(elapsed.total_seconds(), 0.0)))
    return _usd(rate * elapsed_seconds)


#: Provider id of a raw RunPod pod lease created by ``pitwall_runpod_create_pod``.
RAW_POD_PROVIDER_ID = "runpod_direct"


#: An observed hourly price above this is treated as unusable: no GPU pod prices near it, and
#: it keeps a TTL-long charge (at most 168 h) inside the ``NUMERIC(12,6)`` cost column.
_MAX_OBSERVED_USD_PER_HOUR = Decimal("1000")
_RATE_QUANTUM = Decimal("0.0001")


def representable_usd_per_hour(value: object) -> Decimal | None:
    """*value* as a usable hourly price (4 dp, above $0, at most $1000), else ``None``.

    A price RunPod reports that rounds to $0 at 4 dp, is not a finite number, or is
    absurdly large is unusable; the caller then falls back to the reservation rate.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = Decimal(str(value)).quantize(_RATE_QUANTUM)
    except ArithmeticError:
        return None
    if not rate.is_finite() or rate <= 0 or rate > _MAX_OBSERVED_USD_PER_HOUR:
        return None
    return rate


#: The ``costPerHr`` RunPod returned for a pod, as the create's journal ``completed`` row
#: recorded it (``result.resource.cost_per_hour``). ``entity_type``/``entity_id`` use the
#: audit index: a completed pod create's ``entity_id`` is the pod id.
_RAW_POD_RECORDED_PRICE_SQL = """
    SELECT new_value #>> '{result,resource,cost_per_hour}'
    FROM pitwall.config_audit
    WHERE entity_type = 'lease'
      AND entity_id = $1
      AND new_value ->> 'kind' = 'runpod_control_plane_mutation'
      AND new_value ->> 'operation' = 'pod.create'
      AND new_value ->> 'state' = 'completed'
    ORDER BY id DESC
    LIMIT 1
"""


async def raw_pod_observed_usd_per_hour(pool: Any, lease: Lease) -> Decimal | None:
    """RunPod's hourly price for an uncapped raw-pod lease's pod, if Pitwall recorded one.

    The price is the ``costPerHr`` the create's result carried, kept in its journal
    ``completed`` row. ``None`` for a capped lease, a non-raw-pod lease, a pod with no
    recorded or usable price, or an unreadable journal: the settlement then charges the
    reservation rate.
    """
    if (
        lease.provider_id != RAW_POD_PROVIDER_ID
        or lease.max_usd_per_hour is not None
        or not lease.runpod_pod_id
    ):
        return None
    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(_RAW_POD_RECORDED_PRICE_SQL, lease.runpod_pod_id)
    except Exception:  # reason: an unreadable journal falls back to the reservation rate
        log.warning("raw-pod recorded price lookup failed: lease=%s", lease.id)
        return None
    return representable_usd_per_hour(value)


def raw_pod_reservation_usd_per_hour() -> Decimal:
    """The hourly rate the budget gate reserves for a raw pod created without a cap.

    Read from the admission's own estimate (``estimate_raw_pod_lease_cost``) so the
    settlement and the reservation can never disagree.
    """
    from pitwall.api.leases.launch import estimate_raw_pod_lease_cost

    return estimate_raw_pod_lease_cost(60, None)


async def settle_absent_raw_pod_lease(
    pool: Any, redis: Any | None, lease_id: str, *, now: dt.datetime
) -> LeaseTeardownResult:
    """Settle a raw-pod lease whose pod is confirmed absent; raise if the teardown fails.

    The lease is torn down as ``pod_absent``. ``close_teardown`` closes it at
    ``close_lease_cost`` (``rate × (now − created_at)``) and closes its workload at that cost
    in the same transaction, which releases the budget reservation. This module has no
    import-time configuration checks, so the MCP tool can call it without importing the
    reconciler. Callers: the reconciler's lease sweep, after its own absence probe, and
    ``pitwall_runpod_create_pod``, which settles a replayed create's pod it found gone.
    """
    return await run_teardown(
        lease_id,
        pool=pool,
        redis_client=redis,
        reason="operator",
        terminated_reason="pod_absent",
        now=now,
    )


def lease_terminated_event(lease: Lease) -> dict[str, str | None]:
    """Build the Redis pub/sub payload for a terminated lease."""

    state = _state_value(lease.state)
    return {
        "event": LEASE_TERMINATED_EVENT_TYPE,
        "lease_id": lease.id,
        "provider_id": lease.provider_id,
        "external_resource_id": lease.external_resource_id,
        "runpod_pod_id": lease.runpod_pod_id,
        "state": state,
        "terminated_at": (
            lease.terminated_at.isoformat() if lease.terminated_at is not None else None
        ),
        "terminated_reason": lease.terminated_reason,
        "cost_accrued_usd": (
            str(lease.cost_accrued_usd) if lease.cost_accrued_usd is not None else None
        ),
    }


async def publish_lease_terminated(
    redis_client: Any | None,
    event: Mapping[str, str | None],
) -> int:
    """Publish a lease termination event if a Redis client is available."""

    if redis_client is None:
        log.warning(
            "lease termination event not published because redis_client is unavailable: lease=%s",
            event.get("lease_id"),
        )
        return 0

    payload = json.dumps(dict(event), sort_keys=True, separators=(",", ":"))
    try:
        published = redis_client.publish(LEASE_TERMINATED_CHANNEL, payload)
        if inspect.isawaitable(published):
            published = await published
    except RedisError:
        # The pod is terminated and the lease closed before this publish; a Redis
        # outage loses the announcement, not the completed teardown.
        log.warning(
            "lease termination event not published: Redis unavailable: lease=%s",
            event.get("lease_id"),
            exc_info=True,
        )
        return 0
    return int(published) if isinstance(published, int) else 0


async def _mark_stopping(repo: LeaseTeardownRepository, lease: Lease) -> Lease:
    state = _lease_state(lease)
    if state == LeaseState.STOPPING:
        return lease
    if state not in ACTIVE_LEASE_STATES - {LeaseState.STOPPING}:
        raise LeaseStateConflict(lease.id, state.value, "stop")

    stopping = transition_lease_state(state, LeaseState.STOPPING)
    updated = await repo.update_state(lease.id, stopping.value)
    if updated is None:
        raise LeaseNotFound(lease.id)
    return updated


async def settlement_rate_per_second(pool: Any, lease: Lease, provider: Provider | None) -> Decimal:
    """The per-second rate ``close_lease_cost`` settles ``lease`` at; never $0 by default.

    Renewal reserves at this rate, and teardown settles at it, from the same inputs
    (``_settlement_rate_inputs``) and the same decision (``_settlement_rate``).
    """
    adapter_rate, observed_rate = await _settlement_rate_inputs(pool, lease, provider)
    return _settlement_rate(
        lease,
        provider,
        adapter_rate_per_second=adapter_rate,
        raw_pod_usd_per_hour=observed_rate,
    )


async def _settlement_rate_inputs(
    pool: Any, lease: Lease, provider: Provider | None
) -> tuple[Decimal | None, Decimal | None]:
    """What the settlement rate is read from: the adapter rate and RunPod's observed price.

    The adapter rate is set for a Lambda Cloud or Vast lease (``_adapter_rate_per_second``);
    the observed price only for an uncapped provider-less raw-pod lease
    (``raw_pod_observed_usd_per_hour``).
    """
    observed_rate = await raw_pod_observed_usd_per_hour(pool, lease) if provider is None else None
    adapter_rate = await _adapter_rate_per_second(pool, provider)
    return adapter_rate, observed_rate


def _settlement_rate(
    lease: Lease,
    provider: Provider | None,
    *,
    adapter_rate_per_second: Decimal | None,
    raw_pod_usd_per_hour: Decimal | None,
) -> Decimal:
    """The one place a lease's per-second settlement rate is decided.

    In order: the Lambda Cloud/Vast adapter rate; RunPod ``per_second_active``; a RunPod
    provider's tagged pricing (``lease_rate_per_second``, the rate admission reserved); the
    caller's ``max_usd_per_hour`` cap; for an uncapped ``runpod_direct`` lease, RunPod's observed
    price (``representable_usd_per_hour``), else the reservation rate; otherwise the
    ``FALLBACK_LEASE_USD_PER_HOUR`` rate that Lambda Cloud and Vast also fall back to. Never
    ``None``.
    """
    if adapter_rate_per_second is not None:
        return adapter_rate_per_second
    rate = _provider_cost_rate_per_second(provider)
    if rate is None:
        rate = _tagged_cost_rate_per_second(provider)
    if rate is None and lease.max_usd_per_hour is not None:
        rate = lease.max_usd_per_hour / Decimal("3600")
    if rate is None and provider is None and lease.provider_id == RAW_POD_PROVIDER_ID:
        hourly = representable_usd_per_hour(raw_pod_usd_per_hour)
        rate = (hourly or raw_pod_reservation_usd_per_hour()) / Decimal("3600")
    return rate if rate is not None else fallback_lease_rate_per_second()


def _tagged_cost_rate_per_second(provider: Provider | None) -> Decimal | None:
    """The lease rate of a provider's tagged pricing (``kind``/``model``), else ``None``.

    The same rate launch admission reserves (``estimate_lease_launch_cost``). Unparseable
    pricing gives ``None``, so the lease settles at the fallback rate rather than failing a
    teardown whose resource is already terminated.
    """
    if provider is None or not isinstance(provider.config, Mapping):
        return None
    cost = provider.config.get("cost")
    if not isinstance(cost, Mapping) or not ("kind" in cost or "model" in cost):
        return None
    try:
        return lease_rate_per_second(parse_pricing_model(cost))
    except Exception:  # reason: unreadable pricing settles at the fallback, never $0
        log.warning("tagged pricing for provider %s unreadable; using fallback rate", provider.id)
        return None


async def _adapter_rate_per_second(pool: Any, provider: Provider | None) -> Decimal | None:
    """The per-second rate a non-RunPod lease settles at; ``None`` for RunPod leases.

    It is the adapter's own pricing (``pricing_model``) reduced to a lease rate
    (``lease_rate_per_second``). When that cannot be read (the capability or the rate is
    gone), the lease settles at ``FALLBACK_LEASE_USD_PER_HOUR``, never $0.
    """
    if provider is None or provider.adapter_id == ProviderAdapterId.RUNPOD:
        return None
    try:
        capability = await CapabilityRepository(pool).get(provider.capability_id)
        adapter = get_default_registry().lookup_compute(provider.adapter_id.value)
        rate = (
            lease_rate_per_second(adapter.pricing_model(capability, provider))
            if capability is not None
            else None
        )
    except Exception:  # reason: an unreadable rate settles at the fallback, never $0
        log.warning("lease rate for provider %s unreadable; using fallback rate", provider.id)
        rate = None
    return rate if rate is not None else fallback_lease_rate_per_second()


def _provider_cost_rate_per_second(provider: Provider | None) -> Decimal | None:
    if provider is None:
        return None

    config = provider.config if isinstance(provider.config, Mapping) else {}
    cost = config.get("cost")
    sources = [cost, config] if isinstance(cost, Mapping) else [config]
    for source in sources:
        raw_rate = source.get("per_second_active")
        if raw_rate is not None:
            return _non_negative_decimal(raw_rate, "provider cost 'per_second_active'")
    return None


def _non_negative_decimal(raw_value: object, name: str) -> Decimal:
    if isinstance(raw_value, bool):
        raise ValueError(f"{name} must be a decimal value")
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal value") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _usd(value: Decimal) -> Decimal:
    return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)


def _guard_teardown_inputs(
    *,
    lease_id: str,
    reason: StopReason | str | None,
    terminated_reason: str | None,
) -> tuple[str, str | None, str | None]:
    """Inspect operator-visible stop metadata before persistence or egress."""
    if not isinstance(lease_id, str):
        raise ValueError("lease_id must be a string")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string or null")
    if terminated_reason is not None and not isinstance(terminated_reason, str):
        raise ValueError("terminated_reason must be a string or null")

    result = get_pre_spend_inspection_service().inspect(
        {
            "lease_id": lease_id,
            "stop_reason": reason,
            "termination_reason": terminated_reason,
        },
        validate_redacted=_require_teardown_inputs,
    )
    if result.decision == PreSpendDecision.BLOCK:
        raise PreSpendPayloadRejected(
            decision=result.decision.value,
            findings=[finding.to_dict() for finding in result.findings],
        )
    if result.decision == PreSpendDecision.REDACT:
        guarded_lease_id, guarded_reason, guarded_terminated_reason = _require_teardown_inputs(
            result.redacted_payload
        )
        if guarded_lease_id != lease_id:
            raise PreSpendPayloadRejected(
                decision=result.decision.value,
                findings=[finding.to_dict() for finding in result.findings],
            )
        return guarded_lease_id, guarded_reason, guarded_terminated_reason
    return lease_id, reason, terminated_reason


def _require_teardown_inputs(value: object) -> tuple[str, str | None, str | None]:
    if not isinstance(value, dict):
        raise ValueError("teardown inputs must be an object")
    lease_id = value.get("lease_id")
    reason = value.get("stop_reason")
    terminated_reason = value.get("termination_reason")
    if not isinstance(lease_id, str):
        raise ValueError("lease_id must be a string")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("stop_reason must be a string or null")
    if terminated_reason is not None and not isinstance(terminated_reason, str):
        raise ValueError("termination_reason must be a string or null")
    return lease_id, reason, terminated_reason


def _normalize_reason(reason: str | None, *, terminal_state: LeaseState) -> str:
    if reason is None:
        if terminal_state == LeaseState.EXPIRED:
            return _DEFAULT_EXPIRATION_REASON
        return _DEFAULT_TERMINATION_REASON
    normalized = reason.strip()
    if normalized:
        return normalized
    if terminal_state == LeaseState.EXPIRED:
        return _DEFAULT_EXPIRATION_REASON
    return _DEFAULT_TERMINATION_REASON


def _normalize_stop_reason(
    reason: StopReason | str | None,
    *,
    terminal_state: LeaseState,
) -> StopReason:
    allowed = {
        "ttl",
        "idle",
        "operator",
        "kill_switch",
        "budget",
        "max_lifetime",
        "provider_failure",
    }
    if reason in allowed:
        return cast(StopReason, reason)
    if terminal_state is LeaseState.EXPIRED or reason == "lease_expired":
        return "ttl"
    if reason in {"served_model_mismatch", "failover_resume_failed"}:
        return "provider_failure"
    return "operator"


def _lease_state(lease: Lease) -> LeaseState:
    if isinstance(lease.state, LeaseState):
        return lease.state
    return LeaseState(str(lease.state))


def _state_value(state: LeaseState | str) -> str:
    return state.value if isinstance(state, LeaseState) else state


def _teardown_terminal_state(state: LeaseState | str) -> LeaseState:
    coerced = state if isinstance(state, LeaseState) else LeaseState(str(state))
    if coerced not in _TEARDOWN_TERMINAL_STATES:
        allowed = ", ".join(sorted(state.value for state in _TEARDOWN_TERMINAL_STATES))
        raise ValueError(f"teardown terminal_state must be one of: {allowed}")
    return coerced


teardown_lease = run_teardown


__all__ = [
    "LEASE_TERMINATED_CHANNEL",
    "LEASE_TERMINATED_EVENT_TYPE",
    "LeaseTeardownResult",
    "TeardownFailed",
    "TeardownInProgress",
    "RAW_POD_PROVIDER_ID",
    "close_lease_cost",
    "settlement_rate_per_second",
    "disarm_serve_provider",
    "lease_terminated_event",
    "publish_lease_terminated",
    "raw_pod_observed_usd_per_hour",
    "raw_pod_reservation_usd_per_hour",
    "representable_usd_per_hour",
    "run_teardown",
    "settle_absent_raw_pod_lease",
    "teardown_lease",
]
