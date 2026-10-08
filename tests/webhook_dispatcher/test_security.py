"""Outbound webhook SSRF, canonical-body, retry, and secret-storage tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from examples.verify_webhook import verify as verify_independently
from pitwall.webhook_dispatcher import dispatcher
from pitwall.webhook_dispatcher import security as webhook_security
from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher
from pitwall.webhook_dispatcher.security import (
    ResolvedWebhookTarget,
    WebhookTargetRejected,
    resolve_webhook_target,
)
from pitwall.webhook_dispatcher.signer import verify
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.anyio

_TAILSCALE_TEST_ADDRESS = ".".join(("100", "64", "0", "1"))


async def _resolver_with(*addresses: str):
    async def resolve(_hostname: str, _port: int) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        _TAILSCALE_TEST_ADDRESS,
        "0.0.0.0",
        "224.0.0.1",
        "192.0.2.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::",
        "ff02::1",
        "2001:db8::1",
    ],
)
async def test_rejects_every_non_global_dns_address(address: str) -> None:
    resolver = await _resolver_with(address)
    with pytest.raises(WebhookTargetRejected):
        await resolve_webhook_target("https://hooks.example.test/result", resolver=resolver)


async def test_rejects_mixed_public_private_dns_answers() -> None:
    resolver = await _resolver_with("8.8.8.8", "10.0.0.2")
    with pytest.raises(WebhookTargetRejected):
        await resolve_webhook_target("https://hooks.example.test/result", resolver=resolver)


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.test/result",
        "https://user:pass@hooks.example.test/result",
        "https://hooks.example.test:8443/result",
        "https://localhost/result",
        "https://2130706433/result",
        "https://%31%32%37.0.0.1/result",
        "https://hooks.example.test/result#fragment",
        "https:\\127.0.0.1\\result",
    ],
)
async def test_rejects_ambiguous_or_unsafe_urls(url: str) -> None:
    resolver = await _resolver_with("8.8.8.8")
    with pytest.raises(WebhookTargetRejected):
        await resolve_webhook_target(url, resolver=resolver)


async def test_allows_and_normalizes_public_https_target() -> None:
    resolver = await _resolver_with("8.8.8.8", "2606:4700:4700::1111")
    target = await resolve_webhook_target(
        "HTTPS://Hooks.Example.Test/events?source=pitwall",
        resolver=resolver,
    )
    assert target.hostname == "hooks.example.test"
    assert target.port == 443
    assert target.request_target == "/events?source=pitwall"
    assert target.addresses == ("8.8.8.8", "2606:4700:4700::1111")


async def test_allowlisted_loopback_http_is_returned_unchanged() -> None:
    url = "http://127.0.0.1:8765/pitwall?source=routing"
    target = await resolve_webhook_target(
        url,
        loopback_allowlist=("127.0.0.1:8765",),
    )

    assert target.url == url
    assert target.hostname == "127.0.0.1"
    assert target.port == 8765
    assert target.addresses == ("127.0.0.1",)
    assert target.request_target == "/pitwall?source=routing"


_REFUSED_TARGETS = (
    "http://127.0.0.1:8766/pitwall",
    "http://[::1]:8765/pitwall",
    "http://10.0.0.8:8765/pitwall",
    "https://127.0.0.1:8765/pitwall",
)


@pytest.mark.parametrize("url", _REFUSED_TARGETS)
async def test_nonlisted_loopback_and_private_targets_remain_refused(url: str) -> None:
    with pytest.raises(WebhookTargetRejected):
        await resolve_webhook_target(
            url,
            loopback_allowlist=("127.0.0.1:8765",),
        )


async def test_public_https_policy_is_unchanged_with_loopback_allowlist() -> None:
    resolver = await _resolver_with("8.8.8.8")
    target = await resolve_webhook_target(
        "https://hooks.example.test/events",
        resolver=resolver,
        loopback_allowlist=("127.0.0.1:8765",),
    )

    assert target.url == "https://hooks.example.test/events"
    assert target.addresses == ("8.8.8.8",)


async def test_allowlisted_loopback_is_delivered_over_plain_http(
    unused_tcp_port: int,
) -> None:
    received: asyncio.Future[tuple[bytes, dict[str, str]]] = (
        asyncio.get_running_loop().create_future()
    )

    async def receive(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("ascii").split("\r\n")
        headers = {
            key.lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for key, value in [line.split(":", 1)]
        }
        body = await reader.readexactly(int(headers["content-length"]))
        received.set_result((body, headers))
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(receive, "127.0.0.1", unused_tcp_port)
    try:
        outcome = await dispatcher.attempt_delivery(
            f"http://127.0.0.1:{unused_tcp_port}/pitwall",
            {"event": "lease.ready"},
            "loopback-secret",
            retry_delays=(0,),
            loopback_allowlist=(f"127.0.0.1:{unused_tcp_port}",),
        )
        body, headers = await asyncio.wait_for(received, timeout=HANG_GUARD_SECS)
    finally:
        server.close()
        await server.wait_closed()

    assert outcome.success is True
    assert json.loads(body) == {"event": "lease.ready"}
    assert "x-pitwall-signature" in headers
    assert verify_independently(body, headers["x-pitwall-signature"], "loopback-secret")


def _public_target() -> ResolvedWebhookTarget:
    return ResolvedWebhookTarget(
        url="https://hooks.example.test/events",
        hostname="hooks.example.test",
        port=443,
        request_target="/events",
        addresses=("8.8.8.8",),
    )


def test_connection_peer_must_match_the_pinned_dns_address(monkeypatch) -> None:
    class FakeSocket:
        closed = False

        def getpeername(self) -> tuple[str, int]:
            return ("1.1.1.1", 443)

        def close(self) -> None:
            self.closed = True

    fake_socket = FakeSocket()
    monkeypatch.setattr(webhook_security.socket, "create_connection", lambda *_args: fake_socket)
    connection = webhook_security._PinnedHTTPSConnection(
        _public_target(),
        "8.8.8.8",
        1.0,
    )
    assert connection._ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2

    with pytest.raises(WebhookTargetRejected, match="did not match"):
        connection.connect()
    assert fake_socket.closed is True


async def test_dispatch_signs_the_exact_canonical_json_bytes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def post(
        _target: ResolvedWebhookTarget,
        body: bytes,
        headers: dict[str, str],
        _timeout: float,
    ) -> int:
        captured.update(body=body, headers=headers)
        return 204

    monkeypatch.setattr(
        dispatcher, "resolve_webhook_target", AsyncMock(return_value=_public_target())
    )
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)

    outcome = await dispatcher.attempt_delivery(
        "https://hooks.example.test/events",
        {"z": True, "a": "✓"},
        "signing-secret",
        retry_delays=(0,),
    )

    assert outcome.success is True
    assert captured["body"] == '{"a":"✓","z":true}'.encode()
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["Content-Length"] == str(len(captured["body"]))
    assert verify(captured["body"], headers["X-Pitwall-Signature"], "signing-secret")
    assert json.loads(captured["body"]) == {"a": "✓", "z": True}


async def test_timeout_retries_are_bounded_and_delivery_id_is_stable(monkeypatch) -> None:
    delivery_ids: list[str] = []

    async def timeout(
        _target: ResolvedWebhookTarget,
        _body: bytes,
        headers: dict[str, str],
        _timeout: float,
    ) -> int:
        delivery_ids.append(headers["X-Pitwall-Delivery-ID"])
        raise TimeoutError

    monkeypatch.setattr(
        dispatcher, "resolve_webhook_target", AsyncMock(return_value=_public_target())
    )
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", timeout)

    delivery_id: str | None = None
    outcomes = []
    for attempt in range(1, dispatcher.MAX_ATTEMPTS + 1):
        outcome = await dispatcher.attempt_delivery(
            "https://hooks.example.test/events",
            {"ok": True},
            "secret",
            attempt=attempt,
            delivery_id=delivery_id,
            retry_delays=(0, 0.01, 0.01, 0.01),
        )
        delivery_id = outcome.delivery_id
        outcomes.append(outcome)

    assert all(not outcome.success for outcome in outcomes)
    assert [outcome.next_retry_at is not None for outcome in outcomes] == [True, True, True, False]
    assert outcomes[-1].attempt == 4
    assert outcomes[-1].error_message == "Webhook delivery transport failure"
    assert len(delivery_ids) == 4
    assert len(set(delivery_ids)) == 1


async def test_redirect_is_terminal_and_never_followed(monkeypatch) -> None:
    post = AsyncMock(return_value=302)
    monkeypatch.setattr(
        dispatcher, "resolve_webhook_target", AsyncMock(return_value=_public_target())
    )
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)

    outcome = await dispatcher.attempt_delivery(
        "https://hooks.example.test/events",
        {"ok": True},
        "secret",
    )

    assert outcome.success is False
    assert outcome.attempt == 1
    assert outcome.status_code == 302
    post.assert_awaited_once()


async def test_dns_is_revalidated_on_every_attempt(monkeypatch) -> None:
    resolver = AsyncMock(
        side_effect=[_public_target(), WebhookTargetRejected("changed to private address")]
    )
    post = AsyncMock(return_value=503)
    monkeypatch.setattr(dispatcher, "resolve_webhook_target", resolver)
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)

    first = await dispatcher.attempt_delivery(
        "https://hooks.example.test/events",
        {"ok": True},
        "secret",
        retry_delays=(0, 0.01),
    )
    assert first.next_retry_at is not None
    outcome = await dispatcher.attempt_delivery(
        "https://hooks.example.test/events",
        {"ok": True},
        "secret",
        attempt=2,
        delivery_id=first.delivery_id,
        retry_delays=(0, 0.01),
    )

    assert outcome.success is False
    assert outcome.attempt == 2
    assert outcome.error_message == "Webhook target rejected by egress policy"
    post.assert_awaited_once()


def test_webhook_secret_cipher_supports_key_rotation(monkeypatch) -> None:
    old_key = bytes(range(32))
    new_key = bytes(reversed(range(32)))
    monkeypatch.setenv(
        "PITWALL_WEBHOOK_ENCRYPTION_KEYS",
        json.dumps(
            {
                "v1": base64.urlsafe_b64encode(old_key).decode(),
                "v2": base64.urlsafe_b64encode(new_key).decode(),
            }
        ),
    )
    monkeypatch.setenv("PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY", "v2")

    cipher = WebhookSecretCipher.from_env()
    encrypted = cipher.encrypt("consumer-signing-secret")

    assert encrypted.key_version == "v2"
    assert b"consumer-signing-secret" not in encrypted.ciphertext
    assert cipher.decrypt(encrypted) == "consumer-signing-secret"


def test_golden_signature_matches_independent_stdlib_implementation() -> None:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "webhooks" / "completion-v1.json"
    fixture = json.loads(fixture_path.read_text())
    body = fixture["canonical_body"].encode()
    timestamp = fixture["timestamp"]
    expected = hmac.new(
        fixture["secret"].encode(),
        f"{timestamp}.".encode() + body,
        hashlib.sha256,
    ).hexdigest()
    assert fixture["signature"] == f"t={timestamp},v1={expected}"
