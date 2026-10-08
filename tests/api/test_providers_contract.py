"""Task 2b: providers route contract.

Routes: POST/PATCH/enable/disable/hibernate /v1/admin/providers* (admin),
GET /v1/providers, /v1/providers/{id}, /v1/providers/{id}/health (public).
Deps overridden: provider_routes._repo, ._pool. Verified vs source 2026-05-30:
create_provider detects a duplicate via repo.get(body.name) -> ProviderConflict
(409); there is NO capability check on create (no _capability_repo dep). Unknown
provider id on get/patch/enable/disable/hibernate/health -> ProviderNotFound (404).
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pitwall.core.enums import ProviderType
from pitwall.core.models import Provider
from tests.api._contract_helpers import build_app, client_for, override

pytestmark = pytest.mark.anyio
_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)
_ADMIN_SECRET = "test-admin-secret"


def _provider_body(**over):
    body = {
        "name": "bge-m3-lb-us-ks",
        "capability_id": "cap_bge_m3",
        "provider_type": "serverless_lb",
        "runpod_endpoint_id": "eptest00000000",
        "priority": 1,
    }
    body.update(over)
    return body


def _provider() -> Provider:
    return Provider(
        id="prov_bge_m3",
        capability_id="cap_bge_m3",
        name="bge-m3-lb-us-ks",
        provider_type=ProviderType.SERVERLESS_LB,
        runpod_endpoint_id="eptest00000000",
        config={"lb_base_url": "https://eptest00000000.api.runpod.ai"},
        priority=1,
        enabled=True,
        health_status="healthy",
        updated_at=_NOW,
    )


_ANY_CAPABILITY = object()


def _setup(clear_app_module, *, get=None, capability=_ANY_CAPABILITY):
    """get is the value repo.get(...) returns (used for both conflict + lookups)."""
    repo = AsyncMock()
    repo.get.return_value = get
    repo.create.return_value = _provider()
    repo.patch.return_value = get
    repo.enable.return_value = get
    repo.disable.return_value = get
    capability_repo = AsyncMock()
    capability_repo.get.return_value = capability
    mod = build_app(secret=_ADMIN_SECRET, pool=MagicMock())
    from pitwall.api.provider_routes import _capability_repo as capability_repo_dep
    from pitwall.api.provider_routes import _pool as pool_dep
    from pitwall.api.provider_routes import _repo as repo_dep

    override(mod, repo_dep, repo)
    override(mod, capability_repo_dep, capability_repo)
    override(mod, pool_dep, MagicMock())
    return mod, repo, capability_repo


async def test_create_happy_201(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    with patch("pitwall.api.provider_routes.insert_audit", new=AsyncMock()):
        async with client_for(mod) as client:
            resp = await client.post("/v1/admin/providers", json=_provider_body())
    assert resp.status_code == 201
    assert resp.json()["name"] == "bge-m3-lb-us-ks"
    assert resp.json()["adapter_id"] == "runpod"
    assert resp.json()["credential_ref"] == "RUNPOD_API_KEY"


async def test_create_non_runpod_adapter_and_reference_round_trip(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    repo.create.side_effect = lambda provider: provider
    body = _provider_body(
        name="vast-gpu",
        adapter_id="vast",
        credential_ref="PITWALL_VAST_KEY",
        provider_type="pod_lease",
        runpod_endpoint_id=None,
    )
    with patch("pitwall.api.provider_routes.insert_audit", new=AsyncMock()):
        async with client_for(mod) as client:
            resp = await client.post("/v1/admin/providers", json=body)

    assert resp.status_code == 201
    assert resp.json()["adapter_id"] == "vast"
    assert resp.json()["credential_ref"] == "PITWALL_VAST_KEY"
    created = repo.create.await_args.args[0]
    assert created.runpod_endpoint_id is None


async def test_create_rejects_unknown_adapter(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/admin/providers",
            json=_provider_body(adapter_id="unknown"),
        )

    assert resp.status_code == 422
    repo.create.assert_not_awaited()


async def test_create_rejects_raw_secret_without_echoing_it(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    secret = "rest-provider-secret-canary"  # pragma: allowlist secret
    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/admin/providers",
            json=_provider_body(config={"nested": {"api_key": secret}}),
        )

    assert resp.status_code == 422
    assert secret not in resp.text
    repo.create.assert_not_awaited()


async def test_create_rejects_unsafe_identity_before_repository_access(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    canary = "rest.provider@example.com"
    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/admin/providers",
            json=_provider_body(name=canary),
        )

    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_request"
    assert canary not in resp.text
    repo.get.assert_not_awaited()
    repo.create.assert_not_awaited()


async def test_create_rejects_nested_camel_case_secret_without_echoing_it(
    clear_app_module,
) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    secret = "rest-camel-provider-secret-canary"  # pragma: allowlist secret
    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/admin/providers",
            json=_provider_body(config={"items": [[{"clientSecret": secret}]]}),
        )

    assert resp.status_code == 422
    assert secret not in resp.text
    repo.create.assert_not_awaited()


async def test_create_rejects_raw_credential_reference_value_without_echo(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    secret = "sk-rest-credential-reference-canary"  # pragma: allowlist secret
    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/admin/providers",
            json=_provider_body(credential_ref=secret),
        )

    assert resp.status_code == 422
    assert secret not in resp.text
    repo.create.assert_not_awaited()


async def test_create_duplicate_409(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=_provider())
    async with client_for(mod) as client:
        resp = await client.post("/v1/admin/providers", json=_provider_body())
    assert resp.status_code == 409
    assert resp.json()["error"] == "provider_conflict"


async def test_create_missing_capability_422_friendly(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None, capability=None)
    async with client_for(mod) as client:
        resp = await client.post("/v1/admin/providers", json=_provider_body())
    body = resp.json()
    assert resp.status_code == 422
    assert body["error"] == "provider_capability_missing"
    assert body["capability_id"] == "cap_bge_m3"
    assert "create it first" in body["message"]
    repo.create.assert_not_awaited()


async def test_create_missing_field_422(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    bad = {k: v for k, v in _provider_body().items() if k != "capability_id"}
    async with client_for(mod) as client:
        resp = await client.post("/v1/admin/providers", json=bad)
    assert resp.status_code == 422


async def test_create_bad_enum_422(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.post(
            "/v1/admin/providers", json=_provider_body(provider_type="nonsense")
        )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "self_hosted",
    [
        {"readiness": {"kind": "unknown"}},
        {"readiness": {"kind": "llama-swap"}, "cold_start_timeout_s": 0},
        {"readiness": {"kind": "llama-swap"}, "unknown": True},
    ],
)
async def test_create_invalid_self_hosted_profile_422(
    clear_app_module, self_hosted: dict[str, object]
) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    body = _provider_body(
        provider_type="public_endpoint",
        runpod_endpoint_id="endpoint-fixture",
        config={
            "openai_base_url": "https://api.runpod.ai/v2/endpoint-fixture/openai/v1",
            "self_hosted": self_hosted,
        },
    )
    async with client_for(mod) as client:
        resp = await client.post("/v1/admin/providers", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["type"] == "value_error"
    repo.create.assert_not_awaited()


async def test_patch_invalid_self_hosted_profile_422(clear_app_module) -> None:
    existing = _provider().model_copy(
        update={
            "provider_type": ProviderType.PUBLIC_ENDPOINT,
            "runpod_endpoint_id": "endpoint-fixture",
            "config": {"openai_base_url": "https://api.runpod.ai/v2/endpoint-fixture/openai/v1"},
        }
    )
    mod, repo, _ = _setup(clear_app_module, get=existing)
    async with client_for(mod) as client:
        resp = await client.patch(
            f"/v1/admin/providers/{existing.id}",
            json={"config": {"self_hosted": {"readiness": {"kind": "unknown"}}}},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["type"] == "value_error"
    repo.patch.assert_not_awaited()


async def test_create_valid_self_hosted_profile_round_trips(clear_app_module) -> None:
    mod, repo, _ = _setup(clear_app_module, get=None)
    repo.create.side_effect = lambda provider: provider
    profile = {
        "readiness": {"kind": "llama-swap"},
        "cold_start_timeout_s": 600,
        "models": [{"id": "qwen3-32b-awq"}],
    }
    body = _provider_body(
        provider_type="public_endpoint",
        runpod_endpoint_id="endpoint-fixture",
        config={
            "openai_base_url": "https://api.runpod.ai/v2/endpoint-fixture/openai/v1",
            "self_hosted": profile,
        },
    )
    with patch("pitwall.api.provider_routes.insert_audit", new=AsyncMock()):
        async with client_for(mod) as client:
            resp = await client.post("/v1/admin/providers", json=body)
    assert resp.status_code == 201
    assert resp.json()["config"]["self_hosted"] == profile


async def test_get_unknown_404(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.get("/v1/providers/prov_missing")
    assert resp.status_code == 404
    assert resp.json()["error"] == "provider_not_found"


async def test_get_redacts_legacy_camel_case_provider_secret(clear_app_module) -> None:
    secret = "rest-read-provider-secret-canary"  # pragma: allowlist secret
    provider = _provider().model_copy(
        update={
            "config": {
                "imageRef": "example/model:test",
                "nested": {"apiKey": secret},
            }
        }
    )
    mod, _, _ = _setup(clear_app_module, get=provider)

    async with client_for(mod) as client:
        resp = await client.get(f"/v1/providers/{provider.id}")

    assert resp.status_code == 200
    assert secret not in resp.text
    assert resp.json()["config"] == {
        "imageRef": "example/model:test",
        "nested": {"apiKey": "[REDACTED]"},
    }


async def test_patch_unknown_404(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.patch("/v1/admin/providers/prov_missing", json={"priority": 2})
    assert resp.status_code == 404


async def test_enable_unknown_404(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.post("/v1/admin/providers/prov_missing/enable")
    assert resp.status_code == 404


async def test_disable_unknown_404(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.post("/v1/admin/providers/prov_missing/disable")
    assert resp.status_code == 404


async def test_get_health_unknown_404(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.get("/v1/providers/prov_missing/health")
    assert resp.status_code == 404


async def test_method_not_allowed_405(clear_app_module) -> None:
    mod, _, _ = _setup(clear_app_module, get=None)
    async with client_for(mod) as client:
        resp = await client.delete("/v1/providers/prov_bge_m3")
    assert resp.status_code == 405


@pytest.mark.parametrize("in_cooldown", [False, True])
async def test_health_returns_exact_counters_without_provider_config(
    clear_app_module, in_cooldown: bool
) -> None:
    cooldown = _NOW + dt.timedelta(minutes=5) if in_cooldown else None
    provider = _provider().model_copy(
        update={
            "health_status": "unhealthy" if in_cooldown else "healthy",
            "consecutive_failures": 3 if in_cooldown else 0,
            "cooldown_trips": 2 if in_cooldown else 0,
            "recent_error_rate": 0.75 if in_cooldown else 0.0,
            "cooldown_until": cooldown,
        }
    )
    mod, repo, _ = _setup(clear_app_module, get=provider)
    async with client_for(mod) as client:
        response = await client.get("/v1/providers/prov_bge_m3/health")
    assert response.status_code == 200
    assert response.json() == {
        "id": "prov_bge_m3",
        "name": "bge-m3-lb-us-ks",
        "health_status": "unhealthy" if in_cooldown else "healthy",
        "consecutive_failures": 3 if in_cooldown else 0,
        "cooldown_trips": 2 if in_cooldown else 0,
        "recent_error_rate": 0.75 if in_cooldown else 0.0,
        "cooldown_until": cooldown.isoformat() if cooldown else None,
        "updated_at": _NOW.isoformat(),
    }
    repo.get.assert_awaited_once_with("prov_bge_m3")
