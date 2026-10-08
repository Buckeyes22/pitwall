"""Fixtures for the webhook receiver tests."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

RECEIVER_SECRET = "receiver-test-secret-0123456789"  # pragma: allowlist secret


class FakeDeliveryRepo:
    """Records insert_or_skip calls; every delivery is new."""

    calls: list[dict[str, Any]] = []

    def __init__(self, pool: object) -> None:
        self._pool = pool

    async def insert_or_skip(self, **kwargs: Any) -> Any:
        type(self).calls.append(kwargs)

        class _Result:
            is_new = True
            delivery_id = 1

        return _Result()


def _purge() -> None:
    for name in [k for k in sys.modules if k.startswith("pitwall.webhook_receiver")]:
        del sys.modules[name]


@pytest.fixture
def receiver(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """A freshly imported receiver module with a secret and a fake repository."""
    _purge()
    monkeypatch.setenv("PITWALL_WEBHOOK_SECRET", RECEIVER_SECRET)
    monkeypatch.delenv("PITWALL_WEBHOOK_PREVIOUS_SECRETS", raising=False)
    monkeypatch.delenv("PITWALL_WEBHOOK_RATE_LIMIT", raising=False)
    module = importlib.import_module("pitwall.webhook_receiver")
    FakeDeliveryRepo.calls = []
    monkeypatch.setattr(module, "WebhookDeliveryRepository", FakeDeliveryRepo)
    module.app.state.pool = object()
    module.app.state.redis_settings = object()
    yield module
    _purge()
