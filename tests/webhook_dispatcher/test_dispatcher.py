"""Delivery outcome classification and per-subscription completion dispatch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from pitwall.webhook_dispatcher import dispatcher
from pitwall.webhook_dispatcher.dispatcher import MAX_ATTEMPTS, DeliveryOutcome
from pitwall.webhook_dispatcher.security import ResolvedWebhookTarget
from pitwall.webhook_dispatcher.signer import verify

pytestmark = pytest.mark.anyio


def _target(url: str) -> ResolvedWebhookTarget:
    return ResolvedWebhookTarget(
        url=url,
        hostname="hooks.example.test",
        port=443,
        request_target="/events",
        addresses=("8.8.8.8",),
    )


@pytest.mark.parametrize(
    ("success", "attempt", "status_code", "state"),
    [
        (True, 1, 204, "delivered"),
        (False, 1, None, "retry_scheduled"),
        (False, 1, 408, "retry_scheduled"),
        (False, 1, 425, "retry_scheduled"),
        (False, 1, 429, "retry_scheduled"),
        (False, 1, 503, "retry_scheduled"),
        (False, 1, 404, "terminal_failure"),
        (False, MAX_ATTEMPTS, 503, "terminal_failure"),
        (False, MAX_ATTEMPTS, None, "terminal_failure"),
    ],
)
def test_outcome_state_follows_the_retry_policy(
    success: bool, attempt: int, status_code: int | None, state: str
) -> None:
    outcome = DeliveryOutcome(success=success, attempt=attempt, status_code=status_code)

    assert outcome.state == state
    assert outcome.should_retry is (state == "retry_scheduled")


async def test_dispatch_records_each_subscription_and_signs_only_with_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, dict[str, str]] = {}

    async def resolve(url: str, *, loopback_allowlist: tuple[str, ...]) -> ResolvedWebhookTarget:
        del loopback_allowlist
        return _target(url)

    async def post(
        target: ResolvedWebhookTarget, _body: bytes, headers: dict[str, str], _timeout: float
    ) -> int:
        sent[target.url] = headers
        return 204 if target.url.endswith("/signed") else 404

    monkeypatch.setattr(dispatcher, "resolve_webhook_target", resolve)
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    results = await dispatcher.dispatch_completion(
        workload_id="wkl_1",
        consumer="cap_1",
        payload={"ok": True},
        subscriptions=[
            (7, "https://hooks.example.test/signed", "secret"),
            (8, "https://hooks.example.test/unsigned", None),
        ],
        retry_delays=(0,),
    )

    signed, unsigned = results["7"], results["8"]
    assert (signed["success"], signed["state"], signed["status_code"]) == (True, "delivered", 204)
    assert (unsigned["success"], unsigned["state"]) == (False, "terminal_failure")
    assert unsigned["error_message"] == "Non-retryable HTTP status: 404"
    assert signed["delivery_id"] != unsigned["delivery_id"]
    assert (
        sent["https://hooks.example.test/signed"]["X-Pitwall-Delivery-ID"] == signed["delivery_id"]
    )
    assert "X-Pitwall-Signature" in sent["https://hooks.example.test/signed"]
    assert "X-Pitwall-Signature" not in sent["https://hooks.example.test/unsigned"]


async def test_payload_delivery_id_is_used_when_none_is_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def post(
        _target: ResolvedWebhookTarget, body: bytes, headers: dict[str, str], _timeout: float
    ) -> int:
        captured.update(headers)
        assert verify(body, headers["X-Pitwall-Signature"], "secret")
        return 200

    monkeypatch.setattr(
        dispatcher,
        "resolve_webhook_target",
        AsyncMock(return_value=_target("https://hooks.example.test/events")),
    )
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)

    outcome = await dispatcher.attempt_delivery(
        "https://hooks.example.test/events",
        {"delivery_id": "dlv_from_payload"},
        "secret",
        retry_delays=(0,),
    )

    assert outcome.delivery_id == "dlv_from_payload"
    assert captured["X-Pitwall-Delivery-ID"] == "dlv_from_payload"
