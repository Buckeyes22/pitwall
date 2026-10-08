"""A lease teardown lock that is always free, for fake lease repositories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class UnlockedTeardown:
    """Fakes run one teardown at a time, so the lock never contends."""

    @asynccontextmanager
    async def teardown_lock(self, lease_id: str, *, wait: bool) -> AsyncIterator[bool]:
        del lease_id, wait
        yield True
