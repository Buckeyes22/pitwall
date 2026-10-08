"""Migration 0023 upgrades generated-template cache identity."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _REPO_ROOT / "db" / "migrations" / "0023_runpod_templates_config_sha.sql"


def test_config_sha_migration_backfills_before_not_null() -> None:
    sql = _MIGRATION.read_text()

    assert "ADD COLUMN config_sha TEXT" in sql
    assert "UPDATE pitwall.runpod_templates" in sql
    assert "ALTER COLUMN config_sha SET NOT NULL" in sql
    assert sql.index("UPDATE pitwall.runpod_templates") < sql.index(
        "ALTER COLUMN config_sha SET NOT NULL"
    )


def test_config_sha_replaces_image_only_unique_cache_key() -> None:
    sql = _MIGRATION.read_text()

    assert "DROP CONSTRAINT runpod_templates_name_image_sha_key" in sql
    assert "UNIQUE (name, config_sha)" in sql
    assert "idx_runpod_templates_config_sha" in sql
