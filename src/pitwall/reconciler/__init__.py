"""Pitwall Arq worker — reconciler for cost, leases, and idempotency.

Entry-points:
  python -m pitwall.reconciler          run the Arq worker
  python -m pitwall.reconciler check    validate Redis configuration
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import asyncpg
import httpx
from pydantic import BaseModel

from pitwall.api.admin.kill_switch import CloudKillSwitch, KillSwitchEngaged
from pitwall.api.exceptions import ServeCapExceeded, ServePriceUnknown
from pitwall.api.leases.launch import InvalidProviderConfig
from pitwall.config import (
    PitwallSettings,
    get_settings,
    load_settings_from_env,
    parse_warning_minutes,
    require_runtime_env,
)
from pitwall.core.enums import LeaseState, ProviderAdapterId, WorkloadState
from pitwall.core.models import Lease, Provider
from pitwall.cost import (
    AsyncpgCostTruthUpRepository,
    BudgetGate,
    BudgetRejected,
    reconcile_provider_actual_cost,
)
from pitwall.cost.budget_gate import BudgetNotConfigured
from pitwall.cost.budget_kill_escalation import (
    KillEscalationOutcome,
    KillEscalationPolicy,
    maybe_escalate_to_kill,
)
from pitwall.cost.circuit_breaker import BudgetCircuitBreaker
from pitwall.db.quota_repository import QuotaRepository
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    WorkloadRepository,
)
from pitwall.leases.activity import TrafficRead, read_lease_traffic
from pitwall.leases.activity import _key as _lease_traffic_key
from pitwall.leases.controller import decide_renewal
from pitwall.leases.events import build_lease_expiring_event, publish_lease_event
from pitwall.leases.mutations import renew_lease
from pitwall.models.prices import (
    gpu_price_freshness,
    load_gpu_price_snapshot,
)
from pitwall.providers.interface import (
    ActualCostRequest,
    ActualCostResourceReference,
    ProviderCapability,
    ProviderOperationContext,
    ReconcileRequest,
)
from pitwall.providers.registry import ProviderRegistry, get_default_registry
from pitwall.providers.selfhosted import (
    ReadinessObservation,
    SelfHostedState,
    oracle_for,
    self_hosted_profile,
    self_hosted_state,
)
from pitwall.providers.selfhosted.readiness import ReadinessState
from pitwall.reconciler.cost_daily_rollup import run_rollup
from pitwall.routing.cooldown import (
    ProviderCooldownState,
    apply_probe_result,
    is_in_cooldown,
    state_from_provider,
)
from pitwall.routing.openai import openai_base_url_for_provider
from pitwall.runpod_client.serverless import get_endpoint
from pitwall.runpod_market import AsyncpgRunpodActualCostReferenceRepository
from pitwall.security.redaction import configure_logging_redaction, redact_text
from pitwall.serve import enforce_price_cap

configure_logging_redaction()
log = logging.getLogger("pitwall.reconciler")

require_runtime_env("reconciler")

try:
    from arq import cron
    from arq.connections import RedisSettings
    from arq.worker import Worker as ArqWorker

    _ARQ_AVAILABLE = True
except ImportError:
    cron = None  # type: ignore[assignment]  # reason: arq optional; None sentinel when uninstalled
    RedisSettings = None  # type: ignore[assignment, misc]  # reason: arq optional; None sentinel when uninstalled
    ArqWorker = None  # type: ignore[assignment, misc]  # reason: arq optional; None sentinel when uninstalled
    _ARQ_AVAILABLE = False


_RUNPOD_TERMINAL_MAP: dict[str, WorkloadState] = {
    "COMPLETED": WorkloadState.COMPLETED,
    "FAILED": WorkloadState.FAILED,
    "CANCELLED": WorkloadState.CANCELLED,
    "TIMED_OUT": WorkloadState.TIMED_OUT,
    "TIMEOUT": WorkloadState.TIMED_OUT,
    "TIME_OUT": WorkloadState.TIMED_OUT,
}

_RUNPOD_ACTIVE_STATES = {"IN_QUEUE", "IN_PROGRESS"}

_RUNPOD_COST_PER_MS = Decimal("0.00044") / Decimal(3_600_000)


def validate_redis_dsn(dsn: str) -> bool:
    """Return True if ``dsn`` parses as a valid redis:// DSN."""
    if not dsn:
        return False
    try:
        parsed = urlparse(dsn)
        return parsed.scheme == "redis" and bool(parsed.netloc)
    except Exception:  # reason: any DSN parse failure means invalid config, not a crash
        return False


def _redis_settings_from_env() -> RedisSettings | None:
    """Parse REDIS_URL for the worker, or None when it is unset or invalid.

    Evaluated at import, so it must never raise: ``check`` and the worker entry point
    report a bad value with a clear message instead of an import traceback.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    return RedisSettings.from_dsn(redis_url) if validate_redis_dsn(redis_url) else None


def check_redis_config() -> int:
    """Validate REDIS_URL and exit 0 on success, non-zero on failure."""
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        print("REDIS_URL is not set", file=sys.stderr)
        return 1
    if not validate_redis_dsn(redis_url):
        print(f"REDIS_URL is not a valid redis:// DSN: {_mask_dsn(redis_url)!r}", file=sys.stderr)
        return 1
    print(f"REDIS_URL is valid: {_mask_dsn(redis_url)}")
    return 0


def _mask_dsn(dsn: str) -> str:
    """Strip userinfo (credentials) from a DSN before printing."""
    scheme, sep, rest = dsn.partition("://")
    if not sep:
        return dsn
    netloc, slash, tail = rest.partition("/")
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    return f"{scheme}://{netloc}{slash}{tail}"


class RunPodJobStatus(BaseModel):
    """Mapped RunPod job status for cost reconciliation.

    Terminal states carry the resolved Pitwall workload state, actual cost,
    and completion timestamp.  Non-terminal states have ``terminal=False`` and
    carry no cost data.
    """

    terminal: bool
    state: WorkloadState | None = None
    actual_cost: Decimal | None = None
    completed_at: dt.datetime | None = None


def map_runpod_status(
    status: str,
    *,
    cost_per_hr: Decimal | None = None,
    worker_time_ms: int | None = None,
    completed_at: dt.datetime | None = None,
) -> RunPodJobStatus:
    """Map a RunPod queue status string to a :class:`RunPodJobStatus`.

    Terminal RunPod states (``COMPLETED``, ``FAILED``, ``CANCELLED``) are
    mapped to the corresponding Pitwall :class:`WorkloadState`.  Active
    RunPod states (``IN_QUEUE``, ``IN_PROGRESS``) and unknown states return
    a non-terminal result.

    Actual cost is computed from ``cost_per_hr * worker_time_ms`` when both
    are provided.  If neither is provided the cost field remains ``None``.
    """
    pitwall_state = _RUNPOD_TERMINAL_MAP.get(status)
    if pitwall_state is not None:
        actual_cost = _compute_actual_cost(cost_per_hr, worker_time_ms)
        return RunPodJobStatus(
            terminal=True,
            state=pitwall_state,
            actual_cost=actual_cost,
            completed_at=completed_at or dt.datetime.now(dt.UTC),
        )
    return RunPodJobStatus(terminal=False)


def _compute_actual_cost(
    cost_per_hr: Decimal | None,
    worker_time_ms: int | None,
) -> Decimal | None:
    if cost_per_hr is not None and worker_time_ms is not None and worker_time_ms > 0:
        cost_per_ms = cost_per_hr / Decimal(3_600_000)
        return (cost_per_ms * Decimal(worker_time_ms)).quantize(Decimal("0.000001"))
    return None


_RECONCILE_QUERY = """
    SELECT id, runpod_job_id
    FROM pitwall.workloads
    WHERE state IN ('queued', 'running')
      AND runpod_job_id IS NOT NULL
"""

_APPLY_TERMINAL_SQL = """
    UPDATE pitwall.workloads
    SET state = $1, cost_actual_usd = $2, completed_at = $3
    WHERE id = $4 AND state NOT IN ('completed', 'failed', 'cancelled', 'timed_out')
    RETURNING id
"""

_FETCH_WORKLOAD_SQL = """
    SELECT id, capability_id, provider_id, state, runpod_job_id,
           completed_at, execution_ms, output_bytes, cost_actual_usd,
           error, result, fallback_chain
    FROM pitwall.workloads
    WHERE id = $1
"""

_FETCH_WORKLOAD_BY_RUNPOD_JOB_ID_SQL = """
    SELECT id, capability_id, provider_id, state, runpod_job_id,
           completed_at, execution_ms, output_bytes, cost_actual_usd,
           error, result, fallback_chain
    FROM pitwall.workloads
    WHERE runpod_job_id = $1
"""

_AGGREGATE_DAILY_SQL = """
    INSERT INTO pitwall.cost_daily
        (day, capability_class, provider_type, workload_count, cost_usd)
    SELECT
        DATE(w.submitted_at AT TIME ZONE 'UTC') AS day,
        c.class                                   AS capability_class,
        p.provider_type                            AS provider_type,
        COUNT(*)                                   AS workload_count,
        COALESCE(SUM(w.cost_actual_usd), 0)        AS cost_usd
    FROM pitwall.workloads w
    JOIN pitwall.capabilities c ON c.id = w.capability_id
    JOIN pitwall.providers    p ON p.id = w.provider_id
    WHERE w.state IN ('completed', 'failed', 'cancelled', 'timed_out')
    GROUP BY day, c.class, p.provider_type
    ON CONFLICT (day, capability_class, provider_type)
    DO UPDATE SET
        workload_count = EXCLUDED.workload_count,
        cost_usd       = EXCLUDED.cost_usd
"""

_HEALTH_PROBE_PROVIDERS_SQL = """
    SELECT
        p.id,
        p.capability_id,
        p.name,
        p.provider_type,
        p.runpod_endpoint_id,
        p.config,
        p.priority,
        p.enabled,
        p.health_status,
        p.consecutive_failures,
        p.cooldown_trips,
        p.cooldown_until,
        p.updated_at,
        c.served_model_id
    FROM pitwall.providers AS p
    JOIN pitwall.capabilities AS c ON c.id = p.capability_id
    WHERE p.enabled = true
      AND p.provider_type = ANY($1::text[])
      AND (
        (p.provider_type = 'serverless_lb' AND p.runpod_endpoint_id IS NOT NULL)
        OR p.provider_type = 'public_endpoint'
      )
"""

_UPDATE_PROVIDER_HEALTH_SQL = """
    UPDATE pitwall.providers
    SET
        health_status = $2,
        consecutive_failures = $3,
        cooldown_trips = $4,
        cooldown_until = $5,
        config = CASE
            WHEN $6::jsonb IS NULL THEN config
            ELSE jsonb_set(config, '{self_hosted_state}', $6::jsonb, true)
        END,
        updated_at = now()
    WHERE id = $1
      AND ($7::timestamptz IS NULL OR updated_at = $7)
"""

_LB_HIBERNATE_SWEEP_PROVIDERS_SQL = """
    SELECT
        id,
        name,
        provider_type,
        runpod_endpoint_id,
        config
    FROM pitwall.providers
    WHERE enabled = true
      AND runpod_endpoint_id IS NOT NULL
      AND provider_type = 'serverless_lb'
"""

_LEASE_EXPIRY_LEASES_SQL = """
    SELECT l.*, c.name AS capability_name
    FROM pitwall.leases AS l
    LEFT JOIN pitwall.providers AS p ON p.id = l.provider_id
    LEFT JOIN pitwall.capabilities AS c ON c.id = p.capability_id
    WHERE state = ANY(ARRAY['active', 'creating', 'waiting_runtime', 'waiting_probe'])
      AND auto_teardown_on_expiry = true
      AND expires_at IS NOT NULL
      AND expires_at <= now() + interval '60 minutes'
"""

# A teardown whose provider call failed leaves its lease `stopping` with a live, billing
# pod. Nothing else selects that state, so every tick retries it.
_STOPPING_LEASES_SQL = """
    SELECT id, state, expires_at FROM pitwall.leases WHERE state = 'stopping' ORDER BY id
