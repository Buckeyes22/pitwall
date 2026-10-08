"""Atomic cost admission gate for Pitwall workloads."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pitwall.cost.budget_limits import BudgetLimits, read_limits

log = logging.getLogger("pitwall.cost.budget_gate")

PITWALL_BUDGET_LOCK_KEY = int.from_bytes(b"PITWBUDG", "big")
PITWALL_BUDGET_GATE_LOCK_KEY = PITWALL_BUDGET_LOCK_KEY
#: The one definition of a workload's spend, used by every month-to-date reader: the actual
#: cost once known, else the admitted ceiling, else the estimate. Every state counts, because
#: admitted provider spend stays billable when a workload fails or is cancelled.
WORKLOAD_SPEND_EXPR = "COALESCE(cost_actual_usd, cost_ceiling_usd, cost_estimate_usd)"
#: Workloads submitted in the current UTC calendar month. The month start is a UTC wall clock
#: turned into an absolute instant with ``AT TIME ZONE 'UTC'``, so a bare comparison against the
#: ``timestamptz`` column never reinterprets it in the session time zone, and the plain
#: ``submitted_at`` index stays usable.
MONTH_TO_DATE_WHERE = (
    "submitted_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
)
MONTH_TO_DATE_SPEND_SQL = (
    f"SELECT COALESCE(SUM({WORKLOAD_SPEND_EXPR}), 0) AS s"
    " FROM pitwall.workloads"
    f" WHERE {MONTH_TO_DATE_WHERE}"
)
#: Spend in the UTC calendar month containing ``$1`` (same expression, same states).
MONTH_SPEND_AT_SQL = (
    f"SELECT COALESCE(SUM({WORKLOAD_SPEND_EXPR}), 0) AS s"
    " FROM pitwall.workloads"
    " WHERE submitted_at >= date_trunc('month', $1::timestamptz AT TIME ZONE 'UTC')"
    " AT TIME ZONE 'UTC'"
    " AND submitted_at < (date_trunc('month', $1::timestamptz AT TIME ZONE 'UTC')"
    " + interval '1 month') AT TIME ZONE 'UTC'"
)

BudgetRejectionReason = Literal["monthly_budget", "per_request_cap"]


async def month_to_date_spend(conn: Any, *, at: datetime | None = None) -> Decimal:
    """The one month-to-date spend read: every reader of the month's spend calls this.

    ``at`` selects the UTC calendar month containing that instant (used by alert tests and
    replays); without it the current UTC month is read.
    """
    if at is None:
        row = await conn.fetchrow(MONTH_TO_DATE_SPEND_SQL)
    else:
        row = await conn.fetchrow(MONTH_SPEND_AT_SQL, at)
    return _decimal_from_row(row, "s")


@runtime_checkable
class BudgetEstimate(Protocol):
    """Admission estimate object that can provide a pre-spend upper bound."""

    def upper_bound(self) -> Decimal: ...


@runtime_checkable
class StructuredBudgetEstimate(BudgetEstimate, Protocol):
    """Quote that can retain estimate-versus-ceiling semantics at admission."""

    def estimate(self) -> Decimal: ...

    def to_serializable_dict(self) -> dict[str, object]: ...


type BudgetEstimateInput = Decimal | str | int | BudgetEstimate


@dataclass(frozen=True, slots=True)
class _AdmissionCost:
    estimate_usd: Decimal
    ceiling_usd: Decimal
    quote_json: str | None


@dataclass(frozen=True)
class BudgetSnapshot:
    """Budget state captured at the point a launch is rejected."""

    monthly_budget_usd: Decimal
    per_request_max_usd: Decimal
    mtd_spend_usd: Decimal
    estimate_usd: Decimal
    budget_remaining_usd: Decimal

    def model_dump(
        self, *, mode: Literal["python", "json"] = "python", **_: Any
    ) -> dict[str, Decimal] | dict[str, str]:
        data = asdict(self)
        if mode == "python":
            return data
        if mode == "json":
            return {key: str(value) for key, value in data.items()}
        raise ValueError(f"unsupported dump mode: {mode!r}")

    def to_serializable_dict(self) -> dict[str, str]:
        """Return a stdlib-JSON-safe snapshot for HTTP response bodies."""

        return cast(dict[str, str], self.model_dump(mode="json"))

    def model_dump_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_serializable_dict(), **kwargs)


@dataclass(frozen=True)
class BudgetAdmission:
    """Result of an admission attempt under the budget lock."""

    workload_id: str
    is_new: bool


@dataclass(frozen=True)
class BudgetVerdict:
    """What admission would decide right now, computed without the lock or any write."""

    admitted: bool
    reason: BudgetRejectionReason | None
    snapshot: BudgetSnapshot

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason": self.reason,
            "snapshot": self.snapshot.to_serializable_dict(),
        }


class BudgetNotConfigured(ValueError):
    """A budget setting the gate needs is unset or is not a positive, finite decimal.

    The message names the setting and never echoes its value.
    """

    error_code = "budget_not_configured"
    status_code = 503

    @property
    def remedy(self) -> str:
        return (
            f"{self}: configure PITWALL_MONTHLY_BUDGET_USD and "
            "PITWALL_PER_REQUEST_MAX_USD on the broker"
        )

    def to_response_body(self) -> dict[str, str]:
        return {"error": self.error_code, "remedy": self.remedy}


class BudgetRejected(RuntimeError):
    """Raised when a workload cannot be admitted under the configured budget."""

    error_code = "budget_rejected"
    status_code = 402
    routing_error_code = "budget_exhausted"
    routing_status_code = 422

    def __init__(self, reason: BudgetRejectionReason, snapshot: BudgetSnapshot) -> None:
        super().__init__(reason)
        self.reason = reason
        self.snapshot = snapshot

    def to_response_body(self) -> dict[str, Any]:
        """Return the canonical HTTP 402 response body."""

        return {
            "error": self.error_code,
            "reason": self.reason,
            "snapshot": self.snapshot.to_serializable_dict(),
        }

    def to_http_response_body(self) -> dict[str, Any]:
        return self.to_response_body()


class BudgetGate:
    """Postgres-backed whole-account budget admission gate."""

    def __init__(
        self,
        pool: Any,
        *,
        monthly_budget_usd: Decimal | str | int | None = None,
        per_request_max_usd: Decimal | str | int | None = None,
        workload_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.pool = pool
        self.monthly_budget_usd = (
            _positive_decimal(monthly_budget_usd, "monthly_budget_usd")
            if monthly_budget_usd is not None
            else _required_env_budget("PITWALL_MONTHLY_BUDGET_USD")
        )
        self.per_request_max_usd = (
            _positive_decimal(per_request_max_usd, "per_request_max_usd")
            if per_request_max_usd is not None
            else _required_env_budget("PITWALL_PER_REQUEST_MAX_USD")
        )
        self._workload_id_factory = workload_id_factory or _new_workload_id

    async def current_mtd_spend(self) -> Decimal:
        """Return month-to-date spend across all admitted workloads.

        Read-only query (no advisory lock). Mirrors the spend window used by
        admission so callers can inspect budget state without contending for
        the lock. Terminal failures, cancellations, and timeouts still count:
        admitted provider spend remains billable even when the workload fails.
        """
        async with self.pool.acquire() as conn:
            return await month_to_date_spend(conn)

    async def check_available(
        self,
        estimate_usd: BudgetEstimateInput,
        *,
        _conn: Any | None = None,
    ) -> None:
        """Check budget availability under the admission advisory lock without a write."""

        estimate = _admission_cost(estimate_usd).ceiling_usd
        if _conn is not None:
            await self._check_available_on_connection(_conn, estimate)
            return
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                PITWALL_BUDGET_LOCK_KEY,
            )
            await self._check_available_on_connection(conn, estimate)

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        if conn is not None:
            return await read_limits(
                conn,
                default_monthly=self.monthly_budget_usd,
                default_per_request=self.per_request_max_usd,
            )
        async with self.pool.acquire() as owned:
            return await read_limits(
                owned,
                default_monthly=self.monthly_budget_usd,
                default_per_request=self.per_request_max_usd,
            )

    async def evaluate(self, estimate_usd: BudgetEstimateInput) -> BudgetVerdict:
        estimate = _admission_cost(estimate_usd).ceiling_usd
        async with self.pool.acquire() as conn:
            limits = await self.effective_limits(conn)
            spend = await month_to_date_spend(conn)
        reason: BudgetRejectionReason | None = None
        if estimate > limits.per_request_max_usd:
            reason = "per_request_cap"
        elif spend + estimate > limits.monthly_budget_usd:
            reason = "monthly_budget"
        snapshot = self._snapshot(limits=limits, mtd_spend_usd=spend, estimate_usd=estimate)
        return BudgetVerdict(admitted=reason is None, reason=reason, snapshot=snapshot)

    async def _check_available_on_connection(self, conn: Any, estimate: Decimal) -> None:
        limits = await self.effective_limits(conn)
        spend = await month_to_date_spend(conn)
        if estimate > limits.per_request_max_usd:
            log.warning(
                "per-request cap exceeded: %.6f > %.6f", estimate, limits.per_request_max_usd
            )
            raise BudgetRejected(
                "per_request_cap",
                self._snapshot(limits=limits, mtd_spend_usd=spend, estimate_usd=estimate),
            )
        if spend + estimate > limits.monthly_budget_usd:
            log.warning(
                "monthly budget would exceed under advisory lock: %.6f + %.6f > %.6f",
                spend,
                estimate,
                limits.monthly_budget_usd,
            )
            raise BudgetRejected(
                "monthly_budget",
                self._snapshot(limits=limits, mtd_spend_usd=spend, estimate_usd=estimate),
            )

    async def try_launch(
        self,
        *,
        capability_id: str,
        provider_id: str,
        estimate_usd: BudgetEstimateInput,
        workload_type: str = "inference",
        submitted_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Admit a workload under a Postgres advisory lock and return its id.

        When *idempotency_key* is provided and a workload with that key already
        exists, the existing workload id is returned without inserting a
        duplicate row.  This preserves the "create before queue dispatch"
        invariant for async jobs while keeping idempotency semantics intact.
        """
        admission = await self.try_launch_admission(
            capability_id=capability_id,
            provider_id=provider_id,
            estimate_usd=estimate_usd,
            workload_type=workload_type,
            submitted_at=submitted_at,
            idempotency_key=idempotency_key,
        )
        return admission.workload_id

    async def try_launch_admission(
        self,
        *,
        capability_id: str,
        provider_id: str,
        estimate_usd: BudgetEstimateInput,
        workload_type: str = "inference",
        submitted_at: datetime | None = None,
        idempotency_key: str | None = None,
        before_new_admission: Callable[[Any], Awaitable[None]] | None = None,
        after_new_admission: Callable[[Any, str], Awaitable[None]] | None = None,
    ) -> BudgetAdmission:
        """Admit a workload and report whether this call inserted the row."""

        admission_cost = _admission_cost(estimate_usd)
        estimate = admission_cost.ceiling_usd
        submitted = submitted_at or datetime.now(UTC)

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1)",
                PITWALL_BUDGET_LOCK_KEY,
            )
            if idempotency_key is not None:
                existing_id = await conn.fetchval(
                    "SELECT id FROM pitwall.workloads WHERE idempotency_key = $1",
                    idempotency_key,
                )
                if existing_id is not None:
                    log.info(
                        "idempotency key hit: returning existing workload %s",
                        existing_id,
                    )
                    return BudgetAdmission(workload_id=str(existing_id), is_new=False)

            if before_new_admission is not None:
                await before_new_admission(conn)
            await self.check_available(estimate, _conn=conn)
            workload_id = self._workload_id_factory()
            if idempotency_key is None:
                admitted_id = await conn.fetchval(
                    """INSERT INTO pitwall.workloads (
                               id, capability_id, provider_id, type, state,
                               cost_estimate_usd, submitted_at,
                               cost_ceiling_usd, cost_quote
                           )
                           VALUES ($1, $2, $3, $4, 'queued', $5, $6, $7, $8::jsonb)
                           RETURNING id""",
                    workload_id,
                    capability_id,
                    provider_id,
                    workload_type,
                    admission_cost.estimate_usd,
                    submitted,
                    admission_cost.ceiling_usd,
                    admission_cost.quote_json,
                )
            else:
                admitted_id = await conn.fetchval(
                    """INSERT INTO pitwall.workloads (
                               id, capability_id, provider_id, type, state,
                               cost_estimate_usd, submitted_at, idempotency_key,
                               cost_ceiling_usd, cost_quote
                           )
                           VALUES ($1, $2, $3, $4, 'queued', $5, $6, $7, $8, $9::jsonb)
                           RETURNING id""",
                    workload_id,
                    capability_id,
                    provider_id,
                    workload_type,
                    admission_cost.estimate_usd,
                    submitted,
                    idempotency_key,
                    admission_cost.ceiling_usd,
                    admission_cost.quote_json,
                )
            admitted_workload_id = str(admitted_id)
            if after_new_admission is not None:
                await after_new_admission(conn, admitted_workload_id)
            return BudgetAdmission(workload_id=admitted_workload_id, is_new=True)

    def _snapshot(
        self, *, limits: BudgetLimits, mtd_spend_usd: Decimal, estimate_usd: Decimal
    ) -> BudgetSnapshot:
        remaining = limits.monthly_budget_usd - mtd_spend_usd
        return BudgetSnapshot(
            monthly_budget_usd=limits.monthly_budget_usd,
            per_request_max_usd=limits.per_request_max_usd,
            mtd_spend_usd=mtd_spend_usd,
            estimate_usd=estimate_usd,
            budget_remaining_usd=remaining if remaining > 0 else Decimal("0"),
        )


