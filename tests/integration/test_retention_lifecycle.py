"""Real-PostgreSQL evidence for encrypted archive and purge semantics."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import pitwall.retention.archive as archive_module
from pitwall.retention.archive import archive_workloads_to_jsonl
from tests.hang_guard import HANG_GUARD_SECS
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def _seed_retention_graph(pg_pool: asyncpg.Pool, *, object_reference: bool = False) -> None:
    old = datetime.now(UTC) - timedelta(days=120)
    recent = datetime.now(UTC) - timedelta(days=5)
    result: dict[str, Any] = {"answer": 42}
    if object_reference:
        result["object_key"] = "retention/w-old/result.json"

    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pitwall.capabilities (id,name,version,class,cost_mode,config)
            VALUES ('cap-retention','Retention test','1','test','per_request','{}')
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.providers
              (id,capability_id,name,provider_type,config,priority)
            VALUES ('provider-retention','cap-retention','Retention provider',
                    'serverless_queue','{}',1)
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.workloads
              (id,capability_id,provider_id,type,state,runpod_job_id,input,result,submitted_at)
            VALUES
              ('w-old','cap-retention','provider-retention','test','completed',
               'rp-old','{"prompt":"archive me"}',$1::jsonb,$2),
              ('w-recent','cap-retention','provider-retention','test','completed',
               'rp-recent','{}','{}',$3),
              ('w-running','cap-retention','provider-retention','test','running',
               'rp-running','{}','{}',$2)
            """,
            json.dumps(result),
            old,
            recent,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.idempotency_keys (idempotency_key,workload_id,created_at)
            VALUES ('idem-old','w-old',$1)
            """,
            old,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.runpod_webhook_deliveries
              (runpod_job_id,attempt,received_at,payload)
            VALUES ('rp-old',1,$1,'{"status":"COMPLETED"}')
            """,
            old,
        )
        subscription_id = await conn.fetchval(
            """
            INSERT INTO pitwall.webhook_subscriptions (consumer,webhook_url,active)
            VALUES ('retention-test','https://example.invalid/hook',false)
            RETURNING id
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.webhook_delivery_failures
              (workload_id,subscription_id,attempt,payload,status_code,error_message)
            VALUES ('w-old',$1,1,'{"id":"w-old"}',503,'temporary failure')
            """,
            subscription_id,
        )
        await conn.execute(
            """
            INSERT INTO pitwall.kill_log (reason,actor,total_duration_ms)
            VALUES ('retention invariant','test:integration',1)
            """
        )
        await conn.execute(
            """
            INSERT INTO pitwall.config_audit
              (actor,action,entity_type,entity_id,change_reason)
            VALUES ('system','update','capability','cap-retention','retention invariant')
            """
        )


def _key() -> tuple[bytes, str]:
    raw = os.urandom(32)
    return raw, base64.urlsafe_b64encode(raw).decode("ascii")


def _decrypt_run(run_dir: Path, key: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    nonce = base64.urlsafe_b64decode(manifest["nonce_b64"])
    ciphertext = (run_dir / manifest["archive_file"]).read_bytes()
    plaintext = AESGCM(key).decrypt(nonce, ciphertext, manifest["aad"].encode("ascii"))
    rows = [json.loads(line) for line in plaintext.splitlines()]
    return manifest, rows


async def test_archive_purge_is_encrypted_bounded_and_audited(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_retention_graph(pg_pool)
    raw_key, encoded_key = _key()

    result = await archive_workloads_to_jsonl(
        pg_pool,
        tmp_path,
        older_than_days=90,
        batch_size=1,
        purge=True,
        encryption_key=encoded_key,
        key_version="test-v1",
    )

    run_dir = tmp_path / result["run_id"]
    manifest, rows = _decrypt_run(run_dir, raw_key)
    assert [row["id"] for row in rows] == ["w-old"]
    assert rows[0]["related_idempotency_keys"][0]["idempotency_key"] == "idem-old"
    assert rows[0]["related_inbound_webhooks"][0]["runpod_job_id"] == "rp-old"
    assert rows[0]["related_outbound_webhook_failures"][0]["workload_id"] == "w-old"
    assert manifest["database_commit_status"] == "pending"
    assert result["database_committed"] is True
    assert result["deleted_count"] == 1
    assert json.loads((run_dir / "commit.json").read_text())["database_committed"] is True
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    for path in run_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads WHERE id='w-old'") == 0
        assert await conn.fetchval("SELECT count(*) FROM pitwall.idempotency_keys") == 0
        assert await conn.fetchval("SELECT count(*) FROM pitwall.runpod_webhook_deliveries") == 0
        assert await conn.fetchval("SELECT count(*) FROM pitwall.webhook_delivery_failures") == 0
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads") == 2
        assert await conn.fetchval("SELECT count(*) FROM pitwall.kill_log") == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pitwall.config_audit WHERE entity_type='capability'"
            )
            == 1
        )
        run = await conn.fetchrow(
            "SELECT * FROM pitwall.retention_runs WHERE id=$1", result["run_id"]
        )
        assert run is not None
        assert run["status"] == "completed"
        assert run["workload_count"] == 1
        assert run["deleted_count"] == 1
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM pitwall.config_audit "
                "WHERE entity_type='retention_run' AND entity_id=$1 AND action='purge'",
                result["run_id"],
            )
            == 1
        )


