"""Real-Postgres proof for exclusive lease/workload Pod billing identity."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from pitwall.core.enums import LeaseState
from pitwall.core.models import Lease
from pitwall.db.repository import LeaseRepository
from pitwall.runpod_market import AsyncpgRunpodActualCostReferenceRepository
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_linked_terminal_lease_is_the_only_pending_pod_actual_identity(pg_pool) -> None:
    submitted_at = dt.datetime(2026, 8, 30, 10, tzinfo=dt.UTC)
    terminated_at = dt.datetime(2026, 8, 30, 11, tzinfo=dt.UTC)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config)
            VALUES ('cap-pod-cost', 'pod.cost', '1.0.0', 'gpu_lease',
                    'per_second', '{}')
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, adapter_id, config, priority)
            VALUES ('prov-pod-cost', 'cap-pod-cost', 'RunPod cost', 'pod_lease',
                    'runpod', '{}', 1)
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, submitted_at,
                 cost_estimate_usd, cost_ceiling_usd)
            VALUES ('wkl-pod-cost', 'cap-pod-cost', 'prov-pod-cost', 'lease',
                    'queued', $1, $2, $2)
            """,
            submitted_at,
            Decimal("1.000000"),
        )
        await conn.execute(
            """
            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, submitted_at,
                 cost_estimate_usd, cost_ceiling_usd)
            VALUES ('wkl-unrelated', 'cap-pod-cost', 'prov-pod-cost', 'lease',
                    'queued', $1, $2, $2)
            """,
            submitted_at,
            Decimal("0.500000"),
        )

    repository = LeaseRepository(pg_pool)
    lease = await repository.create(
        Lease(
            id="lease-pod-cost",
            provider_id="prov-pod-cost",
            workload_id="wkl-pod-cost",
            external_resource_id="pod-exact-cost",
            state=LeaseState.STOPPING,
            created_at=submitted_at,
            expires_at=submitted_at + dt.timedelta(hours=2),
            renewal_policy="manual",
        )
    )
    assert lease.workload_id == "wkl-pod-cost"
    await repository.mark_linked_workload_running(
        lease.id,
        started_at=submitted_at + dt.timedelta(minutes=1),
    )
    closed = await repository.close_teardown(
        lease.id,
        state=LeaseState.STOPPED.value,
        cost_accrued_usd=Decimal("0.750000"),
        terminated_at=terminated_at,
        terminated_reason="operator_stop",
    )
    assert closed is not None
    retried = await repository.close_teardown(
        lease.id,
        state=LeaseState.STOPPED.value,
        cost_accrued_usd=Decimal("0.750000"),
        terminated_at=terminated_at,
        terminated_reason="operator_stop",
    )
    assert retried is None  # a closed lease is never closed twice

    batches = await AsyncpgRunpodActualCostReferenceRepository(pg_pool).pending_batches(
        now=terminated_at
    )
    assert len(batches) == 1
    assert batches[0].provider_id == "prov-pod-cost"
    assert batches[0].references[0].workload_id == "wkl-pod-cost"
    assert batches[0].references[0].external_resource_id == "pod-exact-cost"

    async with pg_pool.acquire() as conn:
        workload = await conn.fetchrow(
            """
            SELECT state, started_at, completed_at, cost_actual_usd,
                   cost_actual_provenance, cost_reconciled_at
            FROM pitwall.workloads
            WHERE id = $1
            """,
            "wkl-pod-cost",
        )
        assert tuple(workload) == (
            "completed",
            submitted_at + dt.timedelta(minutes=1),
            terminated_at,
            Decimal("0.750000"),
            "lease_teardown",
            terminated_at,
        )
        unrelated = await conn.fetchrow(
            "SELECT state, cost_actual_usd, cost_actual_provenance "
            "FROM pitwall.workloads WHERE id = $1",
            "wkl-unrelated",
        )
        assert tuple(unrelated) == ("queued", None, None)
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO pitwall.leases
                    (id, provider_id, workload_id, external_resource_id, state,
                     created_at, expires_at, renewal_policy)
                VALUES ('lease-duplicate', 'prov-pod-cost', 'wkl-pod-cost',
                        'pod-other', 'stopping', $1, $2, 'manual')
                """,
                submitted_at,
                submitted_at + dt.timedelta(hours=1),
            )


async def test_teardown_reconciles_cost_for_terminal_linked_workload_without_state_change(
    pg_pool: Any,
) -> None:
    """A late lease close fills cost on a terminal workload without reopening it."""
    submitted_at = dt.datetime(2026, 8, 30, 10, tzinfo=dt.UTC)
    failed_at = dt.datetime(2026, 8, 30, 10, 1, tzinfo=dt.UTC)
    terminated_at = dt.datetime(2026, 8, 30, 11, tzinfo=dt.UTC)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config)
            VALUES ('cap-pod-late-cost', 'pod.late.cost', '1.0.0', 'gpu_lease',
                    'per_second', '{}')
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, config, priority)
            VALUES ('prov-pod-late-cost', 'cap-pod-late-cost', 'RunPod late cost',
                    'pod_lease', '{}', 1)
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, submitted_at,
                 completed_at, cost_estimate_usd, cost_ceiling_usd)
            VALUES ('wkl-pod-late-cost', 'cap-pod-late-cost', 'prov-pod-late-cost',
                    'lease', 'failed', $1, $2, $3, $3)
            """,
            submitted_at,
            failed_at,
            Decimal("1.000000"),
        )

    repository = LeaseRepository(pg_pool)
    await repository.create(
        Lease(
            id="lease-pod-late-cost",
            provider_id="prov-pod-late-cost",
            workload_id="wkl-pod-late-cost",
            external_resource_id="pod-late-cost",
            state=LeaseState.STOPPING,
            created_at=submitted_at,
            expires_at=submitted_at + dt.timedelta(hours=2),
            renewal_policy="manual",
        )
    )
    closed = await repository.close_teardown(
        "lease-pod-late-cost",
        state=LeaseState.STOPPED.value,
        cost_accrued_usd=Decimal("0.400000"),
        terminated_at=terminated_at,
        terminated_reason="provider_create_recovery",
    )
    assert closed is not None

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT state, completed_at, cost_actual_usd,
                   cost_actual_provenance, cost_reconciled_at
            FROM pitwall.workloads
            WHERE id = 'wkl-pod-late-cost'
            """
        )
    assert tuple(row) == (
        "failed",
        failed_at,
        Decimal("0.400000"),
        "lease_teardown",
        terminated_at,
    )
