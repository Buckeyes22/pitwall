"""A-26: stored job input and result are visible only to tokens that may create jobs."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from pitwall.core.enums import WorkloadState
from pitwall.core.models import Workload
from pitwall.routing.production import JobResultPage
from tests.conftest import _env_for_app, _import_app

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)
_TOKENS = json.dumps(
    {
        "reader-token": ["read"],  # pragma: allowlist secret
        "spender-token": ["read", "spend"],  # pragma: allowlist secret
    }
)


def _workload() -> Workload:
    return Workload(
        id="wkl_test",
        capability_id="cap_test",
        provider_id="prov_test",
        type="async",
        state=WorkloadState.COMPLETED,
        runpod_job_id="rp-job-001",
        input={"prompt": "private prompt"},
        result={"text": "private answer"},
        submitted_at=_NOW,
    )


def _app(clear: None) -> Any:
    mod = _import_app(_env_for_app(PITWALL_API_SCOPED_TOKENS=_TOKENS))
    workload = _workload()
    service = MagicMock()
    service.get_job = AsyncMock(return_value=workload)
    service.job_result = AsyncMock(
        return_value=JobResultPage(
            workload_id=workload.id,
            plan_id=None,
            provider_id=workload.provider_id,
            state="completed",
            result=workload.result,
            available=True,
            unavailable_reason=None,
        )
    )
    mod.app.state.production_routing_service = service
    return mod


async def _get(mod: Any, path: str, token: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        return await client.get(path)


async def test_read_only_token_sees_metadata_only(clear_app_module: None) -> None:
    mod = _app(clear_app_module)

    job = (await _get(mod, "/v1/jobs/wkl_test", "reader-token")).json()
    assert job["id"] == "wkl_test"
    assert job["state"] == "completed"
    assert job["input"] is None
    assert job["result"] is None

    result = (await _get(mod, "/v1/jobs/wkl_test/result", "reader-token")).json()
    assert result["result"] is None
    assert result["available"] is False
    assert result["unavailable_reason"] == "insufficient_scope"

    body = json.dumps([job, result])
    assert "private prompt" not in body
    assert "private answer" not in body


async def test_spend_token_sees_input_and_result(clear_app_module: None) -> None:
    mod = _app(clear_app_module)

    job = (await _get(mod, "/v1/jobs/wkl_test", "spender-token")).json()
    assert job["input"] == {"prompt": "private prompt"}
    assert job["result"] == {"text": "private answer"}

    result = (await _get(mod, "/v1/jobs/wkl_test/result", "spender-token")).json()
    assert result["result"] == {"text": "private answer"}
    assert result["available"] is True
