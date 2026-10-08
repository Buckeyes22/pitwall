"""The ledger restore must never claim a migration ran when it did not.

Adding a migration and running the suite once made `db migrate` report "All
migrations already applied" while the DDL had never executed: the finalizer
stamped every file on disk. That is silent, permanent schema drift, and the only
symptom is a constraint failing in production.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from tests import _migration_ledger
from tests._migration_ledger import MIGRATION_DIR, migration_records, restore_migration_ledger


def _all_migration_sql() -> str:
    """Build the raw schema used by the live-ledger regression."""
    return "\n".join(
        (MIGRATION_DIR / migration.filename).read_text() for migration in migration_records()
    )


@pytest.fixture
async def pitwall_pool() -> AsyncIterator[asyncpg.Pool]:
    """Provide a raw-applied schema whose ledger precisely records that work."""
    database_url = os.getenv("PITWALL_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PITWALL_TEST_DATABASE_URL not set")

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
    applied_versions = {migration.version for migration in migration_records()}
    migrations_sql = _all_migration_sql()
    try:
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
            await conn.execute(migrations_sql)
            await restore_migration_ledger(conn, applied_versions=applied_versions)
        yield pool
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE")
            await conn.execute(migrations_sql)
            await restore_migration_ledger(conn, applied_versions=applied_versions)
        await pool.close()


def test_restore_accepts_the_set_of_migrations_actually_applied() -> None:
    signature = inspect.signature(_migration_ledger.restore_migration_ledger)
    assert "applied_versions" in signature.parameters, (
        "restore_migration_ledger must be told which migrations really ran"
    )


def test_restore_does_not_blindly_stamp_every_file_on_disk() -> None:
    source = inspect.getsource(_migration_ledger.restore_migration_ledger)
    assert "applied_versions" in source, "the insert must be filtered by what actually ran"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_unapplied_migration_is_never_stamped(pitwall_pool) -> None:
    async with pitwall_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM pitwall.schema_migrations WHERE version = $1",
            "0027_config_audit_lease_actions",
        )
        await restore_migration_ledger(conn)
        row = await conn.fetchval(
            "SELECT count(*) FROM pitwall.schema_migrations WHERE version = $1",
            "0027_config_audit_lease_actions",
        )
        assert row == 0, "restore re-stamped a migration it did not apply"
        await conn.execute(
            "INSERT INTO pitwall.schema_migrations (version, filename, checksum) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            "0027_config_audit_lease_actions",
            "0027_config_audit_lease_actions.sql",
            "448e51a4d86b169cb331e669f31d987759f6e2c64b1957e53fad73785afa7dcf",
        )