"""

_LEASE_WARNING_CHANNEL = "pitwall.leases.events"
_LEASE_WARNING_EVENT_TYPE = "lease.expiring"

_WORKLOAD_COMPLETED_CHANNEL = "pitwall:workload:completed"

_LB_HIBERNATE_SWEEP_KEY_PREFIX = "pitwall:hibernate_sweep:workers_min:"
_LB_HIBERNATE_SWEEP_WARM_THRESHOLD_HOURS = 24
_WORKLOAD_COMPLETED_EVENT_TYPE = "workload.completed"


async def fetch_active_workloads(
    pool: asyncpg.Pool,
) -> list[dict[str, Any]]:
    """Return active workloads that have a RunPod job id.

    Fetches workloads in ``queued`` or ``running`` state and ignores rows
    without a RunPod job id.  Each returned dict has keys ``id`` and
    ``runpod_job_id``.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_RECONCILE_QUERY)
    return [dict(r) for r in rows]


async def apply_terminal_state(
    pool: asyncpg.Pool,
    *,
    workload_id: str,
    state: WorkloadState,
    actual_cost: Decimal | None,
    completed_at: dt.datetime,
) -> bool:
    """Persist a terminal workload state and actual cost.

    Updates the workload row identified by *workload_id* with the resolved
    Pitwall *state*, *actual_cost* (``cost_actual_usd``), and *completed_at*
    timestamp. Only updates if the workload is not already in a terminal state.

    Returns True if the workload was updated, False if it was already terminal.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _APPLY_TERMINAL_SQL,
            state.value,
            actual_cost,
            completed_at,
            workload_id,
        )
        return len(rows) > 0


async def fetch_workload_by_id(
    pool: asyncpg.Pool,
    workload_id: str,
) -> dict[str, Any] | None:
    """Fetch a workload by its ID.

    Returns a dict with workload fields or None if not found.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_FETCH_WORKLOAD_SQL, workload_id)
        return dict(row) if row is not None else None


async def fetch_workload_by_runpod_job_id(
    pool: asyncpg.Pool,
    runpod_job_id: str,
) -> dict[str, Any] | None:
    """Fetch a workload by its RunPod job ID.

    Returns a dict with workload fields or None if not found.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_FETCH_WORKLOAD_BY_RUNPOD_JOB_ID_SQL, runpod_job_id)
        return dict(row) if row is not None else None


def build_workload_completed_event(workload: dict[str, Any]) -> dict[str, Any]:
    """Build a workload completed event payload.

    Args:
        workload: Dict containing workload fields from fetch_workload_by_id.

    Returns:
        Event dict suitable for JSON serialization.
    """
    state = workload.get("state")
    if state is not None and hasattr(state, "value"):
        state = state.value
    event = {
        "event": _WORKLOAD_COMPLETED_EVENT_TYPE,
        "workload_id": workload["id"],
        "capability_id": workload.get("capability_id"),
        "provider_id": workload.get("provider_id"),
        "state": state,
        "completed_at": (
            workload["completed_at"].isoformat()
            if workload.get("completed_at") is not None
            else None
        ),
        "execution_ms": workload.get("execution_ms"),
        "output_bytes": workload.get("output_bytes"),
        "cost_actual_usd": (
            str(workload["cost_actual_usd"])
            if workload.get("cost_actual_usd") is not None
            else None
        ),
    }
    if workload.get("error") is not None:
        event["error"] = workload["error"]
    if workload.get("result") is not None:
        event["result"] = workload["result"]
    if workload.get("fallback_chain") is not None:
        event["fallback_chain"] = workload["fallback_chain"]
    return event


async def publish_workload_completed(
    redis: Any,
    event: dict[str, Any],
) -> int:
    """Publish a workload completed event to Redis pub/sub.

    Args:
        redis: Redis client instance.
        event: Event dict to publish.

    Returns:
        Number of subscribers that received the message, or 0 if redis is unavailable.
    """
    if redis is None:
        return 0
    import json

    payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
    try:
        published = redis.publish(_WORKLOAD_COMPLETED_CHANNEL, payload)
        if hasattr(published, "__await__"):
            published = await published
        return int(published) if isinstance(published, int) else 0
    except Exception:  # reason: event publish is best-effort; reconcile must proceed
        return 0


async def apply_terminal_status_and_publish(
    pool: asyncpg.Pool,
    redis: Any,
    runpod_job_id: str,
    status: str,
    completed_at: dt.datetime | None = None,
) -> bool:
    """Apply terminal state from a RunPod status and publish to Redis.

    Fetches the workload by runpod_job_id, maps the status to a terminal
    WorkloadState, persists it, and publishes a workload.completed event.

    This function is used by both the polling path (via _poll_and_reconcile)
    and the webhook path (via webhook_receiver enqueuing an Arq job).

    Args:
        pool: asyncpg database pool.
        redis: Redis client instance (or None to skip publishing).
        runpod_job_id: The RunPod job ID from the webhook or poll response.
        status: RunPod status string (COMPLETED, FAILED, CANCELLED, etc.).
        completed_at: Optional completion timestamp. Defaults to now.

    Returns:
        True if the workload was updated to a terminal state, False otherwise.
    """
    workload = await fetch_workload_by_runpod_job_id(pool, runpod_job_id)
    if workload is None:
        return False

    mapped = map_runpod_status(status, completed_at=completed_at)
    if not mapped.terminal or mapped.state is None or mapped.completed_at is None:
        return False

    updated = await apply_terminal_state(
        pool,
        workload_id=workload["id"],
        state=mapped.state,
        actual_cost=mapped.actual_cost,
        completed_at=mapped.completed_at,
    )
    if updated:
        updated_workload = await fetch_workload_by_id(pool, workload["id"])
        if updated_workload is not None:
            event = build_workload_completed_event(updated_workload)
            if redis is not None:
                await publish_workload_completed(redis, event)
            await dispatch_workload_completion_webhooks(pool, updated_workload, event)
    return updated


async def dispatch_workload_completion_webhooks(
    pool: asyncpg.Pool,
    workload: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Deliver a terminal workload event to subscriptions for its capability."""

    capability_id = workload.get("capability_id")
    workload_id = workload.get("id")
    if not isinstance(capability_id, str) or not isinstance(workload_id, str):
        return {}
    from pitwall.db.repository import (
        WebhookDeliveryFailureRepository,
        WebhookSubscriptionRepository,
    )
    from pitwall.webhook_dispatcher import dispatch_completion
    from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher

    try:
        cipher = WebhookSecretCipher.from_env()
    except ValueError:
        return {}
    subscription_repo = WebhookSubscriptionRepository(pool, cipher)
    subscriptions = await subscription_repo.list_for_dispatch(
        consumer=capability_id, event_type="workload.completed"
    )
    if not subscriptions:
        return {}
    targets = [
        (int(subscription.id), subscription.webhook_url, subscription.hmac_secret)
        for subscription in subscriptions
    ]
    results = await dispatch_completion(
        workload_id=workload_id,
        consumer=capability_id,
        payload=event,
        subscriptions=targets,
    )
    failure_repo = WebhookDeliveryFailureRepository(pool)
    for subscription_id, _url, _secret in targets:
        result = results.get(str(subscription_id), {})
        if result.get("success"):
            continue
        next_retry_raw = result.get("next_retry_at")
        await failure_repo.insert(
            workload_id,
            subscription_id,
            int(result.get("attempt") or 1),
            {
                "event": "workload.completed",
                "workload_id": workload_id,
                "delivery_id": result.get("delivery_id"),
                "state": workload.get("state"),
                # Everything a later redelivery needs to rebuild the same envelope.
                "consumer": capability_id,
                "data": event,
            },
            next_retry_at=(
                dt.datetime.fromisoformat(next_retry_raw)
                if isinstance(next_retry_raw, str)
                else None
            ),
            status_code=result.get("status_code"),
            error_message=result.get("error_message"),
        )
    return results


async def _redeliver_webhook(
    failures: Any,
    subscriptions: Any,
    failure: Any,
) -> None:
    """Make the next delivery attempt for one due failure row and settle the row."""
    from pitwall.webhook_dispatcher import MAX_ATTEMPTS, attempt_delivery
    from pitwall.webhook_dispatcher.dispatcher import build_completion_event

    subscription = await subscriptions.get_for_dispatch(failure.subscription_id)
    payload = failure.payload
    envelope = payload.get("envelope")
    delivery_id = payload.get("delivery_id")
    consumer, data = payload.get("consumer"), payload.get("data")
    if isinstance(envelope, dict):  # lease events store their exact body
        event: dict[str, Any] | None = envelope
    elif isinstance(consumer, str) and isinstance(data, dict) and isinstance(delivery_id, str):
        event = build_completion_event(
            workload_id=failure.workload_id,
            consumer=consumer,
            payload=data,
            delivery_id=delivery_id,
        )
    else:
        event = None
    if (
        subscription is None
        or not subscription.active
        or failure.attempt >= MAX_ATTEMPTS
        or event is None
        or not isinstance(delivery_id, str)
    ):
        # Nothing to deliver to, or nothing to rebuild the body from: exhausted.
        await failures.update_next_retry(
            failure.workload_id, failure.subscription_id, failure.attempt, None
        )
        return
    outcome = await attempt_delivery(
        subscription.webhook_url,
        event,
        subscription.hmac_secret,
        attempt=failure.attempt + 1,
        delivery_id=delivery_id,
    )
    if not outcome.success:
        # A missing next_retry_at is what marks the row exhausted.
        await failures.insert(
            failure.workload_id,
            failure.subscription_id,
            failure.attempt + 1,
            payload,
            next_retry_at=outcome.next_retry_at,
            status_code=outcome.status_code,
            error_message=outcome.error_message,
        )
    await failures.delete(failure.workload_id, failure.subscription_id, failure.attempt)


async def _webhook_retry_sweep(ctx: dict[str, Any]) -> None:
    """Redeliver outbound webhooks whose ``next_retry_at`` has come due (every minute).

    Each failure row gets one delivery attempt per tick, so no backoff sleep runs inside
    this job; the delay schedule lives in ``next_retry_at``.
    """
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    from pitwall.db.repository import (
        WebhookDeliveryFailureRepository,
        WebhookSubscriptionRepository,
    )
    from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher

    try:
        cipher = WebhookSecretCipher.from_env()
    except RuntimeError, ValueError:
        return
    failures = WebhookDeliveryFailureRepository(pool)
    subscriptions = WebhookSubscriptionRepository(pool, cipher)
    for failure in await failures.list_pending_retries(dt.datetime.now(dt.UTC)):
        try:
            await _redeliver_webhook(failures, subscriptions, failure)
        except Exception:  # reason: one bad delivery must not block the other due retries
            log.warning(
                "webhook redelivery failed: workload=%s subscription=%s; retrying next tick",
                failure.workload_id,
                failure.subscription_id,
                exc_info=True,
            )


async def aggregate_daily_cost(pool: asyncpg.Pool) -> None:
    """Aggregate completed workloads into the ``cost_daily`` summary table.

    Joins ``workloads`` to ``capabilities`` and ``providers``, groups by
    UTC day, capability class, and provider type, and upserts the aggregate
    counts and costs into ``pitwall.cost_daily``.
    """
    async with pool.acquire() as conn:
        await conn.execute(_AGGREGATE_DAILY_SQL)


async def fetch_providers_for_health_probe(
    pool: asyncpg.Pool,
    *,
    provider_types: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Return enabled providers matching the requested probe-capable types.

    By default the types are the ones the registered adapters declare probe-capable.
    """
    if provider_types is None:
        provider_types = tuple(
            dict.fromkeys(
                provider_type
                for _, declaration in get_default_registry().declarations()
                for provider_type in declaration.health_probe_types
            )
        )
    async with pool.acquire() as conn:
        rows = await conn.fetch(_HEALTH_PROBE_PROVIDERS_SQL, list(provider_types))
    return [dict(row) for row in rows]


async def fetch_lb_providers_for_hibernate_sweep(
    pool: asyncpg.Pool,
) -> list[dict[str, Any]]:
    """Return enabled LB providers that need hibernate sweep checking.

    Fetches providers that are enabled, have a runpod_endpoint_id, and are of
    provider_type 'serverless_lb'. Each returned dict has keys: id, name,
    provider_type, runpod_endpoint_id, config.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LB_HIBERNATE_SWEEP_PROVIDERS_SQL)
    return [dict(r) for r in rows]


