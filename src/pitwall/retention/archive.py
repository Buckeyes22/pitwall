"""Encrypted, bounded, auditable workload archive and purge lifecycle."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import asyncpg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from pitwall.config import decode_archive_key
from pitwall.security.redaction import redact_text

ARCHIVE_RETENTION_DAYS = 90
DEFAULT_BATCH_SIZE = 1_000
MAX_BATCH_SIZE = 10_000
MANIFEST_FILENAME = "manifest.json"
ARCHIVE_FORMAT_VERSION = 1
TERMINAL_STATES = ("completed", "failed", "cancelled", "timed_out")
COMMIT_FILENAME = "commit.json"
ObjectDelete = Callable[[list[str]], Awaitable[None]]

log = logging.getLogger(__name__)

_SELECT_PAGE = """
SELECT w.*,
       COALESCE((
         SELECT jsonb_agg(to_jsonb(i)) FROM pitwall.idempotency_keys i
         WHERE i.workload_id = w.id
       ), '[]'::jsonb) AS related_idempotency_keys,
       COALESCE((
         SELECT jsonb_agg(to_jsonb(d)) FROM pitwall.runpod_webhook_deliveries d
         WHERE d.runpod_job_id = w.runpod_job_id
       ), '[]'::jsonb) AS related_inbound_webhooks,
       COALESCE((
         SELECT jsonb_agg(to_jsonb(f)) FROM pitwall.webhook_delivery_failures f
         WHERE f.workload_id = w.id
       ), '[]'::jsonb) AS related_outbound_webhook_failures
FROM pitwall.workloads w
WHERE w.submitted_at < $1
  AND w.state = ANY($2::text[])
  AND ($4::timestamptz IS NULL OR (w.submitted_at, w.id) > ($4::timestamptz, $5::text))
  AND (NOT $6::boolean OR w.id NOT IN (
    SELECT jsonb_array_elements_text(a.new_value -> 'workload_ids')
    FROM pitwall.config_audit a
    WHERE a.entity_type = 'retention_run'
      AND a.action = 'archive'
      AND jsonb_typeof(a.new_value -> 'workload_ids') = 'array'
  ))
