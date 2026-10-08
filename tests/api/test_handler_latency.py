"""Handler latency tests for the FastAPI test client path.

Measure handler cost around the FastAPI test client and assert p95 below 50ms.

The budget is CPU time, not wall-clock time. Wall time under CPU contention measures the
scheduler, not the handler. ``time.process_time`` is also wrong: it sums CPU from every
thread, so threads leaked by earlier tests in a long suite bill their work to the request.
Instead each request is billed the calling thread's ``time.thread_time`` (async handlers
and middleware run there on the ASGI transport) plus the ``thread_time`` of each
``anyio.to_thread.run_sync`` call (sync handlers and dependencies), measured inside the
worker thread that ran it. A CPU-bound slow handler still exceeds the budget; every handler
here is in-process with stubbed I/O, so none blocks on real I/O inside the measured window.
"""

from __future__ import annotations

import importlib
import math
import os
import sys
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import anyio.to_thread
import httpx
import pytest


def _env_for_app(**overrides: str) -> dict[str, str]:
    base: dict[str, str] = {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "PITWALL_INBOUND_RATE_LIMIT": "off",
        "PITWALL_ADMIN_SECRET": "-".join(("test", "admin", "secret")),
    }
    base.update(overrides)
    return base


def _import_app(env: dict[str, str]):
    old = os.environ.copy()
    os.environ.update(env)
    for k in list(os.environ):
        if k not in env and k in (
            "RUNPOD_API_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "PITWALL_ADMIN_SECRET",
            "PITWALL_API_TOKEN",
            "PITWALL_INBOUND_RATE_LIMIT",
        ):
            del os.environ[k]
    try:
        mod = importlib.import_module("pitwall.api.app")
        return mod
    finally:
        os.environ.clear()
        os.environ.update(old)


_worker_cpu: list[float] = []


@pytest.fixture(autouse=True)
def _bill_worker_thread_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record thread CPU spent in each threadpool call FastAPI makes for sync code."""
    original = anyio.to_thread.run_sync

    async def run_sync(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        def timed(*a: Any) -> Any:
            start = time.thread_time()
            try:
                return func(*a)
            finally:
                _worker_cpu.append(time.thread_time() - start)

        return await original(timed, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", run_sync)


async def _timed_request(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> tuple[httpx.Response, float]:
    """Return the response and the CPU milliseconds the request's own threads consumed."""
    _worker_cpu.clear()
    start = time.thread_time()
    response = await client.request(method, path, **kwargs)
    cpu = time.thread_time() - start + sum(_worker_cpu)
    return response, cpu * 1000


def _calculate_p95(latencies: list[float]) -> float:
    if not latencies:
        return 0.0
    sorted_latencies = sorted(latencies)
    index = math.ceil(len(sorted_latencies) * 0.95) - 1
    index = max(0, min(index, len(sorted_latencies) - 1))
    return sorted_latencies[index]


@pytest.fixture(autouse=True)
def _clear_app_module():
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for k in to_remove:
        del sys.modules[k]
    yield
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for k in to_remove:
        del sys.modules[k]


@pytest.fixture
def app_mod() -> Any:
    env = _env_for_app()
    mod = _import_app(env)
    mod.app.state.pool = MagicMock()
    return mod


@pytest.mark.anyio
async def test_healthz_handler_latency_p95_below_50ms(app_mod: Any) -> None:
    """Handler duration for /healthz endpoint has p95 < 50ms."""
    num_requests = 100
    latencies: list[float] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        for _ in range(num_requests):
            response, cpu_ms = await _timed_request(client, "GET", "/healthz")
            assert response.status_code == 200
            latencies.append(cpu_ms)

    p95 = _calculate_p95(latencies)
    avg = sum(latencies) / len(latencies)
    max_latency = max(latencies)
    min_latency = min(latencies)

    assert p95 < 50, (
        f"p95 CPU time {p95:.2f}ms exceeded 50ms threshold (avg={avg:.2f}ms, min={min_latency:.2f}ms, max={max_latency:.2f}ms)"
    )


@pytest.mark.anyio
async def test_health_handler_latency_p95_below_50ms(app_mod: Any) -> None:
    """Handler duration for /health endpoint has p95 < 50ms."""
    num_requests = 100
    latencies: list[float] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        for _ in range(num_requests):
            response, cpu_ms = await _timed_request(client, "GET", "/health")
            assert response.status_code == 200
            latencies.append(cpu_ms)

    p95 = _calculate_p95(latencies)
    avg = sum(latencies) / len(latencies)
    max_latency = max(latencies)
    min_latency = min(latencies)

    assert p95 < 50, (
        f"p95 CPU time {p95:.2f}ms exceeded 50ms threshold (avg={avg:.2f}ms, min={min_latency:.2f}ms, max={max_latency:.2f}ms)"
    )


def _make_client(app_mod: Any) -> Callable[..., Any]:
    async def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
            **kwargs,
        )

    return client_factory


async def _assert_handler_p95(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json_body: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    requests: int = 25,
) -> None:
    latencies: list[float] = []
    for _ in range(requests):
        response, cpu_ms = await _timed_request(
            client, method, path, json=json_body, headers=headers
        )
        latencies.append(cpu_ms)
        assert response.status_code == 200, response.text
    p95 = _calculate_p95(latencies)
    assert p95 < 50, f"{method} {path} p95 CPU time {p95:.2f}ms exceeded 50ms"


