"""The hourly cap stays a Decimal until the one call that hands it to the RunPod SDK."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pitwall.config import PitwallSettings
from pitwall.personal.service import LiveRunPod, ServeSpec
from tests.fakes.personal import GPU, MODEL, FakeRoutes, FakeRunPod, build_service, fake_plan

pytestmark = pytest.mark.anyio


async def test_hourly_cap_converted_only_at_sdk_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", fake_plan)
    runpod = FakeRunPod()
    service = build_service(tmp_path, runpod, FakeRoutes())
    spec = ServeSpec(
        model=MODEL,
        gpu_class=GPU,
        ttl_minutes=60,
        max_usd_per_hour=Decimal("0.335"),
        route="ornith",
    )

    preview = await service.plan(spec)
    await service.serve(spec)

    # Through planning, the preview, and the port handed to the runner: still a Decimal.
    assert preview.request_preview["max_cost_per_hr"] == Decimal("0.335")
    assert isinstance(runpod.created[0]["max_cost_per_hr"], Decimal)
    assert runpod.created[0]["max_cost_per_hr"] == Decimal("0.335")

    # The single conversion: the SDK call, rounded half-up to cents first.
    sdk_calls: list[dict[str, Any]] = []

    async def sdk_create(**kwargs: Any) -> dict[str, Any]:
        sdk_calls.append(kwargs)
        return {"id": "pod-sdk"}

    monkeypatch.setattr("pitwall.runpod_client.pods.create_pod_with_fallback", sdk_create)
    live = LiveRunPod(PitwallSettings(), "key")
    await live.create_pod(**{**runpod.created[0], "max_cost_per_hr": Decimal("0.335")})
    await live.create_pod(**{**runpod.created[0], "max_cost_per_hr": Decimal("1.004")})

    assert [call["max_cost_per_hr"] for call in sdk_calls] == [0.34, 1.0]
    assert all(isinstance(call["max_cost_per_hr"], float) for call in sdk_calls)
