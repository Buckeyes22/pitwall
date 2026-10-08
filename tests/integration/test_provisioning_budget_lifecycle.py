"""Real-Postgres invariants for provider provisioning budget reservations."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from pitwall.api.leases.launch import _terminalize_failed_launch, arm_serve_provider
from pitwall.core.enums import LeaseState
from pitwall.core.models import Lease
from pitwall.cost.budget_gate import BudgetGate, BudgetRejected
from pitwall.db.repository import LeaseRepository, ProviderRepository
from pitwall.providers.provisioning import mark_provision_failed
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def _seed(pool: Any, suffix: str) -> tuple[str, str]:
    capability_id = f"cap-provision-budget-{suffix}"
    provider_id = f"prov-provision-budget-{suffix}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config)
            VALUES ($1, $2, '1.0.0', 'gpu_lease', 'per_second', '{}')
            """,
            capability_id,
            capability_id,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, config, priority)
            VALUES ($1, $2, $1, 'pod_lease', '{}', 1)
            """,
            provider_id,
            capability_id,
        )
    return capability_id, provider_id


async def test_no_resource_failure_closes_exact_admission_and_releases_budget(
    pg_pool: Any,
) -> None:
    capability_id, provider_id = await _seed(pg_pool, "released")
    gate = BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("0.750000"),
        per_request_max_usd=Decimal("0.750000"),
    )
    first = await gate.try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.600000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="no-resource-released",
    )

    await mark_provision_failed(
        pg_pool,
        workload_id=first,
        now=dt.datetime.now(dt.UTC),
        reservation_released=True,
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT state, cost_actual_usd, cost_actual_provenance,
                   completed_at, error
            FROM pitwall.workloads
            WHERE id = $1
            """,
            first,
        )
    assert row["state"] == "failed"
    assert row["cost_actual_usd"] == Decimal("0.000000")
    assert row["cost_actual_provenance"] == "broker_zero_provision_failed"
    assert row["completed_at"] is not None
    assert row["error"]["type"] == "ProviderProvisionError"

    second = await gate.try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.600000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="no-resource-released-retry",
    )
    assert second != first

    # A proven-absent failure also frees its key, so a same-key retry provisions again.
    async with pg_pool.acquire() as conn:
        key = await conn.fetchval(
            "SELECT idempotency_key FROM pitwall.workloads WHERE id = $1", first
        )
    assert key is None
    retried = await gate.try_launch_admission(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.100000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="no-resource-released",
    )
    assert retried.is_new is True and retried.workload_id not in {first, second}


async def test_ambiguous_provider_failure_retains_conservative_reservation(
    pg_pool: Any,
) -> None:
    capability_id, provider_id = await _seed(pg_pool, "ambiguous")
    gate = BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("0.750000"),
        per_request_max_usd=Decimal("0.750000"),
    )
    first = await gate.try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.600000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="ambiguous-provider-create",
    )

    await mark_provision_failed(
        pg_pool,
        workload_id=first,
        now=dt.datetime.now(dt.UTC),
        reservation_released=False,
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT state, cost_actual_usd, cost_ceiling_usd,
                   cost_actual_provenance
            FROM pitwall.workloads
            WHERE id = $1
            """,
            first,
        )
    assert row["state"] == "failed"
    assert row["cost_actual_usd"] is None
    assert row["cost_ceiling_usd"] == Decimal("0.600000")
    assert row["cost_actual_provenance"] is None
    async with pg_pool.acquire() as conn:
        key = await conn.fetchval(
            "SELECT idempotency_key FROM pitwall.workloads WHERE id = $1", first
        )
    assert key == "ambiguous-provider-create"

    with pytest.raises(BudgetRejected, match="monthly_budget"):
        await gate.try_launch(
            capability_id=capability_id,
            provider_id=provider_id,
            estimate_usd=Decimal("0.200000"),
            submitted_at=dt.datetime.now(dt.UTC),
            idempotency_key="ambiguous-provider-create-retry",
        )


async def test_unpersisted_ambiguous_launch_does_not_zero_reservation(
    pg_pool: Any,
) -> None:
    """A transport failure before lease persistence cannot prove no pod exists."""
    capability_id, provider_id = await _seed(pg_pool, "unpersisted-ambiguous")
    gate = BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("0.750000"),
        per_request_max_usd=Decimal("0.750000"),
    )
    first = await gate.try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.600000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="unpersisted-ambiguous",
    )

    await _terminalize_failed_launch(
        pg_pool,
        lease_id="lease-never-persisted",
        workload_id=first,
        error=TimeoutError("provider create outcome is unknown"),
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, cost_actual_usd, cost_ceiling_usd FROM pitwall.workloads WHERE id = $1",
            first,
        )
    assert row["state"] == "failed"
    assert row["cost_actual_usd"] is None
    assert row["cost_ceiling_usd"] == Decimal("0.600000")

    with pytest.raises(BudgetRejected, match="monthly_budget"):
        await gate.try_launch(
            capability_id=capability_id,
            provider_id=provider_id,
            estimate_usd=Decimal("0.200000"),
            submitted_at=dt.datetime.now(dt.UTC),
            idempotency_key="unpersisted-ambiguous-retry",
        )


async def test_late_launch_failure_does_not_reopen_closed_lease(pg_pool: Any) -> None:
    """A delayed launch callback cannot overwrite a teardown that already won."""
    capability_id, provider_id = await _seed(pg_pool, "closed-race")
    workload_id = await BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("1.000000"),
        per_request_max_usd=Decimal("1.000000"),
    ).try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.400000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="closed-race",
    )
    created_at = dt.datetime(2026, 8, 30, 10, tzinfo=dt.UTC)
    terminated_at = dt.datetime(2026, 8, 30, 10, 1, tzinfo=dt.UTC)
    await LeaseRepository(pg_pool).create(
        Lease(
            id="lease-closed-race",
            provider_id=provider_id,
            workload_id=workload_id,
            external_resource_id="pod-closed-race",
            state=LeaseState.STOPPING,
            created_at=created_at,
            expires_at=created_at + dt.timedelta(hours=2),
            renewal_policy="manual",
            cost_accrued_usd=Decimal("0.200000"),
        )
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pitwall.leases
            SET state = 'stopped', terminated_at = $2,
                terminated_reason = 'operator_stop'
            WHERE id = $1
            """,
            "lease-closed-race",
            terminated_at,
        )

    await _terminalize_failed_launch(
        pg_pool,
        lease_id="lease-closed-race",
        workload_id=workload_id,
        error=TimeoutError("late launch callback"),
    )

    async with pg_pool.acquire() as conn:
        lease = await conn.fetchrow(
            "SELECT state, terminated_at, terminated_reason FROM pitwall.leases WHERE id = $1",
            "lease-closed-race",
        )
        workload = await conn.fetchrow(
            "SELECT state, completed_at FROM pitwall.workloads WHERE id = $1",
            workload_id,
        )
    assert tuple(lease) == ("stopped", terminated_at, "operator_stop")
    assert tuple(workload) == ("queued", None)


