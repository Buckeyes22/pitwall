"""Hermetic durable-journal fake for RP-04 service tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace

from pitwall.runpod_files import (
    VolumeFileIdempotencyConflict,
    VolumeFileMutationAmbiguous,
    VolumeFileMutationAudit,
    VolumeFilePreconditionConflict,
    VolumeFileResult,
)


class FakeVolumeFileMutationJournal:
    """Serialize committed keys and retain credential-free mutation records."""

    def __init__(self) -> None:
        self.records: list[tuple[VolumeFileMutationAudit, VolumeFileResult]] = []
        self.started: dict[str, tuple[str, VolumeFileMutationAudit]] = {}
        self._committed: dict[str, tuple[str, VolumeFileResult]] = {}
        self._conflicts: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def run_idempotent(
        self,
        audit: VolumeFileMutationAudit,
        validate: Callable[[], Awaitable[None]],
        apply: Callable[[bool], Awaitable[VolumeFileResult]],
    ) -> VolumeFileResult:
        assert audit.idempotency_key is not None
        key = audit.idempotency_key
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            prior = self._committed.get(key)
            if prior is not None:
                prior_hash, prior_result = prior
                if prior_hash != audit.request_hash:
                    raise VolumeFileIdempotencyConflict("idempotency key conflict")
                return replace(prior_result, replayed=True)
            started = self.started.get(key)
            if started is not None and started[0] != audit.request_hash:
                raise VolumeFileIdempotencyConflict("idempotency key conflict")
            conflict_hash = self._conflicts.get(key)
            if conflict_hash is not None:
                if conflict_hash != audit.request_hash:
                    raise VolumeFileIdempotencyConflict("idempotency key conflict")
                raise VolumeFilePreconditionConflict("provider precondition conflict")
            retry_started = started is not None
            if retry_started and audit.recovery == "manual":
                raise VolumeFileMutationAmbiguous(
                    "the prior provider mutation has an unresolved outcome"
                )
            if not retry_started:
                await validate()
                self.started[key] = (audit.request_hash, audit)
            try:
                result = await apply(retry_started)
            except VolumeFilePreconditionConflict:
                self._conflicts[key] = audit.request_hash
                raise
            await self.complete(audit, result)
            return result

    async def complete(
        self,
        audit: VolumeFileMutationAudit,
        result: VolumeFileResult,
    ) -> None:
        assert audit.idempotency_key is not None
        self.records.append((audit, result))
        self._committed[audit.idempotency_key] = (audit.request_hash, result)

    async def record(
        self,
        audit: VolumeFileMutationAudit,
        result: VolumeFileResult,
    ) -> None:
        self.records.append((audit, result))


__all__ = ["FakeVolumeFileMutationJournal"]
