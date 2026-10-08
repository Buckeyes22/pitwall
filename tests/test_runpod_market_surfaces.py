"""Cross-surface contract tests for isolated RunPod market adapters."""

from __future__ import annotations

import json
from typing import cast

import httpx
import pytest
from fastapi import FastAPI

from pitwall.api.routes.runpod_market import router
from pitwall.cli.output import Output
from pitwall.cli.runpod_market import cmd_runpod_catalogue, run_runpod_catalogue_command
from pitwall.mcp.tools.runpod_market import make_pitwall_runpod_catalogue
from pitwall.runpod_market import RunpodMarketService

pytestmark = pytest.mark.anyio


async def test_rest_mcp_and_cli_json_consume_the_same_service_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = RunpodMarketService(None, None, None)
    app = FastAPI()
    app.state.runpod_market_service = service
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        rest = (await client.get("/v1/runpod/catalogue")).json()

    mcp = await make_pitwall_runpod_catalogue(service)()
    code = await run_runpod_catalogue_command(
        service,
        force_refresh=False,
        output=Output(json_mode=True),
    )
    cli = json.loads(capsys.readouterr().out)

    assert code == 0
    for payload in (rest, mcp, cli):
        payload.pop("cache_hit")
    assert rest == mcp == cli
    assert set(rest) == {
        "provider",
        "state",
        "stale",
        "unavailable",
        "refresh_attempted_at",
        "cache_expires_at",
        "age_seconds",
        "forced_refresh",
        "components",
        "gpus",
        "datacenters",
        "availability",
        "balance",
        "billing_categories",
    }


class _RecordingService:
    def __init__(self, delegate: RunpodMarketService) -> None:
        self._delegate = delegate
        self.calls: list[bool] = []

    async def read(self, *, force_refresh: bool = False):  # type: ignore[no-untyped-def]  # reason: protocol spy preserves delegate type
        self.calls.append(force_refresh)
        return await self._delegate.read(force_refresh=force_refresh)


async def test_rest_refresh_and_mcp_force_are_one_shot_service_inputs() -> None:
    delegate = RunpodMarketService(None, None, None)
    recording = _RecordingService(delegate)
    service = cast(RunpodMarketService, recording)
    app = FastAPI()
    app.state.runpod_market_service = service
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/runpod/catalogue", params={"refresh": "true"})
    mcp = make_pitwall_runpod_catalogue(service)
    await mcp(True)

    assert response.status_code == 200
    assert recording.calls == [True, True]


async def test_cli_human_output_labels_unavailable_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = RunpodMarketService(None, None, None)

    code = await run_runpod_catalogue_command(
        service,
        force_refresh=False,
        output=Output(json_mode=False),
    )
    captured = capsys.readouterr()

    assert code == 0
    assert "RunPod catalogue: unavailable" in captured.out
    assert "RunPod credit balance unavailable" in captured.err
    assert "network_volumes" in captured.out


def test_sync_cli_wrapper_emits_stable_json(capsys: pytest.CaptureFixture[str]) -> None:
    service = RunpodMarketService(None, None, None)

    assert cmd_runpod_catalogue(["--json"], service_factory=lambda: service) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "runpod"
    assert payload["state"] == "unavailable"
