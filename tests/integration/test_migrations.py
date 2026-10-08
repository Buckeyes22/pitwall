"""release program Task 1: migration integrity against real Postgres.

Proves the on-disk db/migrations/*.sql apply cleanly and idempotently, that
discover_migrations() matches the files on disk, and that a drop -> re-migrate
cycle reproduces the same schema. Uses the live pg_pool fixture.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from pitwall.migrations import discover_migrations
from tests._migration_ledger import restore_migration_ledger
from tests.integration.conftest import (
    _MIGRATION_DIR,
    _all_migration_sql,
    _rebuild_schema_and_restore_ledger,
    _restore_migration_ledger,
    requires_pg,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

# This intentionally mirrors the inline raw-schema pattern in
# tests/test_webhook_duplicate_delivery_stress.py rather than using its fixture.
_INLINE_RAW_SCHEMA_SQL = "\n".join(
    path.read_text() for path in sorted(Path(_MIGRATION_DIR).glob("*.sql"))
)


async def _base_tables(conn) -> set[str]:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'pitwall' AND table_type = 'BASE TABLE'"
    )
    return {r["table_name"] for r in rows}


async def test_discover_matches_disk() -> None:
    records = discover_migrations(_MIGRATION_DIR)
    disk = sorted(p.name for p in _MIGRATION_DIR.glob("*.sql"))
    assert [r.filename for r in records] == disk
    assert len(records) == len(disk)
    # versions are unique and ascending in discovery order
    versions = [r.version for r in records]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


async def test_all_migrations_apply_clean(pg_pool) -> None:
    # pg_pool already applied every migration onto a fresh schema.
    async with pg_pool.acquire() as conn:
        tables = await _base_tables(conn)
    # 0001..0021 create well more than 10 base tables.
    assert len(tables) >= 10
    assert "capabilities" in tables
    assert "providers" in tables
    assert "workloads" in tables
    assert "leases" in tables
    assert "retention_runs" in tables


async def test_migrations_are_apply_once_not_idempotent(pg_pool) -> None:
    """Characterize: the raw migration SQL is apply-ONCE, not re-runnable.

    FINDING: 0001 uses bare ``CREATE TABLE`` (no IF NOT EXISTS), so re-running the
    full SQL on an already-migrated schema raises DuplicateTableError. Migrations
    are meant to be tracked/applied once, so this is acceptable — but it is pinned
    here so any future move to idempotent DDL is a conscious change. The
    drop->re-migrate path (next test) is the supported way to rebuild.
    """
    import asyncpg

    async with pg_pool.acquire() as conn:
        with pytest.raises(asyncpg.exceptions.DuplicateTableError):
            await conn.execute(_all_migration_sql())


async def test_drop_then_remigrate_reproduces_schema(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        original = await _base_tables(conn)
        await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
        await conn.execute(_all_migration_sql())
        rebuilt = await _base_tables(conn)
    assert rebuilt == original


async def test_ledger_restoration_marks_raw_schema_as_migrated(pg_pool) -> None:
    """A raw fixture rebuild leaves the CLI with a complete migration ledger."""
    expected = discover_migrations(_MIGRATION_DIR)
    async with pg_pool.acquire() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
        await conn.execute(_all_migration_sql())
        # _all_migration_sql raw-applied every discovered migration above.
        await _restore_migration_ledger(
            conn, applied_versions={migration.version for migration in expected}
        )
        recorded = await conn.fetch(
            "SELECT version, filename, checksum FROM pitwall.schema_migrations ORDER BY version"
        )

    assert [tuple(row.values()) for row in recorded] == [
        (migration.version, migration.filename, migration.checksum) for migration in expected
    ]


async def test_atomic_fixture_cleanup_rolls_back_if_ledger_restoration_fails(
    pg_pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed finalizer never leaves rebuilt relations without their ledger."""
    async with pg_pool.acquire() as conn:
        # pg_pool raw-applied every discovered migration for this test.
        await _restore_migration_ledger(
            conn,
            applied_versions={
                migration.version for migration in discover_migrations(_MIGRATION_DIR)
            },
        )
        original_tables = await _base_tables(conn)

        async def fail_ledger_insert(conn, *, applied_versions: set[str]) -> None:
            assert applied_versions
            raise RuntimeError("simulated ledger insert failure")

        monkeypatch.setattr(
            "tests.integration.conftest._restore_migration_ledger", fail_ledger_insert
        )
        with pytest.raises(RuntimeError, match="simulated ledger insert failure"):
            await _rebuild_schema_and_restore_ledger(conn)

        assert await _base_tables(conn) == original_tables
        assert await conn.fetchval("SELECT count(*) FROM pitwall.schema_migrations") == len(
            discover_migrations(_MIGRATION_DIR)
        )


async def test_raw_schema_ledger_restoration_allows_real_migrate(pg_pool) -> None:
    """Pin the CLI failure from an inline raw schema before restoring its ledger."""
    database_url = os.environ["PITWALL_TEST_DATABASE_URL"]

    async def run_migrate() -> tuple[int, str, str]:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = database_url
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pitwall",
            "db",
            "migrate",
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        return process.returncode or 0, stdout.decode(), stderr.decode()

    async with pg_pool.acquire() as conn:
        await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
        await conn.execute(_INLINE_RAW_SCHEMA_SQL)
        await conn.execute("DROP TABLE IF EXISTS pitwall.schema_migrations")

    red_code, red_stdout, red_stderr = await run_migrate()
    red_output = red_stdout + red_stderr
    assert red_code == 1
    assert 'relation "capabilities" already exists' in red_output

    async with pg_pool.acquire() as conn:
        # _INLINE_RAW_SCHEMA_SQL raw-applied every discovered migration above.
        await restore_migration_ledger(
            conn,
            applied_versions={
                migration.version for migration in discover_migrations(_MIGRATION_DIR)
            },
        )

    green_code, green_stdout, green_stderr = await run_migrate()
    green_output = green_stdout + green_stderr
    assert green_code == 0, green_output
    assert 'relation "capabilities" already exists' not in green_output
