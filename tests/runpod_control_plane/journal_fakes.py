"""In-memory stand-in for the ``config_audit`` rows the control-plane journal reads and writes.

The fake implements only what ``PostgresRunPodMutationJournal`` touches: ``pool.acquire()``,
``conn.execute`` for the per-key advisory lock and unlock, ``conn.transaction()``, and
``conn.fetchrow`` for the latest journal row of one idempotency key. Rows are written through
the module's ``insert_audit`` (patched by :func:`recording_insert_audit`) and stored as a JSON
round trip, so a stored result must survive the same serialization ``jsonb`` imposes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

JOURNAL_KIND = "runpod_control_plane_mutation"


class FakeLockNotAvailableError(Exception):
    """Stands in for asyncpg's ``LockNotAvailableError`` (SQLSTATE 55P03)."""

    sqlstate = "55P03"


class FakeJournalConnection:
    def __init__(self, pool: FakeJournalPool) -> None:
        self._pool = pool
        self._lock_timeout_s: float | None = None

    async def execute(self, sql: str, *args: Any) -> str:
        self._pool.statements.append(sql)
        if sql.startswith("SET LOCAL lock_timeout"):
            self._lock_timeout_s = int(sql.split("'")[1].removesuffix("ms")) / 1000
        elif "pg_advisory_lock" in sql:
            lock = self._pool.lock_for(str(args[0]))
            try:
                await asyncio.wait_for(lock.acquire(), self._lock_timeout_s)
            except TimeoutError as exc:
                raise FakeLockNotAvailableError("canceling statement due to lock timeout") from exc
        elif "pg_advisory_unlock" in sql:
            self._pool.lock_for(str(args[0])).release()
        return "SELECT 1"

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, sql: str, *args: Any) -> Any:
        """Answers the newest-``started``-row age lookup (``_ATTEMPT_AGE_SQL``)."""
        self._pool.statements.append(sql)
        assert "'started'" in sql and f"'{JOURNAL_KIND}'" in sql
        key = args[0]
        for row in reversed(self._pool.rows):
            payload = row["new_value"]
            if payload.get("idempotency_key") == key and payload.get("state") == "started":
                return self._pool.clock - row["created_at"]
        return None

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self._pool.statements.append(sql)
        if self._pool.fail_load:
            raise OSError("database unreachable")
        assert f"'{JOURNAL_KIND}'" in sql
        key = args[0]
        for row in reversed(self._pool.rows):
            payload = row["new_value"]
            if payload.get("kind") == JOURNAL_KIND and payload.get("idempotency_key") == key:
                return {
                    "new_value": payload,
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "action": row["action"],
                    "age_s": self._pool.clock - row["created_at"],
                }
        return None


class FakeJournalPool:
    """Durable journal rows plus the advisory locks that serialize one key."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.statements: list[str] = []
        self.acquired = 0
        self.fail_load = False
        self.fail_states: set[str] = set()
        self.clock = 0.0
        self._locks: dict[str, asyncio.Lock] = {}

    def advance(self, seconds: float) -> None:
        """Move the database clock forward; journal rows age by *seconds*."""
        self.clock += seconds

    def lock_for(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[FakeJournalConnection]:
        self.acquired += 1
        yield FakeJournalConnection(self)

    def states(self, key: str) -> list[str]:
        return [
            row["new_value"]["state"]
            for row in self.rows
            if row["new_value"].get("idempotency_key") == key
        ]


def recording_insert_audit(
    calls: list[dict[str, Any]],
) -> Callable[..., Awaitable[None]]:
    """Route journal rows to their fake pool and every other audit row to *calls*."""

    async def fake_insert_audit(pool: object, **kwargs: Any) -> None:
        new_value = kwargs.get("new_value") or {}
        if isinstance(pool, FakeJournalPool) and new_value.get("kind") == JOURNAL_KIND:
            if new_value.get("state") in pool.fail_states:
                raise OSError("database write failed")
            stored = {name: value for name, value in kwargs.items() if name != "conn"}
            stored["new_value"] = json.loads(json.dumps(new_value))
            stored["created_at"] = pool.clock
            pool.rows.append(stored)
            return
        calls.append(kwargs)

    return fake_insert_audit


__all__ = [
    "JOURNAL_KIND",
    "FakeJournalConnection",
    "FakeLockNotAvailableError",
    "FakeJournalPool",
    "recording_insert_audit",
]
