"""Money columns refuse negative accruals and keep six decimal places (review finding #9)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_negative_lease_accrual_is_refused(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError, match="leases_cost_accrued_nonnegative"):
            await conn.execute(
                "INSERT INTO pitwall.leases (id, provider_id, runpod_pod_id, state, created_at,"
                " expires_at, renewal_policy, cost_accrued_usd)"
                " VALUES ('lease_neg', 'runpod_direct', 'pod-neg', 'stopped', now(), now() + interval '1 hour',"
                " 'manual', -0.01)"
            )


async def test_volume_monthly_cost_keeps_six_decimal_places(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.volumes"
            " (id, runpod_volume_id, name, datacenter_id, size_gb, monthly_cost_usd, config)"
            " VALUES ('vol_precise', 'rp_vol_precise', 'precise', 'US-KS-2', 10, 0.123456, '{}')"
        )
        stored = await conn.fetchval(
            "SELECT monthly_cost_usd FROM pitwall.volumes WHERE id = 'vol_precise'"
        )
    assert stored == Decimal("0.123456")
