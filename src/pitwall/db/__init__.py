"""Pitwall database CLI and asyncpg connection pool management.

CLI commands::

    pitwall db migrate   Apply pending migrations
    pitwall db reset     Drop the pitwall schema (destructive)
    pitwall db status    Show applied and pending migrations

Pool management::

    from pitwall.db import get_pool, close_pool, db_lifespan, get_db_pool

"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, TextIO
from urllib.parse import parse_qs, unquote, urlparse

import asyncpg
from fastapi import Depends, Request

from pitwall.cli.output import Output
from pitwall.migrations import detect_drift, discover_migrations, drift_summaries

if TYPE_CHECKING:
    from fastapi import FastAPI


_pool: asyncpg.Pool | None = None
_pool_lock: tuple[weakref.ref[asyncio.AbstractEventLoop], weakref.ref[asyncio.Lock]] | None = None


def _lock_for_running_loop() -> asyncio.Lock:
    """Return the pool lock for the running loop, replacing one bound to another loop.

    An ``asyncio.Lock`` binds to the first loop that contends for it, so the CLI and
    tests that call ``asyncio.run`` repeatedly need a fresh lock per loop. Both the loop and
    the lock are held weakly (a contended lock references its loop), so a closed loop is not
    kept alive; callers hold the lock strongly while they use it, so concurrent callers share
    one lock, and a dead reference counts as a different loop.
    """
    global _pool_lock
    loop = asyncio.get_running_loop()
    if _pool_lock is not None:
        lock = _pool_lock[1]()
        if lock is not None and _pool_lock[0]() is loop:
            return lock
    lock = asyncio.Lock()
    _pool_lock = (weakref.ref(loop), weakref.ref(lock))
    return lock


class DatabaseNotConfiguredError(RuntimeError):
    """Neither a DSN argument nor ``DATABASE_URL`` was provided."""


async def get_pool(
    dsn: str | None = None, *, min_size: int = 2, max_size: int = 10
) -> asyncpg.Pool:
    """Return a singleton asyncpg pool configured for PgBouncer transaction mode.

    The pool is created on the first call and reused for all subsequent calls.
    Uses ``statement_cache_size=0`` to avoid prepared statement issues with
    PgBouncer in transaction mode. Concurrent first callers share one creation.
    """
    global _pool
    if _pool is not None:
        return _pool
    async with _lock_for_running_loop():
        if _pool is None:
            if dsn is None:
                dsn = os.environ.get("DATABASE_URL")
            if not dsn:
                raise DatabaseNotConfiguredError(
                    "dsn or DATABASE_URL environment variable is required"
                )
            _pool = await asyncpg.create_pool(
                dsn=dsn,
                min_size=min_size,
                max_size=max_size,
                statement_cache_size=0,
                init=_register_codecs,
            )
        return _pool


async def close_pool() -> None:
    """Close the singleton pool if it has been initialized."""
    global _pool
    async with _lock_for_running_loop():
        if _pool is not None:
            await _pool.close()
            _pool = None


def _encode_jsonb(value: object) -> str:
    """Return the text Postgres parses as jsonb.

    Callers pass either a Python object (dict/list) or already-serialized JSON
    text (``model_dump_json()`` / ``json.dumps(...)``). ``json.dumps`` on an
    already-serialized string would double-encode it into a JSON *scalar*
    (``"{\\"k\\":1}"`` instead of ``{"k":1}``), which breaks SQL ``->>``
    introspection (e.g. the leases_active_readiness_signals CHECK) and makes the
    ``isinstance(..., dict)`` decoders fall back to None. Pass strings through
    untouched; serialize everything else.
    """
    return value if isinstance(value, str) else json.dumps(value)


async def _register_codecs(conn: asyncpg.Connection) -> None:
    """Register JSONB codec for dict/list round-trip compatibility."""
    await conn.set_type_codec(
        "jsonb",
        schema="pg_catalog",
        encoder=_encode_jsonb,
        decoder=lambda value: json.loads(value),
        format="text",
    )


@asynccontextmanager
async def db_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan context manager that sets up and tears down the DB pool.

    On startup: creates the pool and attaches it to ``app.state.pool`` — unless a
    pool was already injected before startup (e.g. a test fake), in which case it
    is honored as-is and left untouched on shutdown (the injector owns it). This
    keeps hermetic suites (schemathesis fuzz) from opening a real connection.
    On shutdown: closes the pool it created.
    """
    if getattr(app.state, "pool", None) is not None:
        yield
        return
    pool = await get_pool()
    app.state.pool = pool
    try:
        yield
    finally:
        await close_pool()


