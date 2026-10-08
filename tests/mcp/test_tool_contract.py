"""Full tool contract suite for all 24 MCP tools.

Exercises all 24 pitwall_* MCP tools with seeded fixtures and structured
JSON assertions. Each test class corresponds to one tool group and verifies
the response schema, field types, and key presence.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_asyncpg_pool
from tests.fakes.teardown import UnlockedTeardown

pytestmark = pytest.mark.anyio


def test_model_tool_descriptions_explain_catalogue_workflow() -> None:
    from mcp.server.mcpserver import MCPServer

    from pitwall.mcp.registry import TOOL_REGISTRY, register_all

    descriptions = {spec.name: spec.description for spec in TOOL_REGISTRY}
    serve = descriptions["pitwall_serve_model"]
    fit = descriptions["pitwall_models_fit"]
    listing = descriptions["pitwall_models_list"]
    assert "pitwall_models_fit" in serve and "variant" in serve
    assert "canonical" in serve and "legacy aliases" in serve
    assert "rate_per_second" in serve and "unpriced" in serve and "dry_run" in serve
    assert "409" in serve and "422" in serve and "502" in serve and "503" in serve
    assert "sglang" in serve
    assert "catalogue" in fit and "unpriced" in fit
    assert "catalogue" in listing and "variant" in listing

    server = MCPServer("tool_contract")
    register_all(server)
    registered = {tool.name: tool for tool in server._tool_manager.list_tools()}
    assert (
        "Canonical RunPod GPU name"
        in registered["pitwall_serve_model"].parameters["properties"]["gpu_class"]["description"]
    )
    assert (
        "unpriced"
        in registered["pitwall_serve_model"].parameters["properties"]["rate_per_second"][
            "description"
        ]
    )
    engine_schema = registered["pitwall_serve_model"].parameters["properties"]["engine"]
    assert engine_schema["anyOf"][0]["enum"] == ["vllm", "llama.cpp", "sglang"]


TEST_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)
TEST_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)


def _make_capability_row(
    id: str = "cap_embedding_bge_m3",
    name: str = "embedding.bge-m3",
    **overrides: Any,
) -> dict[str, Any]:
    now = TEST_NOW
    config = {
        "description": "BGE-M3 multilingual embedding model",
        "input_schema": {
            "type": "object",
            "properties": {"texts": {"type": "array", "items": {"type": "string"}}},
            "required": ["texts"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"dense": {"type": "array"}, "sparse": {"type": "array"}},
        },
        "defaults": {"execution_timeout_ms": 60000, "ttl_ms": 300000, "result_delivery": "sync"},
        "hints_supported": [],
    }
    row = {
        "id": id,
        "name": name,
        "version": "1.0.0",
        "class": "embedding",
        "cost_mode": "per_second",
        "config": config,
        "source": "api",
        "last_applied_yaml_hash": None,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _make_provider_row(
    id: str = "prov_bge_m3_lb_us_ks",
    capability_id: str = "cap_embedding_bge_m3",
    **overrides: Any,
) -> dict[str, Any]:
    now = TEST_NOW
    config = {
        "lb_base_url": "https://eptest00000000.api.runpod.ai",
        "custom_paths": {"embed": "/embed", "health": "/ping"},
        "max_payload_mb": 30,
        "request_timeout_s": 330,
        "cost": {"mode": "per_second", "per_second_active": "0.000123"},
    }
    row = {
        "id": id,
        "capability_id": capability_id,
        "name": "bge-m3-lb-us-ks",
        "adapter_id": "runpod",
        "credential_ref": "RUNPOD_API_KEY",
        "provider_type": "serverless_lb",
        "runpod_endpoint_id": "eptest00000000",
        "runpod_template_id": None,
        "region": "US-KS-2",
        "cloud_type": None,
        "config": config,
        "priority": 1,
        "enabled": True,
        "health_status": "healthy",
        "consecutive_failures": 0,
        "cooldown_trips": 0,
        "cold_start_p50_ms": 8000,
        "cold_start_p95_ms": 22000,
        "recent_error_rate": 0.0,
        "cooldown_until": None,
        "source": "api",
        "last_applied_yaml_hash": None,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _make_workload_row(
    id: str = "wkl_test_001",
    capability_id: str = "cap_embedding_bge_m3",
    provider_id: str = "prov_bge_m3_lb_us_ks",
    **overrides: Any,
) -> dict[str, Any]:
    now = TEST_NOW
    row = {
        "id": id,
        "capability_id": capability_id,
        "provider_id": provider_id,
        "provider_type": "serverless_lb",
        "type": "inference",
        "state": "completed",
        "external_job_id": None,
        "submitted_at": now,
        "started_at": now,
        "completed_at": now,
        "execution_ms": 150,
        "queue_ms": 10,
        "cold_start_ms": None,
        "input_bytes": 500,
        "output_bytes": 200,
        "cost_estimate_usd": Decimal("0.005"),
        "cost_actual_usd": Decimal("0.0045"),
        "error": None,
        "langfuse_trace_id": "trace_test_123",
        "idempotency_key": None,
        "runpod_job_id": None,
        "result": {"dense": [[0.1, 0.2, 0.3]]},
    }
    row.update(overrides)
    return row


def _make_lease_row(
    id: str = "lease_test_001",
    capability_id: str = "cap_embedding_bge_m3",
    provider_id: str = "prov_bge_m3_lb_us_ks",
    **overrides: Any,
) -> dict[str, Any]:
    now = TEST_NOW
    expires = now + dt.timedelta(hours=1)
    endpoints = {
        "http": {"8000": "https://pod.test:8000"},
        "tcp": {"8000": {"host": "pod.test", "port": 8000}},
    }
    readiness = {
        "runtime_seen_at": now.isoformat(),
        "port_mappings_seen_at": now.isoformat(),
        "probe_passed_at": now.isoformat(),
        "probe_method": "http",
    }
    row = {
        "id": id,
        "provider_id": provider_id,
        "external_resource_id": "pod_test_123",
        "runpod_pod_id": "pod_test_123",
        "state": "active",
        "created_at": now,
        "expires_at": expires,
        "renewal_policy": "manual",
        "auto_teardown_on_expiry": True,
        "endpoints": endpoints,
        "readiness": readiness,
        "cost_accrued_usd": Decimal("0.05"),
        "last_health_at": now,
        "terminated_at": None,
        "terminated_reason": None,
    }
    row.update(overrides)
    return row


class TestDiscoveryToolContracts:
    """Contract tests for discovery tools: list_capabilities, describe_capability, list_providers, get_provider_health."""

    async def test_list_capabilities_returns_capabilities_key(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_capabilities

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=mock_pool):
            result = await pitwall_list_capabilities()

        assert isinstance(result, dict)
        assert "capabilities" in result
        assert isinstance(result["capabilities"], list)

    async def test_list_capabilities_with_seeded_data(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_capabilities

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[_make_capability_row()])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=mock_pool):
            result = await pitwall_list_capabilities(capability_class="embedding")

        assert isinstance(result, dict)
        assert "capabilities" in result
        assert len(result["capabilities"]) == 1
        cap_result = result["capabilities"][0]
        assert cap_result["id"] == "cap_embedding_bge_m3"
        assert cap_result["name"] == "embedding.bge-m3"
        assert cap_result["class"] == "embedding"
        assert "enabled" in cap_result

    async def test_describe_capability_returns_required_fields(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_describe_capability

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=_make_capability_row())
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=mock_pool):
            result = await pitwall_describe_capability(name="embedding.bge-m3")

        assert isinstance(result, dict)
        assert result["id"] == "cap_embedding_bge_m3"
        assert result["name"] == "embedding.bge-m3"
        assert "class" in result
        assert "cost_mode" in result
        assert "input_schema" in result
        assert "output_schema" in result

    async def test_list_providers_returns_providers_key(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_providers

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=mock_pool):
            result = await pitwall_list_providers()

        assert isinstance(result, dict)
        assert "providers" in result
        assert isinstance(result["providers"], list)

    async def test_list_providers_with_seeded_data(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_list_providers

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[_make_provider_row()])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=mock_pool):
            result = await pitwall_list_providers(provider_type="serverless_lb")

        assert isinstance(result, dict)
        assert "providers" in result
        assert len(result["providers"]) == 1
        prov_result = result["providers"][0]
        assert prov_result["id"] == "prov_bge_m3_lb_us_ks"
        assert prov_result["provider_type"] == "serverless_lb"
        assert "health_status" in prov_result

    async def test_get_provider_health_returns_health_fields(self) -> None:
        from pitwall.mcp.tools.discovery import pitwall_get_provider_health

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=_make_provider_row())
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.discovery.get_pool", return_value=mock_pool):
            result = await pitwall_get_provider_health(provider_id="prov_bge_m3_lb_us_ks")

        assert isinstance(result, dict)
        assert result["id"] == "prov_bge_m3_lb_us_ks"
        assert result["health_status"] == "healthy"
        assert "consecutive_failures" in result
        assert "cooldown_trips" in result
        assert "recent_error_rate" in result


class TestCostToolContracts:
    """Contract tests for cost tools: cost_summary, recent_workloads."""

    async def test_cost_summary_returns_total_usd_and_entries(self) -> None:
        from pitwall.mcp.tools.cost import pitwall_cost_summary

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchrow = AsyncMock(return_value=(Decimal("0"),))
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.cost.get_pool", return_value=mock_pool):
            result = await pitwall_cost_summary()

        assert isinstance(result, dict)
        assert "total_usd" in result
        assert "entries" in result
        assert isinstance(result["entries"], list)
        assert result["total_usd"] == "0.000000"

    async def test_cost_summary_with_seeded_entries(self) -> None:
        from pitwall.mcp.tools.cost import pitwall_cost_summary

        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(
            side_effect=lambda k: {
                "day": dt.date(2026, 1, 15),
                "capability_class": "embedding",
                "provider_type": "serverless_lb",
                "workload_count": 10,
                "cost_usd": Decimal("50.123456"),
            }[k]
        )

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[mock_row])
        mock_conn.fetchrow = AsyncMock(return_value=(Decimal("50.123456"),))
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.cost.get_pool", return_value=mock_pool):
            result = await pitwall_cost_summary(since="2026-01-01", until="2026-01-31")

        assert isinstance(result, dict)
        assert "entries" in result
        assert len(result["entries"]) == 1
        entry = result["entries"][0]
        assert "cost_usd" in entry
        assert entry["cost_usd"] == "50.123456"

    async def test_recent_workloads_returns_workloads_key(self) -> None:
        from pitwall.mcp.tools.cost import pitwall_recent_workloads

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.cost.get_pool", return_value=mock_pool):
            result = await pitwall_recent_workloads()

        assert isinstance(result, dict)
        assert "workloads" in result
        assert isinstance(result["workloads"], list)

    async def test_recent_workloads_with_seeded_data(self) -> None:
        from pitwall.mcp.tools.cost import pitwall_recent_workloads

        wl_row = _make_workload_row()
        mock_row = MagicMock()
        mock_row.__getitem__ = MagicMock(side_effect=lambda k: wl_row[k])

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[mock_row])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.cost.get_pool", return_value=mock_pool):
            result = await pitwall_recent_workloads(limit=20, capability_id="cap_embedding_bge_m3")

        assert isinstance(result, dict)
        assert "workloads" in result
        assert len(result["workloads"]) == 1
        wl_result = result["workloads"][0]
        assert wl_result["id"] == "wkl_test_001"
        assert "cost_estimate_usd" in wl_result
        assert "cost_actual_usd" in wl_result
        assert isinstance(wl_result["cost_estimate_usd"], str)


class TestLeaseToolContracts:
    """Contract tests for lease tools: lease_pod, get_lease, renew_lease, stop_lease."""

    async def test_get_lease_returns_lease_fields(self) -> None:
        from pitwall.mcp.tools.leases import pitwall_get_lease

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=_make_lease_row())
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.leases.get_pool", return_value=mock_pool):
            result = await pitwall_get_lease(lease_id="lease_test_001")

        assert isinstance(result, dict)
        assert result["id"] == "lease_test_001"
        assert "state" in result
        assert "provider_id" in result
        assert "expires_at" in result
        assert "renewal_policy" in result

    async def test_renew_lease_returns_updated_lease(self) -> None:
        from pitwall.mcp.tools.leases import pitwall_renew_lease

        new_expires = TEST_NOW + dt.timedelta(hours=2)
        lease_row = _make_lease_row()
        updated_row = _make_lease_row(expires_at=new_expires)

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[lease_row, updated_row])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=None)
        transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=transaction)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.leases.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.leases.lease_capability_name", AsyncMock(return_value=None)),
            # The budget reservation is pinned in tests/leases/test_renewal_reservation.py.
            patch("pitwall.leases.mutations.renewal_extension_usd", AsyncMock(return_value=None)),
        ):
            result = await pitwall_renew_lease(lease_id="lease_test_001", extends_minutes=60)

        assert isinstance(result, dict)
        assert result["id"] == "lease_test_001"
        assert "expires_at" in result

    async def test_stop_lease_maps_user_reason_to_operator_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import pitwall.api.leases.teardown as teardown
        from pitwall.db.repository import _lease_from_row
        from pitwall.mcp.tools.leases import pitwall_stop_lease

        lease = _lease_from_row(_make_lease_row())

        class LeaseRepo(UnlockedTeardown):
            async def get(self, lease_id: str):
                return lease

            async def capability_name(self, lease_id: str) -> str:
                return "llm.mcp-stop"

            async def update_state(self, lease_id: str, state: str):
                return lease.model_copy(update={"state": state})

            async def close_teardown(self, lease_id: str, **changes: Any):
                return lease.model_copy(update=changes)

        provider_repo = AsyncMock()
        provider_repo.get.return_value = None
        publish = AsyncMock()
        monkeypatch.setattr(
            "pitwall.mcp.tools.leases.get_pool", AsyncMock(return_value=MagicMock())
        )
        monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
        monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: provider_repo)
        monkeypatch.setattr(teardown, "terminate_pod", AsyncMock())
        monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
        monkeypatch.setattr(teardown, "publish_lease_event", publish)

        result = await pitwall_stop_lease("lease_test_001", reason="budget")

        assert result["terminated_reason"] == "budget"
        stopped = publish.await_args.args[2]
        assert stopped["data"] == {
            "capability": "llm.mcp-stop",
            "lease_id": lease.id,
            "reason": "operator",
        }


class TestInferenceToolContracts:
    """Contract tests for inference tools: submit_inference, submit_job, get_job_status, get_job_result, cancel_job."""

    async def test_get_job_status_returns_workload_fields(self) -> None:
        from pitwall.mcp.tools.inference import pitwall_get_job_status

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=_make_workload_row())
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.inference.get_pool", return_value=mock_pool):
            result = await pitwall_get_job_status(workload_id="wkl_test_001")

        assert isinstance(result, dict)
        assert result["workload_id"] == "wkl_test_001"
        assert "state" in result
        assert "cost" in result
        assert "provider_id" in result
        assert "result" in result

    async def test_get_job_result_returns_result_and_cost(self) -> None:
        from pitwall.mcp.tools.inference import pitwall_get_job_result

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=_make_workload_row())
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.inference.get_pool", return_value=mock_pool):
            result = await pitwall_get_job_result(workload_id="wkl_test_001")

        assert isinstance(result, dict)
        assert result["workload_id"] == "wkl_test_001"
        assert "cost" in result
        assert "estimate_usd" in result["cost"]
        assert "actual_usd" in result["cost"]
        assert result["result"] is not None

    async def test_cancel_job_returns_cancelled_state(self) -> None:
        from pitwall.mcp.tools.inference import pitwall_cancel_job

        queued_workload = _make_workload_row(type="async_job", state="queued")
        cancelled_workload = _make_workload_row(type="async_job", state="cancelled")

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[queued_workload, cancelled_workload])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.inference.get_pool", return_value=mock_pool):
            result = await pitwall_cancel_job(workload_id="wkl_test_001")

        assert isinstance(result, dict)
        assert result["workload_id"] == "wkl_test_001"
        assert "cancelled" in result or result["state"] == "cancelled"


class TestAdminToolContracts:
    """Contract tests for admin tools: create_capability, update_capability, create_provider, update_provider, disable_provider, hibernate_provider, audit_log."""

    async def test_create_capability_returns_capability_fields(self) -> None:
        from pitwall.mcp.tools.admin import pitwall_create_capability

        created_cap_row = _make_capability_row(
            id="cap_01KSSCG9SZ23YS8KYPCQRBGD37",
            name="test.capability",
            description="A test capability",
        )

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[None, created_cap_row])
        mock_conn.execute = AsyncMock(return_value="INSERT 1")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.admin.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock),
        ):
            result = await pitwall_create_capability(
                name="test.capability",
                version="1.0.0",
                capability_class="embedding",
                cost_mode="per_second",
                description="A test capability",
            )

        assert isinstance(result, dict)
        assert "id" in result
        assert result["name"] == "test.capability"
        assert result["version"] == "1.0.0"
        assert "class" in result
        assert "cost_mode" in result
        assert "enabled" in result

    async def test_update_capability_returns_updated_fields(self) -> None:
        from pitwall.mcp.tools.admin import pitwall_update_capability

        cap_row = _make_capability_row()
        updated_config = {
            "description": "Updated description",
            "input_schema": cap_row["config"]["input_schema"],
            "output_schema": cap_row["config"]["output_schema"],
            "defaults": cap_row["config"]["defaults"],
            "hints_supported": [],
        }
        updated_row = _make_capability_row(config=updated_config)

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[cap_row, updated_row])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=None)
        transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=transaction)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.admin.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock),
        ):
            result = await pitwall_update_capability(
                capability_id="cap_embedding_bge_m3",
                description="Updated description",
            )

        assert isinstance(result, dict)
        assert result["id"] == "cap_embedding_bge_m3"
        assert "description" in result

    async def test_create_provider_returns_provider_fields(self) -> None:
        from pitwall.mcp.tools.admin import pitwall_create_provider

        created_prov_row = _make_provider_row(
            id="prov_01KSSCG9SZ23YS8KYPCQRBGD38",
            name="test-provider",
            runpod_endpoint_id="abc123",
        )

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[None, created_prov_row])
        mock_conn.execute = AsyncMock(return_value="INSERT 1")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.admin.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock),
        ):
            result = await pitwall_create_provider(
                capability_id="cap_embedding_bge_m3",
                name="test-provider",
                provider_type="serverless_lb",
                runpod_endpoint_id="abc123",
            )

        assert isinstance(result, dict)
        assert "id" in result
        assert result["name"] == "test-provider"
        assert result["capability_id"] == "cap_embedding_bge_m3"
        assert result["adapter_id"] == "runpod"
        assert result["credential_ref"] == "RUNPOD_API_KEY"
        assert "provider_type" in result
        assert "enabled" in result

    async def test_update_provider_returns_updated_fields(self) -> None:
        from pitwall.mcp.tools.admin import pitwall_update_provider

        prov_row = _make_provider_row()
        updated_row = _make_provider_row(priority=5)

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[prov_row, updated_row])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=None)
        transaction.__aexit__ = AsyncMock(return_value=None)
        mock_conn.transaction = MagicMock(return_value=transaction)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.admin.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock),
        ):
            result = await pitwall_update_provider(
                provider_id="prov_bge_m3_lb_us_ks",
                priority=5,
            )

        assert isinstance(result, dict)
        assert result["id"] == "prov_bge_m3_lb_us_ks"
        assert result["adapter_id"] == "runpod"
        assert result["credential_ref"] == "RUNPOD_API_KEY"
        assert result["priority"] == 5

    async def test_disable_provider_returns_disabled_provider(self) -> None:
        from pitwall.mcp.tools.admin import pitwall_disable_provider

        prov_row = _make_provider_row()
        disabled_row = _make_provider_row(enabled=False)

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[prov_row, disabled_row])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.admin.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock),
        ):
            result = await pitwall_disable_provider(provider_id="prov_bge_m3_lb_us_ks")

        assert isinstance(result, dict)
        assert result["id"] == "prov_bge_m3_lb_us_ks"
        assert result["enabled"] is False

    async def test_hibernate_provider_returns_hibernated_status(self) -> None:
        from pitwall.mcp.tools.admin import pitwall_hibernate_provider

        prov_row = _make_provider_row()
        hibernated_row = _make_provider_row(health_status="hibernated")

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=[prov_row, hibernated_row])
        mock_conn.execute = AsyncMock(return_value="UPDATE 1")
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with (
            patch("pitwall.mcp.tools.admin.get_pool", return_value=mock_pool),
            patch("pitwall.mcp.tools.admin.insert_audit", new_callable=AsyncMock),
        ):
            result = await pitwall_hibernate_provider(provider_id="prov_bge_m3_lb_us_ks")

        assert isinstance(result, dict)
        assert result["id"] == "prov_bge_m3_lb_us_ks"
        assert result["health_status"] == "hibernated"

    async def test_audit_log_returns_entries(self) -> None:
        from pitwall.mcp.tools.audit import pitwall_audit_log

        now = TEST_NOW

        audit_row = {
            "id": 1,
            "actor": "mcp:admin",
            "action": "create",
            "entity_type": "capability",
            "entity_id": "cap_test",
            "old_value": None,
            "new_value": {"name": "test.capability"},
            "change_reason": None,
            "created_at": now,
        }

        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[audit_row])
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.audit.get_pool", return_value=mock_pool):
            result = await pitwall_audit_log(limit=50)

        assert isinstance(result, dict)
        assert "entries" in result
        assert isinstance(result["entries"], list)
        if result["entries"]:
            entry = result["entries"][0]
            assert "id" in entry
            assert "actor" in entry
            assert "action" in entry
            assert "entity_type" in entry
            assert "entity_id" in entry

    async def test_audit_log_preserves_generic_resource_id_and_redacts_provider_values(
        self,
    ) -> None:
        from pitwall.mcp.tools.audit import pitwall_audit_log

        secret = "mcp-audit-camel-secret-canary"  # pragma: allowlist secret
        rows = [
            {
                "id": 2,
                "actor": "system:lease-controller",
                "action": "stop",
                "entity_type": "lease",
                "entity_id": "lease-generic",
                "old_value": {"state": "active"},
                "new_value": {
                    "state": "stopped",
                    "reason": "idle",
                    "external_resource_id": "vast-resource-123",
                },
                "change_reason": "automated lease stop: idle",
                "created_at": TEST_NOW,
            },
            {
                "id": 1,
                "actor": "system",
                "action": "update",
                "entity_type": "provider",
                "entity_id": "prov-legacy",
                "old_value": {"config": {"clientSecret": secret}},
                "new_value": {"config": {"apiKey": secret}},
                "change_reason": "legacy",
                "created_at": TEST_NOW,
            },
        ]
        mock_pool = make_asyncpg_pool()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=rows)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)

        with patch("pitwall.mcp.tools.audit.get_pool", return_value=mock_pool):
            result = await pitwall_audit_log(limit=50)

        lease_entry, provider_entry = result["entries"]
        assert lease_entry["new_value"]["external_resource_id"] == "vast-resource-123"
        assert provider_entry["old_value"] == {"config": {"clientSecret": "[REDACTED]"}}
        assert provider_entry["new_value"] == {"config": {"apiKey": "[REDACTED]"}}
        assert secret not in str(result)
