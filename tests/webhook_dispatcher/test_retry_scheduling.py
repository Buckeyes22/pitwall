"""Single-attempt delivery: retry scheduling, egress rejection, addresses, exceptions."""

from __future__ import annotations

import asyncio
import datetime as dt
import http.client
from unittest.mock import AsyncMock

import pytest

from pitwall.webhook_dispatcher import dispatcher
from pitwall.webhook_dispatcher.security import ResolvedWebhookTarget, WebhookTargetRejected

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
_URL = "https://hooks.example.test/events"


def _target(*addresses: str) -> ResolvedWebhookTarget:
    return ResolvedWebhookTarget(
        url=_URL,
        hostname="hooks.example.test",
        port=443,
        request_target="/events",
        addresses=addresses or ("8.8.8.8",),
    )


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: ResolvedWebhookTarget | Exception | None = None,
    post: object,
) -> None:
    resolver = AsyncMock(
        side_effect=target if isinstance(target, Exception) else None,
        return_value=target if isinstance(target, ResolvedWebhookTarget) else _target(),
    )
    monkeypatch.setattr(dispatcher, "resolve_webhook_target", resolver)
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=AssertionError("slept")))


async def test_retryable_failure_sets_next_retry_at(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, post=AsyncMock(return_value=503))

    outcome = await dispatcher.attempt_delivery(
        _URL, {"ok": True}, "secret", attempt=2, loopback_allowlist=(), now=_NOW
    )

    # delay before attempt 3 is DEFAULT_RETRY_DELAYS[2] == 3s plus up to 20% jitter
    assert outcome.next_retry_at is not None
    wait = (outcome.next_retry_at - _NOW).total_seconds()
    assert 3.0 <= wait <= 3.6
    assert outcome.state == "retry_scheduled"
    assert outcome.attempt == 2


async def test_final_attempt_has_no_next_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, post=AsyncMock(return_value=503))

    outcome = await dispatcher.attempt_delivery(
        _URL, {"ok": True}, None, attempt=dispatcher.MAX_ATTEMPTS, loopback_allowlist=(), now=_NOW
    )

    assert outcome.next_retry_at is None
    assert outcome.state == "terminal_failure"


async def test_egress_rejection_is_not_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    post = AsyncMock(return_value=200)
    _patch(monkeypatch, target=WebhookTargetRejected("private address"), post=post)

    outcome = await dispatcher.attempt_delivery(
        _URL, {"ok": True}, None, loopback_allowlist=(), now=_NOW
    )

    assert outcome.success is False
    assert outcome.next_retry_at is None
    assert outcome.should_retry is False
    assert outcome.state == "terminal_failure"
    assert outcome.error_message == "Webhook target rejected by egress policy"
    post.assert_not_awaited()


async def test_all_resolved_addresses_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    tried: list[str] = []

    async def post(target: ResolvedWebhookTarget, *_args: object) -> int:
        tried.append(target.addresses[0])
        if len(tried) < 3:
            raise OSError("connection refused")
        return 204

    _patch(monkeypatch, target=_target("8.8.8.8", "8.8.4.4", "1.1.1.1"), post=post)

    outcome = await dispatcher.attempt_delivery(
        _URL, {"ok": True}, None, loopback_allowlist=(), now=_NOW
    )

    assert tried == ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
    assert outcome.success is True


async def test_every_address_failing_is_a_retryable_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tried: list[str] = []

    async def post(target: ResolvedWebhookTarget, *_args: object) -> int:
        tried.append(target.addresses[0])
        raise TimeoutError

    _patch(monkeypatch, target=_target("8.8.8.8", "8.8.4.4"), post=post)

    outcome = await dispatcher.attempt_delivery(
        _URL, {"ok": True}, None, loopback_allowlist=(), now=_NOW
    )

    assert tried == ["8.8.8.8", "8.8.4.4"]
    assert outcome.success is False
    assert outcome.next_retry_at is not None
    assert outcome.error_message == "Webhook delivery transport failure"


@pytest.mark.parametrize(
    "error",
    [http.client.RemoteDisconnected("closed"), http.client.BadStatusLine("junk")],
)
async def test_http_exception_is_caught(
    monkeypatch: pytest.MonkeyPatch, error: http.client.HTTPException
) -> None:
    _patch(monkeypatch, post=AsyncMock(side_effect=error))

    outcome = await dispatcher.attempt_delivery(
        _URL, {"ok": True}, None, loopback_allowlist=(), now=_NOW
    )

    assert outcome.success is False
    assert outcome.error_message == "Webhook delivery transport failure"
    assert outcome.next_retry_at is not None


async def test_attempt_delivery_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, post=AsyncMock(side_effect=TimeoutError))

    await dispatcher.attempt_delivery(_URL, {"ok": True}, None, loopback_allowlist=(), now=_NOW)
    # _patch makes asyncio.sleep raise; reaching here means no inline backoff ran.
