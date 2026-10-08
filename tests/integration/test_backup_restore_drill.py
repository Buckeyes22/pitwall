"""Real Postgres backup/restore acceptance (J42), including URL-reserved credentials.

The drill runs the host's ``pg_dump``/``pg_restore``/``psql`` when their major version
matches the server. Otherwise it runs the server's own client tools inside the test
Postgres container, through PATH wrappers that stream the archive over stdin and stdout,
so a machine with the test stack never skips the drill.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import pytest

pytestmark = pytest.mark.integration

_TOOLS = ("psql", "pg_dump", "pg_restore")
# Inside the container the server listens on its own port; PGUSER, PGPASSWORD, and
# PGDATABASE pass through by name so credentials never reach argv.
_WRAPPER = """#!/bin/bash
set -euo pipefail
tool=$(basename "$0")
out=""
args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --file) out="$2"; shift 2 ;;
        --file=*) out="${1#--file=}"; shift ;;
        *) args+=("$1"); shift ;;
    esac
done
run=(docker exec -i -e PGHOST=127.0.0.1 -e PGPORT=5432 -e PGUSER -e PGPASSWORD -e PGDATABASE
     CONTAINER "$tool")
if [ "$tool" = pg_restore ] && [ ${#args[@]} -gt 0 ] && [ -f "${args[-1]}" ]; then
    archive="${args[-1]}"
    unset 'args[-1]'
    exec "${run[@]}" "${args[@]}" <"$archive"
fi
if [ -n "$out" ]; then
    exec "${run[@]}" "${args[@]}" >"$out"
fi
exec "${run[@]}" "${args[@]}"
"""


def _major(output: str) -> int:
    return int(output.split()[-1 if "(" not in output else 2].split(".")[0])


def _host_tools_match(server_major: int) -> bool:
    for tool in ("pg_dump", "pg_restore"):
        if shutil.which(tool) is None:
            return False
        version = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True)
        if _major(version.stdout) != server_major:
            return False
    return shutil.which("psql") is not None


def _container_tools(database_url: str, directory: Path) -> Path | None:
    """PATH directory whose client tools run in the container publishing the test port."""
    docker = shutil.which("docker")
    port = urlsplit(database_url).port
    if docker is None or port is None:
        return None
    found = subprocess.run(
        [docker, "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    if len(found) != 1:
        return None
    directory.mkdir()
    for tool in _TOOLS:
        wrapper = directory / tool
        wrapper.write_text(_WRAPPER.replace("CONTAINER", shlex.quote(found[0])))
        wrapper.chmod(0o755)
    return directory


async def _seed(conn: Any) -> None:
    await conn.execute(
        "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config) "
        "VALUES ('cap-drill', 'llm.drill', '1.0.0', 'llm', 'per_token', '{\"k\": 1}')"
    )
    await conn.execute(
        "INSERT INTO pitwall.providers (id, capability_id, name, provider_type, config, priority) "
        "VALUES ('prov-drill', 'cap-drill', 'Drill provider', 'pod_lease', '{}', 1)"
    )
    for index, state in enumerate(("queued", "completed", "failed")):
        await conn.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, "
            "submitted_at, cost_estimate_usd, cost_ceiling_usd) "
            "VALUES ($1, 'cap-drill', 'prov-drill', 'lease', $2, $3, $4, $4)",
            f"wkl-drill-{index}",
            state,
            datetime(2026, 9, 1, index, tzinfo=UTC),
            Decimal(f"{index}.250000"),
        )


async def _workloads_checksum(conn: Any) -> tuple[int, str]:
    rows = await conn.fetch("SELECT to_jsonb(w)::text AS row FROM pitwall.workloads w")
    canonical = sorted(str(row["row"]) for row in rows)
    hasher = hashlib.sha256()
    for row in canonical:
        hasher.update(hashlib.sha256(row.encode()).digest())
    return len(rows), hasher.hexdigest()


def _password_url(database_url: str, password: str) -> str:
    parsed = urlsplit(database_url)
    username = quote(parsed.username or "pitwall", safe="")
    host = parsed.hostname or "127.0.0.1"
    port = f":{parsed.port}" if parsed.port else ""
    return urlunsplit(
        (
            parsed.scheme,
            f"{username}:{quote(password, safe='')}@{host}{port}",
            parsed.path,
            parsed.query,
            "",
        )
    )


async def test_backup_restore_compares_all_tables_with_reserved_password(
    pg_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from pitwall.ops.backup_drill import PITWALL_TABLES, run_pit_restore_drill

    database_url = os.environ["PITWALL_TEST_DATABASE_URL"]
    reserved_password = "p@ss/word:with%reserved"
    async with pg_pool.acquire() as conn:
        server_major = int(str(await conn.fetchval("SHOW server_version")).split(".")[0])
        if not _host_tools_match(server_major):
            tools = _container_tools(database_url, tmp_path / "container-tools")
            if tools is None:
                pytest.skip(
                    f"no PostgreSQL {server_major} client tools on the host and no test "
                    "Postgres container publishing the test port"
                )
            monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
        await _seed(conn)
        source_workloads = await _workloads_checksum(conn)
        await conn.execute(f"ALTER ROLE pitwall PASSWORD '{reserved_password}'")
    monkeypatch.setenv("PITWALL_DATABASE_URL", _password_url(database_url, reserved_password))
    monkeypatch.setenv("PITWALL_DRILL_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    try:
        report = await run_pit_restore_drill(
            {"db_pool": pg_pool},
            target="integration-reserved-password",
        )
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute("ALTER ROLE pitwall PASSWORD 'pitwall'")

    assert report.passed, [(c.table, c.errors) for c in report.checks if c.errors]
    checked_tables = {check.table for check in report.checks}
    assert set(PITWALL_TABLES) <= checked_tables
    assert {"retention_runs", "webhook_subscriptions"} <= checked_tables
    assert all(check.passed for check in report.checks)
    assert not any("reserved" in error for error in report.errors)
    by_table = {check.table: check for check in report.checks}
    assert by_table["capabilities"].row_count >= 1
    assert by_table["providers"].row_count >= 1
    assert source_workloads[0] == 3
    workloads = by_table["workloads"]
    assert (workloads.row_count, workloads.checksum) == source_workloads
