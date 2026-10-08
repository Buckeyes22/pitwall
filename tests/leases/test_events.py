from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Capability, Lease
from pitwall.leases import events
from pitwall.leases.mutations import renew_lease
from pitwall.webhook_dispatcher.dispatcher import DeliveryOutcome

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _capability() -> Capability:
    return Capability(
        id="cap-events",
        name="llm.events",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="per_second",
        source=CapabilitySource.API,
        served_model_id="served-events",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _lease() -> Lease:
    return Lease(
        id="lease-events",
        provider_id="provider-events",
        runpod_pod_id="pod-events",
        state=LeaseState.CREATING,
        created_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        renewal_policy=LeaseRenewalPolicy.ACTIVITY,
        ready_at=_NOW + timedelta(seconds=30),
        idle_timeout_min=20,
    )


def test_ready_event_has_exact_routing_envelope() -> None:
    event = events.build_lease_ready_event(
        lease=_lease(),
        capability=_capability(),
        served_model_id="served-events",
        variant="fp8",
        proxy_base_url="http://pitwall/v1/openai/llm.events/v1",
        created=False,
    )

    assert set(event) == {
        "version",
        "event",
        "delivery_id",
        "occurred_at",
        "capability",
        "data",
    }
    assert event["version"] == "1"
    assert event["event"] == "lease.ready"
    assert event["capability"] == "llm.events"
    assert isinstance(uuid.UUID(event["delivery_id"]), uuid.UUID)
    assert "workload_id" not in event and "consumer" not in event
    assert event["data"]["created"] is False


def test_ready_event_without_lease_keeps_the_consumer_envelope() -> None:
    event = events.build_lease_ready_event(
        lease=None,
        capability_name="llm.selfhosted",
        served_model_id="<model-id>",
        variant=None,
        proxy_base_url="http://test/v1/openai/llm.selfhosted/v1",
        created=True,
    )
    assert event["capability"] == "llm.selfhosted"
    assert event["data"]["lease_id"] is None
    assert event["data"]["expires_at"] is None
    assert event["data"]["idle_timeout_min"] is None


def test_renewed_and_stopped_events_pin_reason_fields() -> None:
    renewed = events.build_lease_renewed_event(
        lease=_lease(),
        capability_name="llm.events",
        renewed_by="activity",
    )
    stopped = events.build_lease_stopped_event(
        lease=_lease(),
        capability_name="llm.events",
        reason="idle",
    )

    assert renewed["data"] == {
        "capability": "llm.events",
        "lease_id": "lease-events",
        "expires_at": "2026-08-28T13:00:00+00:00",
        "renewed_by": "activity",
    }
    assert stopped["data"] == {
        "capability": "llm.events",
        "lease_id": "lease-events",
        "reason": "idle",
    }


def test_expiring_event_has_exact_routing_envelope() -> None:
    event = events.build_lease_expiring_event(
        lease=_lease(), capability_name="llm.events", minutes_left=15
    )

    assert event["event"] == "lease.expiring"
    assert event["capability"] == "llm.events"
    assert event["data"] == {
        "capability": "llm.events",
        "lease_id": "lease-events",
        "expires_at": "2026-08-28T13:00:00+00:00",
        "minutes_left": 15,
    }


@pytest.mark.anyio
async def test_publish_lease_event_uses_shared_channel_and_retry_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    subscriptions = [
        MagicMock(
            id="7",
            webhook_url="https://receiver.example/pitwall",
            hmac_secret="secret",
        )
    ]
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = subscriptions
    send = AsyncMock(return_value=MagicMock(success=True))
    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    monkeypatch.setattr(events, "attempt_delivery", send)
    event = events.build_lease_expiring_event(
        lease=_lease(),
        capability_name="llm.events",
        minutes_left=15,
    )

    await events.publish_lease_event(MagicMock(), redis, event)

    channel, payload = redis.publish.await_args.args
    assert channel == "pitwall.leases.events"
    assert json.loads(payload) == event
    repo.list_for_dispatch.assert_awaited_once_with(
        consumer="llm.events",
        event_type="lease.expiring",
    )
    delivery = send.await_args.kwargs
    assert delivery["webhook_url"] == "https://receiver.example/pitwall"
    assert delivery["hmac_secret"] == "secret"
    assert delivery["delivery_id"] == delivery["payload"]["delivery_id"]
    assert delivery["payload"] == {**event, "delivery_id": delivery["delivery_id"]}


@pytest.mark.anyio
async def test_publish_without_webhook_keys_keeps_redis_and_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    repository = MagicMock()
    monkeypatch.delenv("PITWALL_WEBHOOK_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setattr(events, "_webhook_config_warning_logged", False)
    monkeypatch.setattr(events, "WebhookSubscriptionRepository", repository)
    event = events.build_lease_expiring_event(
        lease=_lease(), capability_name="llm.events", minutes_left=15
    )

    with caplog.at_level("WARNING", logger="pitwall.leases.events"):
        await events.publish_lease_event(MagicMock(), redis, event)
        await events.publish_lease_event(MagicMock(), redis, event)

    assert redis.publish.await_count == 2
    repository.assert_not_called()
    assert [record.message for record in caplog.records].count(
        "lifecycle webhook delivery skipped: PITWALL_WEBHOOK_ENCRYPTION_KEYS not configured"
    ) == 1


@pytest.mark.anyio
async def test_publish_with_webhook_keys_delivers_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = [
        MagicMock(id="7", webhook_url="https://receiver.example/pitwall", hmac_secret="secret")
    ]
    repository_factory = MagicMock(return_value=repo)
    send = AsyncMock(return_value=DeliveryOutcome(True, 1))
    key = base64.urlsafe_b64encode(bytes(range(32))).decode()
    monkeypatch.setenv("PITWALL_WEBHOOK_ENCRYPTION_KEYS", json.dumps({"v1": key}))
    monkeypatch.setenv("PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY", "v1")
    monkeypatch.setattr(events, "_webhook_config_warning_logged", False)
    monkeypatch.setattr(events, "WebhookSubscriptionRepository", repository_factory)
    monkeypatch.setattr(events, "attempt_delivery", send)

    await events.publish_lease_event(
        MagicMock(),
        None,
        events.build_lease_expiring_event(
            lease=_lease(), capability_name="llm.events", minutes_left=15
        ),
    )

    cipher = repository_factory.call_args.args[1]
    assert cipher.current_version == "v1"
    repo.list_for_dispatch.assert_awaited_once_with(
        consumer="llm.events", event_type="lease.expiring"
    )
    send.assert_awaited_once()


@pytest.mark.anyio
async def test_publish_allocates_distinct_delivery_ids_per_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = [
        MagicMock(id="7", webhook_url="https://one.example/pitwall", hmac_secret="one"),
        MagicMock(id="8", webhook_url="https://two.example/pitwall", hmac_secret="two"),
    ]
    sent: list[str] = []

    async def send(**kwargs: object) -> DeliveryOutcome:
        payload = kwargs["payload"]
        delivery_id = kwargs["delivery_id"]
        assert isinstance(payload, dict)
        assert isinstance(delivery_id, str)
        assert payload["delivery_id"] == delivery_id
        sent.append(delivery_id)
        return DeliveryOutcome(True, 2, delivery_id=delivery_id)

    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    monkeypatch.setattr(events, "attempt_delivery", send)

    await events.publish_lease_event(
        MagicMock(),
        None,
        events.build_lease_stopped_event(
            lease=_lease(), capability_name="llm.events", reason="operator"
        ),
    )

    assert len(sent) == 2
    assert sent[0] != sent[1]


@pytest.mark.anyio
async def test_publish_records_terminal_delivery_failure_with_delivery_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    subscription = MagicMock(
        id="7", webhook_url="https://receiver.example/pitwall", hmac_secret="s"
    )
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = [subscription]
    failure_repo = AsyncMock()
    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    monkeypatch.setattr(events, "_failure_repository", lambda _pool: failure_repo)

    async def fail_delivery(**kwargs: object) -> DeliveryOutcome:
        delivery_id = kwargs["delivery_id"]
        assert isinstance(delivery_id, str)
        return DeliveryOutcome(
            success=False,
            attempt=4,
            status_code=503,
            error_message="down",
            delivery_id=delivery_id,
        )

    monkeypatch.setattr(events, "attempt_delivery", fail_delivery)
    event = events.build_lease_stopped_event(
        lease=_lease(), capability_name="llm.events", reason="idle"
    )

    with caplog.at_level("WARNING", logger="pitwall.leases.events"):
        await events.publish_lease_event(MagicMock(), None, event)

    recorded_delivery_id = failure_repo.insert.await_args.args[3]["delivery_id"]
    assert isinstance(recorded_delivery_id, str)
    failure_repo.insert.assert_awaited_once_with(
        "lease-events",
        7,
        4,
        {
            "event": "lease.stopped",
            "lease_id": "lease-events",
            "delivery_id": recorded_delivery_id,
            "envelope": {**event, "delivery_id": recorded_delivery_id},
        },
        next_retry_at=None,
        status_code=503,
        error_message="down",
    )
    assert any(record.message == "lease event webhook delivery failed" for record in caplog.records)


@pytest.mark.anyio
async def test_publish_success_records_no_delivery_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = [
        MagicMock(id="7", webhook_url="https://receiver.example/pitwall", hmac_secret="s")
    ]
    failure_repo = AsyncMock()
    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    monkeypatch.setattr(events, "_failure_repository", lambda _pool: failure_repo)
    monkeypatch.setattr(
        events, "attempt_delivery", AsyncMock(return_value=DeliveryOutcome(True, 1))
    )

    await events.publish_lease_event(
        MagicMock(),
        None,
        events.build_lease_expiring_event(
            lease=_lease(), capability_name="llm.events", minutes_left=5
        ),
    )

    failure_repo.insert.assert_not_awaited()


@pytest.mark.anyio
async def test_publish_mixed_outcomes_delivers_healthy_and_records_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = [
        MagicMock(id="7", webhook_url="https://bad.example/pitwall", hmac_secret="s"),
        MagicMock(id="8", webhook_url="https://good.example/pitwall", hmac_secret="s"),
    ]
    failure_repo = AsyncMock()
    send = AsyncMock(
        side_effect=[
            DeliveryOutcome(False, 1, status_code=400, error_message="bad"),
            DeliveryOutcome(True, 1),
        ]
    )
    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    monkeypatch.setattr(events, "_failure_repository", lambda _pool: failure_repo)
    monkeypatch.setattr(events, "attempt_delivery", send)
    event = events.build_lease_expiring_event(
        lease=_lease(), capability_name="llm.events", minutes_left=5
    )

    await events.publish_lease_event(MagicMock(), None, event)

    assert send.await_count == 2
    failure_repo.insert.assert_awaited_once()
    assert failure_repo.insert.await_args.args[:3] == ("lease-events", 7, 1)


@pytest.mark.anyio
async def test_publish_continues_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(side_effect=ConnectionError("redis down"))
    repo = AsyncMock()
    repo.list_for_dispatch.return_value = []
    monkeypatch.setattr(events, "_subscription_repository", lambda _pool: repo)
    event = events.build_lease_stopped_event(
        lease=_lease(),
        capability_name="llm.events",
        reason="idle",
    )

    await events.publish_lease_event(MagicMock(), redis, event)

    repo.list_for_dispatch.assert_awaited_once()


@pytest.mark.anyio
async def test_shared_renew_mutation_emits_operator_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewed = _lease().model_copy(update={"expires_at": _NOW + timedelta(hours=2)})
    repo = AsyncMock()
    repo.renew.return_value = SimpleNamespace(lease=renewed)
    publish = AsyncMock()
    monkeypatch.setattr(events, "publish_lease_event", publish)
    # The budget reservation is pinned in tests/leases/test_renewal_reservation.py.
    monkeypatch.setattr(
        "pitwall.leases.mutations.renewal_extension_usd", AsyncMock(return_value=None)
    )

    result = await renew_lease(
        repo,
        renewed.id,
        extends_minutes=60,
        actor="rest:lease",
        pool=MagicMock(),
        redis=MagicMock(),
        capability_name="llm.events",
    )

    assert result is renewed
    event = publish.await_args.args[2]
    assert event["event"] == "lease.renewed"
    assert event["data"]["renewed_by"] == "operator"
