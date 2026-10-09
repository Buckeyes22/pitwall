"""One actionable line for the runtime conditions every operator hits first.

A missing ``DATABASE_URL``, an unreachable Postgres, a missing budget setting, and an
unwritable home directory each map to a single sentence that names the fix. The sentences are
fixed text: only the database host and port from ``DATABASE_URL`` and the path that could not
be written are ever interpolated, never a password, a query, or provider text.

The machine-readable error codes stay where an API, MCP, or script contract uses them; the
human reason follows the code (``code: reason``).
"""

from __future__ import annotations

import errno
import os
import socket
import sys
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from pitwall.cli.output import Output

_DB_NOT_SET = (
    "DATABASE_URL is not set. Set it to your Postgres connection URL "
    "(the local stack from `make up` provides one), then run `pitwall db migrate`."
)
_DB_REFUSED = (
    "Postgres refused the connection; check the credentials and database name in DATABASE_URL."
)
_UNWRITABLE_HINT = "Set HOME (or XDG_CONFIG_HOME / XDG_STATE_HOME) to a writable directory."


def _chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def database_endpoint(url: str | None = None) -> str:
    """``host:port`` of the configured database, or ``the configured database``."""
    raw = url if url is not None else os.environ.get("DATABASE_URL", "")
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "the configured database"
    if not host:
        return "the configured database"
    return f"{host}:{port or 5432}"


def _database_unreachable() -> str:
    return (
        f"cannot reach Postgres at {database_endpoint()}. "
        "Start it (the local stack: `make up`) and check DATABASE_URL."
    )


def _is_unwritable(exc: BaseException) -> bool:
    return (
        isinstance(exc, OSError)
        and exc.filename is not None
        and (isinstance(exc, PermissionError) or exc.errno in {errno.EROFS, errno.EACCES})
    )


def _class_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def runtime_reason(exc: BaseException) -> str | None:
    """The one-line fix for *exc*, or None when it is not a known runtime condition.

    Classes are recognised by name so this module imports neither the database layer nor its
    driver: a CLI module must not depend on either.
    """
    for item in _chain(exc):
        if _is_unwritable(item):
            assert isinstance(item, OSError)
            return f"cannot write {item.filename}: {item.strerror}. {_UNWRITABLE_HINT}"
    for item in _chain(exc):
        names = _class_names(item)
        if "DatabaseNotConfiguredError" in names:
            return _DB_NOT_SET
        if getattr(type(item), "error_code", None) == "budget_not_configured":
            return f"{item}. Export it as a positive USD amount, for example 100."
    for item in _chain(exc):
        names = _class_names(item)
        asyncpg_error = type(item).__module__.startswith("asyncpg")
        if (
            isinstance(item, ConnectionError | TimeoutError | socket.gaierror)
            or (isinstance(item, OSError) and item.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH})
            or (asyncpg_error and "PostgresConnectionError" in names)
        ):
            return _database_unreachable()
        if asyncpg_error and names & {"InvalidPasswordError", "InvalidCatalogNameError"}:
            return _DB_REFUSED
    return None


def failure_line(code: str, exc: BaseException) -> str:
    """``code: reason`` for a known runtime condition, else just *code*."""
    reason = runtime_reason(exc)
    return f"{code}: {reason}" if reason else code


def report_failure(
    out: Output,
    code: str,
    exc: BaseException,
    *,
    extra: dict[str, str] | None = None,
    fallback: str | None = None,
) -> None:
    """Report a failed command: the stable *code*, then the human reason when one is known.

    *extra* adds fields to the JSON payload; *fallback* is the human line when no reason is
    known (default ``error: <code>``).
    """
    reason = runtime_reason(exc)
    payload: dict[str, str] = {"error": code, **(extra or {})}
    if reason:
        payload["reason"] = reason
    out.set_json(payload)
    if reason:
        out.print_error_line(f"error: {code}: {reason}")
    else:
        out.print_error_line(fallback or f"error: {code}")


def database_preflight(timeout: float = 5.0) -> str | None:
    """Open and close one TCP connection to the ``DATABASE_URL`` host; return the fix on failure.

    Only reachability is checked: credentials and the database name are the server's to judge.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return _DB_NOT_SET
    try:
        parts = urlsplit(dsn)
        host, port = parts.hostname, parts.port or 5432
    except ValueError:
        return None
    if not host or dsn.startswith(("postgres:///", "postgresql:///")):
        return None  # a unix-socket or default-host DSN: leave it to the driver
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return None
    except OSError:
        return _database_unreachable()


def exit_with(service: str, reason: str, code: int = 1) -> NoReturn:
    """Print ``service: reason`` to stderr and exit non-zero."""
    sys.stderr.write(f"{service}: {reason}\n")
    sys.stderr.flush()
    raise SystemExit(code)


__all__ = [
    "database_endpoint",
    "database_preflight",
    "exit_with",
    "failure_line",
    "report_failure",
    "runtime_reason",
]