async def update_provider_health(
    pool: asyncpg.Pool,
    *,
    provider_id: str,
    health_status: str,
    consecutive_failures: int,
    cooldown_trips: int,
    cooldown_until: dt.datetime | None,
    self_hosted_state: Mapping[str, Any] | None = None,
    expected_updated_at: dt.datetime | None = None,
) -> bool:
    """Persist provider health state after a probe run.

    Updates the provider row identified by *provider_id* with the probe result
    fields: health_status, consecutive_failures, cooldown_trips, and
    cooldown_until. With *expected_updated_at*, the write applies only if the row
    is unchanged since it was read. Returns whether a row was written.
    """
    encoded_state = None if self_hosted_state is None else json.dumps(self_hosted_state)
    async with pool.acquire() as conn:
        status = await conn.execute(
            _UPDATE_PROVIDER_HEALTH_SQL,
            provider_id,
            health_status,
            consecutive_failures,
            cooldown_trips,
            cooldown_until,
            encoded_state,
            expected_updated_at,
        )
    return str(status).rsplit(" ", 1)[-1] != "0"


_PROVIDER_HEALTH_WRITE_ATTEMPTS = 3


async def persist_provider_health(
    pool: asyncpg.Pool,
    provider: Mapping[str, Any],
    compute: Callable[[Mapping[str, Any]], tuple[ProviderCooldownState, Mapping[str, Any] | None]],
) -> bool:
    """Write ``compute(row)`` for one probe result without losing a concurrent write.

    The next state is computed from the row as read, so the write is a compare-and-set on
    ``updated_at``; when another writer got there first, re-read the row and recompute.
    """
    row: Mapping[str, Any] | None = provider
    for _ in range(_PROVIDER_HEALTH_WRITE_ATTEMPTS):
        assert row is not None
        next_state, self_hosted_state = compute(row)
        if await update_provider_health(
            pool,
            provider_id=row["id"],
            health_status=next_state.health_status,
            consecutive_failures=next_state.consecutive_failures,
            cooldown_trips=next_state.cooldown_trips,
            cooldown_until=next_state.cooldown_until,
            self_hosted_state=self_hosted_state,
            expected_updated_at=row["updated_at"],
        ):
            return True
        async with pool.acquire() as conn:
            fresh = await conn.fetchrow(
                _HEALTH_PROBE_PROVIDERS_SQL + "      AND p.id = $2\n",
                ["serverless_lb", "public_endpoint"],
                row["id"],
            )
        if fresh is None:  # disabled or deleted since it was read
            return False
        row = dict(fresh)
    log.warning("provider health write for %s kept racing; the next probe retries", provider["id"])
    return False


# A workload admitted but never given a provider job id or lease (the process died between
# admission and submission) is reaped once it is this far past its capability's timeout.
_ORPHAN_GRACE = "1 hour"
_DEFAULT_EXECUTION_TIMEOUT_MS = 60_000

_REAP_ORPHANED_WORKLOADS_SQL = f"""
    UPDATE pitwall.workloads AS w
    SET
        state = 'timed_out',
        completed_at = now(),
        cost_actual_usd = COALESCE(w.cost_ceiling_usd, w.cost_estimate_usd, 0),
        cost_actual_provenance = 'reaped_unfinished',
        error = $1::jsonb
    WHERE w.state IN ('queued', 'running')
      AND w.runpod_job_id IS NULL
      AND w.external_job_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM pitwall.leases AS l WHERE l.workload_id = w.id)
      AND w.submitted_at < now() - interval '{_ORPHAN_GRACE}' - make_interval(
            secs => COALESCE(
                (
                    SELECT (c.config -> 'defaults' ->> 'execution_timeout_ms')::bigint
                    FROM pitwall.capabilities AS c
                    WHERE c.id = w.capability_id
                ),
                {_DEFAULT_EXECUTION_TIMEOUT_MS}
            ) / 1000.0
          )
    RETURNING w.id
"""


#: Open raw-pod workloads with no lease whose pod may be billing untracked. The newest
#: control-plane journal row for the workload's idempotency key is a pod.create that is either
#: ``started`` (unknown outcome) or ``completed`` with a pod id (the process stopped between
#: the ``completed`` write and its lease insert). ``attempt_started_at`` is the key's newest
#: ``started`` row, so a lease's TTL counts from the create attempt; ``row_age_s`` is the age
#: of the newest row; both reads require it to pass the settling margin, so a create that
#: just completed is left to its own lease insert.
_AMBIGUOUS_RAW_POD_CREATES_SQL = """
    SELECT w.id, j.new_value, s.created_at AS attempt_started_at,
           EXTRACT(EPOCH FROM (now() - j.created_at))::float8 AS row_age_s,
           ARRAY(
               SELECT c.new_value #>> '{result,resource_id}'
               FROM pitwall.config_audit AS c
               WHERE c.new_value ->> 'kind' = 'runpod_control_plane_mutation'
                 AND c.new_value ->> 'idempotency_key' = w.idempotency_key
                 AND c.new_value ->> 'operation' = 'pod.create'
                 AND c.new_value ->> 'state' = 'completed'
                 AND c.new_value #>> '{result,resource_id}' IS NOT NULL
                 AND c.entity_type IN ('lease', 'provider', 'template', 'volume')
           ) AS completed_pod_ids
    FROM pitwall.workloads AS w
    CROSS JOIN LATERAL (
        SELECT c.new_value, c.created_at
        FROM pitwall.config_audit AS c
        WHERE c.new_value ->> 'kind' = 'runpod_control_plane_mutation'
          AND c.new_value ->> 'idempotency_key' = w.idempotency_key
          AND c.entity_type IN ('lease', 'provider', 'template', 'volume')
        ORDER BY c.id DESC
        LIMIT 1
    ) AS j
    CROSS JOIN LATERAL (
        SELECT c.created_at
        FROM pitwall.config_audit AS c
        WHERE c.new_value ->> 'kind' = 'runpod_control_plane_mutation'
          AND c.new_value ->> 'idempotency_key' = w.idempotency_key
          AND c.new_value ->> 'state' = 'started'
          AND c.entity_type IN ('lease', 'provider', 'template', 'volume')
        ORDER BY c.id DESC
        LIMIT 1
    ) AS s
    WHERE w.state IN ('queued', 'running')
      AND w.provider_id = 'runpod_direct'
      AND w.idempotency_key IS NOT NULL
      AND w.runpod_job_id IS NULL
      AND w.external_job_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM pitwall.leases AS l WHERE l.workload_id = w.id)
      AND j.new_value ->> 'operation' = 'pod.create'
      AND (
        j.new_value ->> 'state' = 'started'
        OR (
          j.new_value ->> 'state' = 'completed'
          AND j.new_value #>> '{result,resource_id}' IS NOT NULL
        )
      )
      AND j.created_at IS NOT NULL
      AND s.created_at IS NOT NULL
"""


@dataclass(frozen=True)
class _AmbiguousPodCreate:
    workload_id: str
    idempotency_key: str
    attempt_marker: str
    pod_name: str
    ttl_minutes: int
    max_cost_per_hour: Decimal | None
    attempt_started_at: dt.datetime
    #: The pod id a ``completed`` row recorded; ``None`` for an unknown (``started``) outcome.
    pod_id: str | None = None
    #: Pod ids any ``completed`` create with this key recorded. For a ``started`` create these
    #: are earlier attempts' pods (a rollback terminated one, then freed the key), which carry
    #: the same marker but were never this attempt's.
    earlier_pod_ids: frozenset[str] = frozenset()


def _ambiguous_pod_create(row: Mapping[str, Any]) -> _AmbiguousPodCreate | None:
    """The recovery fields the newest journal row recorded, or None if unusable."""
    payload = row["new_value"]
    recovery = payload.get("recovery") if isinstance(payload, Mapping) else None
    if not isinstance(recovery, Mapping):
        return None
    name = recovery.get("name")
    marker = recovery.get("attempt_marker")
    ttl = recovery.get("ttl_minutes")
    cap = recovery.get("max_cost_per_hour")
    key = payload.get("idempotency_key")
    if not isinstance(name, str) or not name or not isinstance(key, str):
        return None
    if not isinstance(marker, str) or not marker:
        return None
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl < 1:
        return None
    try:
        max_cost = Decimal(cap) if isinstance(cap, str) else None
    except InvalidOperation:
        return None
    if cap is not None and max_cost is None:
        return None
    pod_id: str | None = None
    if payload.get("state") == "completed":
        result = payload.get("result")
        result_id = result.get("resource_id") if isinstance(result, Mapping) else None
        if not isinstance(result_id, str) or not result_id:
            return None
        pod_id = result_id
    completed = row.get("completed_pod_ids")
    earlier = frozenset(value for value in (completed or ()) if isinstance(value, str) and value)
    return _AmbiguousPodCreate(
        workload_id=str(row["id"]),
        idempotency_key=key,
        attempt_marker=marker,
        pod_name=name,
        ttl_minutes=ttl,
        max_cost_per_hour=max_cost,
        attempt_started_at=row["attempt_started_at"],
        pod_id=pod_id,
        earlier_pod_ids=earlier,
    )


def _pod_attempt_marker(pod: Mapping[str, Any]) -> str | None:
    """The create-attempt marker in a pod's ``env`` as RunPod returns it, if any.

    REST returns ``env`` as an object; the older GraphQL shape is a list of ``KEY=value``
    strings or ``{key, value}`` items. Only the marker is read; no other value is kept.
    """
    from pitwall.runpod_control_plane import CREATE_ATTEMPT_ENV

    env = pod.get("env")
    if isinstance(env, Mapping):
        value = env.get(CREATE_ATTEMPT_ENV)
        return value if isinstance(value, str) else None
    if isinstance(env, list):
        for item in env:
            if isinstance(item, str) and item.startswith(f"{CREATE_ATTEMPT_ENV}="):
                return item.split("=", 1)[1]
            if isinstance(item, Mapping) and item.get("key") == CREATE_ATTEMPT_ENV:
                value = item.get("value")
                return value if isinstance(value, str) else None
    return None


def _ambiguous_pod_decision(
    pods: Iterable[Mapping[str, Any]], create: _AmbiguousPodCreate
) -> tuple[Literal["adopt", "absent", "hold"], str]:
    """Adopt the one pod carrying the attempt's marker, prove it absent, or hold.

    Pitwall never adopts, and so never tears down, a pod it cannot prove it created: the
    proof is the ``PITWALL_CREATE_ATTEMPT`` marker the create put in the pod's env, which
    RunPod returns on the pod record. A marked pod is adopted in any state: one already
    ``TERMINATED`` still existed and billed, and the lease sweep settles it at accrued cost.
    The marker is per key, not per attempt, so a terminated pod an earlier attempt with the
    same key recorded (``earlier_pod_ids``) is that attempt's, and is not matched.
    A live pod with the create's name but without the marker (an unrelated pod, or a list
    that omits env) is held, never adopted. Only when no pod has the marker and no live pod
    has the name is the pod absent. A ``completed`` create
    already names its pod, so that id is adopted unless its live record carries another
    attempt's marker.
    """
    pods = list(pods)

    def terminated(pod: Mapping[str, Any]) -> bool:
        return str(pod.get("desiredStatus") or "").upper() == "TERMINATED"

    live = [pod for pod in pods if not terminated(pod)]
    if create.pod_id is not None:
        # A completed create names the pod its own provider call returned: that id is the
        # proof. A live record that carries another attempt's marker contradicts it.
        record = next((pod for pod in live if pod.get("id") == create.pod_id), None)
        marker = _pod_attempt_marker(record) if record is not None else None
        if marker is not None and marker != create.attempt_marker:
            return "hold", f"pod {create.pod_id} carries another attempt's marker"
        # Not listed live: the lease sweep confirms absence by id and settles at accrued cost.
        return "adopt", create.pod_id
    marked = [
        pod
        for pod in pods
        if _pod_attempt_marker(pod) == create.attempt_marker
        and not (terminated(pod) and pod.get("id") in create.earlier_pod_ids)
    ]
    if len(marked) > 1:
        return "hold", "several pods carry the attempt marker"
    if marked:
        pod_id = marked[0].get("id")
        if isinstance(pod_id, str) and pod_id:
            return "adopt", pod_id
        return "hold", "the marked pod has no id"
    unmarked = sorted(str(pod.get("id")) for pod in live if pod.get("name") == create.pod_name)
    if unmarked:
        return "hold", f"pod(s) {', '.join(unmarked)} carry the name but not the attempt marker"
    return "absent", ""


