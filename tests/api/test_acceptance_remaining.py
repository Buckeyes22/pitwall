"""Regression: REST discovery filters wire cost_mode/source/enabled to repositories.

Confirmed defect (release-acceptance): ``GET /v1/capabilities`` declared
``cost_mode`` and ``source`` query parameters but never passed them to
``CapabilityRepository.list``; ``GET /v1/capabilities?enabled=false`` and
``GET /v1/providers?enabled=false`` returned every row because the handlers
only translated ``enabled is True`` into ``enabled_only``.

These hermetic tests pin the handler->repository argument contract; the real
SQL-side behavior is covered by ``tests/integration/test_discovery_filters.py``.
No-filter calls keep the pre-fix call shape (``enabled_only`` plus existing
keyword arguments) so unchanged callers and mocks remain valid.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from tests.api._contract_helpers import build_app, client_for, override

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)


def _capability(
    *,
    enabled: bool = True,
    cost_mode: CostMode = CostMode.PER_SECOND,
    source: CapabilitySource = CapabilitySource.API,
) -> Capability:
    return Capability(
        id="cap_llm_disabled",
        name="llm.disabled",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=cost_mode,
        source=source,
        enabled=enabled,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(*, enabled: bool = True) -> Provider:
    return Provider(
        id="prov_disabled",
        capability_id="cap_llm_disabled",
        name="prov-disabled",
        provider_type=ProviderType.SERVERLESS_LB,
        priority=1,
        enabled=enabled,
        health_status="unknown",
        updated_at=_NOW,
    )


def _capability_app(repo: AsyncMock, provider_repo: AsyncMock):
    mod = build_app(pool=MagicMock())
    import pitwall.api.capability_routes as capability_mod

    override(mod, capability_mod._repo, repo)
    lease_repo = AsyncMock()
    lease_repo.latest_active_for_capabilities.return_value = {}
    override(mod, capability_mod._lease_repo, lease_repo)
    override(mod, capability_mod._provider_repo, provider_repo)
    override(mod, capability_mod._pool, MagicMock())
    return mod


def _provider_app(repo: AsyncMock):
    mod = build_app(pool=MagicMock())
    import pitwall.api.provider_routes as provider_mod

    override(mod, provider_mod._repo, repo)
    override(mod, provider_mod._pool, MagicMock())
    return mod


class TestCapabilityListFilters:
    async def test_cost_mode_filter_reaches_repository(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = [_capability(cost_mode=CostMode.PER_REQUEST)]
        provider_repo = AsyncMock()
        provider_repo.list.return_value = []
        mod = _capability_app(repo, provider_repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/capabilities?cost_mode=per_request")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs["cost_mode"] == "per_request"

    async def test_source_filter_reaches_repository(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = [_capability(source=CapabilitySource.MCP)]
        provider_repo = AsyncMock()
        provider_repo.list.return_value = []
        mod = _capability_app(repo, provider_repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/capabilities?source=mcp")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs["source"] == "mcp"

    async def test_enabled_false_requests_only_disabled(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = [_capability(enabled=False)]
        provider_repo = AsyncMock()
        provider_repo.list.return_value = []
        mod = _capability_app(repo, provider_repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/capabilities?enabled=false")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs["enabled"] is False
        assert repo.list.call_args.kwargs["enabled_only"] is False
        assert resp.json()["items"][0]["enabled"] is False

    async def test_combined_filters_reach_repository(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        provider_repo = AsyncMock()
        provider_repo.list.return_value = []
        mod = _capability_app(repo, provider_repo)

        async with client_for(mod) as client:
            resp = await client.get(
                "/v1/capabilities?class=llm&cost_mode=per_request&source=yaml&enabled=true"
            )

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs == {
            "enabled_only": True,
            "class_filter": "llm",
            "limit": 100,
            "offset": 0,
            "cost_mode": "per_request",
            "source": "yaml",
        }

    async def test_no_filter_call_shape_unchanged(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        provider_repo = AsyncMock()
        provider_repo.list.return_value = []
        mod = _capability_app(repo, provider_repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/capabilities")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs == {
            "enabled_only": False,
            "class_filter": None,
            "limit": 100,
            "offset": 0,
        }
        assert "cost_mode" not in repo.list.call_args.kwargs
        assert "source" not in repo.list.call_args.kwargs
        assert "enabled" not in repo.list.call_args.kwargs

    async def test_invalid_cost_mode_rejected_before_repository(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        provider_repo = AsyncMock()
        provider_repo.list.return_value = []
        mod = _capability_app(repo, provider_repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/capabilities?cost_mode=bogus")

        assert resp.status_code == 422
        repo.list.assert_not_awaited()


class TestProviderListFilters:
    async def test_enabled_false_requests_only_disabled(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = [_provider(enabled=False)]
        mod = _provider_app(repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/providers?enabled=false")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs["enabled"] is False
        assert repo.list.call_args.kwargs["enabled_only"] is False
        assert resp.json()["items"][0]["enabled"] is False

    async def test_no_filter_call_shape_unchanged(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        mod = _provider_app(repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/providers")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs == {
            "capability_id": None,
            "enabled_only": False,
            "provider_type": None,
            "limit": 100,
            "offset": 0,
        }
        assert "enabled" not in repo.list.call_args.kwargs

    async def test_existing_type_filter_unaffected(self, clear_app_module) -> None:
        repo = AsyncMock()
        repo.list.return_value = []
        mod = _provider_app(repo)

        async with client_for(mod) as client:
            resp = await client.get("/v1/providers?provider_type=serverless_lb&enabled=true")

        assert resp.status_code == 200
        assert repo.list.call_args.kwargs == {
            "capability_id": None,
            "enabled_only": True,
            "provider_type": "serverless_lb",
            "limit": 100,
            "offset": 0,
        }
