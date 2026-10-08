"""Audit checks 07, 15, 16: webhook dedupe, idempotent terminate, kill switch and lease verbs."""

from __future__ import annotations

from typing import Any

from pitwall.audit import _probes
from pitwall.audit._common import (
    ADMIN_KILL_SWITCH_ROUTE_PATH,
    KILL_SWITCH_STEPS,
    LEASE_STOP_ROUTE_PATH,
    AuditConfig,
    CheckFailed,
    _bool_config,
    _int_config,
    _str_list,
)
from pitwall.audit._introspect import facts_of


def _router_has_method(router: Any, path: str, method: str) -> bool:
    for route in getattr(router, "routes", ()):
        route_path = getattr(route, "path", None)
        methods: set[str] = getattr(route, "methods", set())
        if route_path == path and method in methods:
            return True
    return False


def _check_l15_verb_separation() -> None:
    from pitwall.api.admin import emergency
    from pitwall.api.routes import leases as lease_routes

    if not _router_has_method(lease_routes.router, LEASE_STOP_ROUTE_PATH, "POST"):
        raise CheckFailed(15, f"single-lease stop route missing: {LEASE_STOP_ROUTE_PATH}")
    if not _router_has_method(emergency.router, ADMIN_KILL_SWITCH_ROUTE_PATH, "POST"):
        raise CheckFailed(
            15,
            f"account-wide kill switch route missing: {ADMIN_KILL_SWITCH_ROUTE_PATH}",
        )
    if LEASE_STOP_ROUTE_PATH == ADMIN_KILL_SWITCH_ROUTE_PATH:
        raise CheckFailed(15, "single-lease stop and account kill switch share a route")
    if not facts_of(lease_routes.stop_lease).references("run_teardown"):
        raise CheckFailed(15, "single-lease stop route does not delegate to lease teardown")
    if not facts_of(emergency.run_kill).references("persist_kill_report"):
        raise CheckFailed(15, "admin kill switch does not persist an account-wide audit log")
    if not facts_of(emergency.activate_kill_switch).has_string_containing("rest:admin"):
        raise CheckFailed(15, "admin kill switch route does not use the admin audit actor")


def _check_l16_patch_validation() -> None:
    from pitwall.api.routes import leases as lease_routes
    from pitwall.api.schemas.leases import LeasePatch, lease_patch_conflicting_fields

    if not _router_has_method(lease_routes.router, "/v1/leases/{lease_id}", "PATCH"):
        raise CheckFailed(16, "lease PATCH route missing")

    conflicts = lease_patch_conflicting_fields(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:sha-1",
            "gpuTypeIds": ["NVIDIA L4"],
            "volume_id": "vol-model-cache",
        }
    )
    if conflicts != ["image_ref", "gpuTypeIds", "volume_id"]:
        raise CheckFailed(16, f"lease PATCH multi-axis validation returned {conflicts!r}")

    single_axis = LeasePatch.model_validate(
        {
            "image_ref": "ghcr.io/acme/pitwall-worker:sha-1",
            "template_name": "pitwall-qwen3",
        }
    )
    if lease_patch_conflicting_fields(single_axis):
        raise CheckFailed(16, "lease PATCH rejected a single-axis image/template change")

    if not facts_of(lease_routes.patch_lease).before(
        "lease_patch_conflicting_fields", "patch_lease_settings"
    ):
        raise CheckFailed(16, "lease PATCH validation does not run before repository access")


def check_07_webhook_idempotent_fast200(cfg: AuditConfig) -> str:
    wc = cfg.webhook_config()
    if not _bool_config(wc.get("idempotent")):
        raise CheckFailed(7, "webhook receiver is not idempotent")
    if not _bool_config(wc.get("fast_200")):
        raise CheckFailed(7, "webhook receiver does not fast-200")
    # The receiver validates its runtime environment when imported, so the audit reads
    # its handler from source and exercises the repository dedupe path it delegates to.
    try:
        handlers = _probes.webhook_handlers()
    except ModuleNotFoundError as exc:
        raise CheckFailed(7, "webhook receiver module not found") from exc
    if not handlers:
        raise CheckFailed(7, "webhook receiver has no RunPod POST endpoint")
    if not _probes.webhook_dedupes_through_repository():
        raise CheckFailed(7, "webhook receiver does not dedupe deliveries through insert_or_skip")
    if not _probes.webhook_repository_skips_duplicates():
        raise CheckFailed(
            7, "webhook delivery repository does not skip a repeated (job, attempt) delivery"
        )
    return (
        "webhook receiver records each delivery through insert_or_skip and a repeated "
        "(job, attempt) delivery is skipped"
    )


def check_15_terminate_idempotent(cfg: AuditConfig) -> str:
    tc = cfg.terminate_config()
    if not _bool_config(tc.get("treat_404_as_success")):
        raise CheckFailed(
            15,
            "terminate calls do not treat 404 as success — not idempotent",
        )
    if not _probes.terminate_treats_404_as_success():
        raise CheckFailed(15, "terminate_pod_sync raised on a 404 response")
    _check_l15_verb_separation()
    return "terminate calls are idempotent; stop and kill-switch verbs are separated"


def check_16_kill_switch_atomic(cfg: AuditConfig) -> str:
    kc = cfg.kill_switch_config()
    if not _bool_config(kc.get("atomic")):
        raise CheckFailed(16, "kill switch is not atomic")
    steps = tuple(_str_list(kc.get("steps", [])))
    if steps != KILL_SWITCH_STEPS:
        raise CheckFailed(
            16,
            f"kill switch steps {steps!r} != {KILL_SWITCH_STEPS!r}",
        )
    budget_s = kc.get("budget_s")
    if budget_s is None:
        raise CheckFailed(16, "kill switch budget_s not configured")
    budget = _int_config(16, "budget_s", budget_s)
    if budget >= 30:
        raise CheckFailed(
            16,
            f"kill switch budget ({budget_s}s) exceeds 30s limit",
        )
    _check_l16_patch_validation()
    return (
        "kill switch latches before teardown and its configured budget is under 30s; "
        "lease PATCH is single-axis"
    )