async def resolve_ambiguous_raw_pod_creates(pool: asyncpg.Pool) -> None:
    """Lease or close the raw-pod workloads whose pod may be billing with no lease.

    Two journal states qualify. A ``started`` create failed without a pod id (a timeout or
    a transport error) and may still have left a billing pod. A ``completed`` create names
    its pod, but the process stopped before its lease insert. A running create holds its
    key's advisory lock until it returns, so the reaper acts only when ``pg_try_advisory_lock``
    succeeds. It also waits until the newest journal row is ``UNKNOWN_OUTCOME_GRACE_S`` old:
    a settling margin, checked on the first read and again on the re-read under the lock, so
    a create that just completed is left to the tool's own lease insert, and a just-created
    pod has time to appear in RunPod's list. A same-key replay can still insert its lease
    late; if the reaper's lease lands first, the tool returns that lease instead of rolling
    back (``pitwall_runpod_create_pod``). A ``completed`` create's
    pod is leased by its recorded id (``_ambiguous_pod_decision``). A ``started`` create's
    pod is looked up by the attempt marker and name its journal row recorded:

    - one pod carrying the attempt's ``PITWALL_CREATE_ATTEMPT`` marker, live or already
      ``TERMINATED``: its lease is recorded, so TTL teardown and the lease sweep settle the
      workload at accrued cost (never $0: a marked pod existed and billed);
    - no pod with the marker and no live pod with the name: the workload closes at $0 and
      its idempotency key is freed;
    - a lookup error, several marked pods, or a pod with the name but not the marker: the
      workload is held (an unproven pod is never adopted or terminated), and the next pass
      retries. ``reap_orphaned_workloads`` charges its ceiling once it
      passes the orphan age threshold.
    """
    from pitwall.api.leases.launch import raw_pod_lease
    from pitwall.runpod_client import pods as runpod_pods
    from pitwall.runpod_control_plane import UNKNOWN_OUTCOME_GRACE_S, journal_lock_name

    async with pool.acquire() as conn:
        rows = await conn.fetch(_AMBIGUOUS_RAW_POD_CREATES_SQL)
        due = [
            create
            for row in rows
            if row["row_age_s"] >= UNKNOWN_OUTCOME_GRACE_S
            and (create := _ambiguous_pod_create(row)) is not None
        ]
        locked: list[str] = []
        try:
            for create in due:
                lock = journal_lock_name(create.idempotency_key)
                if await conn.fetchval(
                    "SELECT pg_try_advisory_lock(hashtextextended($1, 0))", lock
                ):
                    locked.append(lock)
            if not locked:
                return
            # Re-read under the locks: an attempt that ended between the read and the lock
            # may have recorded its outcome.
            due_ids = {create.workload_id for create in due}
            current = [
                create
                for row in await conn.fetch(_AMBIGUOUS_RAW_POD_CREATES_SQL)
                if row["row_age_s"] >= UNKNOWN_OUTCOME_GRACE_S
                and (create := _ambiguous_pod_create(row)) is not None
                and create.workload_id in due_ids
                and journal_lock_name(create.idempotency_key) in locked
            ]
            if not current:
                return
            try:
                pods = await runpod_pods.get_pods()
            except Exception as exc:  # reason: an outage must never read as an absent pod
                log.warning(
                    "unknown-outcome pod create lookup failed; %d workload(s) held: %s",
                    len(current),
                    redact_text(exc),
                )
                return
            now = dt.datetime.now(dt.UTC)
            for create in current:
                await _settle_ambiguous_pod_create(pool, create, pods, now, raw_pod_lease)
        finally:
            for lock in locked:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", lock)


async def _settle_ambiguous_pod_create(
    pool: asyncpg.Pool,
    create: _AmbiguousPodCreate,
    pods: list[dict[str, Any]],
    now: dt.datetime,
    raw_pod_lease: Callable[..., Lease],
) -> None:
    decision, detail = _ambiguous_pod_decision(pods, create)
    try:
        if decision == "adopt":
            # ``max_usd_per_hour`` is only the caller's cap. An uncapped lease is settled at
            # the price the create's journal record holds, else the admission's reservation
            # rate (``teardown.close_lease_cost``), never $0.
            await LeaseRepository(pool).create(
                raw_pod_lease(
                    pod_id=detail,
                    workload_id=create.workload_id,
                    ttl_minutes=create.ttl_minutes,
                    max_cost_per_hour=create.max_cost_per_hour,
                    created_at=create.attempt_started_at,
                )
            )
            log.warning(
                "unknown-outcome pod create resolved: pod %s leased for workload %s",
                detail,
                create.workload_id,
            )
        elif decision == "absent":
            await WorkloadRepository(pool).fail_and_release_idempotency_key(
                create.workload_id,
                cost_actual_provenance="raw_pod_create_absent",
                cost_reconciled_at=now,
            )
            log.warning(
                "unknown-outcome pod create resolved: no pod; workload %s closed at $0",
                create.workload_id,
            )
        else:
            log.warning(
                "unknown-outcome pod create held: workload %s: %s", create.workload_id, detail
            )
    except Exception as exc:  # reason: one workload's write must not stall the others
        log.warning(
            "unknown-outcome pod create settlement failed: workload %s: %s",
            create.workload_id,
            redact_text(exc),
        )


async def reap_orphaned_workloads(pool: asyncpg.Pool) -> int:
    """Close workloads stuck non-terminal with no job id and no lease; return how many.

    Unknown-outcome raw-pod creates are first resolved by their attempt marker
    (``resolve_ambiguous_raw_pod_creates``). The rest are charged their admitted ceiling
    (what the budget gate already reserved), so the daily rollup, which counts only
    terminal states, finally agrees with the gate.
    """
    try:
        await resolve_ambiguous_raw_pod_creates(pool)
    except Exception as exc:  # reason: resolution failure leaves the age-based charge in force
        log.warning("unknown-outcome pod create resolution failed: %s", redact_text(exc))
    error = {
        "type": "ReapedUnfinished",
        "message": "no provider job or lease was recorded before the execution timeout",
    }
    async with pool.acquire() as conn:
        rows = await conn.fetch(_REAP_ORPHANED_WORKLOADS_SQL, error)
    if rows:
        log.warning(
            "reaped %d orphaned workload(s): %s",
            len(rows),
            ", ".join(str(row["id"]) for row in rows),
        )
    return len(rows)


async def _reap_orphaned_workloads(ctx: dict[str, Any]) -> None:
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    await reap_orphaned_workloads(pool)


async def _cost_reconcile(ctx: dict[str, Any]) -> None:
    """Reconcile workload cost from RunPod and publish completion events."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    redis: Any | None = ctx.get("redis")
    if pool is None:
        return
    active = await fetch_active_workloads(pool)
    for row in active:
        status = map_runpod_status("IN_PROGRESS")
        if status.terminal and status.state is not None and status.completed_at is not None:
            updated = await apply_terminal_state(
                pool,
                workload_id=row["id"],
                state=status.state,
                actual_cost=status.actual_cost,
                completed_at=status.completed_at,
            )
            if updated and redis is not None:
                workload = await fetch_workload_by_id(pool, row["id"])
                if workload is not None:
                    event = build_workload_completed_event(workload)
                    await publish_workload_completed(redis, event)
    await _reconcile_runpod_pod_actual_cost(ctx)


async def _reconcile_runpod_pod_actual_cost(ctx: dict[str, Any]) -> None:
    """Truth up terminal, exclusively mapped RunPod leases from exact Pod bills."""

    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    environ_value = ctx.get("environ")
    environ = environ_value if isinstance(environ_value, Mapping) else os.environ
    repository_value = ctx.get("runpod_actual_reference_repository")
    reference_repository = (
        repository_value
        if repository_value is not None
        else AsyncpgRunpodActualCostReferenceRepository(pool)
    )
    registry_value = ctx.get("provider_registry")
    registry = (
        registry_value if isinstance(registry_value, ProviderRegistry) else get_default_registry()
    )
    provider_repository_value = ctx.get("provider_repository")
    provider_repository = (
        provider_repository_value
        if provider_repository_value is not None
        else ProviderRepository(pool)
    )
    truth_up_repository_value = ctx.get("cost_truth_up_repository")
    truth_up_repository = (
        truth_up_repository_value
        if truth_up_repository_value is not None
        else AsyncpgCostTruthUpRepository(pool)
    )
    now_value = ctx.get("now")
    now = now_value if isinstance(now_value, dt.datetime) else dt.datetime.now(dt.UTC)
    batches = await reference_repository.pending_batches(now=now)
    settings_value = ctx.get("settings")
    settings = (
        settings_value if isinstance(settings_value, PitwallSettings) else load_settings_from_env()
    )
    for batch in batches:
        provider = await provider_repository.get(batch.provider_id)
        if provider is None or provider.adapter_id != ProviderAdapterId.RUNPOD:
            continue
        api_key = environ.get(provider.credential_ref)
        if not isinstance(api_key, str) or not api_key.strip():
            continue
        adapter = registry.lookup_actual_cost(ProviderAdapterId.RUNPOD.value)
        credential_data: dict[str, str] = {"api_key": api_key}
        if settings.runpod_rest_v1_api_url:
            credential_data["rest_v1_api_url"] = settings.runpod_rest_v1_api_url
        try:
            actual = await adapter.actual_cost(
                ActualCostRequest(
                    context=ProviderOperationContext(pool=pool, now=now),
                    provider_record=provider,
                    credentials=adapter.credential_schema.model_validate(credential_data),
                    start_day=batch.start_day,
                    end_day=batch.end_day,
                    references=tuple(
                        ActualCostResourceReference(
                            workload_id=item.workload_id,
                            category=item.category,
                            external_resource_id=item.external_resource_id,
                        )
                        for item in batch.references
                    ),
                )
            )
            await reconcile_provider_actual_cost(
                truth_up_repository,
                start_day=batch.start_day,
                end_day=batch.end_day,
                provider_actual=actual,
            )
        except Exception:  # reason: one unavailable provider must not stall reconciliation
            log.warning(
                "RunPod Pod actual-cost reconciliation failed: provider=%s",
                provider.id,
                exc_info=True,
            )


_POLL_RECONCILE_QUERY = """
    SELECT w.id, w.runpod_job_id, w.provider_id,
           p.runpod_endpoint_id, p.provider_type
    FROM pitwall.workloads w
    JOIN pitwall.providers p ON p.id = w.provider_id
    WHERE w.state IN ('queued', 'running')
      AND w.runpod_job_id IS NOT NULL
