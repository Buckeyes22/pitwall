"""Task 5b: lease route contract (hermetic).

Routes: POST /v1/leases, GET/PATCH /v1/leases/{id}, POST /{id}/renew.
Deps via Depends (overridable): _lease_repo, _capability_repo, _provider_repo.

Verified vs source 2026-05-30:
  - create_lease: capability resolves by name, then id; neither -> CapabilityNotFound (404)
  - get/renew unknown id -> LeaseNotFound (404)
  - patch_lease checks lease_patch_conflicting_fields(raw_body) FIRST; a body
    spanning >=2 change-set axes (image / gpu / volume — see schemas/leases.py
    _CHANGE_SET_AXES) -> ChangeSetTooBroad (400), before the repo lookup.
  - accepted-but-unsupported compatibility fields are rejected with a stable
    422 before repository access.
"""

from __future__ import annotations

import datetime as dt
import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Lease, LeaseEndpoints, LeaseReadiness
from pitwall.db.repository import LeaseMutationResult
from tests.api._contract_helpers import build_app, client_for, override

pytestmark = pytest.mark.anyio


def _lease_repo_app(clear_app_module, *, get=None):
    repo = AsyncMock()
    repo.get.return_value = get
    repo.renew.return_value = None if get is None else LeaseMutationResult(get)
    repo.patch_settings.return_value = None if get is None else LeaseMutationResult(get)
    mod = build_app(pool=MagicMock())
    from pitwall.api.routes.leases import _lease_repo

    override(mod, _lease_repo, repo)
    return mod, repo


def _create_app(clear_app_module, *, capability=None, provider=None):
    cap_repo = AsyncMock()
    cap_repo.get_by_name.return_value = capability
    cap_repo.get.return_value = capability
    prov_repo = AsyncMock()
    prov_repo.get.return_value = provider
    mod = build_app(pool=MagicMock())
    from pitwall.api.routes.leases import _capability_repo, _provider_repo

    override(mod, _capability_repo, cap_repo)
    override(mod, _provider_repo, prov_repo)
    return mod


