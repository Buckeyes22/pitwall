"""Direct behaviour tests for the shared fakes that no other test exercised on its own."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from pitwall.api.exceptions import ServeVerificationFailed
from pitwall.runpod_client.pods import RunPodError
from pitwall.runpod_files import (
    VolumeFileIdempotencyConflict,
    VolumeFileMutationAmbiguous,
    VolumeFileMutationAudit,
    VolumeFilePreconditionConflict,
    VolumeFileResult,
)
from tests.fakes.mcp import FakeServiceLayerRecorder
from tests.fakes.personal import (
    ENDPOINT_KEY,
    FakeClock,
    FakeRoutes,
    FakeRunPod,
    fake_verify,
)
from tests.fakes.runpod import RunPodBillingFake
from tests.fakes.teardown import UnlockedTeardown
from tests.fakes.volume_files import FakeVolumeFileMutationJournal

pytestmark = pytest.mark.anyio


def _audit(
    key: str = "key-1",
    *,
    request_hash: str = "hash-a",
    recovery: str = "conditional_create",
) -> VolumeFileMutationAudit:
    return VolumeFileMutationAudit(
        operation="upload",
        action="create",
        volume_id="vol-1",
        data_center_id="US-KS-2",
        object_key="a/b",
        request_hash=request_hash,
        recovery=recovery,  # type: ignore[arg-type]  # reason: test passes a plain str where Literal conditional_create/manual is expected
        idempotency_key=key,
    )


def _result() -> VolumeFileResult:
    return VolumeFileResult(operation="upload", status="completed", volume_id="vol-1")


async def _validate() -> None:
    return None


async def test_journal_replays_a_committed_key_without_reapplying() -> None:
    journal = FakeVolumeFileMutationJournal()
    applied: list[bool] = []

    async def apply(retry_started: bool) -> VolumeFileResult:
        applied.append(retry_started)
        return _result()

    first = await journal.run_idempotent(_audit(), _validate, apply)
    second = await journal.run_idempotent(_audit(), _validate, apply)

    assert first.replayed is False
    assert second.replayed is True
    assert applied == [False]
    assert len(journal.records) == 1


async def test_journal_rejects_a_reused_key_with_a_different_request() -> None:
    journal = FakeVolumeFileMutationJournal()

    async def apply(retry_started: bool) -> VolumeFileResult:
        return _result()

    await journal.run_idempotent(_audit(), _validate, apply)

    with pytest.raises(VolumeFileIdempotencyConflict):
        await journal.run_idempotent(_audit(request_hash="hash-b"), _validate, apply)


async def test_journal_refuses_to_retry_a_started_manual_recovery_mutation() -> None:
    journal = FakeVolumeFileMutationJournal()
    audit = _audit(recovery="manual")

    async def failing(retry_started: bool) -> VolumeFileResult:
        raise RuntimeError("provider outcome unknown")

    with pytest.raises(RuntimeError):
        await journal.run_idempotent(audit, _validate, failing)

    async def apply(retry_started: bool) -> VolumeFileResult:
        return _result()

    with pytest.raises(VolumeFileMutationAmbiguous):
        await journal.run_idempotent(audit, _validate, apply)


async def test_journal_remembers_a_precondition_conflict_for_the_same_request() -> None:
    journal = FakeVolumeFileMutationJournal()

    async def conflicting(retry_started: bool) -> VolumeFileResult:
        raise VolumeFilePreconditionConflict("exists")

    with pytest.raises(VolumeFilePreconditionConflict):
        await journal.run_idempotent(_audit(), _validate, conflicting)
    with pytest.raises(VolumeFilePreconditionConflict):
        await journal.run_idempotent(_audit(), _validate, conflicting)
    with pytest.raises(VolumeFileIdempotencyConflict):
        await journal.run_idempotent(_audit(request_hash="hash-b"), _validate, conflicting)


async def test_journal_serializes_concurrent_calls_for_one_key() -> None:
    journal = FakeVolumeFileMutationJournal()
    applied = 0

    async def apply(retry_started: bool) -> VolumeFileResult:
        nonlocal applied
        applied += 1
        await asyncio.sleep(0)
        return _result()

    results = await asyncio.gather(
        journal.run_idempotent(_audit(), _validate, apply),
        journal.run_idempotent(_audit(), _validate, apply),
    )

    assert applied == 1
    assert sorted(result.replayed for result in results) == [False, True]


async def test_unlocked_teardown_always_grants_the_lock() -> None:
    async with UnlockedTeardown().teardown_lock("lease-1", wait=False) as acquired:
        assert acquired is True


async def test_billing_fake_reports_status_and_cost_per_second_of_worker_time() -> None:
    billing = RunPodBillingFake()
    billing.set("job-1", status="COMPLETED", cost_per_hr=Decimal("3.6"), worker_time_ms=1000)
    billing.set("job-2", status="FAILED", worker_time_ms=0)

    assert billing.get("missing") is None
    data = billing.get("job-1")
    assert data is not None and data.status == "COMPLETED"
    assert billing.calls == ["missing", "job-1"]
    assert billing.terminal_statuses() == {"job-1": "COMPLETED", "job-2": "FAILED"}
    assert billing.actual_costs() == {"job-1": pytest.approx(0.001)}


async def test_mcp_recorder_records_calls_once_and_restores_the_registry() -> None:
    from pitwall.mcp import registry

    original_specs = list(registry.TOOL_REGISTRY)
    spec = original_specs[0]
    seen: list[dict[str, Any]] = []

    async def handler(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        if kwargs.get("boom"):
            raise RuntimeError("boom")
        return {"ok": True}

    registry.TOOL_REGISTRY[0] = replace(spec, handler=handler)
    registry._REGISTRY_BY_NAME[spec.name] = registry.TOOL_REGISTRY[0]
    recorder = FakeServiceLayerRecorder()
    try:
        recorder.install()
        recorder.install()
        assert registry.TOOL_REGISTRY[0].handler is not handler

        assert await recorder.call_tool(spec.name, value=1) == {"ok": True}
        assert await registry.TOOL_REGISTRY[0].handler(value=2) == {"ok": True}
        with pytest.raises(RuntimeError):
            await recorder.call_tool(spec.name, boom=True)
        with pytest.raises(ValueError, match="Unknown tool"):
            await recorder.call_tool("not_a_tool")
    finally:
        recorder.uninstall()
        registry.TOOL_REGISTRY[0] = spec
        registry._REGISTRY_BY_NAME[spec.name] = spec

    assert original_specs == registry.TOOL_REGISTRY
    assert seen == [{"value": 1}, {"value": 2}, {"boom": True}]
    recorder.assert_called(spec.name, times=3)
    recorder.assert_called(spec.name, times=1, value=1)
    assert recorder.get_calls(spec.name)[2].error is not None
    assert recorder.get_calls("other") == []
    recorder.reset()
    assert recorder.calls == []


async def test_personal_runpod_fake_tracks_pods_and_injected_failures() -> None:
    runpod = FakeRunPod()

    pod = await runpod.create_pod(name="pitwall-ornith")
    assert pod == {"id": "pod123"}
    assert await runpod.get_pod("pod123") == pod

    runpod.get_errors = 1
    with pytest.raises(RunPodError):
        await runpod.get_pod("pod123")
    assert await runpod.get_pod("pod123") == pod

    runpod.vanish("pod123")
    assert await runpod.get_pod("pod123") is None

    runpod.terminate_error = RunPodError("cannot terminate")
    with pytest.raises(RunPodError):
        await runpod.terminate_pod("pod-x")
    assert runpod.terminated == ["pod-x"]
    assert await runpod.read_logs("pod-x", max_lines=3) == "pod-x: last 3 lines"


async def test_personal_routes_and_clock_fakes_record_and_advance() -> None:
    routes = FakeRoutes()
    routes.fail_probe = True

    assert routes.available() is True
    assert routes.attach("r", base_url="u", model_id="m", key_env="K").ok is True
    assert routes.probe("r").ok is False
    assert routes.remove("r").ok is True
    assert routes.names("attach") == ["r"]
    assert routes.exists("r") is False

    clock = FakeClock()
    start = clock.now()
    clock.advance(minutes=90)
    assert (clock.now() - start).total_seconds() == 5400


async def test_personal_verify_fake_requires_the_endpoint_key() -> None:
    url = "https://pod.example/v1/models"

    assert await fake_verify(url, "m", headers={"Authorization": f"Bearer {ENDPOINT_KEY}"}) == ["m"]
    with pytest.raises(ServeVerificationFailed):
        await fake_verify(url, "m", headers=None)