async def get_db_pool(request: Request) -> asyncpg.Pool:
    """FastAPI dependency that returns the database pool from ``app.state``.

    Raises:
        RuntimeError: If ``app.state.pool`` is not configured.
    """
    pool: asyncpg.Pool | None = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("Database pool not initialized. Did you forget to use db_lifespan?")
    return pool


DbPoolDep = Annotated[asyncpg.Pool, Depends(get_db_pool)]

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_TEST_POSTGRES_CONTAINER = "pitwall-test-postgres"
_DESTRUCTIVE_RESET_ENV_VAR = "PITWALL_ALLOW_DESTRUCTIVE_RESET"
_LOCAL_DATABASE_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MIGRATION_LOCK_ID = 5_780_473_640_160_951_153

_CREATE_MIGRATIONS_TABLE = (
    "CREATE TABLE IF NOT EXISTS pitwall.schema_migrations ("
    " version TEXT PRIMARY KEY,"
    " filename TEXT NOT NULL,"
    " checksum TEXT NOT NULL,"
    " applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ");"
)
_RECORD_MIGRATION_SQL = """
INSERT INTO pitwall.schema_migrations (version, filename, checksum)
VALUES ($1, $2, $3)
ON CONFLICT (version) DO UPDATE SET
    filename = EXCLUDED.filename,
    checksum = EXCLUDED.checksum,
    applied_at = now();
"""


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(1)
    return url


def _allow_destructive_reset_override() -> bool:
    return os.environ.get(_DESTRUCTIVE_RESET_ENV_VAR) == "1"


def _database_host_label(database_url: str) -> str:
    parsed = urlparse(database_url)
    parsed_host = parsed.hostname or ""
    query_hosts = parse_qs(parsed.query).get("host", [])
    if parsed_host and not _is_local_database_host(parsed_host):
        return unquote(parsed_host)
    for host in query_hosts:
        if not _is_local_database_host(host):
            return host
    if query_hosts:
        return query_hosts[0]
    return unquote(parsed_host or "local socket")


def _is_local_database_host(host: str) -> bool:
    normalized = unquote(host).strip().lower().rstrip(".")
    return not normalized or normalized.startswith("/") or normalized in _LOCAL_DATABASE_HOSTS


def _is_local_database_url(database_url: str) -> bool:
    parsed = urlparse(database_url)
    if not _is_local_database_host(parsed.hostname or ""):
        return False
    query_hosts = parse_qs(parsed.query).get("host", [])
    if query_hosts:
        return all(_is_local_database_host(host) for host in query_hosts)
    return True


def _reset_guard_error(database_url: str, *, force: bool) -> str | None:
    if _allow_destructive_reset_override():
        return None
    if not force:
        return (
            "Refusing destructive database reset. Re-run with --force for a local "
            f"database, or set {_DESTRUCTIVE_RESET_ENV_VAR}=1 for an explicit override."
        )
    if not _is_local_database_url(database_url):
        return (
            "Refusing destructive database reset for non-local database host "
            f"{_database_host_label(database_url)!r}. Use a local DATABASE_URL or set "
            f"{_DESTRUCTIVE_RESET_ENV_VAR}=1 to override."
        )
    return None


