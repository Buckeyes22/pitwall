"""A pool double whose connection records transaction begin, commit, and rollback."""

from __future__ import annotations

from types import TracebackType
from typing import Any


class FakeTransaction:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def __aenter__(self) -> None:
        self._events.append("begin")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._events.append("rollback" if exc_type is not None else "commit")


class FakeConnection:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self._events)


class _Acquire:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class FakePool:
    """``acquire()`` yields one connection; ``events`` holds its transaction trail."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.connection = FakeConnection(self.events)

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)
