"""Tests for Langfuse trace emission in POST /v1/inference."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from pitwall.core.enums import CapabilityClass, ProviderType, WorkloadState
from pitwall.core.models import Capability, Provider, Workload
from pitwall.routing.production import RouteExecutionResult

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)


class _Plan:
    plan_id = "plan_33333333333333333333333333333333"
    capability_name = "embedding.bge-m3"
    selected_provider_id = "prov_1"

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "capability_name": self.capability_name,
            "selected_provider_id": self.selected_provider_id,
        }


class _Service:
    async def capability_class(self, capability_id: str) -> CapabilityClass:
        return CapabilityClass.EMBEDDING  # embedding.bge-m3

    async def execute_sync(self, **_: object) -> RouteExecutionResult:
        result = {"dense_embeddings": [[0.1, 0.2, 0.3]]}
        workload = Workload(
            id="wkl_no_trace",
            capability_id="cap_embedding_bge_m3",
            provider_id="prov_1",
            type="inference",
            state=WorkloadState.COMPLETED,
            submitted_at=_NOW,
            completed_at=_NOW,
            result=result,
            route_plan_id=_Plan.plan_id,
            route_plan=_Plan().to_dict(),
        )
        return RouteExecutionResult(workload=workload, plan=_Plan(), output=result)


def _capability() -> Capability:
    return Capability(
        id="cap_embedding_bge_m3",
        name="embedding.bge-m3",
        version="1.0.0",
        class_="embedding",
        cost_mode="per_second",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(
    provider_id: str,
    *,
    priority: int,
    health_status: str = "healthy",
    per_second_active: str = "0.0001",
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id="cap_embedding_bge_m3",
        name=provider_id,
        provider_type=ProviderType.SERVERLESS_LB,
        runpod_endpoint_id=f"{provider_id}-endpoint",
        priority=priority,
        enabled=True,
        health_status=health_status,
        updated_at=_NOW,
        config={"per_second_active": per_second_active},
    )


class TestLangfuseFailureDoesNotFailInference:
    @pytest.fixture()
    def api_client_langfuse_failure(self) -> object:
        old = os.environ.copy()
        os.environ.update(
            {
                "RUNPOD_API_KEY": "test-key",
                "DATABASE_URL": "postgresql://u:p@localhost/db",
                "REDIS_URL": "redis://localhost:6379/0",
                "LANGFUSE_HOST": "http://langfuse.invalid",
                "LANGFUSE_PUBLIC_KEY": "test",
                "LANGFUSE_SECRET_KEY": "test",
            }
        )

        for mod in list(sys.modules):
            if mod.startswith("pitwall.api"):
                del sys.modules[mod]

        from pitwall.api.app import app
        from pitwall.api.routes.inference import _routing_service
        from pitwall.observability import langfuse as langfuse_module

        async def mock_execute(*args: Any, **kwargs: Any) -> str:
            return None

        async def mock_fetchval(*args: Any, **kwargs: Any) -> Any:
            return None

        async def mock_fetchrow(*args: Any, **kwargs: Any) -> Any:
            return {"s": Decimal("0")}

        mock_conn = MagicMock()
        mock_conn.execute = mock_execute
        mock_conn.fetchval = mock_fetchval
        mock_conn.fetchrow = mock_fetchrow

        class _TransactionCtx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *args):
                pass

        mock_conn.transaction = MagicMock(return_value=_TransactionCtx())

        class _AcquireCtx:
            async def __aenter__(self):
                return mock_conn

            async def __aexit__(self, *args):
                pass

        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=_AcquireCtx())

        app.state.pool = mock_pool
        app.dependency_overrides[_routing_service] = lambda: _Service()

        langfuse_module.reset_client_for_tests()

        yield app

        app.dependency_overrides.clear()
        if hasattr(app.state, "pool"):
            delattr(app.state, "pool")
        os.environ.clear()
        os.environ.update(old)
        for mod in list(sys.modules):
            if mod.startswith("pitwall.api"):
                del sys.modules[mod]

    @pytest.mark.anyio
    async def test_langfuse_failure_does_not_fail_inference(
        self,
        api_client_langfuse_failure: object,
    ) -> None:
        app = api_client_langfuse_failure
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/inference",
                json={
                    "capability": "embedding.bge-m3",
                    "texts": ["hello"],
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "workload_id" in body
        assert "result" in body
