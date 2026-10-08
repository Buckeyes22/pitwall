"""API tests for the Provider CRUD surface.

Covers happy paths, duplicate name rejection, schema validation,
enable/disable/hibernate toggling, health endpoint, and config audit
row creation.  All tests are hermetic — no database or external service
required.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from pitwall.core.enums import CapabilitySource, ProviderType
from pitwall.core.models import Provider, validate_provider_storage_payload
from pitwall.db.repository import ProviderRepository
from tests._fake_transaction_pool import FakePool

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
ADMIN_SECRET = "test-admin-secret"
ADMIN_HEADERS = {"X-Pitwall-Secret": ADMIN_SECRET}
# Shaped like a GitHub token, so the provider storage policy refuses it.
_SECRET_SHAPED_ID = "ghp_" + "a" * 36


def _make_provider(
    id: str = "prov_01HQXR8K9N3JZQP7VW4MEX2YBA",
    capability_id: str = "cap_01HQXR8K9N3JZQP7VW4MEX2YBA",
    name: str = "runpod-bge-m3-lb",
    provider_type: ProviderType = ProviderType.SERVERLESS_LB,
    enabled: bool = True,
    health_status: str = "healthy",
    priority: int = 0,
    region: str | None = None,
    cloud_type: str | None = None,
    config: dict[str, object] | None = None,
    runpod_endpoint_id: str | None = None,
    runpod_template_id: str | None = None,
    cold_start_p50_ms: int | None = None,
    cold_start_p95_ms: int | None = None,
    recent_error_rate: float = 0.0,
    source: CapabilitySource = CapabilitySource.API,
) -> Provider:
    return Provider(
        id=id,
        capability_id=capability_id,
        name=name,
        provider_type=provider_type,
        runpod_endpoint_id=runpod_endpoint_id,
        runpod_template_id=runpod_template_id,
        region=region,
        cloud_type=cloud_type,
        config=config or {},
        priority=priority,
        enabled=enabled,
        health_status=health_status,
        cold_start_p50_ms=cold_start_p50_ms,
        cold_start_p95_ms=cold_start_p95_ms,
        recent_error_rate=recent_error_rate,
        source=source,
        updated_at=_NOW,
    )


def _base_create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "capability_id": "cap_01HQXR8K9N3JZQP7VW4MEX2YBA",
        "name": "runpod-bge-m3-lb",
        "provider_type": "serverless_lb",
        "runpod_endpoint_id": "eptest00000000",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def mock_repo() -> AsyncMock:
    return AsyncMock(spec=ProviderRepository)


def _record(events: list[str], repo: AsyncMock, name: str) -> None:
    """Make ``repo.<name>`` append its name to the pool's transaction trail when awaited."""
    method = getattr(repo, name)

    async def run(*args: object, **kwargs: object) -> object:
        events.append(name)
        return method.return_value

    method.side_effect = run


@pytest.fixture()
def api_client(mock_repo: AsyncMock):
    old = os.environ.copy()
    os.environ.update(
        {
            "RUNPOD_API_KEY": "test-key",
            "DATABASE_URL": "postgresql://u:p@localhost/db",
            "REDIS_URL": "redis://localhost:6379/0",
            "PITWALL_ADMIN_SECRET": ADMIN_SECRET,
        }
    )

    for mod in list(sys.modules):
        if mod.startswith("pitwall.api"):
            del sys.modules[mod]

    from pitwall.api.app import app
    from pitwall.api.provider_routes import _capability_repo as capability_repo_dep
    from pitwall.api.provider_routes import _pool as pool_dep
    from pitwall.api.provider_routes import _repo as repo_dep

    mock_capability_repo = AsyncMock()
    mock_capability_repo.get.return_value = object()
    mock_pool = FakePool()
    app.dependency_overrides[repo_dep] = lambda: mock_repo
    app.dependency_overrides[capability_repo_dep] = lambda: mock_capability_repo
    app.dependency_overrides[pool_dep] = lambda: mock_pool

    yield app, mock_repo, mock_pool

    app.dependency_overrides.clear()
    os.environ.clear()
    os.environ.update(old)
    for mod in list(sys.modules):
        if mod.startswith("pitwall.api"):
            del sys.modules[mod]


