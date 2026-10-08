"""Task 3: admin-secret auth matrix.

With PITWALL_ADMIN_SECRET set, AdminSecretMiddleware (app.py) must reject every
/v1/admin/* request that lacks the X-Pitwall-Secret header or sends the wrong
value (401), and must NOT 401 when the header is correct (the handler may then
404/422 on a fake id — that is fine; we test the auth gate only).

Admin routes derived from the verified Task 0 inventory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from tests.api._contract_helpers import build_app

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def _gate_client(mod):
    """Client that returns handler exceptions as 500 instead of re-raising.

    Task 3 tests the auth gate only; once the correct secret lets a request
    through, the handler may crash on unconfigured deps — that surfaces as a
    500 response (still != 401 = gate passed), not a test error.
    """
    transport = httpx.ASGITransport(app=mod.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


_SECRET = "test-admin-secret"

# (method, path) for every mutation under /v1/admin/*. RunPod GET resource
# routes deliberately use bearer read scope and are covered separately.
_ADMIN_ROUTES = [
    ("POST", "/v1/admin/capabilities"),
    ("PATCH", "/v1/admin/capabilities/cap_x"),
    ("POST", "/v1/admin/capabilities/cap_x/enable"),
    ("POST", "/v1/admin/capabilities/cap_x/disable"),
    ("POST", "/v1/admin/providers"),
    ("PATCH", "/v1/admin/providers/prov_x"),
    ("POST", "/v1/admin/providers/prov_x/enable"),
    ("POST", "/v1/admin/providers/prov_x/disable"),
    ("POST", "/v1/admin/providers/prov_x/hibernate"),
    ("POST", "/v1/admin/audit-capability/embedding.bge-m3"),
    ("POST", "/v1/admin/kill-switch"),
    ("PUT", "/v1/admin/budget"),
    ("POST", "/v1/admin/volumes/vol_x/objects"),
    ("DELETE", "/v1/admin/volumes/vol_x/objects/object.bin"),
    ("POST", "/v1/admin/runpod/pods"),
    ("PATCH", "/v1/admin/runpod/pods/pod_x"),
    ("POST", "/v1/admin/runpod/pods/pod_x/action"),
    ("DELETE", "/v1/admin/runpod/pods/pod_x"),
    ("POST", "/v1/admin/runpod/endpoints"),
    ("PATCH", "/v1/admin/runpod/endpoints/ep_x"),
    ("DELETE", "/v1/admin/runpod/endpoints/ep_x"),
    ("POST", "/v1/admin/runpod/templates"),
    ("PATCH", "/v1/admin/runpod/templates/tpl_x"),
    ("DELETE", "/v1/admin/runpod/templates/tpl_x"),
    ("POST", "/v1/admin/runpod/volumes"),
    ("PATCH", "/v1/admin/runpod/volumes/vol_x"),
    ("DELETE", "/v1/admin/runpod/volumes/vol_x"),
    ("POST", "/v1/admin/runpod/registry-auths"),
    ("POST", "/v1/admin/runpod/registry-auths/reg_x/replace"),
    ("DELETE", "/v1/admin/runpod/registry-auths/reg_x"),
    ("POST", "/v1/admin/runpod/onboarding/plan"),
    ("POST", "/v1/admin/runpod/onboarding/apply"),
    ("POST", "/v1/admin/runpod/onboarding/status"),
    ("POST", "/v1/admin/runpod/onboarding/resume"),
    ("POST", "/v1/admin/runpod/onboarding/rollback"),
]
_IDS = [f"{m}:{p}" for m, p in _ADMIN_ROUTES]


@pytest.mark.parametrize("method,path", _ADMIN_ROUTES, ids=_IDS)
async def test_missing_secret_is_401(clear_app_module, method: str, path: str) -> None:
    mod = build_app(secret=_SECRET)
    async with _gate_client(mod) as client:
        resp = await client.request(method, path, json={})
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path", _ADMIN_ROUTES, ids=_IDS)
async def test_wrong_secret_is_401(clear_app_module, method: str, path: str) -> None:
    mod = build_app(secret=_SECRET)
    async with _gate_client(mod) as client:
        resp = await client.request(method, path, json={}, headers={"X-Pitwall-Secret": "wrong"})
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path", _ADMIN_ROUTES, ids=_IDS)
async def test_correct_secret_is_not_401(clear_app_module, method: str, path: str) -> None:
    mod = build_app(secret=_SECRET)
    async with _gate_client(mod) as client:
        resp = await client.request(method, path, json={}, headers={"X-Pitwall-Secret": _SECRET})
    assert resp.status_code != 401


async def test_runpod_resource_read_does_not_require_admin_secret(clear_app_module) -> None:
    mod = build_app(secret=_SECRET)
    async with _gate_client(mod) as client:
        resp = await client.get("/v1/admin/runpod/pods")
    assert resp.status_code != 401
