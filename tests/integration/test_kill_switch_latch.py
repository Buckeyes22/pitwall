"""With a real database, a kill whose activation fails still refuses new spend (finding #11)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from pitwall.api.admin import emergency
from pitwall.api.admin.kill_switch import KillSwitchEngaged, enforce_kill_switch_admission
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


class _BrokenSever:
    async def deny_all(self, tag: str = "") -> bool:
        raise asyncio.CancelledError("sever crashed")

    async def revoke_devices(self, tag: str = "") -> int:
        return 0

    async def aclose(self) -> None:
        pass


async def test_failed_activation_leaves_admission_closed(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_get_pool() -> Any:
        return pg_pool

    monkeypatch.setattr(emergency, "get_pool", fake_get_pool)
    monkeypatch.setattr(emergency, "_network_sever_from_env", _BrokenSever)

    await enforce_kill_switch_admission(pg_pool)  # open before the kill
    with pytest.raises(asyncio.CancelledError):
        await emergency.run_kill("integration latch", actor="test:latch", terminate_compute=False)

    with pytest.raises(KillSwitchEngaged):
        await enforce_kill_switch_admission(pg_pool)
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT actor, errors FROM pitwall.kill_log")
    assert row["actor"] == "test:latch"
    assert row["errors"] == ["activate: sever crashed"]
