"""Opt-in live check of the personal serve path. Spends money; run deliberately."""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal

import httpx
import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]

TTL_MINUTES = int(os.environ.get("PITWALL_LIVE_TTL_MINUTES", "35"))


@pytest.mark.skipif(
    not os.environ.get("PITWALL_RUN_LIVE"), reason="set PITWALL_RUN_LIVE=1 and RUNPOD_API_KEY"
)
async def test_personal_serve_round_trip_and_self_termination(tmp_path) -> None:
    from pitwall.config import get_settings
    from pitwall.models import load_catalogue
    from pitwall.personal.keys import ensure_endpoint_key, read_endpoint_key
    from pitwall.personal.routes import RouteRunner
    from pitwall.personal.service import LiveRunPod, PersonalServeService, ServeSpec
    from pitwall.personal.state import StateStore

    store = StateStore(tmp_path)
    ensure_endpoint_key(tmp_path)
    key = read_endpoint_key(tmp_path) or ""
    settings = get_settings()
    service = PersonalServeService(
        store=store,
        settings=settings,
        catalogue=load_catalogue(),
        runpod=LiveRunPod(settings, os.environ["RUNPOD_API_KEY"]),
        routes=RouteRunner(
            settings.pitwall_routing_cli, env={**os.environ, "PITWALL_ENDPOINT_KEY": key}
        ),
        endpoint_key=key,
        monthly_budget_usd=settings.pitwall_monthly_budget_usd,
        per_request_max_usd=settings.pitwall_per_request_max_usd,
    )
    spec = ServeSpec(
        model="ornith-ai/Ornith-1.5-35B-A3B-GGUF",
        # The market's CUDA offer per class changes; pick a class that offers the floor.
        gpu_class=os.environ.get("PITWALL_LIVE_GPU_CLASS", "NVIDIA GeForce RTX 3090"),
        cloud="community",
        # Above Ornith's 30-minute startup budget, or serve refuses it (ttl_below_startup).
        ttl_minutes=TTL_MINUTES,
        max_usd_per_hour=Decimal("0.60"),
        route="live-ornith",
    )
    lease = await service.serve(spec, progress=print)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            models = await client.get(
                f"{lease.endpoint_url}/models", headers={"Authorization": f"Bearer {key}"}
            )
            assert models.status_code == 200 and lease.served_model_id in models.text
            denied = await client.get(f"{lease.endpoint_url}/models")
            assert denied.status_code in {401, 403}
        assert service._routes.probe("live-ornith").ok
        # wait for the in-pod timer: TTL plus a grace period
        deadline = time.monotonic() + TTL_MINUTES * 60 + 240
        while time.monotonic() < deadline:
            if await service._runpod.get_pod(lease.pod_id) is None:
                break
            await asyncio.sleep(30)
        assert await service._runpod.get_pod(lease.pod_id) is None, (
            "in-pod timer did not terminate the pod"
        )
    finally:
        await service.stop("live-ornith")