def _new_workload_id() -> str:
    from pitwall.core.ids import ulid_new

    return f"wkl_{ulid_new()}"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise BudgetNotConfigured(f"{name} must be set")
    return value


def _required_env_budget(name: str) -> Decimal:
    """A budget setting read from the environment; unset or invalid is ``BudgetNotConfigured``."""
    raw = _required_env(name)
    try:
        return _positive_decimal(raw.strip(), name)
    except ValueError as exc:
        raise BudgetNotConfigured(f"{name} must be a positive decimal value") from exc


def _positive_decimal(raw_value: Decimal | str | int, name: str) -> Decimal:
    value = _decimal(raw_value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _admission_cost(raw_value: BudgetEstimateInput) -> _AdmissionCost:
    if isinstance(raw_value, StructuredBudgetEstimate):
        estimate = _decimal(raw_value.estimate(), "estimate_usd")
        ceiling = _decimal(raw_value.upper_bound(), "ceiling_usd")
        quote_json = json.dumps(
            raw_value.to_serializable_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
    elif isinstance(raw_value, BudgetEstimate):
        ceiling = _decimal(raw_value.upper_bound(), "estimate_usd")
        estimate = ceiling
        quote_json = None
    else:
        ceiling = _decimal(raw_value, "estimate_usd")
        estimate = ceiling
        quote_json = None
    if estimate < 0:
        raise ValueError("estimate_usd must be non-negative")
    if ceiling < 0:
        raise ValueError("ceiling_usd must be non-negative")
    if ceiling < estimate:
        raise ValueError("ceiling_usd must be greater than or equal to estimate_usd")
    return _AdmissionCost(
        estimate_usd=estimate,
        ceiling_usd=ceiling,
        quote_json=quote_json,
    )


def _decimal(raw_value: Any, name: str) -> Decimal:
    if isinstance(raw_value, bool):
        raise ValueError(f"{name} must be a decimal value")
    if isinstance(raw_value, float):
        raise ValueError(f"{name} must be a Decimal, decimal string, or integer")
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal value") from exc
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return value


def _decimal_from_row(row: Mapping[str, Any] | None, key: str) -> Decimal:
    if row is None:
        return Decimal("0")
    return _decimal(row[key], key)


__all__ = [
    "MONTH_SPEND_AT_SQL",
    "MONTH_TO_DATE_SPEND_SQL",
    "MONTH_TO_DATE_WHERE",
    "month_to_date_spend",
    "WORKLOAD_SPEND_EXPR",
    "BudgetAdmission",
    "BudgetEstimate",
    "BudgetEstimateInput",
    "BudgetGate",
    "BudgetNotConfigured",
    "BudgetRejected",
    "BudgetRejectionReason",
    "BudgetSnapshot",
    "BudgetVerdict",
    "StructuredBudgetEstimate",
    "PITWALL_BUDGET_GATE_LOCK_KEY",
    "PITWALL_BUDGET_LOCK_KEY",
]