async def test_dry_run_has_no_files_mutations_or_audit(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_retention_graph(pg_pool)

    result = await archive_workloads_to_jsonl(
        pg_pool, tmp_path, older_than_days=90, purge=True, dry_run=True
    )

    assert result["workload_count"] == 1
    assert result["deleted_count"] == 0
    assert list(tmp_path.iterdir()) == []
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads") == 3
        assert await conn.fetchval("SELECT count(*) FROM pitwall.retention_runs") == 0


async def test_object_reference_skipped_without_deletion_adapter(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_retention_graph(pg_pool, object_reference=True)
    _, encoded_key = _key()

    manifest = await archive_workloads_to_jsonl(
        pg_pool,
        tmp_path,
        older_than_days=90,
        purge=True,
        encryption_key=encoded_key,
        key_version="test-v1",
    )

    assert manifest["skipped_object_key_count"] == 1
    assert manifest["workload_count"] == 0
    assert manifest["deleted_count"] == 0
    assert manifest["database_committed"] is False
    assert list(tmp_path.glob("ret_*")) == []
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads") == 3
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads WHERE id='w-old'") == 1
        assert await conn.fetchval("SELECT count(*) FROM pitwall.retention_runs") == 0


async def test_database_failure_rolls_back_purge_and_leaves_pending_evidence(
    pg_pool: asyncpg.Pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_retention_graph(pg_pool)
    _, encoded_key = _key()

    async def fail_after_delete(
        conn: asyncpg.Connection,
        rows: list[dict[str, Any]],
        cutoff: datetime,
    ) -> int:
        del rows, cutoff
        await conn.execute("DELETE FROM pitwall.workloads WHERE id='w-old'")
        raise RuntimeError("injected retention failure")

    monkeypatch.setattr(archive_module, "_delete_related", fail_after_delete)
    with pytest.raises(RuntimeError, match="injected retention failure"):
        await archive_workloads_to_jsonl(
            pg_pool,
            tmp_path,
            older_than_days=90,
            purge=True,
            encryption_key=encoded_key,
            key_version="test-v1",
        )

    pending = list(tmp_path.glob("ret_*/manifest.json"))
    assert len(pending) == 1
    assert not (pending[0].parent / "commit.json").exists()
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads WHERE id='w-old'") == 1
        assert await conn.fetchval("SELECT count(*) FROM pitwall.retention_runs") == 0


async def test_retention_cli_skips_object_references_without_deletion_adapter(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_retention_graph(pg_pool, object_reference=True)
    _, encoded_key = _key()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pitwall.retention",
            "run",
            "--archive-dir",
            str(tmp_path),
            "--purge",
        ],
        env={
            **os.environ,
            "DATABASE_URL": os.environ["PITWALL_TEST_DATABASE_URL"],
            "PITWALL_ARCHIVE_ENCRYPTION_KEY": encoded_key,
            "PITWALL_ARCHIVE_ENCRYPTION_KEY_VERSION": "test-cli-v1",
        },
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Traceback" not in completed.stderr
    report = json.loads(completed.stdout)
    assert report["skipped_object_key_count"] == 1
    assert report["workload_count"] == 0
    assert report["deleted_count"] == 0
    assert report["database_committed"] is False
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM pitwall.workloads") == 3
        assert await conn.fetchval("SELECT count(*) FROM pitwall.idempotency_keys") == 1
        assert await conn.fetchval("SELECT count(*) FROM pitwall.retention_runs") == 0
    assert list(tmp_path.glob("ret_*")) == []


_OBJECT_KEY = "retention/w-old/result.json"


async def test_rolled_back_purge_never_deletes_objects(
    pg_pool: asyncpg.Pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding #8: objects were deleted inside the transaction, before a commit that could fail."""
    await _seed_retention_graph(pg_pool, object_reference=True)
    _, encoded_key = _key()
    deleted: list[list[str]] = []

    async def record_delete(keys: list[str]) -> None:
        deleted.append(keys)

    async def fail_delete_related(*_args: Any) -> int:
        raise RuntimeError("injected retention failure")

    monkeypatch.setattr(archive_module, "_delete_related", fail_delete_related)
    with pytest.raises(RuntimeError, match="injected retention failure"):
        await archive_workloads_to_jsonl(
            pg_pool,
            tmp_path,
            older_than_days=90,
            purge=True,
            encryption_key=encoded_key,
            key_version="test-v1",
            object_delete=record_delete,
        )
    assert deleted == []


async def test_objects_are_deleted_after_the_database_commits(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_retention_graph(pg_pool, object_reference=True)
    _, encoded_key = _key()
    rows_left_when_deleting: list[int] = []

    async def delete_after_commit(keys: list[str]) -> None:
        assert keys == [_OBJECT_KEY]
        async with pg_pool.acquire() as conn:  # a separate connection sees only committed state
            rows_left_when_deleting.append(
                await conn.fetchval("SELECT count(*) FROM pitwall.workloads WHERE id='w-old'")
            )

    manifest = await archive_workloads_to_jsonl(
        pg_pool,
        tmp_path,
        older_than_days=90,
        purge=True,
        encryption_key=encoded_key,
        key_version="test-v1",
        object_delete=delete_after_commit,
    )
    assert rows_left_when_deleting == [0]
    assert manifest["pending_object_keys"] == []


async def test_failed_object_delete_is_recorded_and_retried_next_run(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _seed_retention_graph(pg_pool, object_reference=True)
    _, encoded_key = _key()

    async def unavailable(keys: list[str]) -> None:
        raise ConnectionError("object store unavailable")

    first = await archive_workloads_to_jsonl(
        pg_pool,
        tmp_path,
        older_than_days=90,
        purge=True,
        encryption_key=encoded_key,
        key_version="test-v1",
        object_delete=unavailable,
    )
    assert first["database_committed"] is True
    assert first["pending_object_keys"] == [_OBJECT_KEY]
    commit = json.loads((tmp_path / first["run_id"] / "commit.json").read_text())
    assert commit["pending_object_keys"] == [_OBJECT_KEY]

    retried: list[list[str]] = []

    async def available(keys: list[str]) -> None:
        retried.append(keys)

    await archive_workloads_to_jsonl(
        pg_pool,
        tmp_path,
        older_than_days=90,
        purge=True,
        encryption_key=encoded_key,
        key_version="test-v1",
        object_delete=available,
    )
    assert retried == [[_OBJECT_KEY]]
    commit = json.loads((tmp_path / first["run_id"] / "commit.json").read_text())
    assert commit["pending_object_keys"] == []


async def test_a_day_split_by_the_batch_limit_keeps_its_rollup_total(
    pg_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    """Finding #7: the rollup recomputed a partly purged day from the rows left behind."""
    from pitwall.reconciler.cost_daily_rollup import run_rollup

    day = (datetime.now(UTC) - timedelta(days=120)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id,name,version,class,cost_mode,config)"
            " VALUES ('cap-day','Day test','1','test','per_request','{}')"
        )
        for workload_id, hour, cost in (("w-am", 1, "1.25"), ("w-pm", 23, "2.50")):
            await conn.execute(
                "INSERT INTO pitwall.workloads (id,capability_id,provider_id,type,state,"
                " submitted_at,cost_actual_usd) VALUES ($1,'cap-day','prov-day','test',"
                " 'completed',$2,$3::numeric)",
                workload_id,
                day + timedelta(hours=hour),
                cost,
            )
    await run_rollup(pg_pool)
    _, encoded_key = _key()

    manifest = await archive_workloads_to_jsonl(
        pg_pool,
        tmp_path,
        older_than_days=90,
        batch_size=1,
        purge=True,
        encryption_key=encoded_key,
        key_version="test-v1",
    )
    assert manifest["deleted_count"] == 1
    assert datetime.fromisoformat(manifest["cutoff_at"]).time() == datetime.min.time()
    await run_rollup(pg_pool)

    async with pg_pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT cost_usd FROM pitwall.cost_daily WHERE day = $1 AND capability_class = 'test'",
            day.date(),
        )
    assert str(total) == "3.750000"
