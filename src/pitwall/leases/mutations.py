"""Cross-surface lease mutation contract shared by REST and MCP."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, Literal

from pitwall.api.exceptions import PreSpendPayloadRejected
from pitwall.core.models import Lease
from pitwall.cost.budget_gate import PITWALL_BUDGET_LOCK_KEY, BudgetGate
from pitwall.db.repository import (
    LEASE_MUTATION_UNSET,
    CapabilityRepository,
    LeaseMutationExpiryLimitError,
    LeaseMutationIdempotencyError,
    LeaseMutationStateError,
    LeaseRepository,
    ProviderRepository,
)
from pitwall.security.pre_spend import PreSpendDecision, get_pre_spend_inspection_service

MAX_LEASE_EXTENSION_MINUTES = 43_200
MAX_LEASE_EXPIRY_HORIZON_MINUTES = 43_200


class LeaseMutationNotFound(RuntimeError):
    """The requested lease does not exist."""


class LeaseMutationConflict(RuntimeError):
    """The current lifecycle state cannot satisfy the mutation."""

    def __init__(self, state: str, operation: str) -> None:
        super().__init__(f"{state}:{operation}")
        self.state = state
        self.operation = operation


class LeaseMutationExpiryLimitExceeded(RuntimeError):
    """The requested expiry would exceed the absolute renewal horizon."""


class LeaseMutationIdempotencyConflict(RuntimeError):
    """The key was already used for a different mutation."""

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(idempotency_key)
        self.idempotency_key = idempotency_key


def _request_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def patch_lease_settings(
    repo: LeaseRepository,
    lease_id: str,
    *,
    renewal_policy: str | object,
    auto_teardown_on_expiry: bool | object,
    actor: str,
    idempotency_key: str | None = None,
) -> Lease:
    """Persist supported lease settings atomically with a same-transaction audit."""

    _guard_lease_mutation(
        {
            "lease_id": lease_id,
            "idempotency_key": idempotency_key,
        }
    )
    payload = {
        "lease_id": lease_id,
        "renewal_policy": _json_value(renewal_policy),
        "auto_teardown_on_expiry": _json_value(auto_teardown_on_expiry),
    }
    try:
        result = await repo.patch_settings(
            lease_id,
            renewal_policy=renewal_policy,
            auto_teardown_on_expiry=auto_teardown_on_expiry,
            actor=actor,
            idempotency_key=idempotency_key,
            request_hash=_request_hash(payload),
        )
    except LeaseMutationStateError as exc:
        raise LeaseMutationConflict(exc.state, exc.operation) from exc
    except LeaseMutationIdempotencyError as exc:
        raise LeaseMutationIdempotencyConflict(exc.idempotency_key) from exc
    if result is None:
        raise LeaseMutationNotFound(lease_id)
    return result.lease


async def renew_lease(
    repo: LeaseRepository,
    lease_id: str,
    *,
    extends_minutes: int,
    actor: str,
    idempotency_key: str | None = None,
    renewed_by: Literal["operator", "activity"] = "operator",
    pool: Any | None = None,
    redis: Any = None,
    capability_name: str | None = None,
) -> Lease:
    """Add minutes to current expiry, with locking and an absolute 30-day horizon.

    The extension is reserved against the budget: rate x extension, at the rate teardown
    settles at (``settlement_rate_per_second``, never $0), checked under the
    budget lock and added to the lease workload's reserved ceiling in the renewal transaction.
    A renewal that would pass the budget raises ``BudgetRejected`` and changes nothing.
    """

    _guard_lease_mutation(
        {
            "lease_id": lease_id,
            "idempotency_key": idempotency_key,
        }
    )
    if not 1 <= extends_minutes <= MAX_LEASE_EXTENSION_MINUTES:
        raise ValueError(f"extends_minutes must be between 1 and {MAX_LEASE_EXTENSION_MINUTES}")
    if pool is None:
        raise ValueError("renew_lease needs the pool to reserve the extension against the budget")
    extension_usd = await renewal_extension_usd(pool, repo, lease_id, extends_minutes)
    budget_check = _renewal_budget_check(pool) if extension_usd is not None else None
    try:
        result = await repo.renew(
            lease_id,
            extends_minutes=extends_minutes,
            actor=actor,
            idempotency_key=idempotency_key,
            request_hash=_request_hash({"lease_id": lease_id, "extends_minutes": extends_minutes}),
            max_horizon_minutes=MAX_LEASE_EXPIRY_HORIZON_MINUTES,
            extension_usd=extension_usd,
            budget_check=budget_check,
        )
    except LeaseMutationStateError as exc:
        raise LeaseMutationConflict(exc.state, exc.operation) from exc
    except LeaseMutationExpiryLimitError as exc:
        raise LeaseMutationExpiryLimitExceeded(lease_id) from exc
    except LeaseMutationIdempotencyError as exc:
        raise LeaseMutationIdempotencyConflict(exc.idempotency_key) from exc
    if result is None:
        raise LeaseMutationNotFound(lease_id)
    lease = result.lease
    if pool is not None and capability_name is not None:
        from pitwall.leases.events import build_lease_renewed_event, publish_lease_event

        await publish_lease_event(
            pool,
            redis,
            build_lease_renewed_event(
                lease=lease,
                capability_name=capability_name,
                renewed_by=renewed_by,
            ),
        )
    return lease


async def renewal_extension_usd(
    pool: Any, repo: LeaseRepository, lease_id: str, extends_minutes: int
) -> Decimal | None:
    """What a renewal reserves: the lease's settlement rate x the extension (6 dp).

    ``None`` when the lease does not exist (the repository then reports it missing).
    """
    # Resolved at call time: pitwall.api.leases imports the launch path, which imports this.
    from pitwall.api.leases.teardown import settlement_rate_per_second

    lease = await repo.get(lease_id)
    if lease is None:
        return None
    provider = await ProviderRepository(pool).get(lease.provider_id)
    rate = await settlement_rate_per_second(pool, lease, provider)
    return (rate * Decimal(extends_minutes * 60)).quantize(Decimal("0.000001"))


def _renewal_budget_check(pool: Any) -> Callable[[Any, Decimal], Awaitable[None]]:
    """Take the budget lock on the renewal's connection and refuse an extension past budget.

    The gate is built inside ``check``, which the repository runs only after its idempotency
    replay check: a same-key retry of an applied renewal returns its stored result even once
    the budget setting is unset or invalid, and only a new renewal raises ``BudgetNotConfigured``.
    """

    async def check(conn: Any, extension_usd: Decimal) -> None:
        gate = BudgetGate(pool)
        await conn.execute("SELECT pg_advisory_xact_lock($1)", PITWALL_BUDGET_LOCK_KEY)
        await gate.check_available(extension_usd, _conn=conn)

    return check


async def lease_capability_name(pool: Any, repo: LeaseRepository, lease_id: str) -> str | None:
    """Return the capability name that owns ``lease_id``, or ``None`` when it cannot be resolved.

    Renewal events are addressed by capability name, so every surface resolves it the same way.
    """

    lease = await repo.get(lease_id)
    if lease is None:
        return None
    provider = await ProviderRepository(pool).get(lease.provider_id)
    if provider is None:
        return None
    capability = await CapabilityRepository(pool).get(provider.capability_id)
    return capability.name if capability is not None else None


def _guard_lease_mutation(payload: dict[str, str | None]) -> None:
    """Reject unsafe durable lease identifiers before the repository write."""
    result = get_pre_spend_inspection_service().inspect(payload)
    # Identity and idempotency values cannot be schema-safely rewritten.
    if result.decision != PreSpendDecision.ALLOW:
        raise PreSpendPayloadRejected(
            decision=result.decision.value,
            findings=[finding.to_dict() for finding in result.findings],
        )


def _json_value(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    if value is LEASE_MUTATION_UNSET:
        return None
    return value


__all__ = [
    "MAX_LEASE_EXPIRY_HORIZON_MINUTES",
    "MAX_LEASE_EXTENSION_MINUTES",
    "LEASE_MUTATION_UNSET",
    "LeaseMutationConflict",
    "LeaseMutationExpiryLimitExceeded",
    "LeaseMutationIdempotencyConflict",
    "LeaseMutationNotFound",
    "lease_capability_name",
    "patch_lease_settings",
    "renew_lease",
]
