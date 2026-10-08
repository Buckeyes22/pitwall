"""The webhook retry cron redelivers due failures, clears successes, exhausts the rest."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.models import WebhookDeliveryFailure
from pitwall.reconciler import WorkerSettings, _webhook_retry_sweep
from pitwall.webhook_dispatcher import DeliveryOutcome

pytestmark = pytest.mark.anyio

_NOW = dt.datetime.now(dt.UTC)


def _failure(attempt: int, *, subscription_id: int = 7) -> WebhookDeliveryFailure:
    return WebhookDeliveryFailure(
        id=attempt,
        workload_id="wkl_1",
        subscription_id=subscription_id,
        attempt=attempt,
        attempted_at=_NOW - dt.timedelta(minutes=1),
        next_retry_at=_NOW - dt.timedelta(seconds=5),
        payload={
            "event": "workload.completed",
            "workload_id": "wkl_1",
            "delivery_id": "delivery-1",
            "state": "completed",
            "consumer": "cap_1",
            "data": {"event": "workload.completed", "workload_id": "wkl_1"},
        },
        status_code=503,
        error_message="Retryable HTTP status: 503",
    )


class _FailureRepo:
    due: list[WebhookDeliveryFailure] = []
    inserted: list[dict[str, Any]] = []
    deleted: list[tuple[str, int, int]] = []
    exhausted: list[tuple[str, int, int]] = []

    def __init__(self, _pool: object) -> None: ...

    async def list_pending_retries(self, before: dt.datetime, limit: int = 100) -> list[Any]:
        return list(type(self).due)

    async def insert(
        self, workload_id: str, subscription_id: int, attempt: int, payload: Any, **kw: Any
    ) -> None:
        type(self).inserted.append(
            {"subscription_id": subscription_id, "attempt": attempt, "payload": payload, **kw}
        )

    async def delete(self, workload_id: str, subscription_id: int, attempt: int) -> None:
        type(self).deleted.append((workload_id, subscription_id, attempt))

    async def update_next_retry(
        self, workload_id: str, subscription_id: int, attempt: int, next_retry_at: Any
    ) -> None:
        assert next_retry_at is None
        type(self).exhausted.append((workload_id, subscription_id, attempt))


class _SubscriptionRepo:
    def __init__(self, _pool: object, _cipher: object = None) -> None: ...

    async def get_for_dispatch(self, subscription_id: int) -> Any:
        return SimpleNamespace(
            id=str(subscription_id),
            webhook_url="https://hooks.example.test/events",
            hmac_secret="signing-secret",  # pragma: allowlist secret
            active=True,
        )


@pytest.fixture
def sweep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    import pitwall.db.repository as repository
    from pitwall.webhook_dispatcher import dispatcher
    from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher

    _FailureRepo.due, _FailureRepo.inserted = [], []
    _FailureRepo.deleted, _FailureRepo.exhausted = [], []
    monkeypatch.setattr(repository, "WebhookDeliveryFailureRepository", _FailureRepo)
    monkeypatch.setattr(repository, "WebhookSubscriptionRepository", _SubscriptionRepo)
    monkeypatch.setattr(WebhookSecretCipher, "from_env", classmethod(lambda cls: object()))
    delivery = AsyncMock()
    monkeypatch.setattr(dispatcher, "attempt_delivery", delivery)
    monkeypatch.setattr("pitwall.webhook_dispatcher.attempt_delivery", delivery)
    return delivery


async def test_due_retries_are_redelivered_and_cleared(sweep: AsyncMock) -> None:
    _FailureRepo.due = [_failure(1), _failure(2, subscription_id=8)]
    sweep.return_value = DeliveryOutcome(True, 2, status_code=204, delivery_id="delivery-1")

    await _webhook_retry_sweep({"db_pool": MagicMock()})

    assert sweep.await_count == 2
    first = sweep.await_args_list[0]
    assert first.kwargs["attempt"] == 2
    assert first.kwargs["delivery_id"] == "delivery-1"
    assert first.args[0] == "https://hooks.example.test/events"
    assert first.args[1]["workload_id"] == "wkl_1"
    assert first.args[1]["consumer"] == "cap_1"
    assert first.args[1]["delivery_id"] == "delivery-1"
    assert _FailureRepo.deleted == [("wkl_1", 7, 1), ("wkl_1", 8, 2)]
    assert _FailureRepo.inserted == []


async def test_failed_redelivery_reschedules_with_next_attempt(sweep: AsyncMock) -> None:
    _FailureRepo.due = [_failure(1)]
    later = _NOW + dt.timedelta(seconds=3)
    sweep.return_value = DeliveryOutcome(
        False, 2, status_code=503, error_message="Retryable HTTP status: 503", next_retry_at=later
    )

    await _webhook_retry_sweep({"db_pool": MagicMock()})

    assert len(_FailureRepo.inserted) == 1
    assert _FailureRepo.inserted[0]["attempt"] == 2
    assert _FailureRepo.inserted[0]["next_retry_at"] == later
    assert _FailureRepo.deleted == [("wkl_1", 7, 1)]


async def test_final_failure_marked_exhausted(sweep: AsyncMock) -> None:
    _FailureRepo.due = [_failure(3)]
    sweep.return_value = DeliveryOutcome(
        False, 4, status_code=503, error_message="Retryable HTTP status: 503"
    )

    await _webhook_retry_sweep({"db_pool": MagicMock()})

    assert len(_FailureRepo.inserted) == 1
    exhausted = _FailureRepo.inserted[0]
    assert exhausted["attempt"] == 4
    assert exhausted["next_retry_at"] is None
    assert exhausted["status_code"] == 503
    assert _FailureRepo.deleted == [("wkl_1", 7, 3)]


async def test_one_failing_redelivery_does_not_stop_the_sweep(sweep: AsyncMock) -> None:
    _FailureRepo.due = [_failure(1), _failure(1, subscription_id=8)]
    sweep.side_effect = [
        RuntimeError("boom"),
        DeliveryOutcome(True, 2, status_code=204, delivery_id="delivery-1"),
    ]

    await _webhook_retry_sweep({"db_pool": MagicMock()})

    assert _FailureRepo.deleted == [("wkl_1", 8, 1)]


async def test_retry_sweep_is_scheduled_every_minute() -> None:
    jobs = {job.name: job for job in WorkerSettings.cron_jobs}
    assert jobs["cron:_webhook_retry_sweep"].minute == set(range(60))


async def test_dispatch_records_next_retry_at_from_the_dispatcher(
    sweep: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.reconciler import dispatch_workload_completion_webhooks

    later = _NOW + dt.timedelta(seconds=1)
    subscription = SimpleNamespace(
        id="7",
        webhook_url="https://hooks.example.test/e",
        hmac_secret="s",  # pragma: allowlist secret
    )
    monkeypatch.setattr(
        _SubscriptionRepo,
        "list_for_dispatch",
        AsyncMock(return_value=[subscription]),
        raising=False,
    )
    monkeypatch.setattr(
        "pitwall.webhook_dispatcher.dispatch_completion",
        AsyncMock(
            return_value={
                "7": {
                    "success": False,
                    "attempt": 1,
                    "status_code": 503,
                    "error_message": "Retryable HTTP status: 503",
                    "next_retry_at": later.isoformat(),
                    "delivery_id": "delivery-1",
                    "state": "retry_scheduled",
                }
            }
        ),
    )

    await dispatch_workload_completion_webhooks(
        MagicMock(),
        {"id": "wkl_1", "capability_id": "cap_1", "state": "completed"},
        {"event": "workload.completed", "workload_id": "wkl_1"},
    )

    assert _FailureRepo.inserted[0]["attempt"] == 1
    assert _FailureRepo.inserted[0]["next_retry_at"] == later
