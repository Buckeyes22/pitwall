"""``pitwall_health`` reports real checks, not a constant."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pitwall.mcp.tools import health

pytestmark = pytest.mark.anyio


class _Connection:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def fetchval(self, query: str) -> int:
        if self.fail:
            raise ConnectionError("database down")
        assert query == "SELECT 1"
        return 1


def _pool(fail: bool) -> SimpleNamespace:
    @asynccontextmanager
    async def acquire():
        yield _Connection(fail)

    return SimpleNamespace(acquire=acquire)


def _redis_context(client: object | None):
    @asynccontextmanager
    async def context():
        yield client

    return context


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: bool,
    redis: object | None,
    registry_ids: tuple[str, ...],
) -> None:
    monkeypatch.setattr(health, "get_pool", AsyncMock(return_value=_pool(fail=not database)))
    monkeypatch.setattr(health, "optional_redis_from_env", _redis_context(redis))
    monkeypatch.setattr(health, "get_default_registry", lambda: SimpleNamespace(ids=registry_ids))


async def test_health_reports_real_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        database=True,
        redis=SimpleNamespace(ping=AsyncMock(return_value=True)),
        registry_ids=("runpod",),
    )
    assert await health.pitwall_health() == {
        "ok": True,
        "database": True,
        "redis": True,
        "provider_registry": True,
    }

    _patch(monkeypatch, database=False, redis=None, registry_ids=())
    assert await health.pitwall_health() == {
        "ok": False,
        "database": False,
        "redis": False,
        "provider_registry": False,
    }


async def test_a_failing_redis_ping_is_reported_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        database=True,
        redis=SimpleNamespace(ping=AsyncMock(side_effect=ConnectionError("redis down"))),
        registry_ids=("runpod",),
    )
    result = await health.pitwall_health()
    assert result["redis"] is False
    assert result["database"] is True
    assert result["ok"] is False


async def test_pool_failure_is_reported_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, database=True, redis=None, registry_ids=("runpod",))
    monkeypatch.setattr(health, "get_pool", AsyncMock(side_effect=OSError("refused")))
    assert (await health.pitwall_health())["database"] is False