async def test_launch_failure_preserves_existing_provider_actual_cost(pg_pool: Any) -> None:
    """Launch cleanup must not replace an already authoritative provider cost."""
    capability_id, provider_id = await _seed(pg_pool, "provider-cost")
    workload_id = await BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("1.000000"),
        per_request_max_usd=Decimal("1.000000"),
    ).try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=Decimal("0.400000"),
        submitted_at=dt.datetime.now(dt.UTC),
        idempotency_key="provider-cost",
    )
    reconciled_at = dt.datetime(2026, 8, 30, 10, tzinfo=dt.UTC)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pitwall.workloads
            SET cost_actual_usd = $2, cost_actual_provenance = 'provider_invoice',
                cost_reconciled_at = $3
            WHERE id = $1
            """,
            workload_id,
            Decimal("0.123000"),
            reconciled_at,
        )

    created_at = dt.datetime(2026, 8, 30, 9, tzinfo=dt.UTC)
    await LeaseRepository(pg_pool).create(
        Lease(
            id="lease-provider-cost",
            provider_id=provider_id,
            workload_id=workload_id,
            external_resource_id="pod-provider-cost",
            state=LeaseState.STOPPING,
            created_at=created_at,
            expires_at=created_at + dt.timedelta(hours=2),
            renewal_policy="manual",
            cost_accrued_usd=Decimal("0.900000"),
        )
    )
    await _terminalize_failed_launch(
        pg_pool,
        lease_id="lease-provider-cost",
        workload_id=workload_id,
        error=RuntimeError("launch callback failed after provider invoice"),
    )

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT state, cost_actual_usd, cost_actual_provenance, cost_reconciled_at
            FROM pitwall.workloads WHERE id = $1
            """,
            workload_id,
        )
    assert tuple(row) == ("failed", Decimal("0.123000"), "provider_invoice", reconciled_at)


async def test_arm_provider_rolls_back_when_audit_insert_is_rejected(pg_pool: Any) -> None:
    """Provider activation and its audit row commit or roll back together."""
    _, provider_id = await _seed(pg_pool, "arm-atomicity")
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE pitwall.providers SET config = '{\"openai_proxy_port\": 8000}'::jsonb "
            "WHERE id = $1",
            provider_id,
        )

    provider = await ProviderRepository(pg_pool).get(provider_id)
    assert provider is not None
    before_health = provider.health_status
    before_config = dict(provider.config)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE pitwall.config_audit DROP CONSTRAINT config_audit_action_check"
        )
        await conn.execute(
            "ALTER TABLE pitwall.config_audit ADD CONSTRAINT config_audit_action_check "
            "CHECK (action IN ('create'))"
        )
    try:
        with pytest.raises(asyncpg.CheckViolationError):
            await arm_serve_provider(
                pg_pool,
                provider=provider,
                lease_id="lease-arm-atomicity",
                pod_id="pod-arm-atomicity",
            )
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE pitwall.config_audit DROP CONSTRAINT config_audit_action_check"
            )
            await conn.execute(
                "ALTER TABLE pitwall.config_audit ADD CONSTRAINT config_audit_action_check "
                "CHECK (action IN ('create','update','delete','enable','disable','hibernate',"
                "'patch','renew','rotate','deactivate','activate','archive','purge','stop',"
                "'lease_ready','lease_closed'))"
            )

    after = await ProviderRepository(pg_pool).get(provider_id)
    assert after is not None
    assert after.health_status == before_health
    assert after.config == before_config
    async with pg_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM pitwall.config_audit "
                "WHERE entity_id = $1 AND action = 'lease_ready'",
                provider_id,
            )
            == 0
        )