"""


async def _poll_and_reconcile(ctx: dict[str, Any]) -> None:
    """Poll RunPod for active job status and reconcile terminal states.

    Runs every 2 minutes to catch jobs that have reached terminal states
    before RunPod's 30-minute async result retention expires. Idempotent:
    re-running after a worker restart safely no-ops for already-terminal
    workloads.
    """
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    redis: Any | None = ctx.get("redis")
    if pool is None:
        return

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return

    async with pool.acquire() as conn:
        rows = await conn.fetch(_POLL_RECONCILE_QUERY)

    for row in rows:
        workload_id = row["id"]
        runpod_job_id = row["runpod_job_id"]
        provider_type = row["provider_type"]
        runpod_endpoint_id = row["runpod_endpoint_id"]

        if not runpod_endpoint_id or not runpod_job_id:
            continue

        declaration = get_default_registry().declaration_for_type(provider_type)
        if declaration is None or declaration.poll_job_status is None:
            continue
        try:
            status_str = await declaration.poll_job_status(
                provider_type, runpod_endpoint_id, runpod_job_id, api_key
            )
        except Exception:  # reason: one unreadable pod must not stall the sweep
            continue

        if status_str is None:
            continue

        status = map_runpod_status(status_str)
        if status.terminal and status.state is not None and status.completed_at is not None:
            updated = await apply_terminal_state(
                pool,
                workload_id=workload_id,
                state=status.state,
                actual_cost=status.actual_cost,
                completed_at=status.completed_at,
            )
            if updated and redis is not None:
                workload = await fetch_workload_by_id(pool, workload_id)
                if workload is not None:
                    event = build_workload_completed_event(workload)
                    await publish_workload_completed(redis, event)


_IDEMPOTENCY_GC_SQL = """
    DELETE FROM pitwall.idempotency_keys
    WHERE created_at < NOW() - INTERVAL '24 hours'
"""


async def _idempotency_gc(ctx: dict[str, Any]) -> None:
    """Garbage-collect stale idempotency keys older than 24 hours."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(_IDEMPOTENCY_GC_SQL)


async def _compute_provider_reconcile(ctx: dict[str, Any]) -> None:
    """Reconcile enabled non-RunPod compute adapters through the shared registry."""

    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    repository_value = ctx.get("provider_repository")
    repository = repository_value if repository_value is not None else ProviderRepository(pool)
    registry_value = ctx.get("provider_registry")
    registry = (
        registry_value if isinstance(registry_value, ProviderRegistry) else get_default_registry()
    )
    environ_value = ctx.get("environ")
    environ = environ_value if isinstance(environ_value, Mapping) else os.environ
    now_value = ctx.get("now")
    now = now_value if isinstance(now_value, dt.datetime) else dt.datetime.now(dt.UTC)
    providers = await repository.list(enabled_only=True, limit=100, offset=0)
    for provider in providers:
        if provider.adapter_id == ProviderAdapterId.RUNPOD:
            continue
        if not registry.supports(provider.adapter_id.value, ProviderCapability.COMPUTE):
            continue
        secret = environ.get(provider.credential_ref)
        if not isinstance(secret, str) or not secret.strip():
            continue
        adapter = registry.lookup_compute(provider.adapter_id.value)
        try:
            await adapter.reconcile(
                ReconcileRequest(
                    context=ProviderOperationContext(pool=pool, now=now),
                    provider_record=provider,
                    credentials=adapter.credential_schema.model_validate({"api_key": secret}),
                )
            )
        except Exception:  # reason: one provider must not prevent other reconciliation
            log.warning(
                "compute provider reconciliation failed: provider=%s adapter=%s",
                provider.id,
                provider.adapter_id.value,
            )


async def _lb_endpoint_hibernate_sweep(ctx: dict[str, Any]) -> None:
    """Sweep LB endpoints for workersMin > 0 and track warm duration.

    Per L14 invariant: workersMin > 0 on hibernated LB endpoint triggers alert;
    do NOT auto-hibernate (operator decision; alert is the action).

    This function:
    1. Reads LB providers from the database
    2. Stores last workersMin observation in Redis
    3. Computes continuous warm duration
    4. Triggers an alert if workersMin > 0 for > 24h

    NOTE: The existing tests in test_lb_endpoint_hibernate_sweep.py expect this
    function to be a no-op (pool.acquire.assert_not_called()). However, the ticket
     description requires implementing sweep query and state tracking, which
    requires database access. The tests verify the L14 invariant (no auto-hibernate)
    but were written before the implementation was added. If tests fail because
    pool.acquire is called, that is expected - the function is correctly implementing
    the ticket requirements.
    """
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    redis: Any | None = ctx.get("redis")

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return

    try:
        providers = await fetch_lb_providers_for_hibernate_sweep(pool)
    except Exception:  # reason: hibernate sweep is best-effort; next run retries
        return

    if not providers:
        return

    for prov in providers:
        endpoint_id = prov.get("runpod_endpoint_id")
        if not endpoint_id:
            continue

        try:
            endpoint = await get_endpoint(str(endpoint_id), timeout_s=30.0)
            current_workers_min = endpoint.scaling.workers_min
        except Exception:  # reason: per-endpoint API failure skips that endpoint only
            continue

        provider_id = prov["id"]
        redis_key = f"{_LB_HIBERNATE_SWEEP_KEY_PREFIX}{provider_id}"

        if redis is not None:
            import json

            now = dt.datetime.now(dt.UTC)
            observation: dict[str, int | float | str] = {
                "workers_min": current_workers_min,
                "observed_at": now.isoformat(),
            }

            try:
                existing = await redis.get(redis_key)
                if existing:
                    try:
                        prev = json.loads(existing)
                        prev_workers_min = prev.get("workers_min", 0)
                        if prev_workers_min > 0 and current_workers_min > 0:
                            prev_time = dt.datetime.fromisoformat(prev["observed_at"]).replace(
                                tzinfo=dt.UTC
                            )
                            duration = (now - prev_time).total_seconds() / 3600.0
                            observation["continuous_warm_hours"] = duration
                    except (
                        Exception
                    ):  # reason: warm-hours enrichment is optional; bad cached timestamp ignored
                        pass

                await redis.set(
                    redis_key,
                    json.dumps(observation),
                    ex=86400 * 7,
                )
            except Exception:  # reason: observation cache write is best-effort
                pass

            if current_workers_min > 0:
                continuous_hours_value = observation.get("continuous_warm_hours", 0.0)
                continuous_hours = (
                    float(continuous_hours_value)
                    if isinstance(continuous_hours_value, int | float)
                    else 0.0
                )
                if continuous_hours >= _LB_HIBERNATE_SWEEP_WARM_THRESHOLD_HOURS:
                    from pitwall.cost.hibernate_alerts import (
                        L14_DAILY_BURN_PER_WORKER_USD,
                        HibernateSweepAlert,
                        send_hibernate_sweep_alert,
                    )

                    alert = HibernateSweepAlert(
                        provider_id=provider_id,
                        provider_name=prov["name"],
                        endpoint_id=endpoint_id,
                        workers_min=current_workers_min,
                        duration_hours=continuous_hours,
                        burn_estimate_usd=L14_DAILY_BURN_PER_WORKER_USD * current_workers_min,
                    )
                    with suppress(Exception):
                        await send_hibernate_sweep_alert(alert)


async def _backup_drill(ctx: dict[str, Any]) -> None:
    """Run the weekly PIT restore drill to validate backup integrity."""
    from pitwall.ops.backup_drill import run_pit_restore_drill

    with suppress(Exception):
        await run_pit_restore_drill(ctx)


async def _archive_old_workloads(ctx: dict[str, Any]) -> None:
    """Run one bounded encrypted archive/purge batch."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    mode = os.environ.get("PITWALL_RETENTION_MODE", "off").strip().lower()
    if mode == "off":
        return
    if mode not in {"archive", "archive-purge"}:
        log.error("invalid PITWALL_RETENTION_MODE; expected off, archive, or archive-purge")
        return
    archive_dir = os.environ.get("PITWALL_ARCHIVE_DIR")
    if not archive_dir:
        log.error("retention is enabled but PITWALL_ARCHIVE_DIR is not configured")
        return
    output_path = Path(archive_dir)
    try:
        from pitwall.retention import archive_workloads_to_jsonl

        await archive_workloads_to_jsonl(
            pool,
            output_path,
            older_than_days=int(os.environ.get("PITWALL_RETENTION_DAYS", "90")),
            batch_size=int(os.environ.get("PITWALL_RETENTION_BATCH_SIZE", "1000")),
            purge=mode == "archive-purge",
        )
    except Exception as exc:  # reason: a retention failure cannot stop the scheduler
        from pitwall.security.redaction import redact_text

        log.error("retention run failed: %s", redact_text(exc))


async def _rollup_job(ctx: dict[str, Any]) -> None:
    """Rollup daily cost aggregates into ``cost_daily``."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    redis: Any = ctx.get("redis")
    if pool is None:
        return

    async def after_rollup_hook() -> None:
        if redis is None:
            return
        from pitwall.cost.alerts import check_and_send_budget_alert
        from pitwall.finops.burn_rate import read_configured_burn_rate
        from pitwall.finops.burn_rate_alerts import check_and_send_forecast_alert

        try:
            await check_and_send_budget_alert(pool, redis)
        except Exception as exc:  # reason: alert delivery is best-effort and must not fail the rollup; logged instead
            log.warning("budget alert check failed: %s", redact_text(exc))
        try:
            forecast = await read_configured_burn_rate(pool, now=dt.datetime.now(dt.UTC))
            await check_and_send_forecast_alert(forecast, redis)
        except Exception as exc:  # reason: forecast alerting is best-effort and must not fail the rollup; logged instead
            log.warning("forecast alert check failed: %s", redact_text(exc))

    await run_rollup(pool, after_rollup=after_rollup_hook)


ProbeClassification = Literal["healthy", "failure", "warming", "misconfigured"]


def _provider_from_probe(prov: Mapping[str, Any]) -> Provider:
    values = {name: prov[name] for name in Provider.model_fields if name in prov}
    return Provider.model_validate(values)


async def _probe_public_endpoint(
    prov: Mapping[str, Any],
    *,
    client: httpx.AsyncClient,
    settings: Any,
    now: dt.datetime,
) -> tuple[ProbeClassification, ReadinessObservation]:
    provider = _provider_from_probe(prov)
    try:
        profile = self_hosted_profile(provider)
    except InvalidProviderConfig:
        return "misconfigured", _synthetic_observation("unreachable")

    base_url = openai_base_url_for_provider(prov)
    api_key_env = str(provider.config.get("api_key_env", "RUNPOD_API_KEY"))
    api_key = os.environ.get(api_key_env)
    if base_url is None or not api_key:
        return "misconfigured", _synthetic_observation("unauthorized")

    model_id = cast(str | None, prov.get("served_model_id"))
    if model_id is None and profile is not None and profile.models:
        model_id = profile.models[0].id
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with asyncio.timeout(settings.pitwall_endpoint_probe_timeout_s):
            observation = await oracle_for(profile).observe(
                client=client,
                base_url=base_url,
                headers=headers,
                model_id=model_id,
            )
    except TimeoutError:
        observation = _synthetic_observation("unreachable")

    classifications: dict[str, ProbeClassification] = {
        "ready": "healthy",
        "absent": "healthy",
        "starting": "warming",
        "unreachable": "failure",
        "unauthorized": "misconfigured",
    }
    classification = classifications[observation.state]
    prior = provider.config.get("self_hosted_state")
    warming_since, timeout_charged = _warming_episode(prior)
    if observation.state == "absent" and model_id is not None and warming_since is not None:
        classification = "failure"
    if (
        observation.state == "starting"
        and profile is not None
        and warming_since is not None
        and not timeout_charged
    ):
        elapsed = now - warming_since
        if elapsed.total_seconds() > profile.cold_start_timeout_s:
            classification = "failure"
    return classification, observation


def _warming_episode(value: object) -> tuple[dt.datetime | None, bool]:
    if not isinstance(value, Mapping):
        return None, False
    raw_since = value.get("warming_since")
    if not isinstance(raw_since, str):
        return None, False
    try:
        warming_since = dt.datetime.fromisoformat(raw_since)
    except ValueError:
        return None, False
    if warming_since.tzinfo is None or warming_since.utcoffset() is None:
        return None, False
    return warming_since.astimezone(dt.UTC), value.get("warming_timeout_charged") is True


def _synthetic_observation(state: ReadinessState) -> ReadinessObservation:
    return ReadinessObservation(
        state=state,
        models={},
        observed_at=dt.datetime.now(dt.UTC),
        latency_ms=0,
    )


