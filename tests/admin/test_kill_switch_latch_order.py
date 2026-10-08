"""The kill switch closes admission before any destructive step (review finding #11).

``run_kill`` used to write its kill_log row only after ``activate()`` returned, so admission stayed
open while pods were being destroyed, and an exception in ``activate()`` left no latch at all.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pitwall.api.admin import emergency

pytestmark = pytest.mark.anyio


class _RecordingSever:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    async def deny_all(self, tag: str = "") -> bool:
        self.events.append("deny_all")
        if self.fail:
            # activate() absorbs ordinary exceptions into report.errors; cancellation escapes it.
            raise asyncio.CancelledError("tailscale api_key=tskey-abcdef0123456789 refused")
        return True

    async def revoke_devices(self, tag: str = "") -> int:
        self.events.append("revoke_devices")
        return 0

    async def aclose(self) -> None:
        pass


def _install(
    monkeypatch: pytest.MonkeyPatch, events: list[str], sever: _RecordingSever
) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []

    async def fake_get_pool() -> object:
        return object()

    async def fake_persist(_pool: object, **kwargs: Any) -> int:
        events.append("latch")
        writes.append({"op": "insert", **kwargs})
        return 7

    async def fake_update(_pool: object, kill_id: int, **kwargs: Any) -> None:
        events.append("update")
        writes.append({"op": "update", "kill_id": kill_id, **kwargs})

    monkeypatch.setattr(emergency, "get_pool", fake_get_pool)
    monkeypatch.setattr(emergency, "persist_kill_report", fake_persist)
    monkeypatch.setattr(emergency, "update_kill_report", fake_update)
    monkeypatch.setattr(emergency, "_network_sever_from_env", lambda: sever)
    return writes


async def test_latch_row_is_written_before_the_network_is_severed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    writes = _install(monkeypatch, events, _RecordingSever(events))

    report = await emergency.run_kill("latch order", actor="test:latch", terminate_compute=False)

    assert events == ["latch", "deny_all", "revoke_devices", "update"]
    assert writes[0]["errors"] == ["kill in progress"]
    assert writes[0]["pods_terminated"] == 0
    assert writes[1]["kill_id"] == 7
    assert writes[1]["errors"] == report.errors
    assert writes[1]["total_duration_ms"] == report.total_duration_ms


async def test_activate_failure_keeps_the_latch_and_records_the_redacted_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    writes = _install(monkeypatch, events, _RecordingSever(events, fail=True))

    with pytest.raises(asyncio.CancelledError):
        await emergency.run_kill("activate fails", actor="test:latch", terminate_compute=False)

    assert events == ["latch", "deny_all", "update"]
    (error,) = writes[1]["errors"]
    assert error.startswith("activate: ")
    assert "tskey-abcdef0123456789" not in error
