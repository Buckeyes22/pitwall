"""Task 0: pin the full HTTP route table of the Pitwall control plane.

Any route added, removed, or re-pathed must update _EXPECTED below and the
contract test that covers it. This is the source-of-truth inventory for release program.
"""

from __future__ import annotations

from tests.api._route_helpers import iter_effective_routes
from tests.conftest import _env_for_app, _import_app

# (method, path) — one row per concrete (method, path) the router exposes.
_EXPECTED: set[tuple[str, str]] = {
    # health (app.py)
    ("GET", "/healthz"),
    ("GET", "/health"),
    ("GET", "/v1/health"),
    ("GET", "/readyz"),
    # capabilities (capability_routes.py)
    ("POST", "/v1/admin/capabilities"),
    ("PATCH", "/v1/admin/capabilities/{capability_id}"),
    ("POST", "/v1/admin/capabilities/{capability_id}/enable"),
    ("POST", "/v1/admin/capabilities/{capability_id}/disable"),
    ("GET", "/v1/capabilities"),
    ("GET", "/v1/capabilities/{name}"),
    # providers (provider_routes.py)
    ("POST", "/v1/admin/providers"),
    ("PATCH", "/v1/admin/providers/{provider_id}"),
    ("POST", "/v1/admin/providers/{provider_id}/enable"),
    ("POST", "/v1/admin/providers/{provider_id}/disable"),
    ("POST", "/v1/admin/providers/{provider_id}/hibernate"),
    ("GET", "/v1/providers"),
    ("GET", "/v1/providers/{provider_id}"),
    ("GET", "/v1/providers/{provider_id}/health"),
    ("GET", "/v1/provider-ops/descriptors"),
    ("GET", "/v1/provider-ops/descriptors/{provider_id}"),
    ("GET", "/v1/provider-ops/{provider_id}/availability"),
    ("GET", "/v1/provider-ops/{provider_id}/health"),
    # admin audit + emergency
    ("POST", "/v1/admin/audit-capability/{name}"),
    ("POST", "/v1/admin/kill-switch"),
    # runtime budget limits (routes/budget.py)
    ("GET", "/v1/admin/budget"),
    ("PUT", "/v1/admin/budget"),
    # resumable RunPod onboarding administration
    ("POST", "/v1/admin/runpod/onboarding/plan"),
    ("POST", "/v1/admin/runpod/onboarding/apply"),
    ("POST", "/v1/admin/runpod/onboarding/status"),
    ("POST", "/v1/admin/runpod/onboarding/resume"),
    ("POST", "/v1/admin/runpod/onboarding/rollback"),
    # inference (routes/inference.py)
    ("POST", "/v1/inference"),
    # FinOps and pre-spend governance reads/previews
    ("GET", "/v1/cost/summary"),
    ("GET", "/v1/cost/workloads"),
    ("GET", "/v1/cost/burn-rate"),
    ("GET", "/v1/guardrails"),
    ("POST", "/v1/guardrails/preview"),
    # bounded RunPod volume files and pod diagnostics
    ("GET", "/v1/volumes/{volume_id}/objects"),
    ("GET", "/v1/volumes/{volume_id}/objects/{object_key:path}"),
    ("POST", "/v1/admin/volumes/{volume_id}/objects"),
    ("DELETE", "/v1/admin/volumes/{volume_id}/objects/{object_key:path}"),
    ("GET", "/v1/pods/{pod_id}/logs"),
    # RunPod account resource administration
    ("GET", "/v1/admin/runpod/pods"),
    ("POST", "/v1/admin/runpod/pods"),
    ("GET", "/v1/admin/runpod/pods/{resource_id}"),
    ("PATCH", "/v1/admin/runpod/pods/{resource_id}"),
    ("POST", "/v1/admin/runpod/pods/{resource_id}/action"),
    ("DELETE", "/v1/admin/runpod/pods/{resource_id}"),
    ("GET", "/v1/admin/runpod/endpoints"),
    ("POST", "/v1/admin/runpod/endpoints"),
    ("GET", "/v1/admin/runpod/endpoints/{resource_id}"),
    ("PATCH", "/v1/admin/runpod/endpoints/{resource_id}"),
    ("DELETE", "/v1/admin/runpod/endpoints/{resource_id}"),
    ("GET", "/v1/admin/runpod/templates"),
    ("POST", "/v1/admin/runpod/templates"),
    ("GET", "/v1/admin/runpod/templates/{resource_id}"),
    ("PATCH", "/v1/admin/runpod/templates/{resource_id}"),
    ("DELETE", "/v1/admin/runpod/templates/{resource_id}"),
    ("GET", "/v1/admin/runpod/volumes"),
    ("POST", "/v1/admin/runpod/volumes"),
    ("GET", "/v1/admin/runpod/volumes/{resource_id}"),
    ("PATCH", "/v1/admin/runpod/volumes/{resource_id}"),
    ("DELETE", "/v1/admin/runpod/volumes/{resource_id}"),
    ("GET", "/v1/admin/runpod/registry-auths"),
    ("POST", "/v1/admin/runpod/registry-auths"),
    ("GET", "/v1/admin/runpod/registry-auths/{resource_id}"),
    ("POST", "/v1/admin/runpod/registry-auths/{resource_id}/replace"),
    ("DELETE", "/v1/admin/runpod/registry-auths/{resource_id}"),
    ("GET", "/v1/admin/runpod/hub/templates/search"),
    ("GET", "/v1/admin/runpod/hub/templates"),
    ("GET", "/v1/admin/runpod/hub/templates/{resource_id}"),
    ("GET", "/v1/runpod/catalogue"),
    # serve (routes/serve.py)
    ("POST", "/v1/serve"),
    # model catalogue (routes/models.py)
    ("GET", "/v1/models/catalogue"),
    ("GET", "/v1/models/catalogue/{model}"),
    ("GET", "/v1/models/catalogue/{model}/fit"),
    # production routing and jobs
    ("POST", "/v1/routing/preview"),
    ("POST", "/v1/jobs"),
    ("GET", "/v1/jobs/{workload_id}"),
    ("GET", "/v1/jobs/{workload_id}/status"),
    ("GET", "/v1/jobs/{workload_id}/result"),
    ("POST", "/v1/jobs/{workload_id}/cancel"),
    ("GET", "/v1/jobs/{workload_id}/events"),
    # leases (routes/leases.py)
    ("POST", "/v1/leases"),
    ("GET", "/v1/leases/{lease_id}"),
    ("PATCH", "/v1/leases/{lease_id}"),
    ("POST", "/v1/leases/{lease_id}/renew"),
    ("POST", "/v1/leases/{lease_id}/stop"),
    ("DELETE", "/v1/leases/{lease_id}"),
    # webhook subscriptions (routes/webhook_subscriptions.py)
    ("POST", "/v1/webhook-subscriptions"),
    ("GET", "/v1/webhook-subscriptions"),
    ("POST", "/v1/webhook-subscriptions/{subscription_id}/rotate-secret"),
    ("POST", "/v1/webhook-subscriptions/{subscription_id}/deactivate"),
    ("POST", "/v1/webhook-subscriptions/{subscription_id}/activate"),
    ("DELETE", "/v1/webhook-subscriptions/{subscription_id}"),
    # openai proxy (routes/openai.py) — one api_route, six methods
    ("GET", "/v1/openai/{capability}/v1/{path:path}"),
    ("POST", "/v1/openai/{capability}/v1/{path:path}"),
    ("PUT", "/v1/openai/{capability}/v1/{path:path}"),
    ("DELETE", "/v1/openai/{capability}/v1/{path:path}"),
    ("PATCH", "/v1/openai/{capability}/v1/{path:path}"),
    ("OPTIONS", "/v1/openai/{capability}/v1/{path:path}"),
    # quotas (routes/quotas.py) — Task 10
    ("GET", "/v1/quotas"),
    ("POST", "/v1/admin/quotas/refresh"),
    # gateway models (routes/gateway_models.py) — Task 10
    ("GET", "/v1/gateway/models"),
    # Claude-native surface (routes/messages.py) — Task 11
    ("POST", "/v1/messages"),
}

_IGNORED_AUTO_METHODS = {"HEAD"}  # Starlette auto-adds HEAD to GET routes.
# FastAPI auto-mounts interactive docs; these are framework freebies, not part
# of Pitwall's own contract surface, so they are excluded from the inventory.
_IGNORED_PATHS = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}


def _actual_routes(app) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in iter_effective_routes(app.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is None or methods is None:
            continue
        if path in _IGNORED_PATHS:
            continue
        for method in methods:
            if method in _IGNORED_AUTO_METHODS:
                continue
            out.add((method, path))
    return out


def test_route_inventory_matches_expected() -> None:
    mod = _import_app(_env_for_app())
    actual = _actual_routes(mod.app)
    missing = _EXPECTED - actual
    extra = actual - _EXPECTED
    assert not missing, f"expected routes not registered: {sorted(missing)}"
    assert not extra, f"unexpected routes registered: {sorted(extra)}"