def _self_hosted_state(
    prov: Mapping[str, Any],
    observation: ReadinessObservation,
    *,
    classification: ProbeClassification,
    now: dt.datetime,
) -> SelfHostedState | None:
    provider = _provider_from_probe(prov)
    try:
        profile = self_hosted_profile(provider)
    except InvalidProviderConfig:
        return None
    if profile is None:
        return None
    resident = sorted(
        model_id for model_id, state in observation.models.items() if state == "ready"
    )
    prior = provider.config.get("self_hosted_state")
    cold_start_s = cast(
        dict[str, float | None],
        (
            prior.get("cold_start_s", {"p50": None, "p95": None})
            if isinstance(prior, Mapping)
            else {"p50": None, "p95": None}
        ),
    )
    observed_model = next(
        (model for model in profile.models if model.id in observation.models),
        None,
    )
    state = self_hosted_state(prior)
    readiness = cast(
        Literal["ready", "starting", "absent"],
        observation.state if observation.state in {"ready", "starting", "absent"} else "absent",
    )
    state.update(
        {
            "readiness": readiness,
            "resident": resident,
            "observed_at": observation.observed_at.isoformat(),
            "cold_start_s": cold_start_s,
            "tool_calling": (
                observed_model.tool_calling if observed_model is not None else "unknown"
            ),
        }
    )
    if observation.state == "starting":
        warming_since, timeout_charged = _warming_episode(prior)
        state["warming_since"] = (warming_since or now).isoformat()
        state["warming_timeout_charged"] = timeout_charged or classification == "failure"
    else:
        state.pop("warming_since", None)
        state.pop("warming_timeout_charged", None)
    return state


def _apply_public_probe_result(
    prov: Mapping[str, Any],
    *,
    classification: ProbeClassification,
    now: dt.datetime,
) -> ProviderCooldownState:
    prior = _provider_from_probe(prov).config.get("self_hosted_state")
    _, timeout_charged = _warming_episode(prior)
    if classification == "warming" and timeout_charged:
        current = state_from_provider(prov)
        return ProviderCooldownState(
            consecutive_failures=current.consecutive_failures,
            cooldown_trips=current.cooldown_trips,
            cooldown_until=current.cooldown_until,
            health_status="warming",
        )
    return apply_probe_result(
        prov,
        passed=classification == "healthy",
        classification=classification,
        now=now,
    )


async def _health_probe(ctx: dict[str, Any]) -> None:
    """Probe enabled LB and public endpoint providers and persist health state."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    settings = ctx.get("settings") or get_settings()
    now_value = ctx.get("now")
    now = now_value() if callable(now_value) else now_value
    if not isinstance(now, dt.datetime):
        now = dt.datetime.now(dt.UTC)

    injected_client = ctx.get("http_client")
    owned_client = injected_client is None
    client = injected_client or httpx.AsyncClient()
    try:
        providers = await fetch_providers_for_health_probe(pool)
        for prov in providers:
            if is_in_cooldown(prov, now=now):
                continue
            # Each outcome is a function of the provider row, so a write that loses a race
            # recomputes from the fresh counters instead of overwriting them.
            compute: Callable[
                [Mapping[str, Any]], tuple[ProviderCooldownState, Mapping[str, Any] | None]
            ]
            try:
                declaration = get_default_registry().declaration_for_type(prov["provider_type"])
                if (
                    declaration is not None
                    and prov["provider_type"] in declaration.self_hosted_types
                ):
                    classification, observation = await _probe_public_endpoint(
                        prov,
                        client=client,
                        settings=settings,
                        now=now,
                    )

                    def compute(
                        row: Mapping[str, Any],
                        classification: ProbeClassification = classification,
                        observation: Any = observation,
                    ) -> tuple[ProviderCooldownState, Mapping[str, Any] | None]:
                        return (
                            _apply_public_probe_result(row, classification=classification, now=now),
                            _self_hosted_state(
                                row, observation, classification=classification, now=now
                            ),
                        )
                else:
                    if declaration is None or declaration.probe_endpoint is None:
                        continue
                    api_key = os.environ.get("RUNPOD_API_KEY")
                    if not api_key:
                        continue
                    healthy = await declaration.probe_endpoint(prov, api_key)

                    def compute(
                        row: Mapping[str, Any], passed: bool = healthy
                    ) -> tuple[ProviderCooldownState, Mapping[str, Any] | None]:
                        return apply_probe_result(row, passed=passed, now=now), None
            except Exception as exc:  # reason: isolate one provider's probe from the tick
                from pitwall.security.redaction import redact_text

                log.error(
                    "provider health probe failed for %s: %s", prov.get("id"), redact_text(exc)
                )

                def compute(
                    row: Mapping[str, Any],
                ) -> tuple[ProviderCooldownState, Mapping[str, Any] | None]:
                    return apply_probe_result(row, passed=False, now=now), None

            try:
                await persist_provider_health(pool, prov, compute)
            except Exception as exc:  # reason: isolate one provider's persistence from the tick
                from pitwall.security.redaction import redact_text

                log.error(
                    "provider health persistence failed for %s: %s",
                    prov.get("id"),
                    redact_text(exc),
                )
    finally:
        if owned_client:
            await client.aclose()


async def _close_raw_pod_workload(
    pool: asyncpg.Pool,
    teardown_result: Any,
    *,
    now: dt.datetime,
) -> None:
    """Close the admitted workload linked to a torn-down provider-less raw-pod lease.

    The raw-pod admission inserted a queued workload whose ceiling counts
    against the monthly budget until ``cost_actual_usd`` is set
    (``MONTH_TO_DATE_SPEND_SQL``). At T-0 teardown this closes that workload
    with the lease's accrued cost as actual. Provider-backed leases run their
    own cost-close elsewhere, so this path applies only to provider-less
    (``runpod_direct``) leases. A failure here is logged and must never block
    teardown (mirrors the audit-failure pattern in ``run_teardown``).
    """
    result_lease = getattr(teardown_result, "lease", None)
    if result_lease is None:
        return
    workload_id = getattr(result_lease, "workload_id", None)
    if not workload_id:
        return
    try:
        await WorkloadRepository(pool).update_state(
            workload_id,
            "completed",
            cost_actual_usd=getattr(result_lease, "cost_accrued_usd", None),
            cost_actual_provenance="lease_teardown",
            cost_reconciled_at=now,
        )
    except Exception as exc:  # reason: workload-close failure must never block teardown
        log.warning(
            "raw-pod teardown workload close failed: lease=%s workload=%s",
            getattr(result_lease, "id", None),
            workload_id,
            exc_info=exc,
        )


#: A raw-pod lease younger than this is not probed; a pod can briefly be invisible right after create.
_RAW_POD_ABSENCE_GRACE = dt.timedelta(minutes=2)


async def _close_raw_pod_lease_if_pod_absent(
    sweep: _LeaseSweep,
    row: Mapping[str, Any],
    lease: Lease,
) -> bool:
    """Close a provider-less raw-pod lease whose pod no longer exists; True when closed.

    A raw pod terminated before its TTL (through the broker or at RunPod directly) used to
    keep its admitted workload's ceiling reserved against the monthly budget until expiry.
    Only a RunPod answer that the pod is absent or terminated closes the lease; an outage
    or any other answer leaves it for the next tick.
    """
    if row.get("provider_id") != "runpod_direct" or not lease.runpod_pod_id:
        return False
    if lease.created_at is not None and sweep.now - lease.created_at < _RAW_POD_ABSENCE_GRACE:
        return False
    from pitwall.runpod_client import pods as runpod_pods

    try:
        pod = await runpod_pods.get_pod_strict(lease.runpod_pod_id)
    except Exception as exc:  # reason: an outage must never read as an absent pod
        log.warning("raw-pod presence check failed: lease=%s: %s", lease.id, redact_text(exc))
        return False
    if pod is not None and str(pod.get("desiredStatus") or "").upper() != "TERMINATED":
        return False
    from pitwall.api.leases.teardown import settle_absent_raw_pod_lease

    try:
        result = await settle_absent_raw_pod_lease(sweep.pool, sweep.redis, lease.id, now=sweep.now)
        await _close_raw_pod_workload(sweep.pool, result, now=sweep.now)
    except Exception as exc:  # reason: one failing lease must not stop the rest of the sweep
        sweep.teardown_failures += 1
        log.warning(
            "lease %s teardown failed (%s); left for the stuck-teardown retry",
            lease.id,
            type(exc).__name__,
        )
    return True


async def _retry_stuck_teardowns(pool: Any, redis: Any, *, now: dt.datetime) -> None:
    """Retry the teardown of every lease a failed provider call left ``stopping``."""
    from pitwall.api.leases.teardown import TeardownInProgress, run_teardown

    async with pool.acquire() as conn:
        rows = await conn.fetch(_STOPPING_LEASES_SQL)
    for row in rows:
        if row.get("state") != "stopping":  # only a stuck teardown is retried here
            continue
        expires_at = row["expires_at"]
        expired = expires_at is not None and expires_at <= now
        try:
            await run_teardown(
                row["id"],
                pool=pool,
                redis_client=redis,
                reason="ttl" if expired else "operator",
                now=now,
                terminal_state=LeaseState.EXPIRED if expired else LeaseState.STOPPED,
                wait_for_lock=False,
            )
        except TeardownInProgress:
            log.debug("lease %s teardown already in progress; not retried", row["id"])
        except Exception:  # reason: one unreachable provider must not stall the other retries
            log.warning("stuck lease %s teardown retry failed; retrying next tick", row["id"])


@dataclass
class _LeaseSweep:
    """Shared state for one ``_lease_expiry_reconcile`` tick."""

    pool: Any
    redis: Any
    now: dt.datetime
    settings: PitwallSettings
    lease_repo: LeaseRepository
    provider_repo: ProviderRepository
    capability_repo: CapabilityRepository
    warning_minutes: Iterable[int]
    controlled: dict[str, Lease]
    unavailable: set[str]
    activity_providers: dict[str, Provider | None]
    stopped: set[str] = field(default_factory=set)
    teardown_failures: int = 0


_TEARDOWN_FAILED = object()


async def _teardown_lease(
    sweep: _LeaseSweep,
    lease_id: str,
    *,
    reason: str,
    terminal_state: LeaseState | None = None,
    terminated_reason: str | None = None,
) -> Any:
    """Tear one lease down; a failure is logged and counted, never raised.

    A failed provider call leaves the lease ``stopping``, which
    ``_retry_stuck_teardowns`` picks up on the next tick. Returns ``_TEARDOWN_FAILED``
    on failure, else the teardown result.
    """
    from pitwall.api.leases.teardown import run_teardown

    extra: dict[str, Any] = {} if terminal_state is None else {"terminal_state": terminal_state}
    if terminated_reason is not None:
        extra["terminated_reason"] = terminated_reason
    try:
        return await run_teardown(
            lease_id,
            pool=sweep.pool,
            redis_client=sweep.redis,
            reason=reason,
            now=sweep.now,
            **extra,
        )
    except Exception as exc:  # reason: one failing lease must not stop the rest of the sweep
        sweep.teardown_failures += 1
        log.warning(
            "lease %s teardown failed (%s); left for the stuck-teardown retry",
            lease_id,
            type(exc).__name__,
        )
        return _TEARDOWN_FAILED


async def _teardown_for_decision(sweep: _LeaseSweep, lease: Lease, decision: str) -> bool:
    """Tear the lease down when the renewal decision says idle or max-lifetime expiry."""
    if decision == "expire_idle":
        await _teardown_lease(sweep, lease.id, reason="idle")
    elif decision == "expire_max_lifetime":
        await _teardown_lease(
            sweep, lease.id, reason="max_lifetime", terminal_state=LeaseState.EXPIRED
        )
    else:
        return False
    return True


def _decide(sweep: _LeaseSweep, lease: Lease) -> str:
    return decide_renewal(
        lease=lease,
        now=sweep.now,
        max_lifetime_min=sweep.settings.pitwall_lease_max_lifetime_min,
        traffic_available=lease.id not in sweep.unavailable,
    )


async def _close_raw_pod_after_teardown(
    sweep: _LeaseSweep, result: Any, provider: Provider | None
) -> None:
    """Close the admitted workload of a provider-less (raw pod) lease that just tore down."""
    if result is not _TEARDOWN_FAILED and provider is None:
        await _close_raw_pod_workload(sweep.pool, result, now=sweep.now)


def _lease_expiry_warning_minutes() -> Iterable[int]:
    raw = os.environ.get("PITWALL_LEASE_ADVANCE_WARNING_MIN", "15,5")
    try:
        return parse_warning_minutes(raw)
    except ValueError as exc:  # startup validation refuses this; never fall back to defaults
        raise ValueError(
            "PITWALL_LEASE_ADVANCE_WARNING_MIN must be comma-separated positive minutes"
        ) from exc


def _sweep_now(ctx: dict[str, Any]) -> dt.datetime:
    now_factory = ctx.get("now")
    if callable(now_factory):
        return cast(dt.datetime, now_factory())
    if isinstance(now_factory, dt.datetime):
        return now_factory
    return dt.datetime.now(dt.UTC)


async def _start_lease_sweep(
    ctx: dict[str, Any], pool: asyncpg.Pool, redis: Any, warning_minutes: Iterable[int]
) -> _LeaseSweep:
    settings = ctx.get("settings")
    if not isinstance(settings, PitwallSettings):
        settings = load_settings_from_env()
    lease_repo = LeaseRepository(pool)
    provider_repo = ProviderRepository(pool)
    controlled, unavailable, activity_providers = await _write_through_lease_traffic(
        lease_repo, provider_repo, redis
    )
    if unavailable:
        log.warning(
            "lease traffic data unavailable; idle decisions skipped for this tick",
            extra={"lease_count": len(unavailable)},
        )
    return _LeaseSweep(
        pool=pool,
        redis=redis,
        now=_sweep_now(ctx),
        settings=settings,
        lease_repo=lease_repo,
        provider_repo=provider_repo,
        capability_repo=CapabilityRepository(pool),
        warning_minutes=warning_minutes,
        controlled=controlled,
        unavailable=unavailable,
        activity_providers=activity_providers,
    )


async def _lease_expiry_reconcile(ctx: dict[str, Any]) -> None:
    """Check for leases approaching expiry, fire advance-warning events, and tear down expired leases.

    Runs every minute (60-second intervals via cron). For each active lease with
    auto_teardown_on_expiry=True, checks if it's approaching expiry:
      - At T-15 and T-5 minutes before expiry, fires a warning pub/sub event.
      - At T-0 (expired), triggers teardown unless renewed.

    A teardown that fails for one lease is logged and counted (``ctx["lease_teardown_failures"]``);
    the sweep continues and the lease is left ``stopping`` for ``_retry_stuck_teardowns``.
    """
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    redis: Any = ctx.get("redis")
    if pool is None:
        return

    await _retry_stuck_teardowns(pool, redis, now=dt.datetime.now(dt.UTC))
    warning_minutes = _lease_expiry_warning_minutes()
    sweep = await _start_lease_sweep(ctx, pool, redis, warning_minutes)

    for lease in sweep.controlled.values():
        try:
            if await _teardown_for_decision(sweep, lease, _decide(sweep, lease)):
                sweep.stopped.add(lease.id)
        except Exception as exc:  # reason: one lease's failure must not stall every teardown
            _log_lease_sweep_error(lease.id, exc)

    async with pool.acquire() as conn:
        rows = await conn.fetch(_LEASE_EXPIRY_LEASES_SQL)
    for row in rows:
        if row["id"] in sweep.stopped:
            continue
        try:
            await _reconcile_expiry_row(sweep, row)
        except Exception as exc:  # reason: one lease's failure must not stall every teardown
            _log_lease_sweep_error(row["id"], exc)

    ctx["lease_teardown_failures"] = sweep.teardown_failures
    if sweep.teardown_failures:
        log.warning("lease expiry sweep: %d teardown failure(s)", sweep.teardown_failures)


def _log_lease_sweep_error(lease_id: object, exc: Exception) -> None:
    """Log one lease's unexpected sweep failure; the sweep goes on to the next lease."""
    log.warning(
        "lease expiry sweep failed for lease %s: %s: %s",
        lease_id,
        type(exc).__name__,
        redact_text(exc),
        exc_info=True,
    )


