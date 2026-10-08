"""Blast-radius sub-budgets and chargeback for Pitwall.

Partitions the monthly budget into named sub-budgets (per capability/team/tag),
gates admission against the relevant sub-budget, and attributes spend via
chargeback reports.  All money values use :class:`decimal.Decimal` and are
quantised to 6 decimal places for parity with the estimator and persisted
USD columns.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pitwall.cost.budget_gate import BudgetEstimateInput, BudgetGate
from pitwall.cost.budget_limits import BudgetLimits

_USD_QUANTUM = Decimal("0.000001")

# ``(tag, conn) -> spend``; ``conn`` is the admission's own transaction connection.
_TagSpendResolver = Callable[..., Awaitable[Decimal]]


class _SubBudgetBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SubBudget(_SubBudgetBase):
    """One named slice of the monthly budget."""

    tag: str = Field(min_length=1)
    allocation_usd: Decimal
    description: str | None = None

    @field_validator("allocation_usd", mode="before")
    @classmethod
    def _validate_allocation(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "allocation_usd")


class SubBudgetConfig(_SubBudgetBase):
    """Partition of a monthly budget into tagged sub-budgets."""

    total_budget_usd: Decimal
    budgets: list[SubBudget] = Field(default_factory=list)

    @field_validator("total_budget_usd", mode="before")
    @classmethod
    def _validate_total(cls, value: object) -> Decimal:
        return _positive_decimal(value, "total_budget_usd")

    @model_validator(mode="after")
    def _validate_budgets_sum(self) -> SubBudgetConfig:
        seen_tags: set[str] = set()
        duplicate_tags: list[str] = []
        for budget in self.budgets:
            if budget.tag in seen_tags and budget.tag not in duplicate_tags:
                duplicate_tags.append(budget.tag)
            seen_tags.add(budget.tag)
        if duplicate_tags:
            rendered = ", ".join(duplicate_tags)
            raise ValueError(f"duplicate sub-budget tag(s): {rendered}")

        total = sum(b.allocation_usd for b in self.budgets)
        if total > self.total_budget_usd:
            raise ValueError(
                f"sub-budget allocations sum {total} exceeds total {self.total_budget_usd}"
            )
        return self

    def allocation_for(self, tag: str) -> Decimal:
        """Return the allocated amount for *tag*, or zero if unknown."""
        for budget in self.budgets:
            if budget.tag == tag:
                return budget.allocation_usd
        return Decimal("0")

    def tags(self) -> list[str]:
        """Return all defined tags in order."""
        return [budget.tag for budget in self.budgets]


@dataclass(frozen=True)
class SubBudgetSnapshot:
    """Sub-budget state captured at the point a launch is rejected."""

    tag: str
    tag_allocation_usd: Decimal
    tag_mtd_spend_usd: Decimal
    tag_estimate_usd: Decimal
    tag_remaining_usd: Decimal

    def to_serializable_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "tag_allocation_usd": _decimal_to_str(self.tag_allocation_usd),
            "tag_mtd_spend_usd": _decimal_to_str(self.tag_mtd_spend_usd),
            "tag_estimate_usd": _decimal_to_str(self.tag_estimate_usd),
            "tag_remaining_usd": _decimal_to_str(self.tag_remaining_usd),
        }

    def model_dump_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_serializable_dict(), **kwargs)


class SubBudgetRejected(RuntimeError):
    """Raised when a workload cannot be admitted under its sub-budget."""

    error_code = "sub_budget_rejected"
    status_code = 402

    def __init__(self, reason: str, snapshot: SubBudgetSnapshot) -> None:
        super().__init__(reason)
        self.reason = reason
        self.snapshot = snapshot

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "reason": self.reason,
            "snapshot": self.snapshot.to_serializable_dict(),
        }

    def to_http_response_body(self) -> dict[str, Any]:
        return self.to_response_body()


class SubBudgetGate:
    """Budget admission gate with sub-budget partitioning.

    Wraps a :class:`BudgetGate` and adds an additional sub-budget check
    before delegating to the global monthly budget gate.  The sub-budget
    check can be backed by an external MTD-spend resolver (e.g. a DB
    query) or by in-memory tracking maintained by this gate instance.

    A resolver is called as ``tag_mtd_spend(tag, conn)`` while the admission holds a
    pooled connection and the global advisory lock, and must query on ``conn``:
    acquiring another connection deadlocks on a one-connection pool, or on a larger
    pool whose connections are all held by admissions waiting for the lock.  A
    resolver that cannot accept ``conn`` is rejected with ``TypeError`` at construction.
    """

    def __init__(
        self,
        budget_gate: BudgetGate,
        config: SubBudgetConfig,
        *,
        tag_mtd_spend: _TagSpendResolver | None = None,
    ) -> None:
        self._budget_gate = budget_gate
        self._config = config
        self._tag_mtd_spend = tag_mtd_spend
        if tag_mtd_spend is not None and not _accepts_connection(tag_mtd_spend):
            raise TypeError(
                "tag_mtd_spend must accept the admission connection as a second positional "
                "argument, tag_mtd_spend(tag, conn): it runs inside the admission transaction "
                "and must query on that connection, not acquire another from the pool"
            )
        self._memory_spend: dict[str, Decimal] = {}

    async def _current_tag_spend(self, tag: str, conn: Any) -> Decimal:
        if self._tag_mtd_spend is not None:
            raw = await self._tag_mtd_spend(tag, conn)
            return _quantize_usd(_decimal(raw, "tag_mtd_spend"))
        return self._memory_spend.get(tag, Decimal("0"))

    async def _validate_new_tag_admission(
        self, *, tag: str, estimate: Decimal, conn: Any
    ) -> Decimal:
        allocation = self._config.allocation_for(tag)

        if allocation <= 0:
            snapshot = SubBudgetSnapshot(
                tag=tag,
                tag_allocation_usd=allocation,
                tag_mtd_spend_usd=Decimal("0"),
                tag_estimate_usd=estimate,
                tag_remaining_usd=Decimal("0"),
            )
            raise SubBudgetRejected("unknown_tag", snapshot)

        mtd = await self._current_tag_spend(tag, conn)
        if mtd + estimate > allocation:
            remaining = allocation - mtd
            snapshot = SubBudgetSnapshot(
                tag=tag,
                tag_allocation_usd=allocation,
                tag_mtd_spend_usd=mtd,
                tag_estimate_usd=estimate,
                tag_remaining_usd=remaining if remaining > 0 else Decimal("0"),
            )
            raise SubBudgetRejected("sub_budget", snapshot)

        return mtd

    async def try_launch(
        self,
        *,
        tag: str,
        capability_id: str,
        provider_id: str,
        estimate_usd: BudgetEstimateInput,
        workload_type: str = "inference",
        submitted_at: Any | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Admit a workload under its sub-budget and the global budget.

        Raises:
            SubBudgetRejected: when the sub-budget for *tag* would be exceeded.
            BudgetRejected: propagated from the underlying global gate.
            ValueError: when *estimate_usd* is not strictly positive.
        """
        estimate = _positive_estimate(estimate_usd)
        mtd: Decimal | None = None
        recorded = False

        # Both callbacks run inside the global admission lock, so concurrent
        # admissions for one tag are serialized: the tag check sees every
        # admitted workload, and the spend record lands before the lock is freed.
        async def before_new_admission(conn: Any) -> None:
            nonlocal mtd
            mtd = await self._validate_new_tag_admission(tag=tag, estimate=estimate, conn=conn)

        async def after_new_admission(_conn: Any, _workload_id: str) -> None:
            nonlocal recorded
            if mtd is not None:
                self._memory_spend[tag] = mtd + estimate
                recorded = True

        try:
            admission = await self._budget_gate.try_launch_admission(
                capability_id=capability_id,
                provider_id=provider_id,
                estimate_usd=estimate,
                workload_type=workload_type,
                submitted_at=submitted_at,
                idempotency_key=idempotency_key,
                before_new_admission=before_new_admission,
                after_new_admission=after_new_admission,
            )
        except BaseException:
            # The spend record lands inside the gate's transaction; if the commit then
            # fails nothing was admitted. Give back only this call's estimate, since
            # other admissions may have landed on the tag since.
            if recorded:
                self._memory_spend[tag] = self._memory_spend.get(tag, Decimal("0")) - estimate
            raise
        return admission.workload_id

    @property
    def monthly_budget_usd(self) -> Decimal:
        """Expose the underlying global monthly budget."""
        return self._budget_gate.monthly_budget_usd

    async def effective_limits(self, conn: Any | None = None) -> BudgetLimits:
        """Delegate to the wrapped gate so runtime limits apply through sub-budgets too."""
        return await self._budget_gate.effective_limits(conn)

    async def current_mtd_spend(self) -> Decimal:
        """Delegate to the underlying gate for global month-to-date spend."""
        return await self._budget_gate.current_mtd_spend()


