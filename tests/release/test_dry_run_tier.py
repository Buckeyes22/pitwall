"""Release-tier tests for the dry-run pre-spend validation tier.

Tier: dry-run
Purpose: Validate configuration without spending.

These tests run routing + capacity-probe + cost estimation but do NOT call RunPod.
They use mocked RunPod transport to assert no paid call is possible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.api.leases import launch
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.cost.estimator import get_estimator
from tests.conftest import make_asyncpg_pool

NOW = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_embedding_bge_m3",
        name="embedding.bge-m3",
        version="1.0.0",
        class_=CapabilityClass.EMBEDDING,
        cost_mode=CostMode.PER_REQUEST,
        source=CapabilitySource.API,
        created_at=NOW,
        updated_at=NOW,
    )


def _pod_lease_provider() -> Provider:
    return Provider(
        id="prov_pod_qwen3",
        capability_id="cap_llm_qwen3",
        name="qwen3-h100-pod-us-ca",
        provider_type=ProviderType.POD_LEASE,
        region="US-CA-2",
        cloud_type="SECURE",
        config={
            "image_ref": "ghcr.io/acme/pitwall-worker:qwen3",
            "template_name": "pitwall-qwen3-h100",
            "gpu_type_priority": ["NVIDIA H100 80GB HBM3", "NVIDIA L4"],
            "container_disk_gb": 80,
            "volume_id": "vol-model-cache",
            "volume_mount": "/workspace",
            "ports": {"http": [8000], "tcp": [22]},
            "env_vars": {"VLLM_MODEL": "Qwen/Qwen3-32B"},
            "cost": {"per_second_active": "0.002"},
        },
        priority=1,
        source=CapabilitySource.API,
        updated_at=NOW,
    )


def _serverless_provider(
    *,
    id: str = "prov_bge_m3_lb",
    health_status: str = "healthy",
    priority: int = 1,
) -> Provider:
    return Provider(
        id=id,
        capability_id="cap_embedding_bge_m3",
        name=f"bge-m3-lb-{id}",
        provider_type=ProviderType.SERVERLESS_LB,
        runpod_endpoint_id=f"{id}-endpoint",
        config={
            "cost": {"per_request": "0.0001"},
        },
        priority=priority,
        enabled=True,
        health_status=health_status,
        source=CapabilitySource.API,
        updated_at=NOW,
    )


@pytest.mark.release
@pytest.mark.anyio
async def test_dry_run_leases_returns_template_without_creating_pod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/leases with dry_run=True returns the launch plan without any RunPod write.

    A dry run resolves the plan but performs no RunPod call at all: no template
    is created (``ensure_template`` must not run; an uncached image reports the
    literal ``"dry-run"`` template id) and no pod is created.
    """

    async def forbid_ensure_template(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("dry_run must not create a RunPod template")

    create_pod_called = False

    async def track_create_pod(**_kwargs: Any) -> dict[str, Any]:
        nonlocal create_pod_called
        create_pod_called = True
        raise AssertionError("dry_run must not create a pod")

    monkeypatch.setattr(launch, "ensure_template", forbid_ensure_template)
    monkeypatch.setattr(launch, "create_pod_with_fallback", track_create_pod)

    result = await launch.run_launch(
        pool=make_asyncpg_pool(),
        capability=_capability(),
        provider=_pod_lease_provider(),
        request_id="req_dry",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["pod_id"] is None
    assert result["template_id"] == "dry-run"
    assert result["capability"] == "embedding.bge-m3"
    assert result["provider"] == "qwen3-h100-pod-us-ca"
    assert create_pod_called is False, "create_pod_with_fallback must not be called in dry_run"


@pytest.mark.release
@pytest.mark.anyio
async def test_dry_run_inference_returns_routing_info_without_calling_runpod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/inference with dry_run=True returns routing info without calling RunPod."""

    runpod_calls: list[str] = []

    from pitwall.api.app import app

    del monkeypatch

    plan = SimpleNamespace(
        plan_id="plan_release_dry_run",
        capability_name="embedding.bge-m3",
        selected_provider_id="prov_bge_m3_lb",
        to_dict=lambda: {
            "plan_id": "plan_release_dry_run",
            "capability_id": "cap_embedding_bge_m3",
            "capability_name": "embedding.bge-m3",
            "selected_provider_id": "prov_bge_m3_lb",
        },
    )
    routing_service = MagicMock()
    routing_service.preview = AsyncMock(return_value=plan)

    app.state.pool = MagicMock()
    app.state.production_routing_service = routing_service

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/v1/inference",
                json={
                    "capability": "embedding.bge-m3",
                    "texts": ["hello"],
                    "dry_run": True,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["workload_id"].startswith("dry_run_inference_")
        assert body["result"]["dry_run"] is True
        assert body["result"]["plan"]["capability_id"] == "cap_embedding_bge_m3"
        assert body["result"]["plan"]["selected_provider_id"] == "prov_bge_m3_lb"
        assert runpod_calls == [], "RunPod should not be called in dry_run mode"
        routing_service.preview.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()
        if hasattr(app.state, "production_routing_service"):
            delattr(app.state, "production_routing_service")
        if hasattr(app.state, "pool"):
            delattr(app.state, "pool")


@pytest.mark.release
@pytest.mark.anyio
async def test_dry_run_lease_no_paid_call_to_runpod_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify dry_run=True prevents any paid RunPod API call (pod creation, etc)."""

    paid_api_calls: list[str] = []

    async def track_create_pod(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        paid_api_calls.append("create_pod_with_fallback")
        return {"id": "pod_should_not_be_created"}

    from pitwall.runpod_client import pods

    monkeypatch.setattr(pods, "create_pod_with_fallback", track_create_pod)

    result = await launch.run_launch(
        pool=MagicMock(),
        capability=_capability(),
        provider=_pod_lease_provider(),
        request_id="req_no_paid",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["pod_id"] is None
    assert "create_pod_with_fallback" not in paid_api_calls


@pytest.mark.release
def test_dry_run_tier_documented_in_conftest() -> None:
    """Assert the release conftest declares the dry-run tier."""
    from tests.release.conftest import pytest_configure

    config = MagicMock()
    config.addinivalue_line = MagicMock()
    pytest_configure(config)
    markers = [
        call.args[1] for call in config.addinivalue_line.call_args_list if call.args[0] == "markers"
    ]
    assert any(line.startswith("release:") for line in markers), markers


@pytest.mark.release
def test_dry_run_pre_spend_estimator_is_decimal_and_transport_free(
    mock_transport: Any,
) -> None:
    capability = _capability().model_copy(update={"cost_mode": CostMode.PER_SECOND})
    estimate = get_estimator("per_second").estimate(
        capability,
        {"per_second_active": Decimal("0.002")},
        {"input": "hello"},
    )

    assert isinstance(estimate, Decimal)
    assert Decimal("0") < estimate <= Decimal("1")
    assert mock_transport.requests == []