async def test_create_unknown_capability_404(clear_app_module) -> None:
    mod = _create_app(clear_app_module, capability=None)
    async with client_for(mod) as client:
        resp = await client.post("/v1/leases", json={"capability_id": "missing.cap"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "capability_not_found"


async def test_create_rejects_null_byte_capability_id_before_db(clear_app_module) -> None:
    cap_repo = AsyncMock()
    mod = build_app(pool=MagicMock())
    from pitwall.api.routes.leases import _capability_repo

    override(mod, _capability_repo, cap_repo)

    async with client_for(mod) as client:
        resp = await client.post("/v1/leases", json={"capability_id": "\x00"})

    assert resp.status_code == 422
    cap_repo.get_by_name.assert_not_awaited()


async def test_create_lease_success_returns_lease_response(clear_app_module, monkeypatch) -> None:
    """A real (non-dry-run) launch must shape its 201 body as LeaseResponse.

    Regression for the create_lease response-shape bug: the route returned the
    raw run_launch result dict (backend/pod_id/lease_id/...) instead of a
    LeaseResponse, so every successful pod-lease 500'd on response validation
    (observed live, once a lease could finally reach ACTIVE).
    """
    from pitwall.api.routes.leases import _lease_repo, _routing_service, _workload_repo

    class _Plan:
        plan_id = "plan_0123456789abcdef0123456789abcdef"
        selected_provider_id = "prov_x"

        def to_dict(self) -> dict[str, object]:
            return {
                "plan_id": self.plan_id,
                "capability_name": "pod.x",
                "selected_provider_id": self.selected_provider_id,
                "fallback_chain": [self.selected_provider_id],
            }

    route_service = AsyncMock()
    route_service.plan_execution.return_value = (_Plan(), {})

    created = dt.datetime(2026, 5, 31, 12, 0, 0, tzinfo=dt.UTC)
    signal = created + dt.timedelta(seconds=30)
    lease = Lease(
        id="lease_ok",
        provider_id="prov_x",
        workload_id="wkl_lease_ok",
        runpod_pod_id="pod_ok",
        state=LeaseState.ACTIVE,
        created_at=created,
        expires_at=created + dt.timedelta(hours=1),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        endpoints=LeaseEndpoints(http={"80": "https://pod-80.proxy.runpod.net"}),
        readiness=LeaseReadiness(
            runtime_seen_at=signal,
            port_mappings_seen_at=signal,
            probe_passed_at=signal,
            probe_method="runpod_proxy",
        ),
    )

    mod = _create_app(clear_app_module, capability=MagicMock(), provider=MagicMock())
    # The route launches through the shared create_routed_lease; patch the launch module
    # object sys.modules holds, since other suites evict and re-import pitwall.api modules.
    monkeypatch.setattr(
        importlib.import_module("pitwall.api.leases.launch"),
        "run_launch",
        AsyncMock(return_value={"lease_id": "lease_ok", "pod_id": "pod_ok", "backend": "runpod"}),
    )
    lease_repo = AsyncMock()
    lease_repo.get.return_value = lease
    workload_repo = AsyncMock()
    override(mod, _lease_repo, lease_repo)
    override(mod, _workload_repo, workload_repo)
    override(mod, _routing_service, route_service)

    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/leases", json={"capability_id": "pod.x", "provider_id": "prov_x"}
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "lease_ok"
    assert body["runpod_pod_id"] == "pod_ok"
    assert body["state"] == "active"
    assert body["provider_id"] == "prov_x"
    assert body["route_plan_id"] == _Plan.plan_id
    lease_repo.get.assert_awaited_once_with("lease_ok")
    workload_repo.attach_route_plan.assert_awaited_once_with(
        "wkl_lease_ok",
        route_plan_id=_Plan.plan_id,
        route_plan=_Plan().to_dict(),
    )


async def test_get_unknown_404(clear_app_module) -> None:
    mod, _ = _lease_repo_app(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.get("/v1/leases/lease_missing")
    assert resp.status_code == 404
    assert resp.json()["error"] == "lease_not_found"


async def test_renew_unknown_404(clear_app_module) -> None:
    mod, _ = _lease_repo_app(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.post("/v1/leases/lease_missing/renew", json={"extends_minutes": 30})
    assert resp.status_code == 404


async def test_patch_change_set_too_broad_400(clear_app_module) -> None:
    # image axis (image) + gpu axis (gpu_class) => spans 2 change-set axes.
    mod, _ = _lease_repo_app(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.patch(
            "/v1/leases/lease_x", json={"image": "img:v2", "gpu_class": "NVIDIA L4"}
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "change_set_too_broad"


async def test_patch_single_axis_unsupported_422_before_repo(clear_app_module) -> None:
    mod, repo = _lease_repo_app(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.patch("/v1/leases/lease_missing", json={"image": "img:v2"})
    assert resp.status_code == 422
    assert resp.json() == {"error": "unsupported_lease_patch", "fields": ["image"]}
    repo.patch_settings.assert_not_awaited()


@pytest.mark.parametrize("missing", [False, True])
async def test_delete_lease_is_scoped_and_returns_empty_204(
    clear_app_module, monkeypatch, missing: bool
) -> None:
    mod = build_app(pool=MagicMock(), redis=MagicMock())
    from pitwall.api.exceptions import LeaseNotFound
    from pitwall.api.routes import leases

    teardown = AsyncMock(side_effect=LeaseNotFound("lease_delete") if missing else None)
    monkeypatch.setattr(leases, "run_teardown", teardown)
    async with client_for(mod) as client:
        response = await client.delete("/v1/leases/lease_delete")
    assert response.status_code == 204
    assert response.content == b""
    teardown.assert_awaited_once_with(
        "lease_delete",
        pool=mod.app.state.pool,
        redis_client=mod.app.state.redis,
        reason="operator",
        terminated_reason="delete",
    )


async def test_delete_lease_does_not_hide_teardown_failure(clear_app_module, monkeypatch) -> None:
    mod = build_app(pool=MagicMock())
    from pitwall.api.leases.teardown import TeardownFailed
    from pitwall.api.routes import leases

    teardown = AsyncMock(side_effect=TeardownFailed("fixture teardown failed"))
    monkeypatch.setattr(leases, "run_teardown", teardown)
    async with client_for(mod) as client:
        response = await client.delete("/v1/leases/lease_delete")
    # Never a 204: the failure is a typed, retryable 502 that echoes no provider detail.
    assert response.status_code == 502
    assert response.json()["error"] == "teardown_failed"
    assert "fixture" not in response.text
    teardown.assert_awaited_once()


@pytest.mark.parametrize("reason", [None, "operator requested"])
async def test_stop_lease_http_returns_stopped_result_and_exact_reason(
    clear_app_module, monkeypatch, reason: str | None
) -> None:
    from tests.leases.test_teardown import _lease

    stopped = _lease(LeaseState.STOPPED, terminated_reason=reason)
    mod = build_app(pool=MagicMock(), redis=MagicMock())
    from pitwall.api.leases.teardown import LeaseTeardownResult
    from pitwall.api.routes import leases

    teardown = AsyncMock(return_value=LeaseTeardownResult(lease=stopped, event=None))
    monkeypatch.setattr(leases, "run_teardown", teardown)
    async with client_for(mod) as client:
        response = await client.post(
            "/v1/leases/lease_selected/stop", json={"reason": reason} if reason else None
        )
    assert response.status_code == 200
    assert response.json()["id"] == stopped.id
    assert response.json()["state"] == "stopped"
    assert response.json()["terminated_reason"] == reason
    teardown.assert_awaited_once_with(
        "lease_selected",
        pool=mod.app.state.pool,
        redis_client=mod.app.state.redis,
        reason="operator",
        terminated_reason=reason,
    )
