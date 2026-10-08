"""Runtime budget limits against real Postgres: persistence, audit, and lock ordering."""

from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.config import get_settings
from pitwall.cost.budget_limits import budget_status, effective_limits, set_limits

pytestmark = pytest.mark.integration


async def test_set_limits_persists_audits_and_keeps_the_other_value(pg_pool, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    # The audit's old_value must be the limits in effect before the change; pin the
    # environment the resolution reads (5/10) instead of relying on ambient settings.
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "5")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "10")
    get_settings.cache_clear()
    before = await effective_limits(
        pg_pool, default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert before.source == "environment"
    after = await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("50"),
        per_request_max_usd=None,
        reason="4x4090 batch",
        actor="mcp",
    )
    assert (after.monthly_budget_usd, after.per_request_max_usd, after.source) == (
        Decimal("50"),
        Decimal("10"),
        "runtime",
    )
    again = await effective_limits(
        pg_pool, default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert again.monthly_budget_usd == Decimal("50")
    async with pg_pool.acquire() as conn:
        audit = await conn.fetchrow(
            "SELECT actor, action, entity_id, change_reason, old_value, new_value FROM pitwall.config_audit"
            " WHERE entity_type = 'budget_limits' ORDER BY id DESC LIMIT 1"
        )
    assert (audit["actor"], audit["action"], audit["entity_id"], audit["change_reason"]) == (
        "mcp",
        "budget_limits.set",
        "global",
        "4x4090 batch",
    )
    assert (
        audit["old_value"]["monthly_budget_usd"] == "5"
        and audit["new_value"]["monthly_budget_usd"] == "50"
    )


async def test_budget_status_reports_limits_and_spend(pg_pool, monkeypatch) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "5")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "10")
    get_settings.cache_clear()
    status = await budget_status(pg_pool)
    assert status["monthly_budget_usd"] == "5" and status["source"] == "environment"
    assert status["mtd_spend_usd"] == "0" and status["budget_remaining_usd"] == "5"
