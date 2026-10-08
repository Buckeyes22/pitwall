"""Lease tools — pod lease create, get, renew, and stop for the MCP surface.

These tools expose the same lease operations as the REST API endpoints:
- POST /v1/leases          -> pitwall_lease_pod
- GET  /v1/leases/{id}     -> pitwall_get_lease
- POST /v1/leases/{id}/renew -> pitwall_renew_lease
- POST /v1/leases/{id}/stop -> pitwall_stop_lease

All handlers delegate to the same service-layer functions the REST handlers use
(LeaseRepository, run_launch, run_teardown).  Audit context uses actor="mcp" to distinguish
MCP-initiated changes from REST-initiated ones; it is a surface label, not an identity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pitwall.api.serializers import lease_to_response
from pitwall.db import get_pool
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    WorkloadRepository,
)
from pitwall.leases.mutations import (
    MAX_LEASE_EXPIRY_HORIZON_MINUTES,
    LeaseMutationConflict,
    LeaseMutationExpiryLimitExceeded,
    LeaseMutationIdempotencyConflict,
    LeaseMutationNotFound,
    lease_capability_name,
    renew_lease,
)

if TYPE_CHECKING:
    from pitwall.routing.production import ProductionRoutingService


async def get_production_routing_service() -> ProductionRoutingService:
    """Build the same shared service used by REST and operator transports."""
    # Resolved at call time: pitwall.routing.production imports pitwall.api, which other
    # suites evict and re-import.
    from pitwall.config import get_settings
    from pitwall.routing.production import ProductionRoutingService

    return ProductionRoutingService(await get_pool(), settings=get_settings())


async def pitwall_lease_pod(
    capability_id: str,
    provider_id: str | None = None,
    dry_run: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create a pod lease for a capability through the production planner.

    Mirrors POST /v1/leases: the same shared resolve, plan, launch, and plan-recording path.
    A repeated ``idempotency_key`` with the same request returns the first launch's lease
    (``replayed: true``) instead of launching another pod.
    """
    # Resolved at call time: other suites evict and re-import pitwall.api modules.
    from pitwall.api.leases.launch import create_routed_lease

    pool = await get_pool()
    routed = await create_routed_lease(
        pool=pool,
        capability_repo=CapabilityRepository(pool),
        provider_repo=ProviderRepository(pool),
        lease_repo=LeaseRepository(pool),
        workload_repo=WorkloadRepository(pool),
        routing_service=await get_production_routing_service(),
        capability_ref=capability_id,
        provider_id=provider_id,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
    )
    plan = routed.route_plan_fields()
    if routed.lease is None:
        return {
            "id": None,
            "state": "dry_run",
            "dry_run": True,
            "capability_id": routed.capability.id,
            "provider_id": routed.provider.id if routed.provider is not None else None,
            "template_id": routed.launch_result.get("template_id"),
            "template_name": routed.launch_result.get("template_name"),
            **plan,
        }
    return {**lease_to_response(routed.lease), **plan, "replayed": routed.replayed}


async def pitwall_get_lease(
    lease_id: str,
) -> dict[str, Any]:
    """Return the current state and details of a pod lease.

    Mirrors GET /v1/leases/{id}.
    """
    pool = await get_pool()
    lease_repo = LeaseRepository(pool)

    lease = await lease_repo.get(lease_id)
    if lease is None:
        from pitwall.api.exceptions import LeaseNotFound

        raise LeaseNotFound(lease_id)

    return lease_to_response(lease)


async def pitwall_renew_lease(
    lease_id: str,
    extends_minutes: int = 60,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Extend an active pod lease by a number of minutes.

    Mirrors POST /v1/leases/{id}/renew.
    """
    from pitwall.redis_env import optional_redis_from_env

    pool = await get_pool()
    lease_repo = LeaseRepository(pool)
    capability_name = await lease_capability_name(pool, lease_repo, lease_id)

    try:
        async with optional_redis_from_env() as redis_client:
            updated = await renew_lease(
                lease_repo,
                lease_id,
                extends_minutes=extends_minutes,
                actor="mcp",
                idempotency_key=idempotency_key,
                renewed_by="operator",
                pool=pool,
                redis=redis_client,
                capability_name=capability_name,
            )
    except LeaseMutationNotFound as exc:
        from pitwall.api.exceptions import LeaseNotFound

        raise LeaseNotFound(lease_id) from exc
    except LeaseMutationConflict as exc:
        from pitwall.api.exceptions import LeaseStateConflict

        raise LeaseStateConflict(lease_id, exc.state, exc.operation) from exc
    except LeaseMutationExpiryLimitExceeded as exc:
        from pitwall.api.exceptions import LeaseExpiryLimitExceeded

        raise LeaseExpiryLimitExceeded(lease_id, MAX_LEASE_EXPIRY_HORIZON_MINUTES) from exc
    except LeaseMutationIdempotencyConflict as exc:
        from pitwall.api.exceptions import IdempotencyConflict

        raise IdempotencyConflict(exc.idempotency_key) from exc

    return lease_to_response(updated)


async def pitwall_stop_lease(
    lease_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Stop and tear down an active pod lease.

    Mirrors POST /v1/leases/{id}/stop.
    """
    pool = await get_pool()

    from pitwall.api.leases.teardown import run_teardown
    from pitwall.redis_env import optional_redis_from_env

    async with optional_redis_from_env() as redis_client:
        result = await run_teardown(
            lease_id=lease_id,
            pool=pool,
            redis_client=redis_client,
            reason="operator",
            terminated_reason=reason,
        )
    return lease_to_response(result.lease)
