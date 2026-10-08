"""Migration discovery, checksums, and drift detection for Pitwall.

Reads ``db/migrations/*.sql``, sorts lexically, computes SHA-256 checksums,
and rejects drift: a previously-applied migration whose checksum has changed, or
an applied version that is no longer packaged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class MigrationRecord:
    """One discovered migration file and its SHA-256 checksum."""

    version: str
    filename: str
    checksum: str
    sql: str = field(default="", repr=False, compare=False)


def default_migrations_dir() -> Path:
    """Return the source-checkout migration directory.

    Runtime callers should use discover_migrations() without a path so installed
    package resources are preferred.
    """
    return Path(__file__).resolve().parent.parent.parent / "db" / "migrations"


def _resource_migrations() -> list[MigrationRecord]:
    """Read migrations embedded below the installed pitwall.db package."""
    directory = files("pitwall.db").joinpath("migrations")
    if not directory.is_dir():
        return []

    records: list[MigrationRecord] = []
    resources = sorted(
        (resource for resource in directory.iterdir() if resource.name.endswith(".sql")),
        key=lambda resource: resource.name,
    )
    for resource in resources:
        data = resource.read_bytes()
        records.append(
            MigrationRecord(
                version=Path(resource.name).stem,
                filename=resource.name,
                checksum=_sha256(data),
                sql=data.decode("utf-8"),
            )
        )
    return records


def discover_migrations(
    migrations_dir: Path | str | None = None,
) -> list[MigrationRecord]:
    """Discover ``*.sql`` files, sort lexically, and compute checksums.

    Parameters
    ----------
    migrations_dir:
        Directory containing ``*.sql`` migration files.  Defaults to
        ``db/migrations/`` relative to the repo root.

    Returns
    -------
    list[MigrationRecord]
        Sorted lexicographically by filename.

    Raises
    ------
    FileNotFoundError
        If *migrations_dir* does not exist.
    """
    if migrations_dir is None:
        packaged = _resource_migrations()
        if packaged:
            return packaged
        directory = default_migrations_dir()
    else:
        directory = Path(migrations_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"migrations directory not found: {directory}")
    records: list[MigrationRecord] = []
    for path in sorted(directory.glob("*.sql")):
        data = path.read_bytes()
        records.append(
            MigrationRecord(
                version=path.stem,
                filename=path.name,
                checksum=_sha256(data),
                sql=data.decode("utf-8"),
            )
        )
    return records


def detect_drift(
    expected: list[MigrationRecord],
    applied: dict[str, str],
) -> list[DriftEntry]:
    """Compare expected migrations against previously-applied checksums, in both directions.

    Parameters
    ----------
    expected:
        The current on-disk migration records (from :func:`discover_migrations`).
    applied:
        Mapping of ``version → checksum`` for migrations previously recorded
        in the database's ``schema_migrations`` table.

    Returns
    -------
    list[DriftEntry]
        Non-empty when drift is detected, including applied versions with no packaged
        file (``missing_from_package``).  The caller should reject the
        migration run when this list is non-empty.
    """
    drifts: list[DriftEntry] = []
    packaged = {rec.version for rec in expected}
    for rec in expected:
        if rec.version in applied and applied[rec.version] != rec.checksum:
            drifts.append(
                DriftEntry(
                    version=rec.version,
                    recorded_checksum=applied[rec.version],
                    kind="checksum_changed",
                    filename=rec.filename,
                    current_checksum=rec.checksum,
                )
            )
    drifts.extend(
        DriftEntry(
            version=version,
            recorded_checksum=checksum,
            kind="missing_from_package",
        )
        for version, checksum in sorted(applied.items())
        if version not in packaged
    )
    return drifts


def drift_summaries(drifts: list[DriftEntry]) -> list[str]:
    """Return one line per drift kind present, naming the affected migrations."""
    changed = [d.filename or d.version for d in drifts if d.kind == "checksum_changed"]
    missing = [d.version for d in drifts if d.kind == "missing_from_package"]
    lines: list[str] = []
    if changed:
        lines.append(f"applied migration checksums changed: {', '.join(changed)}")
    if missing:
        lines.append(f"migrations applied but not packaged: {', '.join(missing)}")
    return lines


@dataclass(frozen=True)
class DriftEntry:
    """An applied migration that differs from the package.

    ``checksum_changed``: the packaged file's checksum differs from the recorded one.
    ``missing_from_package``: the version is recorded as applied but no packaged file
    exists, so ``filename`` and ``current_checksum`` are ``None``.
    """

    version: str
    recorded_checksum: str
    kind: Literal["checksum_changed", "missing_from_package"] = "checksum_changed"
    filename: str | None = None
    current_checksum: str | None = None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
