"""Atomic, owner-only state for personal leases."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

LeaseState = Literal["launching", "ready", "stopped", "failed", "gone"]

_LEASES_FILE = "leases.json"
# Per-month spend of settled leases: {"months": {"YYYY-MM": "<usd>"}}. Leases are keyed by route
# and a relaunch replaces the record, so spend history lives here, not on the lease.
_LEDGER_FILE = "ledger.json"
# Held exclusively around every read-modify-write of the state files.
_LOCK_FILE = ".lock"
# One JSON object per line for every serve, refusal, stop, and failure.
AUDIT_FILE = "audit.jsonl"
_USD = Decimal("0.000001")


class PersonalLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    pod_id: str = Field(min_length=1)
    model: str
    served_model_id: str
    engine: str
    variant: str | None
    image: str
    gpu_class: str
    gpu_count: int = Field(ge=1)
    cloud: str
    price_per_hour_usd: str | None
    endpoint_url: str
    key_env: str
    launched_at: dt.datetime
    deadline_at: dt.datetime
    state: LeaseState
    failure: str | None = None
    # Set once the lease's cost has been recorded in the ledger, so it is never charged twice.
    accrued_usd: str | None = None


def default_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "pitwall"


class StateStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else default_state_root()

    @property
    def root(self) -> Path:
        return self._root

    def load(self) -> list[PersonalLease]:
        path = self._root / _LEASES_FILE
        if not path.exists():
            return []
        raw: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        return [PersonalLease.model_validate(item) for item in raw]

    def get(self, route: str) -> PersonalLease | None:
        return next((lease for lease in self.load() if lease.route == route), None)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive lock across processes and threads for one read-modify-write.

        Each caller opens its own descriptor, so ``flock`` also excludes threads of one
        process. Closing the descriptor releases the lock, including after a crash.
        """
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, stat.S_IRWXU)
        fd = os.open(self._root / _LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def upsert(self, lease: PersonalLease) -> None:
        with self._locked():
            self._upsert_unlocked(lease)

    def _upsert_unlocked(self, lease: PersonalLease) -> None:
        leases = [item for item in self.load() if item.route != lease.route]
        leases.append(lease)
        self._write(leases)

    def update(self, route: str, **changes: object) -> PersonalLease:
        with self._locked():
            current = self.get(route)
            if current is None:
                raise KeyError(route)
            updated = current.model_copy(update=changes)
            self._upsert_unlocked(updated)
            return updated

    def read_document(self, name: str) -> dict[str, Any] | None:
        path = self._root / name
        if not path.is_file():
            return None
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None

    def write_document(
        self, name: str, document: Mapping[str, Any] | list[Mapping[str, Any]]
    ) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, stat.S_IRWXU)
        payload = json.dumps(document, indent=2)
        fd, temp_name = tempfile.mkstemp(dir=self._root, prefix=".document-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self._root / name)
        except BaseException:
            with suppress(OSError):
                os.unlink(temp_name)
            raise

    def month_spend(self, month: str) -> Decimal:
        """Spend recorded for settled leases launched in ``month`` (``YYYY-MM``)."""
        months = (self.read_document(_LEDGER_FILE) or {}).get("months") or {}
        return Decimal(str(months.get(month, "0")))

    def record_spend(self, month: str, amount: Decimal) -> None:
        with self._locked():
            months = dict((self.read_document(_LEDGER_FILE) or {}).get("months") or {})
            months[month] = str((Decimal(str(months.get(month, "0"))) + amount).quantize(_USD))
            self.write_document(_LEDGER_FILE, {"months": months})

    def append_audit(self, record: Mapping[str, Any]) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, stat.S_IRWXU)
        fd = os.open(self._root / AUDIT_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(record)) + "\n")

    def remove_document(self, name: str) -> None:
        with suppress(OSError):
            (self._root / name).unlink()

    def _write(self, leases: list[PersonalLease]) -> None:
        self.write_document(_LEASES_FILE, [lease.model_dump(mode="json") for lease in leases])
