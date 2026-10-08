"""Job status, result, and cancel endpoints.

Tests for:
    GET  /v1/jobs/{id}          — full workload row
    GET  /v1/jobs/{id}/status   — state summary
    GET  /v1/jobs/{id}/result   — shared bounded result page
    POST /v1/jobs/{id}/cancel   — cancel queued/running jobs
"""

from __future__ import annotations

import datetime as dt
import importlib
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.core.enums import WorkloadState
from pitwall.core.models import Workload
from pitwall.routing.production import JobResultPage

pytestmark = pytest.mark.anyio

TEST_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)


def _make_workload(
    *,
    id: str = "wkl_test",
    state: WorkloadState = WorkloadState.COMPLETED,
    runpod_job_id: str | None = "rp-job-001",
    provider_id: str = "prov_test",
    capability_id: str = "cap_test",
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    started_at: dt.datetime | None = None,
    completed_at: dt.datetime | None = None,
    runpod_endpoint_id: str = "ep-test",
) -> Workload:
    return Workload(
        id=id,
        capability_id=capability_id,
        provider_id=provider_id,
        type="async",
        state=state,
        runpod_job_id=runpod_job_id,
        input={"prompt": "hello"},
        result=result,
        submitted_at=TEST_NOW,
        started_at=started_at,
        completed_at=completed_at,
        error=error,
    )


@pytest.fixture(autouse=True)
def _clear_app_module():
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for k in to_remove:
        del sys.modules[k]
    yield
    to_remove = [k for k in sys.modules if k.startswith("pitwall.api")]
    for k in to_remove:
        del sys.modules[k]


def _import_app():
    old = os.environ.copy()
    env = {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
    }
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


def _attach_service(app_mod, workload: Workload | None) -> MagicMock:
    service = MagicMock()
    if workload is None:
        service.get_job = AsyncMock(side_effect=LookupError("missing workload"))
        service.job_result = AsyncMock(side_effect=LookupError("missing workload"))
        service.cancel_job = AsyncMock(side_effect=LookupError("missing workload"))
    else:
        service.get_job = AsyncMock(return_value=workload)
        state = str(getattr(workload.state, "value", workload.state))
        terminal = state in {"cancelled", "completed", "failed", "timed_out"}
        service.job_result = AsyncMock(
            return_value=JobResultPage(
                workload_id=workload.id,
                plan_id=workload.route_plan_id,
                state=state,
                provider_id=workload.provider_id,
                result=workload.result if terminal else None,
                available=terminal and workload.result is not None,
                unavailable_reason=(
                    None
                    if terminal and workload.result is not None
                    else "provider_result_unavailable"
                    if terminal
                    else "job_not_terminal"
                ),
            )
        )
        service.cancel_job = AsyncMock(return_value=workload)
    app_mod.app.state.production_routing_service = service
    return service


async def _get(client: httpx.AsyncClient, path: str) -> httpx.Response:
    return await client.get(path)


async def _post(client: httpx.AsyncClient, path: str) -> httpx.Response:
    return await client.post(path)


class TestGetJob:
    async def test_returns_full_workload_row(self):
        app_mod = _import_app()
        workload = _make_workload()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "wkl_test"
        assert body["state"] == "completed"
        assert body["capability_id"] == "cap_test"
        assert body["provider_id"] == "prov_test"
        assert body["runpod_job_id"] == "rp-job-001"

    async def test_missing_workload_returns_404(self):
        app_mod = _import_app()
        _attach_service(app_mod, None)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/nonexistent")

        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "workload_not_found"


class TestGetJobStatus:
    async def test_returns_state_string_for_seeded_job(self):
        app_mod = _import_app()
        workload = _make_workload(state=WorkloadState.QUEUED)
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "wkl_test"
        assert body["state"] == "queued"
        assert body["runpod_job_id"] == "rp-job-001"
        assert "submitted_at" in body

    async def test_missing_job_returns_404(self):
        app_mod = _import_app()
        _attach_service(app_mod, None)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/missing/status")

        assert resp.status_code == 404

    async def test_status_includes_all_fields(self):
        started = dt.datetime(2026, 5, 28, 12, 1, 0, tzinfo=dt.UTC)
        completed = dt.datetime(2026, 5, 28, 12, 2, 0, tzinfo=dt.UTC)
        workload = _make_workload(
            state=WorkloadState.COMPLETED,
            started_at=started,
            completed_at=completed,
            error=None,
        )
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test/status")

        body = resp.json()
        assert body["started_at"] is not None
        assert body["completed_at"] is not None
        assert body["error"] is None


