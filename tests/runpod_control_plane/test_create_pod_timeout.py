"""A slow pod create is never cancelled by the service and is always audited."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pitwall.runpod_client.pods import NoCapacityError
from pitwall.runpod_control_plane import (
    PodCreateRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
)
from tests.runpod_control_plane.backend_test_support import RecordingBackend
from tests.runpod_control_plane.backend_test_support import idempotency_key_for as _key
from tests.runpod_control_plane.journal_fakes import FakeJournalPool, recording_insert_audit

pytestmark = pytest.mark.anyio


@pytest.fixture
def audit_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("pitwall.runpod_control_plane.insert_audit", recording_insert_audit(calls))
    return calls


def _request(name: str = "slow-pod") -> PodCreateRequest:
    return PodCreateRequest(
        intent="apply",
        idempotency_key=_key(name),
        name=name,
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
    )


def _service(backend: RecordingBackend, *, timeout_s: float) -> RunPodControlPlaneService:
    return RunPodControlPlaneService(
        backend=backend, audit_pool=FakeJournalPool(), timeout_s=timeout_s
    )


class SlowCreateBackend(RecordingBackend):
    def __init__(self, *, delay_s: float) -> None:
        super().__init__()
        self.delay_s = delay_s
        self.cancelled = False
        self.completed = False

    async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
        self.calls.append("pods.create")
        try:
            await asyncio.sleep(self.delay_s)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return self._pod("pod_slow", request.name)


async def test_slow_create_is_audited_with_pod_id(audit_calls: list[dict[str, Any]]) -> None:
    backend = SlowCreateBackend(delay_s=0.3)

    result = await _service(backend, timeout_s=0.05).create_pod(_request())

    assert result.resource_id == "pod_slow"
    assert backend.completed
    assert [call["new_value"]["resource_id"] for call in audit_calls] == ["pod_slow"]
    assert audit_calls[0]["entity_id"] == "pod_slow"


async def test_no_outer_cancellation_for_create_pod(audit_calls: list[dict[str, Any]]) -> None:
    backend = SlowCreateBackend(delay_s=0.2)

    await _service(backend, timeout_s=0.05).create_pod(_request("no-cancel"))

    assert backend.cancelled is False
    assert backend.completed


async def test_other_operations_keep_their_timeout() -> None:
    class SlowListBackend(RecordingBackend):
        async def list_pods(self) -> list[dict[str, Any]]:
            await asyncio.sleep(1)
            return []

    with pytest.raises(RunPodControlPlaneError) as raised:
        await _service(SlowListBackend(), timeout_s=0.05).list_pods()

    assert raised.value.code == "provider_timeout"


async def test_failed_create_is_audited_without_masking_the_error(
    audit_calls: list[dict[str, Any]],
) -> None:
    class FailingBackend(RecordingBackend):
        async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
            raise NoCapacityError("no capacity", pod_attempts=1, ambiguous_create=True)

    with pytest.raises(RunPodControlPlaneError) as raised:
        await _service(FailingBackend(), timeout_s=1).create_pod(_request("failing"))

    assert raised.value.code == "provider_error"
    assert len(audit_calls) == 1
    recorded = audit_calls[0]["new_value"]
    assert recorded["operation"] == "pod.create"
    assert recorded["outcome"] == "provider_error"
    assert recorded["ambiguous"] is True
