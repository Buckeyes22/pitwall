"""FastAPI route handlers for the Lease surface.

Discovery routes (GET) are mounted under ``/v1/leases``.
Mutating routes (POST/DELETE) are mounted under ``/v1/leases``.

Each handler delegates to :class:`pitwall.db.repository.LeaseRepository`
and raises mapped API exceptions that FastAPI exception handlers translate to
the correct HTTP status codes.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any, cast

from fastapi import APIRouter, Depends, Request, Response

from pitwall.api.exceptions import (
    ChangeSetTooBroad,
    EmptyLeasePatch,
    IdempotencyConflict,
    LeaseExpiryLimitExceeded,
    LeaseNotFound,
    LeaseStateConflict,
    UnsupportedLeasePatch,
)
from pitwall.api.leases.launch import create_routed_lease
from pitwall.api.leases.teardown import run_teardown
from pitwall.api.schemas.leases import (
    LeaseCreate,
    LeasePatch,
    LeaseRenew,
    LeaseResponse,
    LeaseStop,
    lease_patch_conflicting_fields,
    lease_patch_unsupported_fields,
)
from pitwall.api.schemas.params import PathId
from pitwall.api.serializers import lease_to_response
from pitwall.config import get_settings
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    WorkloadRepository,
)
from pitwall.leases.mutations import (
    LEASE_MUTATION_UNSET,
    MAX_LEASE_EXPIRY_HORIZON_MINUTES,
    LeaseMutationConflict,
    LeaseMutationExpiryLimitExceeded,
    LeaseMutationIdempotencyConflict,
    LeaseMutationNotFound,
    patch_lease_settings,
)
from pitwall.leases.mutations import (
    renew_lease as renew_lease_service,
)
from pitwall.routing.production import ProductionRoutingService

router = APIRouter()


def _map_lease_mutation_error(lease_id: str, exc: RuntimeError) -> None:
    if isinstance(exc, LeaseMutationNotFound):
        raise LeaseNotFound(lease_id) from exc
    if isinstance(exc, LeaseMutationConflict):
        raise LeaseStateConflict(lease_id, exc.state, exc.operation) from exc
    if isinstance(exc, LeaseMutationExpiryLimitExceeded):
        raise LeaseExpiryLimitExceeded(lease_id, MAX_LEASE_EXPIRY_HORIZON_MINUTES) from exc
    if isinstance(exc, LeaseMutationIdempotencyConflict):
        raise IdempotencyConflict(exc.idempotency_key) from exc
    raise exc


def _lease_repo(request: Request) -> LeaseRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return LeaseRepository(pool)


def _pool(request: Request) -> Any:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return pool


def _redis_client(request: Request) -> Any | None:
    return getattr(request.app.state, "redis", None)


def _capability_repo(request: Request) -> CapabilityRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return CapabilityRepository(pool)


def _provider_repo(request: Request) -> ProviderRepository:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError(
            "app.state.pool is not configured; "
            "ensure an asyncpg.Pool is attached to app.state before serving requests"
        )
    return ProviderRepository(pool)


def _workload_repo(request: Request) -> WorkloadRepository:
    return WorkloadRepository(_pool(request))


def _routing_service(
    request: Request,
    capability_repo: CapabilityRepository = Depends(_capability_repo),
    provider_repo: ProviderRepository = Depends(_provider_repo),
) -> ProductionRoutingService:
    configured = getattr(request.app.state, "production_routing_service", None)
    if configured is not None:
        return cast(ProductionRoutingService, configured)
    pool = _pool(request)
    return ProductionRoutingService(
        pool,
        settings=get_settings(),
        capability_repository=capability_repo,
        provider_repository=provider_repo,
    )


@router.post(
    "/v1/leases",
    status_code=201,
    response_model=LeaseResponse,
)
async def create_lease(
    body: LeaseCreate,
    pool: Any = Depends(_pool),
    capability_repo: CapabilityRepository = Depends(_capability_repo),
    provider_repo: ProviderRepository = Depends(_provider_repo),
    lease_repo: LeaseRepository = Depends(_lease_repo),
    workload_repo: WorkloadRepository = Depends(_workload_repo),
    routing_service: ProductionRoutingService = Depends(_routing_service),
) -> dict[str, Any]:
    routed = await create_routed_lease(
        pool=pool,
        capability_repo=capability_repo,
        provider_repo=provider_repo,
        lease_repo=lease_repo,
        workload_repo=workload_repo,
        routing_service=routing_service,
        capability_ref=body.capability_id,
        provider_id=body.provider_id,
        idempotency_key=body.idempotency_key,
        dry_run=body.dry_run,
    )
    plan = routed.route_plan_fields()
    if routed.lease is None:
        # Dry-run creates no pod and persists no lease; return the launch plan /
        # cost-estimate result as-is. (Its shape is a separate concern from the
        # LeaseResponse success contract and is not validated against it here.)
        return {**routed.launch_result, **plan}
    return {**lease_to_response(routed.lease), **plan, "replayed": routed.replayed}


@router.get(
    "/v1/leases/{lease_id}",
    response_model=LeaseResponse,
)
async def get_lease(
    lease_id: PathId,
    repo: LeaseRepository = Depends(_lease_repo),
) -> dict[str, Any]:
    lease = await repo.get(lease_id)
    if lease is None:
        raise LeaseNotFound(lease_id)
    return lease_to_response(lease)


@router.patch(
    "/v1/leases/{lease_id}",
    response_model=LeaseResponse,
)
async def patch_lease(
    lease_id: PathId,
    body: LeasePatch,
    request: Request,
    repo: LeaseRepository = Depends(_lease_repo),
) -> dict[str, Any]:
    raw_body = await request.json()
    patch_payload: LeasePatch | Mapping[str, object]
    patch_payload = raw_body if isinstance(raw_body, Mapping) else body
    conflicting_fields = lease_patch_conflicting_fields(patch_payload)
    if conflicting_fields:
        raise ChangeSetTooBroad(conflicting_fields)

    unsupported_fields = lease_patch_unsupported_fields(patch_payload)
    if unsupported_fields:
        raise UnsupportedLeasePatch(unsupported_fields)

    supplied_fields = set(body.model_fields_set)
    mutable_fields = supplied_fields & {"renewal_policy", "auto_teardown_on_expiry"}
    if not mutable_fields:
        raise EmptyLeasePatch()

    try:
        updated = await patch_lease_settings(
            repo,
            lease_id,
            renewal_policy=(
                body.renewal_policy if "renewal_policy" in supplied_fields else LEASE_MUTATION_UNSET
            ),
            auto_teardown_on_expiry=(
                body.auto_teardown_on_expiry
                if "auto_teardown_on_expiry" in supplied_fields
                else LEASE_MUTATION_UNSET
            ),
            actor="rest:lease",
            idempotency_key=body.idempotency_key,
        )
    except RuntimeError as exc:
        _map_lease_mutation_error(lease_id, exc)
        raise AssertionError("unreachable") from exc
    return lease_to_response(updated)


@router.post(
    "/v1/leases/{lease_id}/renew",
    response_model=LeaseResponse,
)
async def renew_lease(
    lease_id: PathId,
    body: LeaseRenew,
    repo: LeaseRepository = Depends(_lease_repo),
    pool: Any = Depends(_pool),
    redis_client: Any | None = Depends(_redis_client),
) -> dict[str, Any]:
    capability_name: str | None = None
    lease = await repo.get(lease_id)
    if lease is not None:
        provider = await ProviderRepository(pool).get(lease.provider_id)
        if provider is not None:
            capability = await CapabilityRepository(pool).get(provider.capability_id)
            if capability is not None:
                capability_name = capability.name
    try:
        updated = await renew_lease_service(
            repo,
            lease_id,
            extends_minutes=body.extends_minutes,
            actor="rest:lease",
            idempotency_key=body.idempotency_key,
            renewed_by="operator",
            pool=pool,
            redis=redis_client,
            capability_name=capability_name,
        )
    except RuntimeError as exc:
        _map_lease_mutation_error(lease_id, exc)
        raise AssertionError("unreachable") from exc
    return lease_to_response(updated)


@router.post(
    "/v1/leases/{lease_id}/stop",
    response_model=LeaseResponse,
)
async def stop_lease(
    lease_id: PathId,
    body: LeaseStop | None = None,
    pool: Any = Depends(_pool),
    redis_client: Any | None = Depends(_redis_client),
) -> dict[str, Any]:
    result = await run_teardown(
        lease_id,
        pool=pool,
        redis_client=redis_client,
        reason="operator",
        terminated_reason=body.reason if body is not None else None,
    )
    return lease_to_response(result.lease)


@router.delete(
    "/v1/leases/{lease_id}",
    status_code=204,
    response_class=Response,
)
async def delete_lease(
    lease_id: PathId,
    pool: Any = Depends(_pool),
    redis_client: Any | None = Depends(_redis_client),
) -> Response:
    with contextlib.suppress(LeaseNotFound):
        await run_teardown(
            lease_id,
            pool=pool,
            redis_client=redis_client,
            reason="operator",
            terminated_reason="delete",
        )
    return Response(status_code=204)


__all__ = ["router"]
