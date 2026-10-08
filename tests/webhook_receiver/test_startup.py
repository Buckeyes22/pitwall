"""The receiver refuses to start without an inbound webhook secret."""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.anyio


async def test_refuses_to_start_without_secret(
    receiver: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(receiver, "_WEBHOOK_SECRETS", ())
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@localhost/unused")

    with pytest.raises(RuntimeError, match="PITWALL_WEBHOOK_SECRET"):
        async with receiver.lifespan(receiver.app):
            pytest.fail("lifespan must not start without a secret")
