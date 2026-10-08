"""Runtime budget limits: one audited database row that overrides the environment defaults.

Every budget reader resolves limits through this module at use time, so a change takes
effect on the next admission without restarting any process.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

_MAX_LIMIT_USD = Decimal("100000000")
_LIMIT_QUANTUM = Decimal("0.000001")

BUDGET_LIMITS_SQL = (
    "SELECT monthly_budget_usd, per_request_max_usd, updated_at, updated_by, reason"
    " FROM pitwall.budget_limits WHERE id = 1"
)
_UPSERT_SQL = """
    INSERT INTO pitwall.budget_limits (id, monthly_budget_usd, per_request_max_usd, updated_at, updated_by, reason)
    VALUES (1, $1, $2, now(), $3, $4)
    ON CONFLICT (id) DO UPDATE SET
        monthly_budget_usd = EXCLUDED.monthly_budget_usd,
        per_request_max_usd = EXCLUDED.per_request_max_usd,
        updated_at = EXCLUDED.updated_at,
        updated_by = EXCLUDED.updated_by,
        reason = EXCLUDED.reason
    RETURNING monthly_budget_usd, per_request_max_usd, updated_at, updated_by, reason
"""


class BudgetLimitsError(ValueError):
    error_code = "invalid_budget_limits"
    status_code = 422


@dataclass(frozen=True, slots=True)
class BudgetLimits:
    monthly_budget_usd: Decimal
    per_request_max_usd: Decimal
    source: Literal["runtime", "environment"]
    updated_at: dt.datetime | None = None
    updated_by: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "monthly_budget_usd": _text(self.monthly_budget_usd),
            "per_request_max_usd": _text(self.per_request_max_usd),
            "source": self.source,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
            "reason": self.reason,
        }


def _text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _from_row(row: Any) -> BudgetLimits:
    return BudgetLimits(
        monthly_budget_usd=Decimal(str(row["monthly_budget_usd"])),
        per_request_max_usd=Decimal(str(row["per_request_max_usd"])),
        source="runtime",
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
        reason=row["reason"],
    )


async def read_limits(
    conn: Any, *, default_monthly: Decimal, default_per_request: Decimal
) -> BudgetLimits:
    row = await conn.fetchrow(BUDGET_LIMITS_SQL)
    if row is None:
        return BudgetLimits(default_monthly, default_per_request, "environment")
    return _from_row(row)


def environment_defaults() -> tuple[Decimal, Decimal]:
    from pitwall.config import get_settings

    settings = get_settings()
    return Decimal(str(settings.pitwall_monthly_budget_usd)), Decimal(
        str(settings.pitwall_per_request_max_usd)
    )


async def effective_limits(
    pool: Any,
    *,
    default_monthly: Decimal | None = None,
    default_per_request: Decimal | None = None,
) -> BudgetLimits:
    if default_monthly is None or default_per_request is None:
        env_monthly, env_per_request = environment_defaults()
        default_monthly = env_monthly if default_monthly is None else default_monthly
        default_per_request = (
            env_per_request if default_per_request is None else default_per_request
        )
    async with pool.acquire() as conn:
        return await read_limits(
            conn, default_monthly=default_monthly, default_per_request=default_per_request
        )


def _positive(value: Decimal | str | int | float, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BudgetLimitsError(f"{name} must be a positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise BudgetLimitsError(f"{name} must be a positive decimal")
    # The columns are NUMERIC(14, 6) with a > 0 check: a larger value overflows and a
    # smaller one rounds to zero, and either would fail in the database instead.
    stored = parsed.quantize(_LIMIT_QUANTUM, ROUND_HALF_UP) if parsed < _MAX_LIMIT_USD else parsed
    if not 0 < stored < _MAX_LIMIT_USD:
        raise BudgetLimitsError(f"{name} must be between 0.000001 and 99999999.999999")
    return parsed


async def effective_monthly_budget(pool: Any, *, default: Decimal) -> Decimal:
    """The monthly budget in force: the runtime row's value, else ``default``."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(BUDGET_LIMITS_SQL)
    return default if row is None else Decimal(str(row["monthly_budget_usd"]))


async def set_limits(
    pool: Any,
    *,
    monthly_budget_usd: Decimal | str | None,
    per_request_max_usd: Decimal | str | None,
    reason: str,
    actor: str,
) -> BudgetLimits:
    if monthly_budget_usd is None and per_request_max_usd is None:
        raise BudgetLimitsError("set at least one of monthly_budget_usd or per_request_max_usd")
    monthly = (
        None if monthly_budget_usd is None else _positive(monthly_budget_usd, "monthly_budget_usd")
    )
    per_request = (
        None
        if per_request_max_usd is None
        else _positive(per_request_max_usd, "per_request_max_usd")
    )
    reason = (reason or "").strip()
    if not reason:
        raise BudgetLimitsError("a reason is required for every budget change")
    if "\x00" in reason:
        raise BudgetLimitsError("the reason must not contain NUL characters")
    from pitwall.cost.budget_gate import PITWALL_BUDGET_LOCK_KEY
    from pitwall.db.repository import insert_audit

    env_monthly, env_per_request = environment_defaults()
    async with pool.acquire() as conn, conn.transaction():
        # Same lock as admission: a change and an admission never interleave.
        await conn.execute("SELECT pg_advisory_xact_lock($1)", PITWALL_BUDGET_LOCK_KEY)
        current = await read_limits(
            conn, default_monthly=env_monthly, default_per_request=env_per_request
        )
        row = await conn.fetchrow(
            _UPSERT_SQL,
            monthly if monthly is not None else current.monthly_budget_usd,
            per_request if per_request is not None else current.per_request_max_usd,
            actor,
            reason,
        )
        updated = _from_row(row)
        await insert_audit(
            pool,
            actor=actor,
            action="budget_limits.set",
            entity_type="budget_limits",
            entity_id="global",
            old_value=dict(current.to_dict()),
            new_value=dict(updated.to_dict()),
            change_reason=reason,
            conn=conn,
        )
    return updated


async def budget_status(pool: Any) -> dict[str, Any]:
    from pitwall.cost.budget_gate import month_to_date_spend

    env_monthly, env_per_request = environment_defaults()
    async with pool.acquire() as conn:
        limits = await read_limits(
            conn, default_monthly=env_monthly, default_per_request=env_per_request
        )
        spend = await month_to_date_spend(conn)
    remaining = max(limits.monthly_budget_usd - spend, Decimal("0"))
    return {
        **limits.to_dict(),
        "mtd_spend_usd": _text(spend),
        "budget_remaining_usd": _text(remaining),
    }


__all__ = [
    "BUDGET_LIMITS_SQL",
    "BudgetLimits",
    "BudgetLimitsError",
    "budget_status",
    "effective_limits",
    "environment_defaults",
    "read_limits",
    "set_limits",
]