class TestGetJobResult:
    async def test_returns_result_for_completed_job(self):
        result_payload = {"output": "done", "tokens": 42}
        workload = _make_workload(
            state=WorkloadState.COMPLETED,
            result=result_payload,
            completed_at=TEST_NOW,
        )
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test/result")

        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "id": "wkl_test",
            "route_plan_id": None,
            "selected_provider_id": "prov_test",
            "result": result_payload,
            "available": True,
            "unavailable_reason": None,
        }

    async def test_preserves_409_for_queued_job(self):
        workload = _make_workload(state=WorkloadState.QUEUED)
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test/result")

        assert resp.status_code == 409
        body = resp.json()
        assert body == {"error": "job_not_ready", "id": "wkl_test", "state": "queued"}

    async def test_preserves_409_for_running_job(self):
        workload = _make_workload(state=WorkloadState.RUNNING)
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test/result")

        assert resp.status_code == 409
        assert resp.json() == {"error": "job_not_ready", "id": "wkl_test", "state": "running"}

    async def test_missing_job_returns_404(self):
        app_mod = _import_app()
        _attach_service(app_mod, None)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/missing/result")

        assert resp.status_code == 404

    async def test_returns_result_for_failed_job(self):
        workload = _make_workload(
            state=WorkloadState.FAILED,
            error={"message": "OOM"},
            completed_at=TEST_NOW,
        )
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _get(client, "/v1/jobs/wkl_test/result")

        assert resp.status_code == 200
        assert resp.json()["available"] is False
        assert resp.json()["unavailable_reason"] == "provider_result_unavailable"


class TestCancelJob:
    async def test_cancel_queued_job(self):
        workload = _make_workload(state=WorkloadState.QUEUED)
        cancelled = workload.model_copy(update={"state": WorkloadState.CANCELLED})
        service = MagicMock()
        service.cancel_job = AsyncMock(return_value=cancelled)
        app_mod = _import_app()
        app_mod.app.state.production_routing_service = service
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/wkl_test/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "cancelled"
        service.cancel_job.assert_awaited_once_with("wkl_test")

    async def test_cancel_running_job_with_mocked_runpod(self):
        workload = _make_workload(
            id="wkl_running",
            state=WorkloadState.RUNNING,
            runpod_job_id="rp-running-001",
        )
        cancelled = workload.model_copy(update={"state": WorkloadState.CANCELLED})
        service = MagicMock()
        service.cancel_job = AsyncMock(return_value=cancelled)
        app_mod = _import_app()
        app_mod.app.state.production_routing_service = service
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/wkl_running/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "cancelled"
        service.cancel_job.assert_awaited_once_with("wkl_running")

    async def test_cancel_idempotent_on_completed(self):
        workload = _make_workload(state=WorkloadState.COMPLETED)
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/wkl_test/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "completed"

    async def test_cancel_idempotent_on_failed(self):
        workload = _make_workload(state=WorkloadState.FAILED)
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/wkl_test/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "failed"

    async def test_cancel_idempotent_on_cancelled(self):
        workload = _make_workload(state=WorkloadState.CANCELLED)
        app_mod = _import_app()
        _attach_service(app_mod, workload)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/wkl_test/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "cancelled"

    async def test_cancel_missing_job_returns_404(self):
        app_mod = _import_app()
        _attach_service(app_mod, None)
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/nonexistent/cancel")

        assert resp.status_code == 404

    async def test_cancel_running_without_runpod_job_id(self):
        workload = _make_workload(
            state=WorkloadState.RUNNING,
            runpod_job_id=None,
        )
        cancelled = workload.model_copy(update={"state": WorkloadState.CANCELLED})
        app_mod = _import_app()
        service = _attach_service(app_mod, workload)
        service.cancel_job.return_value = cancelled
        app_mod.app.state.runpod_api_key = "test-key"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app),
            base_url="http://test",
        ) as client:
            resp = await _post(client, "/v1/jobs/wkl_test/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "cancelled"
