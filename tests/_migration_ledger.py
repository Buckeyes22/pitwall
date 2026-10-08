"""Shared helpers that make a raw migration schema safe for the migration CLI."""

from __future__ import annotations

from pathlib import Path

import asyncpg

from pitwall.migrations import MigrationRecord, discover_migrations

_REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = _REPO_ROOT / "db" / "migrations"


def migration_records() -> list[MigrationRecord]:
    """Discover the migration ledger entries from the on-disk SQL files."""
    return discover_migrations(MIGRATION_DIR)


async def restore_migration_ledger(
    conn: asyncpg.Connection,
    *,
    applied_versions: set[str] | None = None,
) -> None:
    """Record the migrations this harness actually raw-applied.

    Only versions in ``applied_versions`` are stamped. When it is ``None`` the set is
    read back from the database's own ledger, so a migration file that was never
    executed is never claimed as applied — stamping one would make ``db migrate``
    skip it forever and leave the schema permanently behind the code.
    """
    migrations = migration_records()
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS pitwall.schema_migrations ("
        "version TEXT PRIMARY KEY, filename TEXT NOT NULL, checksum TEXT NOT NULL, "
        "applied_at TIMESTAMPTZ NOT NULL DEFAULT now());"
    )
    if applied_versions is None:
        rows = await conn.fetch("SELECT version FROM pitwall.schema_migrations")
        applied_versions = {row["version"] for row in rows}
    stampable = [
        (migration.version, migration.filename, migration.checksum)
        for migration in migrations
        if migration.version in applied_versions
    ]
    if not stampable:
        return
    await conn.executemany(
        "INSERT INTO pitwall.schema_migrations (version, filename, checksum) "
        "VALUES ($1, $2, $3) "
        "ON CONFLICT (version) DO UPDATE SET filename = EXCLUDED.filename, "
        "checksum = EXCLUDED.checksum, applied_at = now();",
        stampable,
    )
