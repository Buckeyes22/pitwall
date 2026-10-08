"""Session-import environment normalization for hermetic pytest collection."""

from __future__ import annotations

import os
import socket
import subprocess
from typing import Any, Protocol
from urllib.parse import urlparse

# Remove these before any ``pitwall`` import so an operator shell cannot enable
# request authentication or middleware in tests. The config aliases cover auth,
# inbound rate limiting, and budget-breach kill-switch behavior; the remaining
# API/webhook values are read directly by their request middleware.
HERMETIC_REQUEST_BEHAVIOR_ENV_VARS = (
    "PITWALL_API_TOKEN",
    "PITWALL_ENDPOINT_KEY",
    "PITWALL_ADMIN_SECRET",
    "PITWALL_API_SCOPED_TOKENS",
    "PITWALL_INBOUND_RATE_LIMIT",
    "PITWALL_API_MAX_BODY_BYTES",
    "PITWALL_WEBHOOK_SECRET",
    "PITWALL_WEBHOOK_PREVIOUS_SECRETS",
    "PITWALL_WEBHOOK_MAX_BODY_BYTES",
    "PITWALL_WEBHOOK_RATE_LIMIT",
    "PITWALL_BUDGET_BREACH_KILL_MODE",
    "PITWALL_BUDGET_BREACH_KILL_HEADROOM_FLOOR_USD",
)
for _env_var in HERMETIC_REQUEST_BEHAVIOR_ENV_VARS:
    os.environ.pop(_env_var, None)

# Import-time placeholder only: port 1 is unroutable, so a test that reaches for the
# database without the ``integration`` marker fails loudly instead of touching the
# shared test database. Integration tests get the real URL from
# PITWALL_TEST_DATABASE_URL (see ``apply_postgres_policy``).
HERMETIC_PLACEHOLDER_DATABASE_URL = "postgresql://placeholder@127.0.0.1:1/placeholder"

# These module-level defaults apply before pytest collects test modules.
if not os.environ.get("RUNPOD_API_KEY"):
    os.environ["RUNPOD_API_KEY"] = "test-key"
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = HERMETIC_PLACEHOLDER_DATABASE_URL
if not os.environ.get("REDIS_URL"):
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"

_POSTGRES_PORTS = frozenset({5432, 5444})
# The placeholder DSN's port. Nothing listens there, so a connect to it (for example a
# best-effort database read that must degrade) cannot reach a real database.
_UNROUTABLE_PORT = 1


def _dsn_port(dsn: Any) -> int | None:
    if not isinstance(dsn, str):
        return None
    try:
        return urlparse(dsn).port
    except ValueError:
        return None


class _KeywordNode(Protocol):
    nodeid: str

    def get_closest_marker(self, name: str) -> Any: ...


class _Patcher(Protocol):
    def setattr(self, target: Any, name: str, value: Any) -> None: ...

    def setenv(self, name: str, value: str) -> None: ...


def _refuse(test_id: str, what: str) -> None:
    import pytest

    pytest.fail(
        f"{test_id} attempted a Postgres access ({what}) in the non-integration suite; "
        "mark it integration (pytest.mark.integration) so it only runs under make test-int.",
        pytrace=False,
    )


def _argv_touches_postgres(args: Any) -> str | None:
    if isinstance(args, (str, bytes, os.PathLike)):
        argv = str(os.fsdecode(args)).split()
    else:
        argv = [str(os.fsdecode(part)) for part in args]
    names = [os.path.basename(part) for part in argv]
    if "psql" in names:
        return "psql"
    if "docker" in names and "exec" in argv:
        return "docker exec"
    return None


def apply_postgres_policy(node: _KeywordNode, patcher: _Patcher) -> None:
    """Refuse Postgres access for non-integration tests; wire the URL for integration ones."""
    # A marker, not item.keywords: keywords also hold parent directory names.
    if node.get_closest_marker("integration") is not None:
        test_url = os.environ.get("PITWALL_TEST_DATABASE_URL")
        if test_url:
            patcher.setenv("DATABASE_URL", test_url)
        return

    import asyncpg
    import asyncpg.connection
    import asyncpg.pool

    test_id = node.nodeid

    def guard_asyncpg(original: Any, what: str) -> Any:
        def guarded(*args: Any, **kwargs: Any) -> Any:
            dsn = kwargs.get("dsn") or (args[0] if args else None)
            if _dsn_port(dsn) != _UNROUTABLE_PORT:
                _refuse(test_id, what)
            return original(*args, **kwargs)

        return guarded

    patcher.setattr(asyncpg, "connect", guard_asyncpg(asyncpg.connect, "asyncpg connect"))
    patcher.setattr(
        asyncpg.connection, "connect", guard_asyncpg(asyncpg.connection.connect, "asyncpg connect")
    )
    patcher.setattr(
        asyncpg, "create_pool", guard_asyncpg(asyncpg.create_pool, "asyncpg create_pool")
    )
    patcher.setattr(
        asyncpg.pool, "create_pool", guard_asyncpg(asyncpg.pool.create_pool, "asyncpg create_pool")
    )

    def guard_socket(original: Any) -> Any:
        def guarded(self: socket.socket, address: Any) -> Any:
            if (
                self.family in (socket.AF_INET, socket.AF_INET6)
                and isinstance(address, tuple)
                and len(address) >= 2
                and address[1] in _POSTGRES_PORTS
            ):
                _refuse(test_id, f"TCP connect to port {address[1]}")
            return original(self, address)

        return guarded

    patcher.setattr(socket.socket, "connect", guard_socket(socket.socket.connect))
    patcher.setattr(socket.socket, "connect_ex", guard_socket(socket.socket.connect_ex))

    original_popen_init = subprocess.Popen.__init__

    def guarded_popen_init(self: subprocess.Popen[Any], args: Any, *a: Any, **kw: Any) -> None:
        what = _argv_touches_postgres(args)
        if what is not None:
            _refuse(test_id, what)
        original_popen_init(self, args, *a, **kw)

    patcher.setattr(subprocess.Popen, "__init__", guarded_popen_init)