@dataclass(frozen=True)
class ChargebackLineItem:
    """Spend attributed to one tag."""

    tag: str
    allocation_usd: Decimal
    spend_usd: Decimal
    remaining_usd: Decimal


@dataclass(frozen=True)
class ChargebackReport:
    """Chargeback attribution for a set of workloads against sub-budgets."""

    total_spend_usd: Decimal
    line_items: tuple[ChargebackLineItem, ...]
    unallocated_spend_usd: Decimal

    def to_serializable_dict(self) -> dict[str, Any]:
        return {
            "total_spend_usd": _decimal_to_str(self.total_spend_usd),
            "line_items": [
                {
                    "tag": item.tag,
                    "allocation_usd": _decimal_to_str(item.allocation_usd),
                    "spend_usd": _decimal_to_str(item.spend_usd),
                    "remaining_usd": _decimal_to_str(item.remaining_usd),
                }
                for item in self.line_items
            ],
            "unallocated_spend_usd": _decimal_to_str(self.unallocated_spend_usd),
        }


def generate_chargeback_report(
    config: SubBudgetConfig,
    workloads: Iterable[Mapping[str, Any]],
    *,
    tag_resolver: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> ChargebackReport:
    """Produce a deterministic chargeback report from workload records.

    Each workload mapping is inspected for ``cost_actual_usd`` (preferred)
    and falls back to ``cost_estimate_usd``.  *tag_resolver* receives the
    mapping and returns a tag string, or ``None`` to count the workload as
    unallocated.  When no resolver is supplied every workload is treated as
    unallocated.
    """
    tag_spend: dict[str, Decimal] = {}
    unallocated = Decimal("0")

    for workload in workloads:
        # Same precedence as the budget gate: actual, then the admitted ceiling, then estimate.
        cost = _decimal_from_mapping(workload, "cost_actual_usd")
        if cost is None:
            cost = _decimal_from_mapping(workload, "cost_ceiling_usd")
        if cost is None:
            cost = _decimal_from_mapping(workload, "cost_estimate_usd")
        if cost is None:
            continue

        resolved_tag: str | None = None
        if tag_resolver is not None:
            resolved_tag = tag_resolver(workload)

        if resolved_tag is not None:
            tag_spend[resolved_tag] = tag_spend.get(resolved_tag, Decimal("0")) + cost
        else:
            unallocated += cost

    total_spend = sum(tag_spend.values(), Decimal("0")) + unallocated

    line_items: list[ChargebackLineItem] = []
    for budget in config.budgets:
        spend = tag_spend.get(budget.tag, Decimal("0"))
        remaining = budget.allocation_usd - spend
        if remaining < 0:
            remaining = Decimal("0")
        line_items.append(
            ChargebackLineItem(
                tag=budget.tag,
                allocation_usd=budget.allocation_usd,
                spend_usd=spend,
                remaining_usd=remaining,
            )
        )

    return ChargebackReport(
        total_spend_usd=_quantize_usd(total_spend),
        line_items=tuple(line_items),
        unallocated_spend_usd=_quantize_usd(unallocated),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a decimal value")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal value") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{name} must be finite")
    return decimal_value


def _positive_decimal(raw_value: object, name: str) -> Decimal:
    value = _decimal(raw_value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_decimal(raw_value: object, name: str) -> Decimal:
    value = _decimal(raw_value, name)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _positive_estimate(raw_value: BudgetEstimateInput) -> Decimal:
    from pitwall.cost.budget_gate import BudgetEstimate

    if isinstance(raw_value, BudgetEstimate):
        return _positive_decimal(raw_value.upper_bound(), "estimate_usd")
    return _positive_decimal(raw_value, "estimate_usd")


def _accepts_connection(resolver: _TagSpendResolver) -> bool:
    """Return whether *resolver* takes the admission connection as a second positional argument."""
    try:
        parameters = inspect.signature(resolver).parameters.values()
    except TypeError, ValueError:
        return False
    positional = 0
    for parameter in parameters:
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 2


def _quantize_usd(value: Decimal) -> Decimal:
    try:
        return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"USD value is out of representable range: {value}") from exc


def _decimal_to_str(value: Decimal) -> str:
    return f"{value:.6f}"


def _decimal_from_mapping(mapping: Mapping[str, Any], key: str) -> Decimal | None:
    raw = mapping.get(key)
    if raw is None:
        return None
    try:
        return _quantize_usd(Decimal(str(raw)))
    except InvalidOperation, ValueError:
        return None


__all__ = [
    "ChargebackLineItem",
    "ChargebackReport",
    "SubBudget",
    "SubBudgetConfig",
    "SubBudgetGate",
    "SubBudgetRejected",
    "SubBudgetSnapshot",
    "generate_chargeback_report",
]
