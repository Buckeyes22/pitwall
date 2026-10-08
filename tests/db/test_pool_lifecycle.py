"""A-29: pool lifecycle closes in ``finally`` and missing configuration is a typed error."""

from __future__ import annotations

import asyncio
import gc
import weakref
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from pitwall import db

pytestmark = pytest.mark.anyio


async def test_get_pool_without_dsn_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "_pool", None)

    with pytest.raises(db.DatabaseNotConfiguredError, match="DATABASE_URL"):
        await db.get_pool()


async def test_db_lifespan_closes_pool_when_the_app_body_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    monkeypatch.setattr(db, "get_pool", AsyncMock(return_value=object()))
    close = AsyncMock()
    monkeypatch.setattr(db, "close_pool", close)

    with pytest.raises(RuntimeError, match="boom"):
        async with db.db_lifespan(app):
            raise RuntimeError("boom")

    close.assert_awaited_once()


def test_dead_async_helper_is_gone() -> None:
    assert not hasattr(db, "_applied_migrations_async")


class _FakePool:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


def _patch_create_pool(monkeypatch: pytest.MonkeyPatch) -> list[_FakePool]:
    created: list[_FakePool] = []

    async def fake_create_pool(**_kwargs: object) -> _FakePool:
        await asyncio.sleep(0)
        pool = _FakePool()
        created.append(pool)
        return pool

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(db, "_pool", None)
    return created


async def test_concurrent_first_use_creates_one_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _patch_create_pool(monkeypatch)

    first, second = await asyncio.gather(
        db.get_pool("postgresql://x/y"), db.get_pool("postgresql://x/y")
    )

    assert len(created) == 1
    assert first is second is created[0]
    await db.close_pool()
    assert created[0].closed == 1


def test_pool_lock_is_not_tied_to_an_earlier_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _patch_create_pool(monkeypatch)

    async def contend_then_close() -> None:
        await asyncio.gather(db.get_pool("postgresql://x/y"), db.get_pool("postgresql://x/y"))
        await db.close_pool()

    asyncio.run(contend_then_close())
    asyncio.run(contend_then_close())

    assert len(created) == 2
    assert [pool.closed for pool in created] == [1, 1]


def test_pool_lock_does_not_keep_a_closed_loop_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_create_pool(monkeypatch)
    monkeypatch.setattr(db, "_pool_lock", None)

    async def contend_then_close() -> weakref.ref[asyncio.AbstractEventLoop]:
        await asyncio.gather(db.get_pool("postgresql://x/y"), db.get_pool("postgresql://x/y"))
        await db.close_pool()
        return weakref.ref(asyncio.get_running_loop())

    loop_ref = asyncio.run(contend_then_close())
    gc.collect()

    assert loop_ref() is None
