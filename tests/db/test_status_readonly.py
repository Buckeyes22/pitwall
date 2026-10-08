"""A-28: ``pitwall db status`` is read-only, reports connection errors, and detects drift."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall import db
from pitwall.migrations import discover_migrations
from tests.conftest import make_asyncpg_pool


def _write(path: Path, name: str, sql: str) -> str:
    target = path / name
    target.write_text(sql)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def _patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pool: Any) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@remote.example/db")
    monkeypatch.setattr(db, "discover_migrations", lambda: discover_migrations(tmp_path))
    monkeypatch.setattr(db, "get_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(db, "close_pool", AsyncMock())


def test_status_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _write(tmp_path, "0001_first.sql", "SELECT 1;")
    _write(tmp_path, "0002_second.sql", "SELECT 2;")
    pool = make_asyncpg_pool(
        fetchval="pitwall.schema_migrations",
        fetch=[{"version": "0001_first", "checksum": first}],
    )
    _patch(monkeypatch, tmp_path, pool)
    monkeypatch.setattr(db, "_run_sql", lambda *a, **k: pytest.fail("psql path used"))

    assert db.cmd_status() == 0

    assert "1 applied, 1 pending, 2 total" in capsys.readouterr().out
    pool.conn.execute.assert_not_awaited()
    statements = [call.args[0] for call in pool.conn.fetch.await_args_list]
    statements += [call.args[0] for call in pool.conn.fetchval.await_args_list]
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_status_without_ledger_table_reports_all_pending_without_creating_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    pool = make_asyncpg_pool(fetchval=None)
    _patch(monkeypatch, tmp_path, pool)

    assert db.cmd_status() == 0

    assert "0 applied, 1 pending, 1 total" in capsys.readouterr().out
    pool.conn.execute.assert_not_awaited()
    pool.conn.fetch.assert_not_awaited()


def test_connection_error_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    _patch(monkeypatch, tmp_path, make_asyncpg_pool())
    monkeypatch.setattr(db, "get_pool", AsyncMock(side_effect=OSError("connection refused")))

    assert db.cmd_status() == 1

    captured = capsys.readouterr()
    assert "connect" in captured.err.lower()
    assert "connection refused" in captured.err
    assert "pending" not in captured.out


def test_drift_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "0001_first.sql", "SELECT 1;")
    pool = make_asyncpg_pool(
        fetchval="pitwall.schema_migrations",
        fetch=[{"version": "0001_first", "checksum": "0" * 64}],
    )
    _patch(monkeypatch, tmp_path, pool)

    assert db.cmd_status() == 1

    err = capsys.readouterr().err
    assert "0001_first.sql" in err
    assert "checksum" in err.lower()


def test_drift_message_keeps_the_filename_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "0001_Init.sql", "SELECT 1;")
    pool = make_asyncpg_pool(
        fetchval="pitwall.schema_migrations",
        fetch=[{"version": "0001_Init", "checksum": "0" * 64}],
    )
    _patch(monkeypatch, tmp_path, pool)

    assert db.cmd_status() == 1

    err = capsys.readouterr().err
    assert "Applied migration checksums changed: 0001_Init.sql" in err


def test_applied_version_missing_from_the_package_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    second = _write(tmp_path, "0002_second.sql", "SELECT 2;")
    pool = make_asyncpg_pool(
        fetchval="pitwall.schema_migrations",
        fetch=[
            {"version": "0001", "checksum": "a" * 64},
            {"version": "0002_second", "checksum": second},
        ],
    )
    _patch(monkeypatch, tmp_path, pool)

    assert db.cmd_status(json_mode=True) == 1

    payload = json.loads(capsys.readouterr().out)
    assert "Migrations applied but not packaged: 0001" in payload["error"]
    assert payload["missing_from_package"] == ["0001"]
    assert payload["drifted"] == []


def test_both_drift_kinds_are_reported_together_in_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path, "0002_second.sql", "SELECT 2;")
    pool = make_asyncpg_pool(
        fetchval="pitwall.schema_migrations",
        fetch=[
            {"version": "0001", "checksum": "a" * 64},
            {"version": "0002_second", "checksum": "0" * 64},
        ],
    )
    _patch(monkeypatch, tmp_path, pool)

    assert db.cmd_status(json_mode=True) == 1

    error = json.loads(capsys.readouterr().out)["error"]
    assert "checksums changed: 0002_second.sql" in error
    assert "applied but not packaged: 0001" in error