def _psql_available(database_url: str) -> str | None:
    psql = shutil.which("psql")
    if psql is None:
        return None
    probe = subprocess.run(
        [
            psql,
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            "SELECT 'pitwall_psql_probe';",
        ],
        capture_output=True,
        text=True,
        env=_libpq_env(database_url),
        timeout=10,
        check=False,
    )
    if probe.returncode == 0 and "pitwall_psql_probe" in probe.stdout:
        return psql
    return None


def _libpq_env(database_url: str) -> dict[str, str]:
    """Translate a PostgreSQL URL into libpq environment variables.

    This keeps the URL and password out of process arguments where they would
    be visible to other local users through process listings.
    """

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("DATABASE_URL must use the postgres or postgresql scheme")
    result = os.environ.copy()
    if parsed.hostname:
        result["PGHOST"] = unquote(parsed.hostname)
    if parsed.port:
        result["PGPORT"] = str(parsed.port)
    if parsed.username:
        result["PGUSER"] = unquote(parsed.username)
    if parsed.password:
        result["PGPASSWORD"] = unquote(parsed.password)
    if parsed.path and parsed.path != "/":
        result["PGDATABASE"] = unquote(parsed.path.removeprefix("/"))
    query = parse_qs(parsed.query)
    query_to_env = {
        "host": "PGHOST",
        "port": "PGPORT",
        "user": "PGUSER",
        "dbname": "PGDATABASE",
        "sslmode": "PGSSLMODE",
        "sslrootcert": "PGSSLROOTCERT",
        "sslcert": "PGSSLCERT",
        "sslkey": "PGSSLKEY",
    }
    for key, env_name in query_to_env.items():
        if values := query.get(key):
            result[env_name] = unquote(values[-1])
    return result


