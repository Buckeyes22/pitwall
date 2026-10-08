"""Lease-event webhook delivery: one attempt, retry scheduled, redelivered by the sweep."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.models import WebhookDeliveryFailure
from pitwall.leases import events
from pitwall.reconciler import _webhook_retry_sweep
from pitwall.webhook_dispatcher import dispatcher
from pitwall.webhook_dispatcher.security import ResolvedWebhookTarget

pytestmark = pytest.mark.anyio

_EVENT = {
    "version": "1",
    "event": "lease.expiring",
    "capability": "llm.events",
    "delivery_id": "original",
    "occurred_at": "2026-09-28T12:00:00Z",
    "data": {"lease_id": "lease-1", "capability": "llm.events", "minutes_left": 15},
}


class _FailureRepo:
    inserted: list[dict[str, Any]] = []
    deleted: list[tuple[str, int, int]] = []
    due: list[WebhookDeliveryFailure] = []

    def __init__(self, _pool: object) -> None: ...

    async def insert(
        self, workload_id: str, subscription_id: int, attempt: int, payload: Any, **kw: Any
    ) -> None:
        type(self).inserted.append(
            {
                "id": workload_id,
                "sub": subscription_id,
                "attempt": attempt,
                "payload": payload,
                **kw,
            }
        )

    async def list_pending_retries(self, before: dt.datetime, limit: int = 100) -> list[Any]:
        return list(type(self).due)

    async def delete(self, workload_id: str, subscription_id: int, attempt: int) -> None:
        type(self).deleted.append((workload_id, subscription_id, attempt))


@pytest.fixture
def failing_transport(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    _FailureRepo.inserted, _FailureRepo.deleted, _FailureRepo.due = [], [], []
    target = ResolvedWebhookTarget(
        url="https://receiver.example/pitwall",
        hostname="receiver.example",
        port=443,
        request_target="/pitwall",
        addresses=("8.8.8.8",),
    )
    monkeypatch.setattr(dispatcher, "resolve_webhook_target", AsyncMock(return_value=target))
    post = AsyncMock(return_value=503)
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)
    sleep = AsyncMock(side_effect=AssertionError("inline sleep"))
    monkeypatch.setattr(asyncio, "sleep", sleep)
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = [
        SimpleNamespace(
            id="7",
            webhook_url=target.url,
            hmac_secret="secret",  # pragma: allowlist secret
        )
    ]
    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    monkeypatch.setattr(events, "_failure_repository", lambda pool: _FailureRepo(pool))
    monkeypatch.setattr(
        dispatcher,
        "get_settings",
        lambda: SimpleNamespace(pitwall_webhook_loopback_allowlist=()),
    )
    return post


async def test_lease_event_failure_scheduled_for_retry(failing_transport: AsyncMock) -> None:
    await events.publish_lease_event(MagicMock(), None, dict(_EVENT))

    failing_transport.assert_awaited_once()
    assert len(_FailureRepo.inserted) == 1
    row = _FailureRepo.inserted[0]
    assert row["id"] == "lease-1"
    assert row["attempt"] == 1
    assert row["next_retry_at"] is not None
    assert row["payload"]["envelope"]["event"] == "lease.expiring"
    assert row["payload"]["envelope"]["delivery_id"] == row["payload"]["delivery_id"]


async def test_lease_event_delivery_never_sleeps(failing_transport: AsyncMock) -> None:
    # The fixture makes asyncio.sleep raise; a completed publish proves no inline backoff.
    await events.publish_lease_event(MagicMock(), None, dict(_EVENT))
    assert failing_transport.await_count == 1


async def test_failed_lease_event_is_redelivered_by_the_sweep(
    failing_transport: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pitwall.db.repository as repository
    from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher

    await events.publish_lease_event(MagicMock(), None, dict(_EVENT))
    stored = _FailureRepo.inserted[0]
    now = dt.datetime.now(dt.UTC)
    _FailureRepo.due = [
        WebhookDeliveryFailure(
            workload_id=stored["id"],
            subscription_id=stored["sub"],
            attempt=stored["attempt"],
            attempted_at=now - dt.timedelta(minutes=1),
            next_retry_at=now - dt.timedelta(seconds=1),
            payload=stored["payload"],
            status_code=503,
        )
    ]
    _FailureRepo.inserted, _FailureRepo.deleted = [], []

    class _Subs:
        def __init__(self, *_a: object) -> None: ...

        async def get_for_dispatch(self, _id: int) -> Any:
            return SimpleNamespace(
                active=True,
                webhook_url="https://receiver.example/pitwall",
                hmac_secret="secret",  # pragma: allowlist secret
            )

    monkeypatch.setattr(repository, "WebhookDeliveryFailureRepository", _FailureRepo)
    monkeypatch.setattr(repository, "WebhookSubscriptionRepository", _Subs)
    monkeypatch.setattr(WebhookSecretCipher, "from_env", classmethod(lambda cls: object()))
    failing_transport.reset_mock()
    failing_transport.return_value = 204

    await _webhook_retry_sweep({"db_pool": MagicMock()})

    failing_transport.assert_awaited_once()
    sent_body = failing_transport.await_args.args[1]
    assert b'"event":"lease.expiring"' in sent_body
    assert stored["payload"]["delivery_id"].encode() in sent_body
    assert _FailureRepo.deleted == [("lease-1", 7, 1)]
    assert _FailureRepo.inserted == []
