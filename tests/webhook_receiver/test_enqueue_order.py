"""The receiver enqueues before recording a delivery, and reuses one Arq pool."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pitwall.webhook_dispatcher.signer import sign
from tests.webhook_receiver.conftest import RECEIVER_SECRET, FakeDeliveryRepo

pytestmark = pytest.mark.anyio


class _FakeArqPool:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, name: str, *args: Any, **_kwargs: Any) -> object:
        if self.fail:
            raise ConnectionError("redis is down")
        self.enqueued.append((name, args))
        return object()

    async def aclose(self) -> None:
        return None


async def _post_terminal(receiver: Any, job_id: str = "rp-1") -> httpx.Response:
    body = json.dumps({"id": job_id, "status": "COMPLETED"}).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=receiver.app), base_url="http://test"
    ) as client:
        return await client.post(
            "/webhooks/runpod",
            content=body,
            headers={
                "content-type": "application/json",
                "X-Pitwall-Webhook-Signature": sign(body, RECEIVER_SECRET),
            },
        )


async def test_enqueue_failure_does_not_mark_delivery_seen(
    receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_create_pool(_settings: object) -> _FakeArqPool:
        return _FakeArqPool(fail=True)

    monkeypatch.setattr("arq.create_pool", failing_create_pool)

    response = await _post_terminal(receiver)

    assert response.status_code == 503
    assert FakeDeliveryRepo.calls == []


async def test_successful_enqueue_uses_registered_name_then_marks_seen(
    receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = _FakeArqPool()

    async def create_pool(_settings: object) -> _FakeArqPool:
        return pool

    monkeypatch.setattr("arq.create_pool", create_pool)

    response = await _post_terminal(receiver)

    assert response.status_code == 200
    assert pool.enqueued == [("_process_webhook_terminal_status", ("rp-1", "COMPLETED"))]
    assert len(FakeDeliveryRepo.calls) == 1


async def test_pool_reused_across_requests(receiver: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[_FakeArqPool] = []

    async def create_pool(_settings: object) -> _FakeArqPool:
        created.append(_FakeArqPool())
        return created[-1]

    monkeypatch.setattr("arq.create_pool", create_pool)

    for job_id in ("rp-1", "rp-2", "rp-3"):
        assert (await _post_terminal(receiver, job_id)).status_code == 200

    assert len(created) == 1
    assert len(created[0].enqueued) == 3