ORDER BY w.submitted_at, w.id
LIMIT $3
"""

# Rows are selected without locks so encryption and file writes hold no row locks; the purge
# transaction re-locks the selected ids and deletes only rows it still holds.
_LOCK_BY_ID = """
SELECT id FROM pitwall.workloads
WHERE id = ANY($1::text[]) AND state = ANY($2::text[])
FOR UPDATE SKIP LOCKED
"""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_to_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    column_names = row.keys()
    for key in column_names:
        value = row[key]
        if isinstance(value, (dict, list)) or value is None:
            result[key] = value
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        os.chmod(temporary, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(0o600)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _object_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for name, child in value.items():
            if name in {"r2_key", "object_key", "staging_key"} and isinstance(child, str):
                keys.add(child)
            keys.update(_object_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_object_keys(child))
    return keys


async def _delete_related(
    conn: asyncpg.Connection, rows: list[dict[str, Any]], cutoff: datetime
) -> int:
    workload_ids = [str(row["id"]) for row in rows]
    runpod_job_ids = [str(row["runpod_job_id"]) for row in rows if row.get("runpod_job_id")]
    await conn.execute(
        "DELETE FROM pitwall.idempotency_keys WHERE workload_id = ANY($1::text[])",
        workload_ids,
    )
    if runpod_job_ids:
        await conn.execute(
            "DELETE FROM pitwall.runpod_webhook_deliveries WHERE runpod_job_id = ANY($1::text[])",
            runpod_job_ids,
        )
    await conn.execute(
        "DELETE FROM pitwall.webhook_delivery_failures WHERE workload_id = ANY($1::text[])",
        workload_ids,
    )
    result = await conn.execute(
        "DELETE FROM pitwall.workloads WHERE id = ANY($1::text[]) "
        "AND submitted_at < $2 AND state = ANY($3::text[])",
        workload_ids,
        cutoff,
        list(TERMINAL_STATES),
    )
    return int(result.rsplit(" ", 1)[-1])


async def _select_batch(
    pool: asyncpg.Pool,
    cutoff: datetime,
    batch_size: int,
    *,
    exclude_archived: bool,
    skip_keyed: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Read up to ``batch_size`` eligible rows without locking; return them and the skip count.

    Rows carrying object-storage keys are skipped (and counted) when ``skip_keyed`` is set, and
    pages continue past them so a run of skipped rows never starves later eligible rows.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    after_at: datetime | None = None
    after_id: str | None = None
    async with pool.acquire() as conn:
        while len(rows) < batch_size:
            page = await conn.fetch(
                _SELECT_PAGE,
                cutoff,
                list(TERMINAL_STATES),
                batch_size,
                after_at,
                after_id,
                exclude_archived,
            )
            for record in page:
                row = _row_to_dict(record)
                if skip_keyed and _object_keys(row):
                    skipped += 1
                elif len(rows) < batch_size:
                    rows.append(row)
            if len(page) < batch_size or len(rows) >= batch_size:
                break
            after_at, after_id = page[-1]["submitted_at"], str(page[-1]["id"])
    return rows, skipped


def _encrypt_archive(
    rows: list[dict[str, Any]],
    run_directory: Path,
    *,
    run_id: str,
    key_text: str,
    version: str,
) -> tuple[dict[str, Any], Path]:
    """Encrypt ``rows`` into ``run_directory`` and write the preliminary manifest."""
    _secure_directory(run_directory)
    plaintext = b"".join(_canonical_json(row) + b"\n" for row in rows)
    nonce = os.urandom(12)
    aad = f"pitwall-retention:{ARCHIVE_FORMAT_VERSION}:{run_id}:{version}".encode()
    ciphertext = AESGCM(decode_archive_key(key_text)).encrypt(nonce, plaintext, aad)
    archive_path = run_directory / "workloads.jsonl.enc"
    _atomic_write(archive_path, ciphertext)
    manifest: dict[str, Any] = {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "key_version": version,
        "cipher": "AES-256-GCM",
        "nonce_b64": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "aad": aad.decode("ascii"),
        "archive_file": archive_path.name,
        "archive_size_bytes": len(ciphertext),
        "archive_sha256": _sha256_file(archive_path),
    }
    return manifest, run_directory / MANIFEST_FILENAME


async def _retry_pending_object_deletes(output_dir: Path, object_delete: ObjectDelete) -> None:
    """Delete objects an earlier committed run could not, and clear them from its evidence."""
    for commit_path in sorted(output_dir.glob(f"ret_*/{COMMIT_FILENAME}")):
        evidence = cast(dict[str, Any], json.loads(commit_path.read_text(encoding="utf-8")))
        pending = [str(key) for key in evidence.get("pending_object_keys") or []]
        if not pending:
            continue
        try:
            await object_delete(pending)
        except Exception as exc:  # reason: stays pending; retried again on the next run
            log.warning(
                "retention object delete retry failed for %s: %s",
                commit_path.parent.name,
                redact_text(exc),
            )
            continue
        evidence["pending_object_keys"] = []
        evidence["objects_deleted_at"] = datetime.now(UTC).isoformat()
        _atomic_write(commit_path, _canonical_json(evidence))


async def _delete_objects_after_commit(
    run_id: str, keys: list[str], object_delete: ObjectDelete | None
) -> list[str]:
    """Delete object-storage keys after the database commit; return the keys that failed."""
    if not keys or object_delete is None:
        return []
    try:
        await object_delete(keys)
    except Exception as exc:  # reason: rows are committed; keys are recorded and retried
        log.warning("retention object delete failed for %s: %s", run_id, redact_text(exc))
        return keys
    return []


async def archive_workloads_to_jsonl(
    pool: asyncpg.Pool,
    output_dir: Path,
    *,
    older_than_days: int = ARCHIVE_RETENTION_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    purge: bool = False,
    dry_run: bool = False,
    encryption_key: str | None = None,
    key_version: str | None = None,
    object_delete: ObjectDelete | None = None,
) -> dict[str, Any]:
    """Archive one bounded terminal-workload batch and optionally purge it.

    Files use AES-256-GCM and mode 0600 under a mode-0700 per-run directory.
    Rows are read without locks, encrypted and written, then re-locked by id for the purge, so no
    row lock is held across file I/O. The database deletion happens only after the encrypted file
    and preliminary manifest have been durably written. A failed database commit leaves an
    explicit uncommitted manifest instead of silently claiming a purge. Object-storage deletes run
    after the commit; a failed delete is recorded and retried by the next run. Nothing is written
    when no row is archived.
    """
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    key_text = encryption_key or os.environ.get("PITWALL_ARCHIVE_ENCRYPTION_KEY", "")
    version = key_version or os.environ.get("PITWALL_ARCHIVE_ENCRYPTION_KEY_VERSION", "")
    if not dry_run and (not key_text or not version):
        raise ValueError("archive encryption key and key version are required")

    started_at = datetime.now(UTC)
    # Whole UTC days: the daily rollup buckets by submitted_at day, so a purge must not split
    # one by time of day (the rollup also freezes days before a purge cutoff; see cost_daily).
    cutoff = (started_at - timedelta(days=older_than_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    run_id = f"ret_{started_at.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:12]}"
    mode = "dry-run" if dry_run else "archive-purge" if purge else "archive"
    if purge and not dry_run and object_delete is not None:
        await _retry_pending_object_deletes(output_dir, object_delete)

    rows, skipped = await _select_batch(
        pool,
        cutoff,
        batch_size,
        exclude_archived=not purge,
        skip_keyed=purge and object_delete is None,
    )
    if skipped:
        log.warning(
            "retention skipped %d workloads with object-storage keys: no object_delete adapter",
            skipped,
        )
    summary: dict[str, Any] = {
        "format_version": ARCHIVE_FORMAT_VERSION,
        "run_id": run_id,
        "mode": mode,
        "cutoff_at": cutoff.isoformat(),
        "workload_count": len(rows),
        "deleted_count": 0,
        "skipped_object_key_count": skipped,
        "database_committed": False,
    }
    if dry_run or not rows:
        return summary

    run_directory = output_dir / run_id
    encryption, manifest_path = await asyncio.to_thread(
        _encrypt_archive, rows, run_directory, run_id=run_id, key_text=key_text, version=version
    )
    manifest: dict[str, Any] = {
        **encryption,
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at.isoformat(),
        "cutoff_at": cutoff.isoformat(),
        "workload_count": len(rows),
        "deleted_count": 0,
        "skipped_object_key_count": skipped,
        "database_commit_status": "pending",
        "commit_evidence_file": COMMIT_FILENAME,
    }
    _atomic_write(manifest_path, _canonical_json(manifest))

    deleted_count, object_keys = await _commit_run(
        pool,
        rows,
        purge=purge,
        cutoff=cutoff,
        run_id=run_id,
        mode=mode,
        started_at=started_at,
        run_directory=run_directory,
        manifest_path=manifest_path,
        version=version,
    )
    pending_object_keys = await _delete_objects_after_commit(run_id, object_keys, object_delete)

    completed_manifest = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    completed_manifest["deleted_count"] = deleted_count
    completed_manifest["completed_at"] = datetime.now(UTC).isoformat()
    completed_manifest["database_committed"] = True
    completed_manifest["pending_object_keys"] = pending_object_keys
    _atomic_write(
        run_directory / COMMIT_FILENAME,
        _canonical_json(
            {
                "run_id": run_id,
                "database_committed": True,
                "manifest_sha256": _sha256_file(manifest_path),
                "pending_object_keys": pending_object_keys,
            }
        ),
    )
    return completed_manifest


async def _commit_run(
    pool: asyncpg.Pool,
    rows: list[dict[str, Any]],
    *,
    purge: bool,
    cutoff: datetime,
    run_id: str,
    mode: str,
    started_at: datetime,
    run_directory: Path,
    manifest_path: Path,
    version: str,
) -> tuple[int, list[str]]:
    """Re-lock the archived rows by id, purge them, and record the run in one transaction.

    Returns the purged row count and the object keys to delete after the commit.
    """
    deleted_count = 0
    object_keys: list[str] = []
    async with pool.acquire() as conn, conn.transaction():
        if purge:
            locked = await conn.fetch(
                _LOCK_BY_ID, [str(row["id"]) for row in rows], list(TERMINAL_STATES)
            )
            locked_ids = {str(record["id"]) for record in locked}
            purged = [row for row in rows if str(row["id"]) in locked_ids]
            if purged:
                object_keys = sorted(_object_keys(purged))
                deleted_count = await _delete_related(conn, purged, cutoff)
        await conn.execute(
            """
            INSERT INTO pitwall.retention_runs
              (id, started_at, completed_at, cutoff_at, mode, archive_path,
               manifest_sha256, workload_count, deleted_count, key_version, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'completed')
            """,
            run_id,
            started_at,
            datetime.now(UTC),
            cutoff,
            mode,
            str(run_directory),
            _sha256_file(manifest_path),
            len(rows),
            deleted_count,
            version,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.config_audit
              (entity_type, entity_id, action, old_value, new_value, actor, change_reason)
            VALUES ('retention_run',$1,$2,NULL,$3::jsonb,'system',$4)
            """,
            run_id,
            "purge" if purge else "archive",
            json.dumps(
                {
                    "workload_count": len(rows),
                    "deleted_count": deleted_count,
                    "workload_ids": [str(row["id"]) for row in rows],
                }
            ),
            f"bounded {mode} retention run",
        )
    return deleted_count, object_keys
