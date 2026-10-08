"""In-memory asyncpg stand-ins that record transaction state around every call."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def workload(workload_id: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": workload_id,
        "state": "completed",
        "submitted_at": datetime.now(UTC) - timedelta(days=400),
        "runpod_job_id": None,
        "result": None,
        "related_idempotency_keys": [],
        "related_inbound_webhooks": [],
        "related_outbound_webhook_failures": [],
    }
    row.update(fields)
    return row


class FakeConn:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self.pool)

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        pool = self.pool
        pool.events.append(("fetch", pool.in_transaction))
        if "FOR UPDATE" in sql:
            wanted = set(args[0])
            return [{"id": row["id"]} for row in pool.rows if row["id"] in wanted]
        if pool.selection_calls >= 1:
            return []
        pool.selection_calls += 1
        return list(pool.rows)

    async def execute(self, sql: str, *args: Any) -> str:
        pool = self.pool
        pool.executed.append((" ".join(sql.split()), args))
        pool.events.append(("execute", pool.in_transaction))
        if sql.lstrip().startswith("DELETE FROM pitwall.workloads"):
            ids = set(args[0])
            pool.deleted_ids |= ids
            return f"DELETE {len(ids)}"
        return "OK"


class FakeTransaction:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool

    async def __aenter__(self) -> None:
        self.pool.in_transaction = True
        self.pool.events.append(("begin", True))

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        self.pool.in_transaction = False
        self.pool.events.append(("commit" if exc_type is None else "rollback", False))


class FakeAcquire:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool

    async def __aenter__(self) -> FakeConn:
        return FakeConn(self.pool)

    async def __aexit__(self, *_: object) -> None:
        return None


class FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.in_transaction = False
        self.selection_calls = 0
        self.events: list[tuple[str, Any]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.deleted_ids: set[str] = set()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self)
