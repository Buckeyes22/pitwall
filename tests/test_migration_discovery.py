"""Tests for migration discovery, checksums, and drift detection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pitwall import db
from pitwall.migrations import (
    MigrationRecord,
    detect_drift,
    discover_migrations,
)
from tests.conftest import make_asyncpg_pool

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_DIR = _REPO_ROOT / "db" / "migrations"


class TestDiscoverMigrations:
    def test_default_discovery_reads_packaged_or_checkout_resources(self) -> None:
        records = discover_migrations()
        assert records
        assert all(record.sql.strip() for record in records)

    def test_discovers_all_sql_files(self) -> None:
        records = discover_migrations(_MIGRATION_DIR)
        filenames = [r.filename for r in records]
        assert filenames == sorted(filenames), "migrations must be sorted lexically"

    def test_returns_migration_records(self) -> None:
        records = discover_migrations(_MIGRATION_DIR)
        assert len(records) >= 10
        for rec in records:
            assert isinstance(rec, MigrationRecord)
            assert rec.filename.endswith(".sql")
            assert rec.version == Path(rec.filename).stem
            assert len(rec.checksum) == 64

    def test_checksum_is_sha256_of_file_contents(self, tmp_path: Path) -> None:
        sql_file = tmp_path / "0001_test.sql"
        sql_file.write_text("CREATE TABLE foo (id int);")
        records = discover_migrations(tmp_path)
        expected = hashlib.sha256(sql_file.read_bytes()).hexdigest()
        assert records[0].checksum == expected

    def test_sorts_lexically_by_filename(self, tmp_path: Path) -> None:
        (tmp_path / "0003_charlie.sql").write_text("C;")
        (tmp_path / "0001_alpha.sql").write_text("A;")
        (tmp_path / "0002_bravo.sql").write_text("B;")
        records = discover_migrations(tmp_path)
        versions = [r.version for r in records]
        assert versions == ["0001_alpha", "0002_bravo", "0003_charlie"]

    def test_ignores_non_sql_files(self, tmp_path: Path) -> None:
        (tmp_path / "0001_valid.sql").write_text("SELECT 1;")
        (tmp_path / "notes.txt").write_text("not a migration")
        records = discover_migrations(tmp_path)
        assert len(records) == 1
        assert records[0].filename == "0001_valid.sql"

    def test_raises_on_missing_directory(self) -> None:
        with pytest.raises(FileNotFoundError):
            discover_migrations("/nonexistent/path/migrations")

    def test_empty_directory_returns_empty_list(self, tmp_path: Path) -> None:
        assert discover_migrations(tmp_path) == []


class TestDetectDrift:
    def test_no_drift_when_checksums_match(self) -> None:
        records = [
            MigrationRecord("0001", "0001.sql", "abc123"),
            MigrationRecord("0002", "0002.sql", "def456"),
        ]
        applied = {"0001": "abc123", "0002": "def456"}
        assert detect_drift(records, applied) == []

    def test_detects_changed_checksum(self) -> None:
        records = [
            MigrationRecord("0001", "0001.sql", "newhash"),
        ]
        applied = {"0001": "oldhash"}
        drifts = detect_drift(records, applied)
        assert len(drifts) == 1
        assert drifts[0].version == "0001"
        assert drifts[0].recorded_checksum == "oldhash"
        assert drifts[0].current_checksum == "newhash"

    def test_new_migration_is_not_drift(self) -> None:
        records = [
            MigrationRecord("0001", "0001.sql", "abc"),
            MigrationRecord("0002", "0002.sql", "def"),
        ]
        applied = {"0001": "abc"}
        drifts = detect_drift(records, applied)
        assert drifts == []

    def test_multiple_drifts(self) -> None:
        records = [
            MigrationRecord("0001", "0001.sql", "a1"),
            MigrationRecord("0002", "0002.sql", "b2"),
            MigrationRecord("0003", "0003.sql", "c3"),
        ]
        applied = {"0001": "a1", "0002": "changed", "0003": "also_changed"}
        drifts = detect_drift(records, applied)
        assert len(drifts) == 2
        drifted_versions = {d.version for d in drifts}
        assert drifted_versions == {"0002", "0003"}

    def test_applied_version_missing_from_the_package_is_drift(self) -> None:
        records = [MigrationRecord("0002", "0002.sql", "b")]
        applied = {"0001": "a", "0002": "b"}
        drifts = detect_drift(records, applied)
        assert len(drifts) == 1
        assert drifts[0].version == "0001"
        assert drifts[0].kind == "missing_from_package"
        assert drifts[0].recorded_checksum == "a"
        assert drifts[0].filename is None
        assert drifts[0].current_checksum is None

    def test_changed_checksum_keeps_its_kind(self) -> None:
        records = [MigrationRecord("0001", "0001.sql", "new")]
        drifts = detect_drift(records, {"0001": "old"})
        assert [d.kind for d in drifts] == ["checksum_changed"]


class TestMigrateRefusesOrphanedLedgerRows:
    def test_migrate_names_the_applied_version_that_is_not_packaged(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        second = tmp_path / "0002_second.sql"
        second.write_text("SELECT 2;")
        checksum = hashlib.sha256(second.read_bytes()).hexdigest()
        pool = make_asyncpg_pool(
            fetch=[
                {"version": "0001", "checksum": "a"},
                {"version": "0002_second", "checksum": checksum},
            ]
        )
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@remote.example/db")
        monkeypatch.setattr(db, "discover_migrations", lambda: discover_migrations(tmp_path))
        monkeypatch.setattr(db, "get_pool", AsyncMock(return_value=pool))
        monkeypatch.setattr(db, "close_pool", AsyncMock())

        assert db.cmd_migrate() == 1

        err = capsys.readouterr().err
        assert "0001" in err
        assert "applied but not packaged" in err
        executed = [call.args[0] for call in pool.conn.execute.await_args_list]
        assert "SELECT pg_advisory_unlock($1);" in executed
        assert "SELECT 2;" not in executed


class TestMigrationRecordAgainstRealFiles:
    def test_all_migrations_have_stable_checksums(self) -> None:
        records = discover_migrations(_MIGRATION_DIR)
        for rec in records:
            path = _MIGRATION_DIR / rec.filename
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            assert rec.checksum == expected, f"checksum mismatch for {rec.filename}"

    def test_version_stem_matches_filename(self) -> None:
        records = discover_migrations(_MIGRATION_DIR)
        for rec in records:
            assert rec.version == Path(rec.filename).stem
