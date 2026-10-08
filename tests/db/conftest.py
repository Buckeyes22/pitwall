"""Fixtures for migration/schema tests that read the migrated test database back.

``migrated_schema`` rebuilds ``pitwall`` from the complete on-disk migration ledger once per
module (and restores the matching ``schema_migrations`` rows), so a schema assertion always sees
what the full ledger produces, whatever an earlier test left behind. ``db_catalog`` is a read-only
view over it. Both need the test stack (``make up``); without ``PITWALL_TEST_DATABASE_URL`` they
skip, and modules that use them are integration-marked, so the fast suite never reaches them.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest

from tests.conftest import _INTEGRATION_DATABASE_USED
from tests.db.schema_catalog import Catalog, MigrationSandbox
from tests.integration.conftest import _rebuild_schema_and_restore_ledger


async def _rebuild(url: str) -> None:
    connection = await asyncpg.connect(url)
    try:
        await _rebuild_schema_and_restore_ledger(connection)
    finally:
        await connection.close()


@pytest.fixture(scope="module")
def migrated_schema(pytestconfig: pytest.Config) -> Iterator[str]:
    url = os.environ.get("PITWALL_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("PITWALL_TEST_DATABASE_URL not set")
    asyncio.run(_rebuild(url))
    pytestconfig.stash[_INTEGRATION_DATABASE_USED] = True
    yield url


@pytest.fixture
async def db_catalog(migrated_schema: str) -> AsyncIterator[Catalog]:
    connection = await asyncpg.connect(migrated_schema)
    try:
        yield Catalog(connection)
    finally:
        await connection.close()


@pytest.fixture
async def db_sandbox(migrated_schema: str) -> AsyncIterator[MigrationSandbox]:
    """A connection inside a transaction that is always rolled back (DDL included)."""
    connection = await asyncpg.connect(migrated_schema)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield MigrationSandbox(connection)
    finally:
        await transaction.rollback()
        await connection.close()
