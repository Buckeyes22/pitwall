"""The on-disk migration ledger is discovered whole, in order, and names what other code expects.

These checks read file *names* and versions only; what the migrations do is asserted against the
migrated database in the integration-marked migration tests next to this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.migrations import discover_migrations

_MIGRATION_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
_ON_DISK = sorted(_MIGRATION_DIR.glob("*.sql"))


def test_migration_files_exist() -> None:
    # Migrations only ever grow; guard against accidental deletion below the known baseline
    # without pinning an exact count that every new migration must bump.
    assert len(_ON_DISK) >= 13, (
        f"expected at least 13 migration files: {[p.name for p in _ON_DISK]}"
    )


def test_migrations_are_lexically_ordered_and_discovered_whole() -> None:
    names = [path.name for path in _ON_DISK]
    records = discover_migrations(_MIGRATION_DIR)

    assert names == sorted(names)
    # Discovery must return exactly the .sql files on disk, in version order.
    assert [record.filename for record in records] == names
    versions = [record.version for record in records]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)


def test_every_migration_creates_in_pitwall_schema() -> None:
    for path in _ON_DISK:
        assert "pitwall." in path.read_text(), f"{path.name} does not reference pitwall schema"


@pytest.mark.parametrize(
    "version",
    [
        "0022_capability_served_model",
        "0024_leases_activity",
        "0033_gateway_quotas",
        "0034_capabilities_zero_cost_mode",
        "0035_model_studio",
    ],
)
def test_migration_the_code_depends_on_is_in_the_ledger(version: str) -> None:
    assert version in {record.version for record in discover_migrations(_MIGRATION_DIR)}


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        ("0030_lease_workload_billing_identity", "0031_workload_route_plan"),
        ("0031_workload_route_plan", "0032_normalize_route_plan_cost_quotes"),
    ],
)
def test_data_migration_follows_the_record_it_depends_on(earlier: str, later: str) -> None:
    versions = [record.version for record in discover_migrations(_MIGRATION_DIR)]

    assert versions.index(later) == versions.index(earlier) + 1


def test_served_model_column_precedes_lease_activity() -> None:
    names = [record.filename for record in discover_migrations(_MIGRATION_DIR)]

    assert names.index("0022_capability_served_model.sql") < names.index("0024_leases_activity.sql")