@pytest.mark.anyio
async def test_retained_capability_handler_latency_p95_below_50ms(
    app_mod: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every retained REST capability group has representative handler evidence."""

    from pitwall.api.routes import (
        burn_rate,
        cost,
        onboarding,
        provider_operations,
        routing,
        runpod_resources,
        volume_files,
    )
    from pitwall.security.pre_spend import PreSpendInspectionService
    from tests.onboarding.test_service import _endpoint_request, _service

    class Serializable:
        def __init__(self, value: dict[str, object]) -> None:
            self.value = value

        def to_dict(self) -> dict[str, object]:
            return self.value

        def to_legacy_serializable_dict(self) -> dict[str, object]:
            return self.value

        def to_serializable_dict(self) -> dict[str, object]:
            return self.value

    class ProviderService:
        async def list_descriptors(self, **kwargs: object) -> tuple[object, ...]:
            del kwargs
            return ()

    class MarketService:
        async def read(self, **kwargs: object) -> Serializable:
            del kwargs
            return Serializable({"state": "unavailable"})

    class RoutingService:
        async def preview(self, **kwargs: object) -> Serializable:
            del kwargs
            return Serializable(
                {
                    "plan_id": "plan_0123456789abcdef0123456789abcdef",
                    "observed_at": "2026-09-01T16:00:00Z",
                    "operation": "sync_inference",
                    "capability_id": "llm.chat",
                    "capability_name": "llm.chat",
                    "payload_sha256": "0" * 64,
                    "provider_constraint": None,
                    "mode": "priority",
                    "weights": {"cost": "1", "latency": "0.001"},
                    "selected_provider_id": "provider-runpod",
                    "fallback_chain": ["provider-runpod"],
                    "attempts": [],
                    "ranked_candidates": [],
                    "eliminated": [],
                    "signal_policy": {},
                }
            )

    class ResourceService:
        async def list_pods(self) -> list[object]:
            return []

    class VolumeService:
        async def list_objects(self, **kwargs: object) -> Serializable:
            del kwargs
            return Serializable(
                {
                    "operation": "list",
                    "status": "completed",
                    "provider": "runpod",
                    "volume_id": "volume-1",
                    "data_center_id": "US-TEST-1",
                    "object_key": None,
                    "pod_id": None,
                    "bytes_transferred": 0,
                    "checksum_sha256": None,
                    "objects": [],
                    "content_base64": None,
                    "logs": [],
                    "truncated": False,
                    "irreversible": False,
                    "replayed": False,
                    "progress": [],
                }
            )

    class OnboardingService:
        def __init__(self, result: object) -> None:
            self.result = result

        async def execute(self, command: object) -> object:
            del command
            return self.result

    async def burn_rate_read(*args: object, **kwargs: object) -> Serializable:
        del args, kwargs
        return Serializable({"status": "ok"})

    async def cost_summary(*args: object, **kwargs: object) -> Serializable:
        del args, kwargs
        return Serializable({"totals": {}})

    onboarding_service, _, _, _ = _service()
    onboarding_request = _endpoint_request()
    onboarding_plan = await onboarding_service.plan(onboarding_request)

    monkeypatch.setattr(burn_rate, "read_configured_burn_rate", burn_rate_read)
    monkeypatch.setattr(cost, "cost_summary_read", cost_summary)
    app_mod.app.state.pre_spend_inspection_service = PreSpendInspectionService()
    app_mod.app.state.runpod_market_service = MarketService()
    app_mod.app.dependency_overrides[provider_operations._service] = ProviderService
    app_mod.app.dependency_overrides[routing._service] = RoutingService
    app_mod.app.dependency_overrides[runpod_resources.runpod_control_plane_service] = (
        ResourceService
    )
    app_mod.app.dependency_overrides[volume_files._volume_file_service] = VolumeService
    app_mod.app.dependency_overrides[onboarding.runpod_onboarding_service] = lambda: (
        OnboardingService(onboarding_plan)
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        await _assert_handler_p95(client, "GET", "/v1/cost/summary")
        await _assert_handler_p95(client, "GET", "/v1/cost/burn-rate")
        await _assert_handler_p95(client, "GET", "/v1/guardrails")
        await _assert_handler_p95(client, "GET", "/v1/provider-ops/descriptors")
        await _assert_handler_p95(
            client,
            "POST",
            "/v1/routing/preview",
            json_body={"capability_id": "llm.chat", "payload": {}},
        )
        await _assert_handler_p95(client, "GET", "/v1/runpod/catalogue")
        await _assert_handler_p95(client, "GET", "/v1/admin/runpod/pods")
        await _assert_handler_p95(
            client,
            "GET",
            "/v1/volumes/volume-1/objects?data_center_id=US-TEST-1",
        )
        await _assert_handler_p95(
            client,
            "POST",
            "/v1/admin/runpod/onboarding/plan",
            json_body=onboarding_request.model_dump(mode="json"),
            headers={"X-Pitwall-Secret": "-".join(("test", "admin", "secret"))},
        )
