"""S5a: the webhook route handler itself accepts an unsigned POST when no secret is loaded.

This is a route-level test. The app is built without its lifespan, and the lifespan is where the
receiver enforces the mandatory ``PITWALL_WEBHOOK_SECRET`` (``require_webhook_secret()``: the
service refuses to start without it, see ``tests/webhook_receiver/test_startup.py``). With the
lifespan not run and the secret unset, the handler accepts an unsigned POST (200). The signature
check lives in the handler only when a secret is loaded; ``test_webhook_receiver_signed.py`` covers
that path.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.security


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/webhooks/runpod", "/runpod"])
async def test_unsigned_webhook_accepted_when_secret_unset(
    webhook_app_builder: Any, path: str
) -> None:
    module = webhook_app_builder(secret=None)

    transport = httpx.ASGITransport(app=module.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(path, json={"id": "job-1", "status": "IN_PROGRESS"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