def _docker_psql(database_url: str) -> tuple[list[str], dict[str, str]] | None:
    """Build a ``docker exec psql`` command aimed at the database ``database_url`` names.

    Returns ``None`` unless the test Postgres container is running and the URL points
    at it: a local host on the port the container publishes for 5432. The password
    travels in the returned environment, never in the command line.
    """
    docker = shutil.which("docker")
    if docker is None:
        return None
    inspected = subprocess.run(
        [
            docker,
            "inspect",
            "-f",
            "{{.State.Running}} {{json .NetworkSettings.Ports}}",
            _TEST_POSTGRES_CONTAINER,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    running, _, ports_json = inspected.stdout.strip().partition(" ")
    if inspected.returncode != 0 or running != "true":
        return None
    parsed = urlparse(database_url)
    host = unquote(parsed.hostname or "")
    if not host or not _is_local_database_host(host):
        return None
    try:
        published = json.loads(ports_json).get("5432/tcp") or []
    except ValueError, AttributeError:
        return None
    url_port = str(parsed.port or 5432)
    if not any(str(binding.get("HostPort")) == url_port for binding in published):
        return None
    user = unquote(parsed.username or "") or "pitwall"
    database = unquote(parsed.path.removeprefix("/")) or "pitwall_test"
    env = os.environ.copy()
    command = [docker, "exec", "-i", "-e", "PGPASSWORD"]
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    else:
        env.pop("PGPASSWORD", None)
    command += [
        _TEST_POSTGRES_CONTAINER,
        "psql",
        "-h",
        host,
        "-p",
        "5432",
        "-U",
        user,
        "-d",
        database,
    ]
    return command, env


def _run_sql(
    database_url: str, sql: str, *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    psql = _psql_available(database_url)
    if psql is not None:
        return subprocess.run(
            [psql, "-v", "ON_ERROR_STOP=1"],
            input=sql,
            cwd=cwd or _REPO_ROOT,
            capture_output=True,
            text=True,
            env=_libpq_env(database_url),
            timeout=60,
            check=False,
        )
    docker_target = _docker_psql(database_url)
    if docker_target is not None:
        docker_cmd, docker_env = docker_target
        return subprocess.run(
            docker_cmd + ["-v", "ON_ERROR_STOP=1"],
            input=sql,
            cwd=cwd or _REPO_ROOT,
            capture_output=True,
            text=True,
            env=docker_env,
            timeout=60,
            check=False,
        )
    print(
        "No psql available, and DATABASE_URL does not point at the running "
        f"{_TEST_POSTGRES_CONTAINER} container. Install psql or point DATABASE_URL at it.",
        file=sys.stderr,
    )
    raise SystemExit(1)


async def _cmd_migrate_async(database_url: str, out: Output) -> int:
    migrations = discover_migrations()
    if not migrations:
        out.print("No migrations found.")
        out.emit()
        return 0

    ensure_sql = "CREATE SCHEMA IF NOT EXISTS pitwall;\n" + _CREATE_MIGRATIONS_TABLE
    pool = await get_pool(database_url, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1);", _MIGRATION_LOCK_ID)
            try:
                try:
                    await conn.execute(ensure_sql)
                except (
                    Exception
                ) as exc:  # reason: migration CLI boundary: print DB error and exit nonzero
                    out.print_error(f"Failed to ensure schema_migrations table:\n{exc}")
                    out.emit()
                    return 1
                applied_rows = await conn.fetch(
                    "SELECT version, checksum FROM pitwall.schema_migrations ORDER BY version;"
                )
                applied = {row["version"]: row["checksum"] for row in applied_rows}
                drifts = detect_drift(migrations, applied)
                if drifts:
                    out.print_error(
                        "\n".join(
                            f"Refusing to migrate because {line}"
                            for line in drift_summaries(drifts)
                        )
                    )
                    out.emit()
                    return 1

                pending = [m for m in migrations if m.version not in applied]
                if not pending:
                    out.print_success(f"All {len(migrations)} migrations already applied.")
                    out.emit()
                    return 0

                rows: list[list[Any]] = []
                for migration in pending:
                    sql = migration.sql
                    if not sql:
                        raise RuntimeError(
                            f"migration resource {migration.filename!r} did not contain SQL"
                        )
                    try:
                        async with conn.transaction():
                            await conn.execute(sql)
                            await conn.execute(
                                _RECORD_MIGRATION_SQL,
                                migration.version,
                                migration.filename,
                                migration.checksum,
                            )
                    except (
                        Exception
                    ) as exc:  # reason: migration CLI boundary: print DB error and exit nonzero
                        out.print_error(f"Failed to apply {migration.filename}:\n{exc}")
                        out.emit()
                        return 1
                    out.print(f"  applied {migration.filename}")
                    rows.append([migration.filename])
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1);", _MIGRATION_LOCK_ID)

        if rows:
            out.print_table("Applied Migrations", ["filename"], rows)
        out.print_success(f"Applied {len(pending)} migration(s).")
        out.add_json("applied", [m.filename for m in pending])
        out.emit()
        return 0
    finally:
        await close_pool()


def cmd_migrate(*, json_mode: bool = False) -> int:
    out = Output(json_mode)
    database_url = _database_url()
    try:
        return asyncio.run(_cmd_migrate_async(database_url, out))
    except (
        Exception
    ) as exc:  # reason: CLI boundary: any command failure becomes printed error + exit 1
        out.print_error(
            "Failed to run migrations through asyncpg. "
            f"Check DATABASE_URL and migration SQL compatibility:\n{exc}"
        )
        out.emit()
        return 1


async def _cmd_reset_async(database_url: str, out: Output) -> int:
    pool = await get_pool(database_url, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA IF EXISTS pitwall CASCADE;")
    finally:
        await close_pool()
    out.print_success("Dropped pitwall schema.")
    out.add_json("status", "dropped")
    out.emit()
    return 0


def cmd_reset(*, json_mode: bool = False, force: bool = False) -> int:
    out = Output(json_mode)
    database_url = _database_url()
    guard_error = _reset_guard_error(database_url, force=force)
    if guard_error is not None:
        out.print_error(guard_error)
        out.emit()
        return 1
    try:
        return asyncio.run(_cmd_reset_async(database_url, out))
    except Exception as exc:  # reason: CLI boundary: any command failure becomes printed error
        out.print_error(f"Reset failed:\n{exc}")
        out.emit()
        return 1


async def _read_applied_migrations(database_url: str) -> dict[str, str]:
    """Read the migration ledger with SELECTs only; a missing ledger means nothing applied."""
    pool = await get_pool(database_url, min_size=1, max_size=1)
    try:
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT to_regclass('pitwall.schema_migrations')") is None:
                return {}
            rows = await conn.fetch(
                "SELECT version, checksum FROM pitwall.schema_migrations ORDER BY version;"
            )
    finally:
        await close_pool()
    return {row["version"]: row["checksum"] for row in rows}


def cmd_status(*, json_mode: bool = False) -> int:
    out = Output(json_mode)
    database_url = _database_url()
    migrations = discover_migrations()
    if not migrations:
        out.print("No migrations found.")
        out.emit()
        return 0

    try:
        applied = asyncio.run(_read_applied_migrations(database_url))
    except Exception as exc:  # reason: CLI boundary: a failed connection is an error, not "pending"
        out.print_error(f"Could not connect to the database or read migrations:\n{exc}")
        out.emit()
        return 1

    drifts = detect_drift(migrations, applied)
    drifted = {entry.version for entry in drifts}
    applied_count = 0
    pending_count = 0
    rows: list[list[Any]] = []
    for m in migrations:
        if m.version in drifted:
            applied_count += 1
            rows.append([m.filename, "drift"])
        elif m.version in applied:
            applied_count += 1
            rows.append([m.filename, "applied"])
        else:
            pending_count += 1
            rows.append([m.filename, "pending"])

    if rows:
        out.print_table("Migrations", ["filename", "status"], rows)
    out.print_success(f"{applied_count} applied, {pending_count} pending, {len(migrations)} total.")
    out.add_json("applied_count", applied_count)
    out.add_json("pending_count", pending_count)
    out.add_json("total", len(migrations))
    if drifts:
        out.print_error("\n".join(line[:1].upper() + line[1:] for line in drift_summaries(drifts)))
        out.add_json("drifted", [entry.filename for entry in drifts if entry.filename])
        out.add_json(
            "missing_from_package",
            [entry.version for entry in drifts if entry.kind == "missing_from_package"],
        )
        out.emit()
        return 1
    out.emit()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    json_flag = "--json" in args
    if json_flag:
        args = [a for a in args if a != "--json"]
    if any(arg in ("-h", "--help", "help") for arg in args):
        _usage(stream=sys.stdout)
        return 0
    if not args:
        _usage(stream=sys.stderr)
        return 1
    command = args[0]
    reset_force = False
    if command == "reset" and "--force" in args[1:]:
        reset_force = True
        args = [command, *(a for a in args[1:] if a != "--force")]
    dispatch = {
        "migrate": lambda: cmd_migrate(json_mode=json_flag),
        "reset": lambda: cmd_reset(json_mode=json_flag, force=reset_force),
        "status": lambda: cmd_status(json_mode=json_flag),
    }
    handler = dispatch.get(command)
    if handler is None:
        print(f"Unknown command: {command}", file=sys.stderr)
        _usage(stream=sys.stderr)
        return 1
    if extra := args[1:]:
        # Match argparse: a mistyped flag must not be silently ignored.
        print(
            f"pitwall db {command}: error: unrecognized arguments: {' '.join(extra)}",
            file=sys.stderr,
        )
        _usage(stream=sys.stderr)
        return 2
    return handler()


def _usage(*, stream: TextIO) -> None:
    print("Usage: pitwall db {migrate|reset [--force]|status} [--json]", file=stream)


__all__ = [
    "DatabaseNotConfiguredError",
    "cmd_migrate",
    "cmd_reset",
    "cmd_status",
    "main",
    "get_pool",
    "close_pool",
    "db_lifespan",
    "get_db_pool",
    "DbPoolDep",
]
