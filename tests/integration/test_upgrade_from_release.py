"""J43: a database built by the last release upgrades in place with the working tree.

A fresh database gets the migration set of the ``v0.1.0a2`` release, applied by the migrate
command with discovery pointed at those files (the migrate code and the ledger shape are
unchanged since the release), and rows are seeded through that release's schema. The release's
files are the working tree's, each checked against the SHA-256 recorded in
``tests/fixtures/release_v0.1.0a2_migrations.json``, so a released migration edited later fails
here and the test needs no git history. The
working tree's ``pitwall db migrate`` then applies each later migration exactly once,
``db status`` reports nothing pending, a second migrate applies nothing, and every
seeded value is intact.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
RELEASE = "v0.1.0a2"
RELEASE_MANIFEST = ROOT / "tests" / "fixtures" / "release_v0.1.0a2_migrations.json"
PITWALL = ROOT / ".venv" / "bin" / "pitwall"
# The seeded rows, keyed by table, compared column by column after the upgrade.
SEED_TABLES = ("capabilities", "providers", "workloads", "leases", "config_audit")

_RELEASE_MIGRATE = """
import sys
import pitwall.db as db
from pitwall.migrations import discover_migrations
db.discover_migrations = lambda: discover_migrations(sys.argv[1])
sys.exit(db.cmd_migrate(json_mode=True))
"""


def _url_for(database: str) -> str:
    parsed = urlsplit(os.environ["PITWALL_TEST_DATABASE_URL"])
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", "", ""))


@pytest.fixture
async def fresh_database() -> AsyncIterator[str]:
    if not os.environ.get("PITWALL_TEST_DATABASE_URL"):
        pytest.skip("PITWALL_TEST_DATABASE_URL not set")
    name = f"pitwall_j43_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(os.environ["PITWALL_TEST_DATABASE_URL"])
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        yield _url_for(name)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


def _release_migrations(directory: Path) -> list[str]:
    manifest = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    directory.mkdir()
    for filename, checksum in manifest.items():
        sql = (ROOT / "db" / "migrations" / filename).read_bytes()
        assert hashlib.sha256(sql).hexdigest() == checksum, f"{filename} changed since {RELEASE}"
        (directory / filename).write_bytes(sql)
    return sorted(manifest)


def _pitwall(database_url: str, *args: str) -> dict[str, object]:
    env = {**os.environ, "DATABASE_URL": database_url}
    result = subprocess.run(
        [str(PITWALL), *args], env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, (args, result.stdout[-800:], result.stderr[-800:])
    return dict(json.loads(result.stdout))


async def _seed(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config) "
        "VALUES ('cap-j43', 'llm.upgrade', '1.0.0', 'llm', 'per_token', '{\"max_tokens\": 64}')"
    )
    await conn.execute(
        "INSERT INTO pitwall.providers (id, capability_id, name, provider_type, config, priority) "
        "VALUES ('prov-j43', 'cap-j43', 'Upgrade provider', 'pod_lease', "
        '\'{"gpu": "L4"}\', 2)'
    )
    await conn.execute(
        "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, "
        "submitted_at, completed_at, cost_estimate_usd, cost_actual_usd) "
        "VALUES ('wkl-j43', 'cap-j43', 'prov-j43', 'inference', 'completed', "
        "'2026-01-02T03:04:05Z', '2026-01-02T03:05:05Z', 0.125, 0.1)"
    )
    await conn.execute(
        "INSERT INTO pitwall.leases (id, provider_id, runpod_pod_id, state, created_at, "
        "expires_at, renewal_policy) VALUES ('lease-j43', 'prov-j43', 'pod-j43', 'stopped', "
        "'2026-01-02T03:04:05Z', '2026-01-02T04:04:05Z', 'manual')"
    )
    await conn.execute(
        "INSERT INTO pitwall.config_audit (entity_type, entity_id, action, new_value, actor, "
        "change_reason) VALUES ('capability', 'cap-j43', 'create', '{\"name\": \"llm.upgrade\"}', "
        "'system', 'seeded by the last release')"
    )


async def _snapshot(conn: asyncpg.Connection) -> dict[str, list[dict[str, object]]]:
    tables: dict[str, list[dict[str, object]]] = {}
    for table in SEED_TABLES:
        rows = await conn.fetch(f"SELECT to_jsonb(t)::text AS row FROM pitwall.{table} t")
        tables[table] = sorted((json.loads(row["row"]) for row in rows), key=json.dumps)
    return tables


async def test_last_release_database_upgrades_in_place(fresh_database: str, tmp_path: Path) -> None:
    release_files = _release_migrations(tmp_path / "release-migrations")
    current_files = sorted(path.name for path in (ROOT / "db" / "migrations").glob("*.sql"))
    later = [name for name in current_files if name not in release_files]
    assert release_files == current_files[: len(release_files)]
    assert later, "the working tree adds no migrations after the release"

    release = subprocess.run(
        [sys.executable, "-c", _RELEASE_MIGRATE, str(tmp_path / "release-migrations")],
        env={**os.environ, "DATABASE_URL": fresh_database},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert release.returncode == 0, release.stdout[-800:] + release.stderr[-800:]
    assert json.loads(release.stdout)["applied"] == release_files

    conn = await asyncpg.connect(fresh_database)
    try:
        await _seed(conn)
        before = await _snapshot(conn)
    finally:
        await conn.close()
    assert all(before[table] for table in SEED_TABLES)

    assert _pitwall(fresh_database, "db", "migrate", "--json")["applied"] == later
    status = _pitwall(fresh_database, "db", "status", "--json")
    assert (status["pending_count"], status["applied_count"]) == (0, len(current_files))
    assert _pitwall(fresh_database, "db", "migrate", "--json").get("applied") is None

    conn = await asyncpg.connect(fresh_database)
    try:
        ledger = await conn.fetch("SELECT filename FROM pitwall.schema_migrations")
        after = await _snapshot(conn)
    finally:
        await conn.close()
    assert sorted(row["filename"] for row in ledger) == current_files
    for table in SEED_TABLES:
        assert len(after[table]) == len(before[table]), table
        for old, new in zip(before[table], after[table], strict=True):
            # Later migrations may add columns; every value the release stored survives.
            assert {key: new[key] for key in old} == old, table
