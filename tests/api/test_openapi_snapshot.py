"""Task 10: OpenAPI schema generates and contains the expected paths.

Guards against route regressions and produces the artifact release program fuzzes with
schemathesis. Asserts app.openapi() builds without error and that a
representative set of Pitwall paths is present.
"""

from __future__ import annotations

from tests.conftest import _env_for_app, _import_app

_EXPECTED_PATHS = {
    "/v1/capabilities",
    "/v1/capabilities/{name}",
    "/v1/admin/capabilities",
    "/v1/admin/capabilities/{capability_id}",
    "/v1/providers",
    "/v1/providers/{provider_id}",
    "/v1/provider-ops/descriptors",
    "/v1/provider-ops/descriptors/{provider_id}",
    "/v1/provider-ops/{provider_id}/availability",
    "/v1/provider-ops/{provider_id}/health",
    "/v1/admin/providers",
    "/v1/inference",
    "/v1/cost/summary",
    "/v1/cost/workloads",
    "/v1/cost/burn-rate",
    "/v1/guardrails",
    "/v1/guardrails/preview",
    "/v1/volumes/{volume_id}/objects",
    "/v1/volumes/{volume_id}/objects/{object_key}",
    "/v1/admin/volumes/{volume_id}/objects",
    "/v1/admin/volumes/{volume_id}/objects/{object_key}",
    "/v1/pods/{pod_id}/logs",
    "/v1/admin/runpod/pods",
    "/v1/admin/runpod/pods/{resource_id}",
    "/v1/admin/runpod/pods/{resource_id}/action",
    "/v1/admin/runpod/endpoints",
    "/v1/admin/runpod/endpoints/{resource_id}",
    "/v1/admin/runpod/templates",
    "/v1/admin/runpod/templates/{resource_id}",
    "/v1/admin/runpod/volumes",
    "/v1/admin/runpod/volumes/{resource_id}",
    "/v1/admin/runpod/registry-auths",
    "/v1/admin/runpod/registry-auths/{resource_id}",
    "/v1/admin/runpod/registry-auths/{resource_id}/replace",
    "/v1/admin/runpod/onboarding/plan",
    "/v1/admin/runpod/onboarding/apply",
    "/v1/admin/runpod/onboarding/status",
    "/v1/admin/runpod/onboarding/resume",
    "/v1/admin/runpod/onboarding/rollback",
    "/v1/admin/runpod/hub/templates",
    "/v1/admin/runpod/hub/templates/search",
    "/v1/admin/runpod/hub/templates/{resource_id}",
    "/v1/runpod/catalogue",
    "/v1/serve",
    "/v1/jobs/{workload_id}",
    "/v1/jobs",
    "/v1/jobs/{workload_id}/cancel",
    "/v1/jobs/{workload_id}/events",
    "/v1/jobs/{workload_id}/result",
    "/v1/jobs/{workload_id}/status",
    "/v1/routing/preview",
    "/v1/leases",
    "/v1/leases/{lease_id}",
    "/v1/admin/kill-switch",
    "/v1/admin/audit-capability/{name}",
    "/v1/webhook-subscriptions",
}


def test_openapi_builds_and_has_paths() -> None:
    mod = _import_app(_env_for_app())
    schema = mod.app.openapi()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"]
    paths = set(schema["paths"].keys())
    missing = _EXPECTED_PATHS - paths
    assert not missing, f"OpenAPI missing expected paths: {sorted(missing)}"


def test_openapi_is_deterministic() -> None:
    mod = _import_app(_env_for_app())
    a = mod.app.openapi()
    b = mod.app.openapi()
    assert set(a["paths"].keys()) == set(b["paths"].keys())


def test_runpod_resource_openapi_uses_read_and_admin_scopes() -> None:
    mod = _import_app(_env_for_app())
    paths = mod.app.openapi()["paths"]

    assert paths["/v1/admin/runpod/pods"]["get"]["x-required-scope"] == "read"
    assert paths["/v1/admin/runpod/pods"]["post"]["x-required-scope"] == "server:admin"


def test_provider_operations_openapi_uses_read_scope() -> None:
    mod = _import_app(_env_for_app())
    paths = mod.app.openapi()["paths"]

    for path in (
        "/v1/provider-ops/descriptors",
        "/v1/provider-ops/descriptors/{provider_id}",
        "/v1/provider-ops/{provider_id}/availability",
        "/v1/provider-ops/{provider_id}/health",
    ):
        assert paths[path]["get"]["x-required-scope"] == "read"
