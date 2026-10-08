"""CI drift gate: any avoid-list or known-unreachable change fails; count
changes warn (§8.2)."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Mirror sync_catalog.py's repo-root injection so this script can be run as
# `python tools/gateway/check_catalog_drift.py` without packaging.
_REPO_ROOT_HINT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT_HINT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_HINT))

REPO_ROOT = _REPO_ROOT_HINT


@dataclass(frozen=True, slots=True)
class DriftReport:
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _hard_fail_if_changed(
    report: DriftReport, *, key: str, current: list[str], locked: list[str]
) -> None:
    added = sorted(set(current) - set(locked))
    removed = sorted(set(locked) - set(current))
    if added or removed:
        report.hard_failures.append(f"{key} changed: +{added} -{removed}")


def compare(current: Mapping[str, Any], locked: Mapping[str, Any]) -> DriftReport:
    report = DriftReport()
    _hard_fail_if_changed(
        report,
        key="avoid-list",
        current=list(current.get("avoid_list", [])),
        locked=list(locked.get("avoid_list", [])),
    )
    _hard_fail_if_changed(
        report,
        key="known_unreachable",
        current=list(current.get("known_unreachable", [])),
        locked=list(locked.get("known_unreachable", [])),
    )
    for key in ("row_count", "steady_monthly", "pool_count", "registry_covered"):
        if current.get(key) != locked.get(key):
            report.warnings.append(f"{key} {locked.get(key)} -> {current.get(key)}")
    return report


def main() -> int:
    catalog = json.loads(
        (REPO_ROOT / "config" / "gateway-catalog.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (REPO_ROOT / "config" / "gateway-catalog.lock.json").read_text(encoding="utf-8")
    )
    # KNOWN_UNREACHABLE is the source of truth for what the sync skips today;
    # importing it (rather than reading the lock) catches drift in either direction.
    from tools.gateway.sync_catalog import KNOWN_UNREACHABLE

    current = {
        "avoid_list": catalog["avoid_list"],
        "row_count": len(catalog["providers"]),
        "steady_monthly": catalog["totals"]["steady_monthly"],
        "pool_count": lock["pool_count"],
        "registry_covered": lock["registry_covered"],
        "known_unreachable": sorted(KNOWN_UNREACHABLE),
    }
    report = compare(current, lock)
    for line in report.warnings:
        print(f"::warning::gateway catalog {line}")
    for line in report.hard_failures:
        print(f"::error::gateway catalog {line}")
    return 1 if report.hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