VALID_CREATE = _base_create_payload()


@pytest.mark.anyio
class TestCreateProvider:
    async def test_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None
        mock_repo.create.return_value = _make_provider()

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post("/v1/admin/providers", json=VALID_CREATE)

        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "runpod-bge-m3-lb"
        assert body["provider_type"] == "serverless_lb"
        assert body["enabled"] is True
        assert "id" in body
        assert "updated_at" in body

    async def test_with_all_optional_fields(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None
        mock_repo.create.return_value = _make_provider(
            runpod_endpoint_id="ep_abc123",
            runpod_template_id="tpl_xyz789",
            region="US",
            cloud_type="SECURE",
            config={"gpu_type": "NVIDIA A100 80GB"},
            priority=5,
            cold_start_p50_ms=1200,
            cold_start_p95_ms=3500,
            recent_error_rate=0.02,
            source=CapabilitySource.YAML,
        )

        payload = _base_create_payload(
            runpod_endpoint_id="ep_abc123",
            runpod_template_id="tpl_xyz789",
            region="US",
            cloud_type="SECURE",
            config={"gpu_type": "NVIDIA A100 80GB"},
            priority=5,
            cold_start_p50_ms=1200,
            cold_start_p95_ms=3500,
            recent_error_rate=0.02,
            source="yaml",
        )

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post("/v1/admin/providers", json=payload)

        assert resp.status_code == 201
        body = resp.json()
        assert body["runpod_endpoint_id"] == "ep_abc123"
        assert body["region"] == "US"
        assert body["priority"] == 5
        assert body["source"] == "yaml"

    async def test_duplicate_name_returns_409(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = _make_provider()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post("/v1/admin/providers", json=VALID_CREATE)

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "provider_conflict"
        assert body["name"] == "runpod-bge-m3-lb"

    async def test_schema_validation_missing_required_returns_422(self, api_client: tuple):
        app, mock_repo, _ = api_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post("/v1/admin/providers", json={})

        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "invalid_request"
        assert body["detail"]
        assert all(item == {"type": "missing"} for item in body["detail"])
        assert "loc" not in resp.text
        assert "input" not in resp.text
        assert mock_repo.mock_calls == []

    async def test_schema_validation_empty_name_returns_422(self, api_client: tuple):
        app, _, _ = api_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post(
                "/v1/admin/providers",
                json=_base_create_payload(name=""),
            )

        assert resp.status_code == 422

    async def test_schema_validation_invalid_provider_type_returns_422(self, api_client: tuple):
        app, _, _ = api_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post(
                "/v1/admin/providers",
                json=_base_create_payload(provider_type="not_real"),
            )

        assert resp.status_code == 422

    async def test_create_writes_audit_row(self, api_client: tuple):
        app, mock_repo, mock_pool = api_client
        mock_repo.get.return_value = None
        mock_repo.create.return_value = _make_provider()

        with patch(
            "pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock
        ) as mock_audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post("/v1/admin/providers", json=VALID_CREATE)

        assert resp.status_code == 201
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args
        assert call_kwargs[1]["actor"] == "rest:admin"
        assert call_kwargs[1]["action"] == "create"
        assert call_kwargs[1]["entity_type"] == "provider"
        assert call_kwargs[1]["new_value"] is not None


@pytest.mark.anyio
class TestListProviders:
    async def test_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.list.return_value = [
            _make_provider(name="prov-a"),
            _make_provider(id="prov_02ANOTHERULID0000000000", name="prov-b"),
        ]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2

    async def test_empty_list(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.list.return_value = []

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_enabled_filter_passes_enabled_only_true(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.list.return_value = [_make_provider()]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers?enabled=true")

        assert resp.status_code == 200
        mock_repo.list.assert_called_once_with(
            capability_id=None,
            enabled_only=True,
            provider_type=None,
            limit=100,
            offset=0,
        )

    async def test_capability_id_filter_passed_through(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.list.return_value = []

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers?capability_id=cap_01HQXR8K9N3JZQP7VW4MEX2YBA")

        assert resp.status_code == 200
        mock_repo.list.assert_called_once_with(
            capability_id="cap_01HQXR8K9N3JZQP7VW4MEX2YBA",
            enabled_only=False,
            provider_type=None,
            limit=100,
            offset=0,
        )

    async def test_provider_type_filter_passed_through(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.list.return_value = []

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers?provider_type=serverless_lb")

        assert resp.status_code == 200
        mock_repo.list.assert_called_once_with(
            capability_id=None,
            enabled_only=False,
            provider_type="serverless_lb",
            limit=100,
            offset=0,
        )


@pytest.mark.anyio
class TestGetProviderHealth:
    async def test_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = _make_provider(health_status="healthy")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers/prov_01HQXR8K9N3JZQP7VW4MEX2YBA/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "prov_01HQXR8K9N3JZQP7VW4MEX2YBA"
        assert body["health_status"] == "healthy"
        assert body["recent_error_rate"] == 0.0
        assert body["updated_at"] == "2026-05-28T12:00:00+00:00"

    async def test_not_found_returns_404(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.get("/v1/providers/prov_nonexistent/health")

        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "provider_not_found"
        assert body["id"] == "prov_nonexistent"


@pytest.mark.anyio
class TestPatchProvider:
    async def test_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        original = _make_provider()
        updated = _make_provider(priority=10)
        mock_repo.get.return_value = original
        mock_repo.patch.return_value = updated

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.patch(
                    f"/v1/admin/providers/{original.id}",
                    json={"priority": 10},
                )

        assert resp.status_code == 200
        assert resp.json()["priority"] == 10

    async def test_not_found_returns_404(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.patch(
                "/v1/admin/providers/prov_nonexistent",
                json={"priority": 5},
            )

        assert resp.status_code == 404

    async def test_schema_validation_empty_name_returns_422(self, api_client: tuple):
        app, _, _ = api_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.patch(
                "/v1/admin/providers/prov_test",
                json={"name": ""},
            )

        assert resp.status_code == 422

    async def test_patch_validates_config_against_existing_provider(self, api_client: tuple):
        app, mock_repo, _ = api_client
        original = _make_provider(
            provider_type=ProviderType.PUBLIC_ENDPOINT,
            runpod_endpoint_id="qwen3-32b-awq",
        )
        mock_repo.get.return_value = original

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.patch(
                f"/v1/admin/providers/{original.id}",
                json={"config": {"openai_base_url": "https://api.runpod.ai/v2/wrong/openai/v1"}},
            )

        assert resp.status_code == 422
        mock_repo.patch.assert_not_called()

    async def test_patch_writes_audit_row(self, api_client: tuple):
        app, mock_repo, mock_pool = api_client
        original = _make_provider()
        updated = _make_provider(priority=10)
        mock_repo.get.return_value = original
        mock_repo.patch.return_value = updated

        with patch(
            "pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock
        ) as mock_audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.patch(
                    f"/v1/admin/providers/{original.id}",
                    json={"priority": 10},
                )

        assert resp.status_code == 200
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args
        assert call_kwargs[1]["actor"] == "rest:admin"
        assert call_kwargs[1]["action"] == "update"
        assert call_kwargs[1]["entity_type"] == "provider"
        assert call_kwargs[1]["old_value"] is not None
        assert call_kwargs[1]["new_value"] is not None

    async def _patch_enabled(
        self,
        api_client: tuple,
        *,
        current: bool,
        requested: bool,
        extra: dict[str, object] | None = None,
    ) -> tuple[httpx.Response, AsyncMock, AsyncMock]:
        app, mock_repo, pool = api_client
        original = _make_provider(enabled=current)
        mock_repo.get.return_value = original
        mock_repo.patch.return_value = original
        mock_repo.enable.return_value = _make_provider(enabled=True)
        mock_repo.disable.return_value = _make_provider(enabled=False)
        for name in ("patch", "enable", "disable"):
            _record(pool.events, mock_repo, name)

        with patch(
            "pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock
        ) as mock_audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.patch(
                    f"/v1/admin/providers/{original.id}",
                    json={"enabled": requested, **(extra or {})},
                )
        return resp, mock_repo, mock_audit

    async def test_patch_enabled_false_disables_provider(self, api_client: tuple):
        resp, mock_repo, mock_audit = await self._patch_enabled(
            api_client, current=True, requested=False
        )

        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        mock_repo.disable.assert_awaited_once()
        mock_repo.enable.assert_not_awaited()
        mock_audit.assert_called_once()
        assert mock_audit.call_args[1]["action"] == "disable"
        assert mock_audit.call_args[1]["old_value"]["enabled"] is True
        assert mock_audit.call_args[1]["new_value"]["enabled"] is False

    async def test_patch_enabled_true_enables_provider(self, api_client: tuple):
        resp, mock_repo, mock_audit = await self._patch_enabled(
            api_client, current=False, requested=True
        )

        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
        mock_repo.enable.assert_awaited_once()
        mock_repo.disable.assert_not_awaited()
        mock_audit.assert_called_once()
        assert mock_audit.call_args[1]["action"] == "enable"
        assert mock_audit.call_args[1]["old_value"]["enabled"] is False
        assert mock_audit.call_args[1]["new_value"]["enabled"] is True

    async def test_patch_enabled_with_other_fields_audits_both(self, api_client: tuple):
        resp, _, mock_audit = await self._patch_enabled(
            api_client, current=True, requested=False, extra={"priority": 4}
        )

        assert resp.status_code == 200
        assert [call.kwargs["action"] for call in mock_audit.call_args_list] == [
            "disable",
            "update",
        ]

    async def test_patch_enabled_same_value_does_not_toggle(self, api_client: tuple):
        resp, mock_repo, mock_audit = await self._patch_enabled(
            api_client, current=True, requested=True
        )

        assert resp.status_code == 200
        assert resp.json()["enabled"] is True
        mock_repo.enable.assert_not_awaited()
        mock_repo.disable.assert_not_awaited()
        mock_audit.assert_not_called()

    async def test_patch_enabled_with_a_null_field_audits_only_the_toggle(self, api_client: tuple):
        resp, _, mock_audit = await self._patch_enabled(
            api_client, current=True, requested=False, extra={"priority": None}
        )

        assert resp.status_code == 200
        assert [call.kwargs["action"] for call in mock_audit.call_args_list] == ["disable"]

    async def test_patch_old_value_is_the_locked_row_not_the_earlier_read(self, api_client: tuple):
        app, mock_repo, pool = api_client
        mock_repo.get.return_value = _make_provider(enabled=True)
        # A concurrent toggle landed between the first read and the patch.
        mock_repo.patch.return_value = _make_provider(enabled=False)
        mock_repo.enable.return_value = _make_provider(enabled=True)

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock) as audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.patch("/v1/admin/providers/prov_test", json={"enabled": True})

        assert resp.status_code == 200
        audit.assert_awaited_once()
        assert audit.call_args.kwargs["action"] == "enable"
        assert audit.call_args.kwargs["old_value"] == {"enabled": False}

    async def test_patch_runs_before_the_toggle_in_one_transaction(self, api_client: tuple):
        resp, _, _ = await self._patch_enabled(api_client, current=True, requested=False)

        assert resp.status_code == 200
        assert api_client[2].events == ["begin", "patch", "disable", "commit"]

    async def test_a_failing_toggle_rolls_the_patch_back(self, api_client: tuple):
        app, mock_repo, pool = api_client
        original = _make_provider(enabled=True)
        mock_repo.get.return_value = original
        mock_repo.patch.return_value = original
        _record(pool.events, mock_repo, "patch")
        mock_repo.disable.side_effect = RuntimeError("toggle failed")

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock) as audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.patch(
                    f"/v1/admin/providers/{original.id}",
                    json={"priority": 9, "enabled": False},
                )

        assert resp.status_code == 500
        assert pool.events == ["begin", "patch", "rollback"]
        audit.assert_not_awaited()

    async def test_patch_enabled_toggle_returning_none_is_404(self, api_client: tuple):
        app, mock_repo, pool = api_client
        original = _make_provider(enabled=True)
        mock_repo.get.return_value = original
        mock_repo.patch.return_value = original
        mock_repo.disable.return_value = None

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock) as audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.patch(
                    f"/v1/admin/providers/{original.id}",
                    json={"enabled": False},
                )

        assert resp.status_code == 404
        assert pool.events == ["begin", "rollback"]
        audit.assert_not_awaited()


@pytest.mark.anyio
class TestEnableProvider:
    async def test_enable_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        prov = _make_provider(enabled=False)
        enabled_prov = _make_provider(enabled=True)
        mock_repo.get.return_value = prov
        mock_repo.enable.return_value = enabled_prov

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post(f"/v1/admin/providers/{prov.id}/enable")

        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    async def test_enable_not_found_returns_404(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post("/v1/admin/providers/prov_nonexistent/enable")

        assert resp.status_code == 404

    async def test_enable_writes_audit_row(self, api_client: tuple):
        app, mock_repo, mock_pool = api_client
        prov = _make_provider(enabled=False)
        enabled_prov = _make_provider(enabled=True)
        mock_repo.get.return_value = prov
        mock_repo.enable.return_value = enabled_prov

        with patch(
            "pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock
        ) as mock_audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post(f"/v1/admin/providers/{prov.id}/enable")

        assert resp.status_code == 200
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args
        assert call_kwargs[1]["action"] == "enable"
        assert call_kwargs[1]["old_value"] == {"enabled": False}
        assert call_kwargs[1]["new_value"] == {"enabled": True}


@pytest.mark.anyio
class TestDisableProvider:
    async def test_disable_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        prov = _make_provider(enabled=True)
        disabled_prov = _make_provider(enabled=False)
        mock_repo.get.return_value = prov
        mock_repo.disable.return_value = disabled_prov

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post(f"/v1/admin/providers/{prov.id}/disable")

        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    async def test_disable_not_found_returns_404(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post("/v1/admin/providers/prov_nonexistent/disable")

        assert resp.status_code == 404

    async def test_disable_writes_audit_row(self, api_client: tuple):
        app, mock_repo, mock_pool = api_client
        prov = _make_provider(enabled=True)
        disabled_prov = _make_provider(enabled=False)
        mock_repo.get.return_value = prov
        mock_repo.disable.return_value = disabled_prov

        with patch(
            "pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock
        ) as mock_audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post(f"/v1/admin/providers/{prov.id}/disable")

        assert resp.status_code == 200
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args
        assert call_kwargs[1]["action"] == "disable"
        assert call_kwargs[1]["old_value"] == {"enabled": True}
        assert call_kwargs[1]["new_value"] == {"enabled": False}


@pytest.mark.anyio
class TestHibernateProvider:
    async def test_hibernate_happy_path(self, api_client: tuple):
        app, mock_repo, _ = api_client
        prov = _make_provider(health_status="healthy")
        hibernated = _make_provider(health_status="hibernated")
        mock_repo.get.return_value = prov
        mock_repo.patch.return_value = hibernated

        with patch("pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post(f"/v1/admin/providers/{prov.id}/hibernate")

        assert resp.status_code == 200
        body = resp.json()
        assert body["health_status"] == "hibernated"
        assert body["enabled"] is True
        assert "id" in body

    async def test_hibernate_not_found_returns_404(self, api_client: tuple):
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.post("/v1/admin/providers/prov_nonexistent/hibernate")

        assert resp.status_code == 404

    async def test_hibernate_writes_audit_row(self, api_client: tuple):
        app, mock_repo, mock_pool = api_client
        prov = _make_provider(health_status="healthy")
        hibernated = _make_provider(health_status="hibernated")
        mock_repo.get.return_value = prov
        mock_repo.patch.return_value = hibernated

        with patch(
            "pitwall.api.provider_routes.insert_audit", new_callable=AsyncMock
        ) as mock_audit:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers=ADMIN_HEADERS,
            ) as client:
                resp = await client.post(f"/v1/admin/providers/{prov.id}/hibernate")

        assert resp.status_code == 200
        mock_audit.assert_called_once()
        call_kwargs = mock_audit.call_args
        assert call_kwargs[1]["action"] == "hibernate"
        assert call_kwargs[1]["old_value"] == {"health_status": "healthy"}
        assert call_kwargs[1]["new_value"] == {"health_status": "hibernated"}


@pytest.mark.anyio
class TestAdminProviderPathAndPolicyErrors:
    """Regressions from the authenticated admin fuzz: each input below was a 500."""

    @pytest.mark.parametrize(
        ("method", "suffix"),
        [("PATCH", ""), ("POST", "/enable"), ("POST", "/disable"), ("POST", "/hibernate")],
    )
    async def test_nul_provider_id_is_422_before_the_repository(
        self, api_client: tuple, method: str, suffix: str
    ):
        # Postgres text cannot hold NUL, so the lookup failed in the driver.
        app, mock_repo, _ = api_client
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            resp = await client.request(method, f"/v1/admin/providers/prov%00x{suffix}", json={})

        assert resp.status_code == 422
        mock_repo.get.assert_not_called()

    @pytest.mark.parametrize("route", ["hibernate", "patch"])
    async def test_storage_policy_rejection_is_422_without_echo(
        self, api_client: tuple, route: str
    ):
        # repo.patch runs the provider storage policy, which refuses a credential-shaped id.
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = _make_provider()

        async def real_policy(provider_id: str, **_fields: object) -> Provider:
            validate_provider_storage_payload({"provider_id": provider_id})
            raise AssertionError("the storage policy should have refused this id")

        mock_repo.patch.side_effect = real_policy
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            if route == "hibernate":
                resp = await client.post(f"/v1/admin/providers/{_SECRET_SHAPED_ID}/hibernate")
            else:
                resp = await client.patch(
                    f"/v1/admin/providers/{_SECRET_SHAPED_ID}", json={"priority": 2}
                )

        assert resp.status_code == 422
        assert _SECRET_SHAPED_ID not in resp.text
        assert "pre-spend" not in resp.text

    @pytest.mark.parametrize("route", ["hibernate", "patch"])
    async def test_corrupt_stored_row_stays_a_server_error(self, api_client: tuple, route: str):
        # A row that no longer parses after the UPDATE is a server fault, not bad input.
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = _make_provider()

        async def corrupt_row(provider_id: str, **_fields: object) -> Provider:
            return Provider.model_validate({"id": provider_id})

        mock_repo.patch.side_effect = corrupt_row
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            headers=ADMIN_HEADERS,
        ) as client:
            if route == "hibernate":
                resp = await client.post("/v1/admin/providers/prov_x/hibernate")
            else:
                resp = await client.patch("/v1/admin/providers/prov_x", json={"priority": 2})

        assert resp.status_code == 500

    @pytest.mark.parametrize(
        "config",
        ['{"a\\u0000": 1}', '{"nested": {"k\\u0000": "v"}}', '{"k": ["\\ud800"]}'],
        ids=["nul-key", "nested-nul-key", "lone-surrogate"],
    )
    @pytest.mark.parametrize("route", ["create", "patch"])
    async def test_unstorable_config_is_422_before_the_repository(
        self, api_client: tuple, config: str, route: str
    ):
        # Postgres jsonb cannot hold NUL and asyncpg cannot encode a lone surrogate;
        # a NUL in a config key passed the storage policy and failed in the driver.
        app, mock_repo, _ = api_client
        mock_repo.get.return_value = None if route == "create" else _make_provider()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=ADMIN_HEADERS
        ) as client:
            if route == "create":
                body = (
                    '{"capability_id": "cap_x", "name": "p", "provider_type": "serverless_lb",'
                    f' "runpod_endpoint_id": "eptest00000000", "config": {config}}}'
                )
                resp = await client.post(
                    "/v1/admin/providers",
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
            else:
                resp = await client.patch(
                    "/v1/admin/providers/prov_x",
                    content=f'{{"config": {config}}}',
                    headers={"Content-Type": "application/json"},
                )

        assert resp.status_code == 422
        mock_repo.create.assert_not_called()
        mock_repo.patch.assert_not_called()