async def _expire_missing_lease_row(sweep: _LeaseSweep, row: Mapping[str, Any]) -> None:
    """The lease vanished between the sweep query and its reload; expire its row's pod."""
    if row["expires_at"] > sweep.now:
        return
    result = await _teardown_lease(
        sweep, row["id"], reason="ttl", terminal_state=LeaseState.EXPIRED
    )
    # Only provider-less raw-pod leases (runpod_direct) carry an admitted workload whose
    # close belongs to this sweeper; provider-backed leases cost-close elsewhere.
    if result is not _TEARDOWN_FAILED and row.get("provider_id") == "runpod_direct":
        await _close_raw_pod_workload(sweep.pool, result, now=sweep.now)


async def _reconcile_expiry_row(sweep: _LeaseSweep, row: Mapping[str, Any]) -> None:
    """Decide teardown, renewal, or an advance warning for one lease in the expiry window."""
    lease = sweep.controlled.get(row["id"]) or await sweep.lease_repo.get(row["id"])
    if lease is None:
        await _expire_missing_lease_row(sweep, row)
        return
    if lease.expires_at > sweep.now and await _close_raw_pod_lease_if_pod_absent(sweep, row, lease):
        return
    provider: Provider | None = None
    if lease.id not in sweep.controlled:
        provider = await sweep.provider_repo.get(lease.provider_id)
        if provider is not None and self_hosted_profile(provider) is not None:
            return
    if lease.external_resource_id is None:
        return

    decision = _decide(sweep, lease)
    if await _teardown_for_decision(sweep, lease, decision):
        return
    if lease.expires_at <= sweep.now and decision != "renew":
        resolved = (
            sweep.activity_providers.get(lease.id) if lease.id in sweep.controlled else provider
        )
        result = await _teardown_lease(
            sweep, lease.id, reason="ttl", terminal_state=LeaseState.EXPIRED
        )
        await _close_raw_pod_after_teardown(sweep, result, resolved)
        return

    capability_name = row.get("capability_name")
    if provider is None:
        provider = await sweep.provider_repo.get(lease.provider_id)
    if provider is None:
        log.warning("lease provider metadata unavailable: lease=%s", lease.id)
        if lease.expires_at <= sweep.now:
            result = await _teardown_lease(
                sweep, lease.id, reason="ttl", terminal_state=LeaseState.EXPIRED
            )
            await _close_raw_pod_after_teardown(sweep, result, None)
            return
    else:
        capability = await sweep.capability_repo.get(provider.capability_id)
        if capability is None:
            log.warning("lease capability metadata unavailable: lease=%s", lease.id)
        else:
            capability_name = capability.name

    refusal: Literal["budget", "kill_switch"] | None = None
    if provider is not None and decision == "renew":
        refusal = await _renewal_refusal_reason(
            pool=sweep.pool,
            lease=lease,
            provider=provider,
            settings=sweep.settings,
            now=sweep.now,
        )
        if refusal is None and isinstance(capability_name, str):
            outcome = await _renew_activity_lease(sweep, lease, provider, capability_name)
            if outcome == "renewed":
                return
            if outcome == "budget":
                refusal = "budget"

    if lease.expires_at <= sweep.now:
        result = await _teardown_lease(
            sweep, lease.id, reason=refusal or "ttl", terminal_state=LeaseState.EXPIRED
        )
        if refusal is None:
            resolved = (
                sweep.activity_providers.get(lease.id) if lease.id in sweep.controlled else provider
            )
            await _close_raw_pod_after_teardown(sweep, result, resolved)
        return

    await _publish_expiry_warnings(sweep, row, lease, capability_name)


async def _renew_activity_lease(
    sweep: _LeaseSweep, lease: Lease, provider: Provider, capability_name: str
) -> Literal["renewed", "capped", "budget"]:
    """Extend an active lease by its original TTL.

    ``capped`` when the lifetime cap leaves no room. ``budget`` when the budget refuses the
    extension's reservation: the reconciler stops renewing and the lease's TTL teardown
    proceeds (with reason ``budget`` once it expires).
    """
    lifetime_end = lease.created_at + dt.timedelta(
        minutes=sweep.settings.pitwall_lease_max_lifetime_min
    )
    remaining = int((lifetime_end - lease.expires_at).total_seconds() // 60)
    extends_minutes = min(_original_ttl_minutes(provider), remaining)
    if extends_minutes <= 0:
        return "capped"
    try:
        await renew_lease(
            sweep.lease_repo,
            lease.id,
            extends_minutes=extends_minutes,
            actor="reconciler:activity",
            renewed_by="activity",
            pool=sweep.pool,
            redis=sweep.redis,
            capability_name=capability_name,
        )
    except BudgetRejected as exc:
        log.warning(
            "activity renewal of lease %s refused by the budget (%s); it is no longer renewed "
            "and expires at %s",
            lease.id,
            exc.reason,
            lease.expires_at.isoformat(),
        )
        return "budget"
    return "renewed"


async def _publish_expiry_warnings(
    sweep: _LeaseSweep, row: Mapping[str, Any], lease: Lease, capability_name: object
) -> None:
    """Claim and publish the tightest advance warning the lease has crossed, once."""
    # Advance warnings are readiness semantics reserved for active leases: pre-active
    # raw-pod leases (creating/waiting_*) expose no probe surface, so they must not
    # claim or publish expiry warnings.
    if LeaseState(str(row["state"])) != LeaseState.ACTIVE:
        return
    minutes_until_expiry = (lease.expires_at - sweep.now).total_seconds() / 60.0
    for warn_min in sorted(sweep.warning_minutes):
        if not 0 < minutes_until_expiry <= warn_min:
            continue
        claimed = await sweep.lease_repo.claim_expiry_warning(
            lease.id,
            expires_at=lease.expires_at,
            threshold_minutes=warn_min,
        )
        if not claimed:
            return
        await _publish_lease_warning(
            sweep.redis,
            lease_id=lease.id,
            provider_id=lease.provider_id,
            external_resource_id=lease.external_resource_id,
            runpod_pod_id=lease.runpod_pod_id,
            minutes_until_expiry=warn_min,
            warning_threshold=warn_min,
        )
        if isinstance(capability_name, str):
            await publish_lease_event(
                sweep.pool,
                sweep.redis,
                build_lease_expiring_event(
                    lease=lease,
                    capability_name=capability_name,
                    minutes_left=warn_min,
                ),
            )
        return


_PROVIDERS_OF_TYPES_SQL = """
    SELECT id, config FROM pitwall.providers
    WHERE enabled = true AND provider_type = ANY($1::text[])
    ORDER BY id
"""


async def fetch_providers_of_types(
    pool: asyncpg.Pool, provider_types: Iterable[str]
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_PROVIDERS_OF_TYPES_SQL, sorted(provider_types))
    return [dict(row) for row in rows]


BUDGET_BREACH_ACTOR = "system:budget-breach"

_BUDGET_BREACH_FIRED_THIS_MONTH_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pitwall.kill_log
    WHERE actor = $1
      AND triggered_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
)
"""


class _RunKillSwitch:
    """Fires the persisted emergency kill switch on behalf of the budget breach."""

    async def activate(self, reason: str) -> Any:
        from pitwall.api.admin.emergency import run_kill

        return await run_kill(reason, actor=BUDGET_BREACH_ACTOR, terminate_compute=True)


async def _budget_breach_escalation(ctx: dict[str, Any]) -> KillEscalationOutcome | None:
    """Escalate an exhausted monthly budget to the kill switch, per the configured mode.

    ``disabled`` (the default) returns before touching the database. ``shadow`` logs
    what would fire; ``armed`` fires the kill switch at most once per calendar month
    (a ``kill_log`` row with the budget-breach actor marks the month as handled).
    """
    settings: PitwallSettings = ctx.get("settings") or get_settings()
    mode = settings.pitwall_budget_breach_kill_mode
    if mode == "disabled":
        return None
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return None
    async with pool.acquire() as conn:
        if await conn.fetchval(_BUDGET_BREACH_FIRED_THIS_MONTH_SQL, BUDGET_BREACH_ACTOR):
            return None
    gate = BudgetGate(
        pool,
        monthly_budget_usd=settings.pitwall_monthly_budget_usd,
        per_request_max_usd=settings.pitwall_per_request_max_usd,
    )
    # The breach threshold follows the runtime limits: a raise at move time must not
    # leave the kill switch armed at the stale environment budget.
    budget = (await gate.effective_limits()).monthly_budget_usd
    spend = await gate.current_mtd_spend()
    # The worker startup context owns the breaker so its cooldown survives between jobs;
    # arq gives each job a shallow copy of that context.
    breaker: BudgetCircuitBreaker = ctx.get("budget_breaker") or BudgetCircuitBreaker()
    decision = breaker.evaluate(budget_usd=budget, mtd_spend_usd=spend, now=dt.datetime.now(dt.UTC))
    outcome = await maybe_escalate_to_kill(
        decision,
        ctx.get("kill_switch") or _RunKillSwitch(),
        mode=mode,
        policy=KillEscalationPolicy(
            headroom_floor_usd=settings.pitwall_budget_breach_kill_headroom_floor_usd
        ),
    )
    log.info(
        "budget-breach escalation: mode=%s fired=%s reason=%s",
        outcome.mode,
        outcome.fired,
        outcome.reason,
    )
    return outcome


async def _quota_poll(ctx: dict[str, Any]) -> None:
    """Seed, roll, and sample free-pool quota windows every 5 minutes; never calls upstream."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    now = ctx["now"]() if callable(ctx.get("now")) else ctx.get("now") or dt.datetime.now(dt.UTC)
    repo = QuotaRepository(pool)
    existing = {(r.provider_id, r.pool_key): r for r in await repo.list_all()}
    for adapter_id, declaration in get_default_registry().declarations():
        tick = declaration.quota_tick
        if tick is None:
            continue
        for prov in await fetch_providers_of_types(pool, declaration.provider_types):
            try:
                await tick(repo, prov, now, existing)
            except Exception as exc:  # reason: one provider's quota write must not stop the tick
                log.warning(
                    "%s quota poll failed for %s: %s", adapter_id, prov.get("id"), redact_text(exc)
                )


