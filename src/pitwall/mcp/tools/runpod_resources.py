"""Thin MCP tools for the shared RunPod resource control-plane service.

``RUNPOD_RESOURCE_TOOL_SPECS`` is the feature manifest consumed by the
canonical global registry, keeping every tool bound to the shared service.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mcp.shared.exceptions import MCPError
from pydantic import BaseModel

from pitwall.api.leases.launch import (
    admit_raw_pod_lease,
    admit_resource_mutation,
    preview_raw_pod_lease_budget,
    raw_pod_lease,
)
from pitwall.core.models import Lease
from pitwall.db import get_pool
from pitwall.db.repository import LeaseRepository, WorkloadRepository
from pitwall.mcp.error_adapter import adapt_error
from pitwall.mcp.safe_boundary import BUDGET_REMEDY
from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointUpdateRequest,
    IdentifiedMutationRequest,
    MutationRequest,
    PodActionRequest,
    PodCreateRequest,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    TemplateCreateRequest,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
)

log = logging.getLogger("pitwall.mcp.tools.runpod_resources")


async def _service(*, mutation: bool = False) -> RunPodControlPlaneService:
    return RunPodControlPlaneService(
        audit_pool=await get_pool() if mutation else None,
        actor="mcp:admin" if mutation else "mcp",
    )


def _is_budget_rejection(exc: Exception) -> bool:
    """Whether *exc* is a budget-admission refusal (surfaces as ``budget_rejected``)."""
    if getattr(exc, "error_code", None) in ("budget_rejected", "budget_exhausted"):
        return True
    if getattr(exc, "code", None) == "budget_rejected":
        return True
    return exc.__class__.__name__ in ("BudgetRejected", "BudgetExhausted")


async def _admit_mutation() -> None:
    """Run the D6(b) budget gate for one non-pod resource mutation.

    Single gate for all mutating tools: checks budget availability before any
    provider I/O and adapts a refusal to a ``budget_rejected`` ``MCPError``. Recording
    stays with the control-plane service, which writes a ``config_audit`` row
    for every applied mutation (``_service(mutation=True)`` wires the audit
    pool; previews and reads never touch it).
    """
    pool = await get_pool()
    try:
        await admit_resource_mutation(pool)
    except Exception as exc:  # reason: inspect any admission failure, adapt budget rejections, re-raise the rest unchanged
        if _is_budget_rejection(exc):
            raise adapt_error(exc) from exc
        raise


def _dump(value: BaseModel) -> dict[str, Any]:
    result: dict[str, Any] = value.model_dump(mode="json")
    return result


def _dump_many(values: Sequence[BaseModel], key: str) -> dict[str, Any]:
    return {key: [_dump(value) for value in values]}


async def _call[T](
    operation: Callable[[RunPodControlPlaneService], Awaitable[T]],
    *,
    mutation: bool = False,
    request: MutationRequest | None = None,
    admit_budget: bool = True,
) -> T:
    """Run one control-plane call, surfacing a known ``RunPodControlPlaneError``.

    A ``RunPodControlPlaneError`` is a stable, already-redacted service
    failure (see ``RunPodControlPlaneError.to_dict``), so it is adapted to an
    ``MCPError`` carrying that same structured code. Any other exception is
    left to propagate: ``install_safe_call_boundary`` remains the backstop
    that degrades a genuinely unexpected failure to ``tool_execution_failed``.

    When *request* is given for a live (non-preview) mutation, the D6(b)
    budget gate runs first: exhausted budgets refuse before any provider I/O,
    and the service's ``config_audit`` write records the applied mutation.
    Previews and reads pass no request and are never gated or recorded.
    (``pitwall_runpod_create_pod`` carries its own TTL-based admission and
    does not pass *request* here, so it is admitted exactly once.)

    *admit_budget* is False for the mutations whose only effect is to stop or
    remove billing (terminate, stop, delete). An exhausted budget must never
    refuse the operations that end spend; they are still audited.
    """
    if mutation and request is not None and not request.dry_run and admit_budget:
        await _admit_mutation()
    try:
        service = await _service(mutation=mutation)
        return await operation(service)
    except RunPodControlPlaneError as exc:
        raise adapt_error(exc) from exc


async def pitwall_runpod_list_pods() -> dict[str, Any]:
    return _dump_many(await _call(lambda service: service.list_pods()), "pods")


async def pitwall_runpod_get_pod(resource_id: str) -> dict[str, Any]:
    return _dump(await _call(lambda service: service.get_pod(resource_id)))


async def pitwall_runpod_create_pod(request: PodCreateRequest) -> dict[str, Any]:
    # D6(a) lease wrapping — ttl_minutes is required for every pod create (preview or apply).
    # This enforces the new contract before any provider I/O.
    ttl = getattr(request, "ttl_minutes", None)
    if ttl is None or not isinstance(ttl, int) or ttl < 1:
        raise adapt_error(
            RunPodControlPlaneError(
                "invalid_request",
                "ttl_minutes is required and must be >= 1",
                operation="pod.create",
                resource_type="pod",
            )
        )

    # The lease stores the cap at 4 decimal places and must be above $0. A smaller cap would
    # fail lease validation only after the pod exists, so refuse it before any provider I/O.
    cap = request.max_cost_per_hour
    if cap is not None and cap.quantize(Decimal("0.0001")) <= 0:
        raise adapt_error(
            RunPodControlPlaneError(
                "invalid_request",
                "max_cost_per_hour must be at least 0.0001 USD/hour",
                operation="pod.create",
                resource_type="pod",
            )
        )

    # Preview path: validate, budget-free, no lease row. The underlying service already
    # returns a preview MutationResult without touching RunPod or the audit store.
    if request.dry_run:
        result = _dump(await _call(lambda service: service.create_pod(request), mutation=False))
        budget = await preview_raw_pod_lease_budget(
            await get_pool(), ttl_minutes=ttl, max_cost_per_hour=request.max_cost_per_hour
        )
        if not budget.get("admitted"):
            budget.setdefault("remedy", BUDGET_REMEDY)
        result["budget"] = budget
        return result

    # Mutation path: budget admission → pod create → lease row → return lease_id.
    # Delegates budget admission to the lease service entry point so this MCP
    # module does not import pitwall.cost directly (hermetic MCP guard). The
    # existing reconciler converges the lease (TTL/teardown/cost-close); no
    # second sweeper.
    pool = await get_pool()
    try:
        admission = await admit_raw_pod_lease(
            pool,
            ttl_minutes=ttl,
            max_cost_per_hour=request.max_cost_per_hour,
            idempotency_key=request.idempotency_key,
        )
    except Exception as exc:  # reason: adapt budget rejections; re-raise others
        # Adapt budget rejections to the budget class code; leave other exceptions to the boundary.
        if _is_budget_rejection(exc):
            raise adapt_error(exc) from exc
        raise

    # Workload id comes from the admission (BudgetAdmission) or a plain string.
    workload_id: str | None = None
    if isinstance(admission, str):
        workload_id = admission
    else:
        workload_id = getattr(admission, "workload_id", None)

    # Close the admitted workload so its ceiling stops counting against the
    # monthly budget. Never blocks the original failure; mirrors the teardown
    # audit-failure pattern.
    async def _close_workload_failed() -> None:
        if not workload_id:
            return
        try:
            # One statement closes the workload and frees its idempotency key: a same-key
            # retry must admit a fresh workload through the budget check, not reuse this
            # zero-cost one, and no admission can slip between the close and the release.
            await WorkloadRepository(pool).fail_and_release_idempotency_key(
                workload_id,
                cost_actual_provenance="raw_pod_create_failed",
                cost_reconciled_at=dt.datetime.now(dt.UTC),
            )
        except Exception as exc:  # reason: rollback bookkeeping must not mask the failure
            log.warning(
                "raw-pod create failure: workload close failed: workload=%s",
                workload_id,
                exc_info=exc,
            )

    # The lease row is required for TTL convergence; a lease-insert failure
    # after a successful pod create must not leave a billable pod behind, so
    # best-effort terminate the pod, close the admitted workload, then let the
    # original failure surface.
    def _new_lease(pod_id: str) -> Lease:
        # ``max_usd_per_hour`` is only the caller's cap; an uncapped lease keeps it null. Its
        # settlement reads RunPod's price from the create's journal record instead
        # (``teardown.raw_pod_observed_usd_per_hour``).
        return raw_pod_lease(
            pod_id=pod_id,
            workload_id=workload_id,
            ttl_minutes=ttl,
            max_cost_per_hour=request.max_cost_per_hour,
        )

    try:
        mutation_result = await _call(lambda service: service.create_pod(request), mutation=True)
    except Exception as exc:  # reason: settle or hold this attempt's workload, then re-raise
        if getattr(exc.__cause__, "code", None) in _JOURNAL_REFUSALS:
            # A refusal against a workload an earlier attempt admitted leaves it open: that
            # attempt's pod may exist, so its ceiling must keep counting. A workload this
            # call admitted is closed as usual.
            if getattr(admission, "is_new", True) is not False:
                await _close_workload_failed()
            raise
        if not await _pod_create_outcome_unknown(request.idempotency_key):
            await _close_workload_failed()
            raise
        # The journal kept this attempt ``started``: a pod may exist and bill. The workload
        # stays open with its reservation for the reconciler to settle. A known pod id gets
        # its lease, so the lease sweep closes the workload at accrued cost once the pod is
        # confirmed absent or reaches its TTL teardown. Without an id, the orphaned-workload
        # reaper adopts only the pod carrying its attempt marker
        # (``reconciler.reap_orphaned_workloads``).
        held_pod_id = _created_pod_id(exc)
        if held_pod_id is not None:
            try:
                await LeaseRepository(pool).create(_new_lease(held_pod_id))
            except Exception as lease_exc:  # reason: the create failure, not the lease, surfaces
                log.warning(
                    "raw-pod create outcome unknown: lease insert failed: pod=%s workload=%s",
                    held_pod_id,
                    workload_id,
                    exc_info=lease_exc,
                )
        log.error(
            "raw-pod create outcome unknown: workload %s stays open with its reservation for "
            "the reconciler; pod=%s idempotency_key=%s",
            workload_id,
            held_pod_id,
            request.idempotency_key,
        )
        raise

    # Derive the pod identifier for the lease row.
    pod_id: str | None = getattr(mutation_result, "resource_id", None)
    if not pod_id:
        resource = getattr(mutation_result, "resource", None)
        if isinstance(resource, dict):
            pod_id = resource.get("id")
    if not isinstance(pod_id, str) or not pod_id:
        # The provider may have created a pod we cannot name, so this is not a
        # clean failure: no lease can track it, and the admitted workload stays
        # open so its ceiling keeps counting until the operator resolves it.
        log.error(
            "raw-pod create returned no pod id: idempotency_key=%s result_keys=%s",
            request.idempotency_key,
            sorted((mutation_result.resource or {}).keys()),
        )
        raise adapt_error(
            RunPodControlPlaneError(
                "malformed_provider_response",
                "RunPod reported a pod create without a pod id; no lease was recorded. "
                "Check the RunPod console for a pod named "
                f"{request.name!r} and terminate it by hand if it exists.",
                operation="pod.create",
                resource_type="pod",
                changed=True,
            )
        )

    if mutation_result.replayed:
        # The first attempt with this key created the pod and, normally, its lease; return
        # that lease instead of tracking one pod twice. A replay with no lease row (the first
        # attempt stopped before its lease insert, or its rollback could not terminate the
        # pod) records the lease now. A rollback that did terminate the pod released the key.
        recorded = await LeaseRepository(pool).latest_for_external_resource("runpod_direct", pod_id)
        if recorded is None and not await _replayed_pod_exists(pod_id):
            if getattr(admission, "is_new", True) is False and workload_id:
                # The original attempt's workload, kept open after a failed rollback or an
                # unreadable lease store: the pod existed and billed. Lease it by its id and
                # settle that lease now, at accrued cost from the attempt's start, rather
                # than closing the workload at $0.
                await _lease_gone_replayed_pod(pool, request, pod_id, workload_id, ttl)
            else:
                # This call's own fresh workload never got a pod: close it at $0 and free
                # its key, or a later retry would skip the budget check.
                await _close_workload_failed()
            raise adapt_error(
                RunPodControlPlaneError(
                    "resource_not_found",
                    "the pod this idempotency_key created no longer exists; retry with a new "
                    "idempotency_key",
                    operation="pod.create",
                    resource_type="pod",
                    resource_id=pod_id,
                )
            )
        if recorded is not None:
            lease_workload_id = recorded.workload_id
            if (
                getattr(admission, "is_new", False) is True
                and lease_workload_id is not None
                and lease_workload_id != workload_id
            ):
                # The lease belongs to another attempt's workload. This call's freshly
                # admitted workload will never get a lease; close it rather than leave a
                # phantom that the orphan reaper later charges at its full ceiling.
                await _close_workload_failed()
            replayed = _dump(mutation_result)
            replayed["lease_id"] = recorded.id
            reported_workload_id = lease_workload_id or workload_id
            if reported_workload_id is not None:
                replayed["workload_id"] = reported_workload_id
            replayed["ttl_minutes"] = ttl
            replayed["expires_at"] = recorded.expires_at.isoformat()
            return replayed

    lease = _new_lease(pod_id)
    lease_id = lease.id
    expires_at = lease.expires_at

    # Persist the lease so the reconciler can drive TTL/teardown/cost-close.
    # A failure here must not silently leave a billable pod without a lease
    # row: best-effort terminate the known pod, close the admitted workload,
    # then let the original exception surface so the caller observes the
    # partial failure.
    repo = LeaseRepository(pool)
    try:
        await repo.create(lease)
    except Exception as insert_exc:
        # The orphaned-workload reaper may have leased this pod for this workload first
        # (one lease per workload). That lease already tracks the pod: return it rather
        # than destroy a pod the caller wanted.
        existing, ownership_known = await _lease_recorded_meanwhile(pool, pod_id, workload_id)
        if existing is not None:
            return _leased_result(
                mutation_result, existing.id, workload_id, ttl, existing.expires_at
            )
        if not ownership_known:
            # The lease store answered neither the insert nor the re-read, so a lease for
            # this pod may exist. Never terminate a pod whose ownership is unknown: keep
            # the workload open with its key; the journal's ``completed`` row names the
            # pod, so the reaper leases it by that id once the database is back, and TTL
            # teardown bounds its cost.
            log.error(
                "lease insert and lease re-read both failed: pod %s kept, workload %s stays "
                "open with its key for the reconciler; idempotency_key=%s",
                pod_id,
                workload_id,
                request.idempotency_key,
            )
            raise adapt_error(
                RunPodControlPlaneError(
                    "audit_unavailable",
                    "the pod was created but its lease could not be recorded because the "
                    "broker database is unavailable; the pod is kept and tracked by the "
                    "reconciler. Retry with the same idempotency_key once the database is "
                    "available to receive its lease",
                    operation="pod.create",
                    resource_type="pod",
                    resource_id=pod_id,
                    retryable=True,
                    changed=True,
                )
            ) from insert_exc
        terminated = False
        if pod_id is not None:
            service: RunPodControlPlaneService | None = None
            try:
                service = await _service(mutation=True)
                await service.terminate_pod(
                    IdentifiedMutationRequest(
                        intent="apply",
                        idempotency_key=f"{lease_id}:rollback",
                        resource_id=pod_id,
                    )
                )
            except Exception as exc:  # reason: termination is best-effort only
                service = None
                log.warning(
                    "lease insert failure rollback terminate failed: pod=%s",
                    pod_id,
                    exc_info=exc,
                )
            if service is not None:
                terminated = True
                # The pod is gone: free the create key so a retry creates a new pod
                # instead of replaying the terminated one.
                try:
                    await service.release_idempotency_key(
                        request.idempotency_key,
                        reason="lease insert failed; the created pod was terminated",
                    )
                except Exception as exc:  # reason: the lease failure, not the release, surfaces
                    log.warning(
                        "lease insert failure rollback: pod %s terminated but its create "
                        "key release failed; a same-key retry is refused, not replayed",
                        pod_id,
                        exc_info=exc,
                    )
        if terminated:
            await _close_workload_failed()
        else:
            # The pod may still bill. Keep the workload open with its key: the journal
            # holds ``completed`` with this pod id, so the orphaned-workload reaper leases
            # the pod by that id and the lease sweep settles it at accrued cost.
            log.error(
                "lease insert failure rollback could not terminate pod %s; workload %s stays "
                "open with its key for the reconciler",
                pod_id,
                workload_id,
            )
        raise

    return _leased_result(mutation_result, lease_id, workload_id, ttl, expires_at)


async def _replayed_pod_exists(pod_id: str) -> bool:
    """Whether a replayed create's pod still exists and may be leased.

    A 404 or a ``terminated`` pod is gone, the way the lease reconciler treats a
    ``TERMINATED`` desired status. An ``exited`` pod still exists, bills storage, and
    belongs under the lease's TTL teardown. Any other read failure propagates.
    """
    try:
        pod = await _call(lambda service: service.get_pod(pod_id))
    except MCPError as exc:
        if getattr(exc.__cause__, "code", None) == "resource_not_found":
            return False
        raise
    return pod.status != "terminated"


async def _pod_create_outcome_unknown(idempotency_key: str) -> bool:
    """Whether a failed pod create left its journal entry ``started`` (outcome unknown).

    A pod create that reached RunPod never records ``failed`` (its fallback attempts can
    leave a pod behind), so ``started`` after a failure means a pod may exist. No entry, or
    a ``failed`` or ``compensated`` one, means the attempt stopped before its ``started``
    row and created nothing. An unreadable journal counts as unknown: closing the workload
    at $0 would drop a live pod's spend.
    """
    try:
        service = await _service(mutation=True)
        entry = await service.idempotency_key_status(idempotency_key)
    except Exception as exc:  # reason: an unreadable journal must never read as a clean failure
        log.warning(
            "raw-pod create failure: journal status read failed: idempotency_key=%s",
            idempotency_key,
            exc_info=exc,
        )
        return True
    return entry is not None and entry.state == "started"


async def _lease_recorded_meanwhile(
    pool: Any, pod_id: str, workload_id: str | None
) -> tuple[Any | None, bool]:
    """A lease for *pod_id* bound to *workload_id* that another writer recorded, if any.

    The second value is whether the lease store answered: ``False`` means ownership of
    the pod is unknown, and the caller must not terminate it.
    """
    try:
        recorded = await LeaseRepository(pool).latest_for_external_resource("runpod_direct", pod_id)
    except Exception as exc:  # reason: an unreadable lease store leaves ownership unknown
        log.warning("lease re-read after insert failure failed: pod=%s", pod_id, exc_info=exc)
        return None, False
    if (
        workload_id is None
        or recorded is None
        or getattr(recorded, "workload_id", None) != workload_id
    ):
        return None, True
    return recorded, True


async def _lease_gone_replayed_pod(
    pool: Any,
    request: PodCreateRequest,
    pod_id: str,
    workload_id: str,
    ttl: int,
) -> None:
    """Lease a replayed create's gone pod by its id, then settle that lease at once.

    The lease counts from the attempt's start (the key's newest journal ``started`` row),
    as the orphaned-workload reaper's does. The pod was just confirmed gone, so the lease is
    settled now as ``pod_absent`` through ``settle_absent_raw_pod_lease`` in
    ``pitwall.api.leases.teardown`` (the reconciler's sweep uses the same function): its cost
    closes at ``rate × (now − attempt start)`` and the workload's reservation is released,
    rather than waiting for the lease sweep, which probes only leases near expiry. If the
    journal or the lease store cannot answer, the workload stays open with its key and the
    reaper leases the pod by the same id.
    """
    try:
        service = await _service(mutation=True)
        entry = await service.idempotency_key_status(request.idempotency_key)
        if entry is None or entry.attempt_age_s is None:
            raise LookupError("no journal started row for the replayed create")
        now = dt.datetime.now(dt.UTC)
        lease = raw_pod_lease(
            pod_id=pod_id,
            workload_id=workload_id,
            ttl_minutes=ttl,
            max_cost_per_hour=request.max_cost_per_hour,
            created_at=now - dt.timedelta(seconds=entry.attempt_age_s),
        )
        await LeaseRepository(pool).create(lease)
    except Exception as exc:  # reason: the gone-pod refusal, not this lease, reaches the caller
        log.warning(
            "replayed create: pod %s is gone and its lease was not recorded; workload %s "
            "stays open for the reconciler",
            pod_id,
            workload_id,
            exc_info=exc,
        )
        return
    # Imported from the teardown module, which has no import-time configuration checks;
    # importing the reconciler here could raise SystemExit on an MCP-valid config.
    from pitwall.api.leases.teardown import settle_absent_raw_pod_lease
    from pitwall.redis_env import optional_redis_from_env

    try:
        async with optional_redis_from_env() as redis_client:
            await settle_absent_raw_pod_lease(pool, redis_client, lease.id, now=now)
    except Exception as exc:  # reason: the gone-pod refusal, not the settlement, reaches the caller
        log.warning(
            "replayed create: lease %s for gone pod %s was recorded but not settled; the "
            "lease sweep settles it",
            lease.id,
            pod_id,
            exc_info=exc,
        )


def _leased_result(
    mutation_result: BaseModel,
    lease_id: str,
    workload_id: str | None,
    ttl: int,
    expires_at: dt.datetime,
) -> dict[str, Any]:
    dumped = _dump(mutation_result)
    dumped["lease_id"] = lease_id
    if workload_id is not None:
        dumped["workload_id"] = workload_id
    dumped["ttl_minutes"] = ttl
    dumped["expires_at"] = expires_at.isoformat()
    return dumped


def _created_pod_id(exc: BaseException) -> str | None:
    """The pod id a failed create's error names, as the failed-create audit row records it."""
    error = exc.__cause__ if isinstance(exc, MCPError) else exc
    pod_id = getattr(error, "resource_id", None) or getattr(
        getattr(error, "__cause__", None), "pod_id", None
    )
    return pod_id if isinstance(pod_id, str) and pod_id else None


#: Control-plane journal refusals: the key belongs to an earlier attempt of this request.
_JOURNAL_REFUSALS = frozenset(
    {"idempotency_conflict", "mutation_in_progress", "mutation_outcome_ambiguous"}
)


async def pitwall_runpod_update_pod(request: PodUpdateRequest) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.update_pod(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_action_pod(request: PodActionRequest) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.action_pod(request),
            mutation=not request.dry_run,
            request=request,
            admit_budget=request.action != "stop",
        )
    )


