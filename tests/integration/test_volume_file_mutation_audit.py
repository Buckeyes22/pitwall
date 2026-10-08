"""Real-PostgreSQL evidence for RP-04 durable mutation preparation and replay."""

from __future__ import annotations

import asyncio
import json

import pytest

from pitwall.runpod_files import (
    PostgresVolumeFileMutationJournal,
    VolumeFileMutationAmbiguous,
    VolumeFileMutationAudit,
    VolumeFilePreconditionConflict,
    VolumeFileResult,
)
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_concurrent_exact_key_session_is_serialized_and_completed_replays(pg_pool) -> None:
    journal_a = PostgresVolumeFileMutationJournal(pg_pool, actor="rest:admin")
    journal_b = PostgresVolumeFileMutationJournal(pg_pool, actor="mcp:admin")
    audit = VolumeFileMutationAudit(
        operation="upload",
        action="create",
        volume_id="vol-rp04-audit",
        data_center_id="US-KS-2",
        object_key="configs/audit.json",
        request_hash="a" * 64,
        recovery="conditional_create",
        idempotency_key="rp04-concurrent-exact-key",
    )

    validate_count = 0
    apply_count = 0

    async def validate() -> None:
        nonlocal validate_count
        validate_count += 1

    async def apply(_retry_started: bool) -> VolumeFileResult:
        nonlocal apply_count
        apply_count += 1
        await asyncio.sleep(0.05)
        return VolumeFileResult(
            operation="upload",
            status="completed",
            volume_id=audit.volume_id,
            data_center_id=audit.data_center_id,
            object_key=audit.object_key,
            bytes_transferred=4,
            checksum_sha256="b" * 64,
        )

    first, second = await asyncio.gather(
        journal_a.run_idempotent(audit, validate, apply),
        journal_b.run_idempotent(audit, validate, apply),
    )

    assert {first.replayed, second.replayed} == {False, True}
    assert validate_count == 1
    assert apply_count == 1
    async with pg_pool.acquire() as conn:
        started = await conn.fetch(
            """
            SELECT new_value
            FROM pitwall.config_audit
            WHERE entity_type = 'volume'
              AND new_value->>'idempotency_key' = $1
            ORDER BY id
            """,
            audit.idempotency_key,
        )
    assert len(started) == 2
    assert [row["new_value"]["state"] for row in started] == ["started", "completed"]
    assert started[0]["new_value"]["result"] is None
    assert started[0]["new_value"]["data_center_id"] == audit.data_center_id
    assert started[0]["new_value"]["object_key"] == audit.object_key
    assert second.object_key == audit.object_key
    rendered = json.dumps([row["new_value"] for row in started], sort_keys=True)
    assert "RUNPOD_S3_SECRET_KEY" not in rendered
    assert '"content_base64": null' in rendered


async def test_provider_precondition_conflict_is_durable_and_terminal(pg_pool) -> None:
    journal = PostgresVolumeFileMutationJournal(pg_pool, actor="rest:admin")
    audit = VolumeFileMutationAudit(
        operation="upload",
        action="create",
        volume_id="vol-rp04-conflict",
        data_center_id="US-KS-2",
        object_key="configs/conflict.json",
        request_hash="c" * 64,
        recovery="conditional_create",
        idempotency_key="rp04-provider-precondition-conflict",
    )
    validate_count = 0
    apply_count = 0

    async def validate() -> None:
        nonlocal validate_count
        validate_count += 1

    async def apply(_retry_started: bool) -> VolumeFileResult:
        nonlocal apply_count
        apply_count += 1
        raise VolumeFilePreconditionConflict("provider precondition conflict")

    with pytest.raises(VolumeFilePreconditionConflict):
        await journal.run_idempotent(audit, validate, apply)
    with pytest.raises(VolumeFilePreconditionConflict):
        await journal.run_idempotent(audit, validate, apply)

    assert validate_count == 1
    assert apply_count == 1
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT new_value
            FROM pitwall.config_audit
            WHERE entity_type = 'volume'
              AND new_value->>'idempotency_key' = $1
            ORDER BY id
            """,
            audit.idempotency_key,
        )
    assert [row["new_value"]["state"] for row in rows] == ["started", "conflict"]


async def test_manual_recovery_policy_never_repeats_a_started_provider_mutation(pg_pool) -> None:
    journal = PostgresVolumeFileMutationJournal(pg_pool, actor="rest:admin")
    audit = VolumeFileMutationAudit(
        operation="delete",
        action="delete",
        volume_id="vol-rp04-ambiguous",
        data_center_id="US-KS-2",
        object_key="configs/recreated.json",
        request_hash="d" * 64,
        recovery="manual",
        idempotency_key="rp04-manual-recovery",
    )
    initial_apply_count = 0
    retry_apply_count = 0

    async def validate() -> None:
        return None

    async def interrupted_apply(_retry_started: bool) -> VolumeFileResult:
        nonlocal initial_apply_count
        initial_apply_count += 1
        raise asyncio.CancelledError

    async def unsafe_retry(_retry_started: bool) -> VolumeFileResult:
        nonlocal retry_apply_count
        retry_apply_count += 1
        return VolumeFileResult(operation="delete", status="completed")

    with pytest.raises(asyncio.CancelledError):
        await journal.run_idempotent(audit, validate, interrupted_apply)
    with pytest.raises(VolumeFileMutationAmbiguous):
        await journal.run_idempotent(audit, validate, unsafe_retry)

    assert initial_apply_count == 1
    assert retry_apply_count == 0
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT new_value
            FROM pitwall.config_audit
            WHERE entity_type = 'volume'
              AND new_value->>'idempotency_key' = $1
            ORDER BY id
            """,
            audit.idempotency_key,
        )
    assert [row["new_value"]["state"] for row in rows] == ["started"]
    assert rows[0]["new_value"]["recovery"] == "manual"