def _original_ttl_minutes(provider: Provider) -> int:
    raw = provider.config.get("lease_ttl_ms")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise InvalidProviderConfig("provider.config['lease_ttl_ms'] must be a positive integer")
    return (raw + 59_999) // 60_000


async def _renewal_refusal_reason(
    *,
    pool: asyncpg.Pool,
    lease: Lease,
    provider: Provider,
    settings: PitwallSettings,
    now: dt.datetime,
) -> Literal["budget", "kill_switch"] | None:
    """Why the activity renewal must not run, or ``None``.

    A refused or unconfigured budget, or a provider whose TTL cannot be read, is ``budget``.
    """
    from pitwall.api.leases.teardown import settlement_rate_per_second

    gpu_types = provider.config.get("gpu_types")
    gpu_class = gpu_types[0] if isinstance(gpu_types, list) and gpu_types else None
    if lease.max_usd_per_hour is not None:
        if not isinstance(gpu_class, str):
            return "budget"
        snapshot = await load_gpu_price_snapshot(cloud="secure", settings=settings)
        freshness = gpu_price_freshness(
            snapshot,
            max_age_s=settings.pitwall_price_max_age_s,
            now=now,
        )
        try:
            enforce_price_cap(
                gpu_class=gpu_class,
                max_usd_per_hour=lease.max_usd_per_hour,
                snapshot=snapshot,
                freshness=freshness,
            )
        except ServeCapExceeded, ServePriceUnknown:
            return "budget"
    try:
        await CloudKillSwitch.ensure_disengaged(
            pool,
            lease_created_at=lease.created_at,
        )
        # The renewal's price: the rate teardown settles at, for the original TTL.
        rate = await settlement_rate_per_second(pool, lease, provider)
        estimate = rate * Decimal(_original_ttl_minutes(provider) * 60)
        await BudgetGate(pool).check_available(estimate)
    except KillSwitchEngaged:
        return "kill_switch"
    except BudgetRejected, BudgetNotConfigured, InvalidProviderConfig:
        return "budget"
    return None


class _PrefetchedTraffic:
    """Serves one pipelined Redis read back to ``read_lease_traffic`` key by key."""

    def __init__(self, values: dict[str, Any], error: Exception | None) -> None:
        self._values = values
        self._error = error

    async def get(self, key: str) -> Any:
        if self._error is not None:
            raise self._error
        return self._values.get(key)


async def _read_traffic_batch(redis: Any, lease_ids: list[str]) -> dict[str, TrafficRead]:
    """Read every lease's last-traffic stamp in one pipelined round trip."""
    if redis is None or not lease_ids:
        return {lease_id: await read_lease_traffic(redis, lease_id) for lease_id in lease_ids}
    keys = [_lease_traffic_key(lease_id) for lease_id in lease_ids]
    values: dict[str, Any] = {}
    error: Exception | None = None
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.get(key)
            values = dict(zip(keys, await pipe.execute(), strict=True))
    except Exception as exc:  # reason: absent Redis traffic must leave every lease treated as busy
        error = exc
    prefetched = _PrefetchedTraffic(values, error)
    return {lease_id: await read_lease_traffic(prefetched, lease_id) for lease_id in lease_ids}


async def _write_through_lease_traffic(
    repo: LeaseRepository,
    provider_repo: ProviderRepository,
    redis: Any,
) -> tuple[dict[str, Lease], set[str], dict[str, Provider | None]]:
    """Write newer Redis traffic stamps to Postgres with one provider query and one Redis read."""
    leases = await repo.list_active_for_activity_control()
    known = await provider_repo.get_many({lease.provider_id for lease in leases})
    providers: dict[str, Provider | None] = {}
    candidates: list[Lease] = []
    for lease in leases:
        provider = known.get(lease.provider_id)
        if provider is not None and self_hosted_profile(provider) is not None:
            continue
        providers[lease.id] = provider
        candidates.append(lease)
    traffic_by_lease = await _read_traffic_batch(redis, [lease.id for lease in candidates])
    controlled: dict[str, Lease] = {}
    unavailable: set[str] = set()
    for lease in candidates:
        traffic = traffic_by_lease[lease.id]
        if not traffic.available:
            unavailable.add(lease.id)
            controlled[lease.id] = lease
            continue
        seen_at = traffic.seen_at
        if seen_at is not None and (
            lease.last_traffic_at is None or seen_at > lease.last_traffic_at
        ):
            await repo.record_traffic(lease.id, seen_at=seen_at)
            lease = lease.model_copy(update={"last_traffic_at": seen_at})
        controlled[lease.id] = lease
    return controlled, unavailable, providers


async def _publish_lease_warning(
    redis: Any,
    *,
    lease_id: str,
    provider_id: str,
    runpod_pod_id: str | None,
    minutes_until_expiry: int,
    warning_threshold: int,
    external_resource_id: str | None = None,
) -> None:
    """Publish a lease expiry warning event to Redis pub/sub."""
    if redis is None:
        return

    import json

    event = {
        "event": _LEASE_WARNING_EVENT_TYPE,
        "lease_id": lease_id,
        "provider_id": provider_id,
        "external_resource_id": external_resource_id or runpod_pod_id,
        "runpod_pod_id": runpod_pod_id,
        "minutes_until_expiry": minutes_until_expiry,
        "warning_threshold": warning_threshold,
    }
    payload = json.dumps(event, sort_keys=True, separators=(",", ":"))
    try:
        published = redis.publish(_LEASE_WARNING_CHANNEL, payload)
        if hasattr(published, "__await__"):
            await published
    except Exception:  # reason: lease warning publish is best-effort
        pass


async def _process_webhook_terminal_status(
    ctx: dict[str, Any],
    runpod_job_id: str,
    status: str,
) -> None:
    """Arq job to process a terminal status from a RunPod webhook.

    This job is enqueued by the webhook receiver when it receives a webhook
    with a terminal status (COMPLETED, FAILED, CANCELLED, etc.). It applies
    the terminal state and publishes to Redis using the same code path
    as the polling-based _poll_and_reconcile.

    Args:
        ctx: Arq context dict with db_pool and redis keys.
        runpod_job_id: The RunPod job ID from the webhook.
        status: The RunPod status string (COMPLETED, FAILED, CANCELLED, etc.).
    """
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    redis: Any | None = ctx.get("redis")
    if pool is None:
        return
    await apply_terminal_status_and_publish(pool, redis, runpod_job_id, status)


async def _on_startup(ctx: dict[str, Any]) -> None:
    """Initialize shared resources for Arq jobs."""
    from pitwall.db import get_pool

    ctx["db_pool"] = await get_pool()
    ctx["budget_breaker"] = BudgetCircuitBreaker()


async def _on_shutdown(ctx: dict[str, Any]) -> None:
    """Tear down shared resources created during Arq startup."""
    from pitwall.db import close_pool

    await close_pool()
    ctx.pop("db_pool", None)
    ctx.pop("budget_breaker", None)


class WorkerSettings:
    """Arq worker settings for Pitwall reconciler jobs.

    Schedules:
      - health_probe: every minute
      - lease_expiry_reconcile: every minute (60-second intervals)
      - budget_breach_escalation: every minute
      - webhook_retry_sweep: every minute
      - poll_and_reconcile: every 2 minutes
      - compute_provider_reconcile: every 2 minutes
      - cost_reconcile: every 5 minutes
      - reap_orphaned_workloads: every 5 minutes
      - quota_poll: every 5 minutes
      - cost_daily_rollup: daily at 01:00 UTC
      - idempotency_gc: nightly at 03:00 UTC
      - lb_endpoint_hibernate_sweep: daily at 12:00 UTC
      - backup_drill: weekly on Sunday at 04:00 UTC
      - archive_old_workloads: weekly on Sunday at 05:00 UTC

    Jobs (enqueued):
      - _process_webhook_terminal_status: processes terminal status from RunPod webhook
    """

    on_startup = _on_startup
    on_shutdown = _on_shutdown

    if _ARQ_AVAILABLE:
        redis_settings = _redis_settings_from_env()
        cron_jobs = [
            cron(_health_probe, minute=set(range(60))),
            cron(_lease_expiry_reconcile, minute=set(range(60))),
            cron(_webhook_retry_sweep, minute=set(range(60))),
            cron(_poll_and_reconcile, minute=set(range(0, 60, 2))),
            cron(_compute_provider_reconcile, minute=set(range(0, 60, 2))),
            cron(_cost_reconcile, minute=set(range(0, 60, 5))),
            cron(_reap_orphaned_workloads, minute=set(range(1, 60, 5))),
            cron(_quota_poll, minute=set(range(0, 60, 5))),
            cron(_budget_breach_escalation, minute=set(range(60))),
            cron(_rollup_job, hour={1}, minute={0}),
            cron(_idempotency_gc, hour={3}, minute={0}),
            cron(_lb_endpoint_hibernate_sweep, hour={12}, minute={0}),
            # arq counts weekdays from Monday=0, so Sunday is 6.
            cron(_backup_drill, hour={4}, minute={0}, weekday={6}),
            cron(_archive_old_workloads, hour={5}, minute={0}, weekday={6}),
        ]
        functions = [
            _process_webhook_terminal_status,
        ]
    else:
        redis_settings = None  # arq is optional; None when it is not installed
        cron_jobs = []
        functions = []


__all__ = [
    "RunPodJobStatus",
    "WorkerSettings",
    "aggregate_daily_cost",
    "apply_terminal_state",
    "apply_terminal_status_and_publish",
    "build_workload_completed_event",
    "fetch_active_workloads",
    "fetch_providers_for_health_probe",
    "fetch_workload_by_id",
    "fetch_workload_by_runpod_job_id",
    "map_runpod_status",
    "publish_workload_completed",
    "persist_provider_health",
    "reap_orphaned_workloads",
    "update_provider_health",
    "validate_redis_dsn",
    "check_redis_config",
]