async def pitwall_runpod_terminate_pod(
    request: IdentifiedMutationRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.terminate_pod(request),
            mutation=not request.dry_run,
            request=request,
            admit_budget=False,
        )
    )


async def pitwall_runpod_list_endpoints() -> dict[str, Any]:
    return _dump_many(await _call(lambda service: service.list_endpoints()), "endpoints")


async def pitwall_runpod_get_endpoint(resource_id: str) -> dict[str, Any]:
    return _dump(await _call(lambda service: service.get_endpoint(resource_id)))


async def pitwall_runpod_create_endpoint(
    request: EndpointCreateRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.create_endpoint(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_update_endpoint(
    request: EndpointUpdateRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.update_endpoint(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_delete_endpoint(
    request: IdentifiedMutationRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.delete_endpoint(request),
            mutation=not request.dry_run,
            request=request,
            admit_budget=False,
        )
    )


async def pitwall_runpod_list_templates() -> dict[str, Any]:
    return _dump_many(await _call(lambda service: service.list_templates()), "templates")


async def pitwall_runpod_get_template(resource_id: str) -> dict[str, Any]:
    return _dump(await _call(lambda service: service.get_template(resource_id)))


async def pitwall_runpod_create_template(
    request: TemplateCreateRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.create_template(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_update_template(
    request: TemplateUpdateRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.update_template(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_delete_template(
    request: IdentifiedMutationRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.delete_template(request),
            mutation=not request.dry_run,
            request=request,
            admit_budget=False,
        )
    )


async def pitwall_runpod_list_volumes() -> dict[str, Any]:
    return _dump_many(await _call(lambda service: service.list_volumes()), "volumes")


async def pitwall_runpod_get_volume(resource_id: str) -> dict[str, Any]:
    return _dump(await _call(lambda service: service.get_volume(resource_id)))


async def pitwall_runpod_create_volume(request: VolumeCreateRequest) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.create_volume(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_grow_volume(request: VolumeGrowRequest) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.grow_volume(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_delete_volume(
    request: IdentifiedMutationRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.delete_volume(request),
            mutation=not request.dry_run,
            request=request,
            admit_budget=False,
        )
    )


async def pitwall_runpod_list_registry_auths() -> dict[str, Any]:
    return _dump_many(await _call(lambda service: service.list_registry_auths()), "registry_auths")


async def pitwall_runpod_get_registry_auth(resource_id: str) -> dict[str, Any]:
    return _dump(await _call(lambda service: service.get_registry_auth(resource_id)))


async def pitwall_runpod_create_registry_auth(
    request: RegistryAuthCreateRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.create_registry_auth(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_replace_registry_auth(
    request: RegistryAuthReplaceRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.replace_registry_auth(request),
            mutation=not request.dry_run,
            request=request,
        )
    )


async def pitwall_runpod_delete_registry_auth(
    request: IdentifiedMutationRequest,
) -> dict[str, Any]:
    return _dump(
        await _call(
            lambda service: service.delete_registry_auth(request),
            mutation=not request.dry_run,
            request=request,
            admit_budget=False,
        )
    )


async def pitwall_runpod_list_hub_templates(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return _dump_many(
        await _call(lambda service: service.list_hub_templates(limit=limit, offset=offset)),
        "hub_templates",
    )


async def pitwall_runpod_get_hub_template(resource_id: str) -> dict[str, Any]:
    return _dump(await _call(lambda service: service.get_hub_template(resource_id)))


async def pitwall_runpod_search_hub_templates(query: str, limit: int = 50) -> dict[str, Any]:
    return _dump_many(
        await _call(lambda service: service.search_hub_templates(query, limit=limit)),
        "hub_templates",
    )


@dataclass(frozen=True, slots=True)
class McpToolAddition:
    """Feature-local data needed for one global ``ToolSpec`` addition."""

    name: str
    description: str
    handler: Callable[..., Awaitable[dict[str, Any]]]


RUNPOD_RESOURCE_TOOL_SPECS: tuple[McpToolAddition, ...] = (
    McpToolAddition(
        "pitwall_runpod_list_pods", "List sanitized RunPod account pods.", pitwall_runpod_list_pods
    ),
    McpToolAddition(
        "pitwall_runpod_get_pod", "Get one sanitized RunPod account pod.", pitwall_runpod_get_pod
    ),
    McpToolAddition(
        "pitwall_runpod_create_pod",
        'Preview or create a raw RunPod pod as a lease — requires ttl_minutes, budget-admitted via BudgetGate, creates a pitwall.leases row and returns {"lease_id": …}. TTL-bounded: expired raw-pod leases are terminated by the existing lease expiry reconciler and cost-closed from max_cost_per_hour when supplied; raw pods expose no probe surface, so no readiness lifecycle applies.',
        pitwall_runpod_create_pod,
    ),
    McpToolAddition(
        "pitwall_runpod_update_pod",
        "Preview or update mutable fields on a raw RunPod pod.",
        pitwall_runpod_update_pod,
    ),
    McpToolAddition(
        "pitwall_runpod_action_pod",
        "Preview or start, stop, restart, or reset a raw RunPod pod.",
        pitwall_runpod_action_pod,
    ),
    McpToolAddition(
        "pitwall_runpod_terminate_pod",
        "Preview or idempotently terminate a raw RunPod pod.",
        pitwall_runpod_terminate_pod,
    ),
    McpToolAddition(
        "pitwall_runpod_list_endpoints",
        "List RunPod serverless endpoints and nested scaling state.",
        pitwall_runpod_list_endpoints,
    ),
    McpToolAddition(
        "pitwall_runpod_get_endpoint",
        "Get one RunPod serverless endpoint.",
        pitwall_runpod_get_endpoint,
    ),
    McpToolAddition(
        "pitwall_runpod_create_endpoint",
        "Preview or create a RunPod serverless endpoint.",
        pitwall_runpod_create_endpoint,
    ),
    McpToolAddition(
        "pitwall_runpod_update_endpoint",
        "Preview or replace nested endpoint workers/scaling/GPU selection.",
        pitwall_runpod_update_endpoint,
    ),
    McpToolAddition(
        "pitwall_runpod_delete_endpoint",
        "Preview or idempotently delete a serverless endpoint.",
        pitwall_runpod_delete_endpoint,
    ),
    McpToolAddition(
        "pitwall_runpod_list_templates",
        "List mutable account-owned RunPod templates.",
        pitwall_runpod_list_templates,
    ),
    McpToolAddition(
        "pitwall_runpod_get_template",
        "Get one mutable account-owned RunPod template.",
        pitwall_runpod_get_template,
    ),
    McpToolAddition(
        "pitwall_runpod_create_template",
        "Preview or create an account template without Hub publication.",
        pitwall_runpod_create_template,
    ),
    McpToolAddition(
        "pitwall_runpod_update_template",
        "Preview or update an account-owned RunPod template.",
        pitwall_runpod_update_template,
    ),
    McpToolAddition(
        "pitwall_runpod_delete_template",
        "Preview or idempotently delete an account template.",
        pitwall_runpod_delete_template,
    ),
    McpToolAddition(
        "pitwall_runpod_list_volumes", "List RunPod network volumes.", pitwall_runpod_list_volumes
    ),
    McpToolAddition(
        "pitwall_runpod_get_volume", "Get one RunPod network volume.", pitwall_runpod_get_volume
    ),
    McpToolAddition(
        "pitwall_runpod_create_volume",
        "Preview or create a RunPod network volume.",
        pitwall_runpod_create_volume,
    ),
    McpToolAddition(
        "pitwall_runpod_grow_volume",
        "Preview or grow a volume; shrink/equal sizes are rejected.",
        pitwall_runpod_grow_volume,
    ),
    McpToolAddition(
        "pitwall_runpod_delete_volume",
        "Preview or idempotently delete a network volume and its data.",
        pitwall_runpod_delete_volume,
    ),
    McpToolAddition(
        "pitwall_runpod_list_registry_auths",
        "List registry-auth IDs and names; credentials are never returned.",
        pitwall_runpod_list_registry_auths,
    ),
    McpToolAddition(
        "pitwall_runpod_get_registry_auth",
        "Get one registry-auth ID and name.",
        pitwall_runpod_get_registry_auth,
    ),
    McpToolAddition(
        "pitwall_runpod_create_registry_auth",
        "Preview or create registry auth from a credential environment reference.",
        pitwall_runpod_create_registry_auth,
    ),
    McpToolAddition(
        "pitwall_runpod_replace_registry_auth",
        "Preview or delete/recreate registry auth; partial failure is explicit.",
        pitwall_runpod_replace_registry_auth,
    ),
    McpToolAddition(
        "pitwall_runpod_delete_registry_auth",
        "Preview or idempotently delete registry auth.",
        pitwall_runpod_delete_registry_auth,
    ),
    McpToolAddition(
        "pitwall_runpod_list_hub_templates",
        "Read-only list of public RunPod Hub templates.",
        pitwall_runpod_list_hub_templates,
    ),
    McpToolAddition(
        "pitwall_runpod_get_hub_template",
        "Read-only get of one public RunPod Hub template.",
        pitwall_runpod_get_hub_template,
    ),
    McpToolAddition(
        "pitwall_runpod_search_hub_templates",
        "Read-only local search over public RunPod Hub templates.",
        pitwall_runpod_search_hub_templates,
    ),
)


__all__ = [
    "McpToolAddition",
    "RUNPOD_RESOURCE_TOOL_SPECS",
    *(spec.handler.__name__ for spec in RUNPOD_RESOURCE_TOOL_SPECS),
]
