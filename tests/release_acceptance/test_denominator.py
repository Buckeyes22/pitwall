"""The discovered surface set is pinned; any change is a reviewed update."""

from __future__ import annotations

import collections
import json
from pathlib import Path

from tools.release_acceptance import discovery

ROOT = Path(__file__).resolve().parents[2]
PIN = ROOT / "release_acceptance" / "denominator.json"


def _counts() -> dict[str, int]:
    report = discovery.build_report(ROOT)
    counts = collections.Counter(
        f"{row['origin']['domain']}:{row['kind']}" for row in report["surfaces"]
    )
    return dict(sorted(counts.items()))


def test_surface_counts_match_the_reviewed_pin() -> None:
    pinned = json.loads(PIN.read_text(encoding="utf-8"))
    assert _counts() == pinned["counts"], (
        "surfaces changed: bind the new surfaces to tests (journey-coverage families) and "
        "update release_acceptance/denominator.json with a reason"
    )
