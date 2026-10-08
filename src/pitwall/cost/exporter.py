"""Pitwall cost exporter — Prometheus-style metrics endpoint.

Runs as ``python -m pitwall.cost`` (or ``pitwall-cost-exporter``) on port 9109. It
refuses to start without ``PITWALL_MONTHLY_BUDGET_USD``, the same setting the budget gate needs.

Exposes:
- ``pitwall_cloud_spend_month_usd`` — monthly cloud spend (USD)
- ``pitwall_cloud_budget_pct`` — percent of monthly budget consumed
- ``pitwall_active_workers`` — active lease count from DB
- ``pitwall_kill_log_triggers_7d`` — kill-switch activations in the last 7 days
- ``pitwall_providers_unhealthy`` — count of providers with health_status = 'unhealthy'
- ``pitwall_workload_queue_depth`` — queued workload count
- ``pitwall_reconciliation_lag_seconds`` — age of the oldest queued/running workload
- ``pitwall_webhook_delivery_retries_due`` — outbound retries currently due
- ``pitwall_webhook_delivery_terminal_failures_24h`` — terminal delivery failures in 24h
- ``pitwall_provider_spend_month_usd`` — monthly spend by provider
- ``pitwall_retention_last_success_timestamp_seconds`` — latest completed retention run
- ``pitwall_retention_last_deleted_count`` — rows deleted by that run

State source: Postgres ``pitwall.leases`` (active count), ``pitwall.kill_log`` (triggers), ``pitwall.providers`` (health status).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

import asyncpg
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from starlette.responses import JSONResponse, Response

from pitwall.config import require_runtime_env
from pitwall.cost.budget_gate import (
    MONTH_TO_DATE_WHERE,
    WORKLOAD_SPEND_EXPR,
    BudgetNotConfigured,
    month_to_date_spend,
)
from pitwall.cost.budget_limits import effective_monthly_budget
from pitwall.db import close_pool, get_pool
from pitwall.security.redaction import configure_logging_redaction

configure_logging_redaction()
require_runtime_env("cost-exporter")

log = logging.getLogger("pitwall.cost_exporter")

cloud_spend_month_usd = Gauge(
    "pitwall_cloud_spend_month_usd",
    "Cumulative monthly cloud spend (USD)",
)
cloud_budget_pct = Gauge("pitwall_cloud_budget_pct", "% of monthly budget consumed")
cloud_budget_usd = Gauge("pitwall_cloud_budget_usd", "Monthly budget (USD)")
active_workers = Gauge(
    "pitwall_active_workers",
    "Active lease count from pitwall.leases",
    ["provider"],
)
kill_log_triggers_7d = Gauge(
    "pitwall_kill_log_triggers_7d",
    "Kill-switch activations in the last 7 days",
)
providers_unhealthy = Gauge(
    "pitwall_providers_unhealthy",
    "Count of providers with health_status = 'unhealthy'",
)
workload_queue_depth = Gauge(
    "pitwall_workload_queue_depth", "Count of workloads waiting for reconciliation"
)
reconciliation_lag_seconds = Gauge(
    "pitwall_reconciliation_lag_seconds",
    "Age in seconds of the oldest queued or running workload",
)
webhook_delivery_retries_due = Gauge(
    "pitwall_webhook_delivery_retries_due",
    "Count of outbound webhook retry attempts currently due",
)
webhook_delivery_terminal_failures_24h = Gauge(
    "pitwall_webhook_delivery_terminal_failures_24h",
    "Count of terminal outbound webhook delivery failures in the last 24 hours",
)
provider_spend_month_usd = Gauge(
    "pitwall_provider_spend_month_usd",
    "Cumulative monthly workload spend by provider (USD)",
    ["provider"],
)
retention_last_success_timestamp_seconds = Gauge(
    "pitwall_retention_last_success_timestamp_seconds",
    "Unix timestamp of the latest completed retention run",
)
retention_last_deleted_count = Gauge(
    "pitwall_retention_last_deleted_count",
    "Rows deleted by the latest completed retention run",
)
free_pool_headroom = Gauge(
    "pitwall_free_pool_headroom",
    "Remaining share of a free pool's window (0..1)",
    ["provider", "pool"],
)
free_pool_reset_seconds = Gauge(
    "pitwall_free_pool_reset_seconds",
    "Seconds until a free pool's window resets",
    ["provider", "pool"],
)
free_pool_locked = Gauge(
    "pitwall_free_pool_locked",
    "1 when a (provider, model) tuple is locked out",
    ["provider", "model"],
)
free_tokens_used_month = Gauge(
    "pitwall_free_tokens_used_month",
    "Free tokens consumed this window",
    ["pool"],
)
free_pool_429_total = Counter(
    "pitwall_free_pool_429_total",
    "Quota/rate-limit signals by reason",
    ["provider", "model", "reason"],
)


# Lockouts are recorded by the API process and persisted to provider_quotas.evidence; this
# process reads that copy. The counter advances by the persisted failure delta per key, so a
# 429 the API saw shows up here on the next refresh.
_seen_failures: dict[tuple[str, str], int] = {}

BUDGET_ENV = "PITWALL_MONTHLY_BUDGET_USD"


def configured_monthly_budget(environ: Mapping[str, str] | None = None) -> Decimal:
    """The monthly budget the exporter starts with; refuses to guess one.

    Read as the budget gate reads its environment default (a set, positive ``Decimal``), so a
    missing value stops the service instead of publishing a made-up budget. A runtime limits
    row (``pitwall budget set``) still overrides it on every refresh.
    """
    raw = (os.environ if environ is None else environ).get(BUDGET_ENV, "")
    if not raw.strip():
        raise BudgetNotConfigured(f"{BUDGET_ENV} must be set")
    try:
        budget = Decimal(raw.strip())
    except ArithmeticError as exc:
        raise BudgetNotConfigured(f"{BUDGET_ENV} must be a decimal value") from exc
    if not budget.is_finite() or budget <= 0:
        raise BudgetNotConfigured(f"{BUDGET_ENV} must be a positive decimal value")
    return budget


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    budget = configured_monthly_budget()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL is not set")
        raise SystemExit(1)
    pool = await get_pool(database_url, min_size=1, max_size=3)
    app.state.pool = pool
    app.state.budget = budget
    cloud_budget_usd.set(float(budget))
    app.state._poll_task = asyncio.create_task(_poll_loop(app))
    try:
        yield
    finally:
        app.state._poll_task.cancel()
        await close_pool()


async def _poll_loop(app: FastAPI) -> None:
    while True:
        try:
            await _refresh(app)
        except Exception as exc:  # reason: poll loop must survive any refresh failure and retry
            log.exception("refresh failed: %s", exc)
        await asyncio.sleep(60)


async def _refresh(app: FastAPI) -> None:
    pool: asyncpg.Pool = app.state.pool

    async with pool.acquire() as conn:
        total_spend = await month_to_date_spend(conn)
        active_count_rows = await conn.fetch(
            """SELECT p.name AS provider, COUNT(l.id) AS cnt
               FROM pitwall.leases l
               JOIN pitwall.providers p ON l.provider_id = p.id
               WHERE l.state = 'active'
               GROUP BY p.name"""
        )
        kills = await conn.fetchval(
            "SELECT COUNT(*) FROM pitwall.kill_log WHERE triggered_at > now() - interval '7 days'"
        )
        unhealthy_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pitwall.providers WHERE health_status = 'unhealthy'"
        )
        queued_count = await conn.fetchval(
            "SELECT COUNT(*) FROM pitwall.workloads WHERE state = 'queued'"
        )
        reconciliation_lag = await conn.fetchval(
            """SELECT COALESCE(
                   EXTRACT(EPOCH FROM (now() - MIN(submitted_at))), 0
               )
               FROM pitwall.workloads
               WHERE state IN ('queued', 'running')"""
        )
        webhook_delivery = await conn.fetchrow(
            """SELECT
                 COUNT(*) FILTER (
                   WHERE next_retry_at IS NOT NULL AND next_retry_at <= now()
                 ) AS retries_due,
                 COUNT(*) FILTER (
                   WHERE next_retry_at IS NULL
                     AND attempted_at > now() - interval '24 hours'
                 ) AS terminal_failures_24h
               FROM pitwall.webhook_delivery_failures"""
        )
        # LEFT JOIN: raw-pod workloads (runpod_direct) have no provider row but still bill.
        provider_spend_rows = await conn.fetch(
            f"""SELECT COALESCE(p.name, w.provider_id) AS provider,
                       COALESCE(SUM({WORKLOAD_SPEND_EXPR}), 0) AS spend
                FROM pitwall.workloads w
                LEFT JOIN pitwall.providers p ON w.provider_id = p.id
                WHERE {MONTH_TO_DATE_WHERE}
                GROUP BY COALESCE(p.name, w.provider_id)"""
        )
        retention_run = await conn.fetchrow(
            """SELECT EXTRACT(EPOCH FROM completed_at) AS completed_timestamp,
                      deleted_count
               FROM pitwall.retention_runs
               WHERE status = 'completed'
               ORDER BY completed_at DESC
               LIMIT 1"""
        )
        quota_rows = await conn.fetch(
            "SELECT provider_id, pool_key, budget_units, used_units, reset_at, evidence "
            "FROM pitwall.provider_quotas"
        )

    for row in quota_rows:
        budget = Decimal(row["budget_units"]) if row["budget_units"] else None
        used = Decimal(row["used_units"])
        headroom = float((budget - used) / budget) if budget else 1.0
        free_pool_headroom.labels(provider=row["provider_id"], pool=row["pool_key"]).set(
            max(0.0, min(1.0, headroom))
        )
        seconds = (
            (row["reset_at"] - dt.datetime.now(dt.UTC)).total_seconds() if row["reset_at"] else 0.0
        )
        free_pool_reset_seconds.labels(provider=row["provider_id"], pool=row["pool_key"]).set(
            max(0.0, seconds)
        )
        free_tokens_used_month.labels(pool=row["pool_key"] or row["provider_id"]).set(float(used))

    cloud_spend_month_usd.set(float(total_spend))
    # The budget in force: a runtime limits row (pitwall budget set) overrides the startup value.
    monthly_budget = await effective_monthly_budget(pool, default=app.state.budget)
    cloud_budget_usd.set(float(monthly_budget))
    cloud_budget_pct.set(float(total_spend / monthly_budget * 100) if monthly_budget else 0.0)

    active_workers.clear()
    for row in active_count_rows:
        active_workers.labels(provider=row["provider"]).set(row["cnt"])

    kill_log_triggers_7d.set(int(kills or 0))
    providers_unhealthy.set(int(unhealthy_count or 0))
    workload_queue_depth.set(int(queued_count or 0))
    reconciliation_lag_seconds.set(float(reconciliation_lag or 0))
    webhook_delivery_retries_due.set(int(webhook_delivery["retries_due"] or 0))
    webhook_delivery_terminal_failures_24h.set(int(webhook_delivery["terminal_failures_24h"] or 0))

    provider_spend_month_usd.clear()
    for row in provider_spend_rows:
        provider_spend_month_usd.labels(provider=row["provider"]).set(float(row["spend"] or 0))

    retention_last_success_timestamp_seconds.set(
        float(retention_run["completed_timestamp"] or 0) if retention_run else 0
    )
    retention_last_deleted_count.set(
        int(retention_run["deleted_count"] or 0) if retention_run else 0
    )

    free_pool_locked.clear()
    now = dt.datetime.now(dt.UTC)
    for row in quota_rows:
        state = _persisted_lockout(row)
        if state is None:
            continue
        provider, model = row["provider_id"], str(state["model_id"])
        free_pool_locked.labels(provider=provider, model=model).set(
            1 if _lockout_active(state, now) else 0
        )
        failures = int(state.get("failures") or 0)
        seen = _seen_failures.get((provider, model), 0)
        if failures > seen:
            free_pool_429_total.labels(
                provider=provider, model=model, reason=str(state.get("reason") or "unknown")
            ).inc(failures - seen)
        _seen_failures[(provider, model)] = failures


def _persisted_lockout(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    evidence = row.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            return None
    if not isinstance(evidence, Mapping):
        return None
    state = evidence.get("lockout")
    if not isinstance(state, Mapping) or not state.get("model_id"):
        return None
    return state


def _lockout_active(state: Mapping[str, Any], now: dt.datetime) -> bool:
    if state.get("permanent"):
        return True
    locked_until = state.get("locked_until")
    if not locked_until:
        return False
    try:
        until = dt.datetime.fromisoformat(str(locked_until))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=dt.UTC)
    return now < until


app = FastAPI(lifespan=lifespan, title="Pitwall Cost Exporter", version="1")


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "cost-exporter"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "cost-exporter"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    """Return success only when the metrics source database is reachable."""

    pool: asyncpg.Pool | None = getattr(app.state, "pool", None)
    postgres: dict[str, Any]
    try:
        if pool is None:
            raise RuntimeError("pool unavailable")
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        postgres = {"ok": True}
    except Exception:  # pragma: no cover  # reason: dependency failures are reported, not raised
        postgres = {"ok": False, "error": "unavailable"}
    ok = bool(postgres["ok"])
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"ok": ok, "postgres": postgres},
    )


if __name__ == "__main__":
    # ``python -m pitwall.cost.exporter``: uvicorn imports "pitwall.cost.exporter:app" by name, so
    # register this module under that name first or the metrics would be defined twice.
    import sys

    from pitwall.cost.__main__ import main

    sys.modules["pitwall.cost.exporter"] = sys.modules["__main__"]
    main()
