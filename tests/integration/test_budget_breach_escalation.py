"""An exhausted monthly budget escalates to the kill switch when, and only when, armed.

The reconciler evaluates the budget circuit breaker against month-to-date spend each
minute. ``armed`` fires the kill switch once for the month's breach; ``shadow`` records
that it would have; ``disabled`` (the default) reads nothing. The headroom floor comes
from ``PITWALL_BUDGET_BREACH_KILL_HEADROOM_FLOOR_USD``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from pitwall.config import PitwallSettings
from pitwall.db.kill_log import persist_kill_report
from pitwall.reconciler import BUDGET_BREACH_ACTOR, _budget_breach_escalation

pytestmark = pytest.mark.integration


class RecordingKillSwitch:
    """Records each activation to the kill log the way the real kill switch does."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.reasons: list[str] = []

    async def activate(self, reason: str) -> Any:
        self.reasons.append(reason)
        await persist_kill_report(
            self.pool,
            triggered_at=dt.datetime.now(dt.UTC),
            reason=reason,
            actor=BUDGET_BREACH_ACTOR,
            pods_terminated=0,
            total_duration_ms=0,
            errors=[],
        )
        return SimpleNamespace(reason=reason)


async def _seed_spend(conn: Any, usd: str) -> None:
    await conn.execute(
        "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config) "
        "VALUES ('cap-breach', 'llm.breach', '1.0.0', 'llm', 'per_token', '{}')"
    )
    await conn.execute(
        "INSERT INTO pitwall.providers (id, capability_id, name, provider_type, config, priority) "
        "VALUES ('prov-breach', 'cap-breach', 'Breach', 'pod_lease', '{}', 1)"
    )
    await conn.execute(
        "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, "
        "submitted_at, cost_actual_usd) VALUES ('wkl-breach', 'cap-breach', 'prov-breach', "
        "'inference', 'completed', now(), $1)",
        Decimal(usd),
    )


def _ctx(
    pool: Any, kill_switch: Any, *, mode: str, budget: str, floor: str = "0"
) -> dict[str, Any]:
    settings = PitwallSettings(
        pitwall_monthly_budget_usd=Decimal(budget),
        pitwall_per_request_max_usd=Decimal(budget),
        pitwall_budget_breach_kill_mode=mode,
        pitwall_budget_breach_kill_headroom_floor_usd=Decimal(floor),
    )
    return {"db_pool": pool, "settings": settings, "kill_switch": kill_switch}


async def test_armed_fires_once_for_an_exhausted_budget(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        await _seed_spend(conn, "5.00")
    kill_switch = RecordingKillSwitch(pg_pool)
    ctx = _ctx(pg_pool, kill_switch, mode="armed", budget="1.00")

    first = await _budget_breach_escalation(ctx)
    second = await _budget_breach_escalation(ctx)

    assert first is not None and first.fired
    assert second is None  # the month's breach already fired the switch
    assert len(kill_switch.reasons) == 1
    assert kill_switch.reasons[0].startswith("budget-breach auto-kill:")


async def test_shadow_records_the_breach_without_firing(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        await _seed_spend(conn, "5.00")
    kill_switch = RecordingKillSwitch(pg_pool)

    outcome = await _budget_breach_escalation(
        _ctx(pg_pool, kill_switch, mode="shadow", budget="1.00")
    )

    assert outcome is not None and not outcome.fired
    assert outcome.reason.startswith("SHADOW")
    assert kill_switch.reasons == []


async def test_the_headroom_floor_decides_how_close_to_the_cap_armed_fires(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        await _seed_spend(conn, "99.00")  # $1 of a $100 budget left
    kill_switch = RecordingKillSwitch(pg_pool)

    held = await _budget_breach_escalation(
        _ctx(pg_pool, kill_switch, mode="armed", budget="100.00", floor="0")
    )
    fired = await _budget_breach_escalation(
        _ctx(pg_pool, kill_switch, mode="armed", budget="100.00", floor="2")
    )

    assert held is not None and not held.fired
    assert fired is not None and fired.fired
    assert len(kill_switch.reasons) == 1


async def test_breach_uses_the_runtime_monthly_budget(pg_pool: Any) -> None:
    from pitwall.cost.budget_limits import set_limits

    async with pg_pool.acquire() as conn:
        await _seed_spend(conn, "5.00")
    await set_limits(
        pg_pool,
        monthly_budget_usd=Decimal("50"),
        per_request_max_usd=None,
        reason="raise",
        actor="cli",
    )
    kill_switch = RecordingKillSwitch(pg_pool)
    await _budget_breach_escalation(_ctx(pg_pool, kill_switch, mode="armed", budget="1.00"))
    assert (
        kill_switch.reasons == []
    )  # $5 spent is under the runtime $50 budget, although over the $1 setting
