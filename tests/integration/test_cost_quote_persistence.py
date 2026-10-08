"""Real-Postgres proof that admission preserves the complete COST-01 quote."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from pitwall.core.cost_reporting import recent_workloads_read
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
from pitwall.core.models import Capability
from pitwall.cost.budget_gate import BudgetGate
from pitwall.cost.estimator import CostQuote, PerUnitPricing
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_budget_admission_round_trips_estimate_ceiling_and_quote(pg_pool) -> None:
    now = dt.datetime.now(dt.UTC)
    capability_id = "cap-cost-quote-persistence"
    provider_id = "prov-cost-quote-persistence"
    capability = Capability(
        id=capability_id,
        name="cost.quote.persistence",
        version="1.0.0",
        class_=CapabilityClass.EMBEDDING,
        cost_mode=CostMode.PER_REQUEST,
        defaults={"execution_timeout_ms": 60_000},
        source=CapabilitySource.API,
        created_at=now,
        updated_at=now,
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities
                (id, name, version, class, cost_mode, config)
            VALUES ($1, $2, $3, 'embedding', 'per_request', '{}')
            """,
            capability_id,
            capability.name,
            capability.version,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
                (id, capability_id, name, provider_type, config, priority)
            VALUES ($1, $2, $1, 'public_endpoint', '{}', 1)
            """,
            provider_id,
            capability_id,
        )

    quote = CostQuote(
        pricing=PerUnitPricing(unit="image", rate_per_unit="0.10"),
        capability=capability,
        payload={"unit_count": "1", "max_unit_count": "6"},
    )
    gate = BudgetGate(
        pg_pool,
        monthly_budget_usd=Decimal("100"),
        per_request_max_usd=Decimal("100"),
        workload_id_factory=lambda: "wkl-cost-quote-persistence",
    )

    workload_id = await gate.try_launch(
        capability_id=capability_id,
        provider_id=provider_id,
        estimate_usd=quote,
        submitted_at=now,
    )

    async with pg_pool.acquire() as conn:
        stored = await conn.fetchrow(
            """
            SELECT cost_estimate_usd, cost_ceiling_usd, cost_quote
            FROM pitwall.workloads
            WHERE id = $1
            """,
            workload_id,
        )
    assert stored["cost_estimate_usd"] == Decimal("0.100000")
    assert stored["cost_ceiling_usd"] == Decimal("0.600000")
    assert stored["cost_quote"]["confidence"] == "bounded"

    recent = await recent_workloads_read(pg_pool, provider_id=provider_id, limit=1)
    cost = recent.workloads[0].cost
    assert cost.model == "per_unit"
    assert cost.estimate == Decimal("0.100000")
    assert cost.ceiling == Decimal("0.600000")
    assert cost.confidence == "bounded"
    assert cost.components[0].estimated_count == Decimal("1")
    assert cost.components[0].ceiling_count == Decimal("6")
    assert cost.actual_kind == "none"
