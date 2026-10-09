"""Every open discovery issue has exactly one current, evidenced review entry.

Discovery never closes an issue on the strength of a review (the review file is linked as
provenance, not approval). This test is what enforces the review: each issue discovery
reports on this tree has one entry in ``release_acceptance/discovery-review.json`` with
the same code, domain, and source; an entry with a source carries the SHA-256 of the
reviewed definition (its normalized AST span, not the whole file, so comments,
layout, and edits elsewhere in the file never force a re-review); and every piece of
evidence names a test file that exists and, when it names a test, a test that exists in
that file. Entries for issues that no longer exist are refused too.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import discovery, review_records

ROOT = Path(__file__).resolve().parents[2]
REVIEW = json.loads((ROOT / "release_acceptance" / "discovery-review.json").read_text())


def _key(code: Any, domain: Any, source: Any) -> tuple[str, str, str | None]:
    return str(code), str(domain), source


_REFRESH = review_records.REFRESH_HINT


@pytest.fixture(scope="module")
def issue_keys() -> Counter[tuple[str, str, str | None]]:
    keys: Counter[tuple[str, str, str | None]] = Counter()
    for issue in discovery.build_report(ROOT)["issues"]:
        keys[review_records.issue_key(issue)] += 1
    return keys


def test_every_issue_has_exactly_one_entry_and_every_entry_an_issue(
    issue_keys: Counter[tuple[str, str, str | None]],
) -> None:
    entries = Counter(
        _key(entry["issue_code"], entry["domain"], entry["source"]) for entry in REVIEW["entries"]
    )
    # A moved line is the expected cause of a mismatch: the refresh command re-lines entries
    # whose reviewed definition is unchanged and lists the ones a human must re-read.
    assert sorted(set(issue_keys) - set(entries)) == [], (
        f"open issues with no review entry (a moved line? {_REFRESH}; a new issue needs an "
        "entry in release_acceptance/discovery-review.json)"
    )
    assert sorted(set(entries) - set(issue_keys)) == [], (
        f"review entries for no open issue ({_REFRESH}, or delete the entry if the issue is gone)"
    )
    assert [key for key, count in entries.items() if count > 1] == []


@pytest.mark.parametrize(
    "entry",
    [entry for entry in REVIEW["entries"] if entry["source"]],
    ids=lambda entry: f"{entry['issue_code']}@{entry['source']}",
)
def test_a_sourced_entry_reviews_the_file_as_it_is_now(entry: dict[str, Any]) -> None:
    current = review_records.span_digest(ROOT, entry["source"])
    assert entry["span_sha256"] == current, (
        f"{entry['source']} no longer holds the reviewed definition: {_REFRESH} (it re-lines "
        f"unchanged definitions); if it lists this entry, {review_records.ACCEPT_HINT}"
    )


@pytest.mark.parametrize(
    "entry",
    REVIEW["entries"],
    ids=lambda entry: f"{entry['issue_code']}@{entry['source'] or entry['domain']}",
)
def test_an_entry_interprets_and_names_tests_that_exist(entry: dict[str, Any]) -> None:
    assert entry["interpretation"].strip()
    assert entry["evidence"]
    for item in entry["evidence"]:
        path, _, name = item.partition("::")
        assert (ROOT / path).is_file(), item
        if name:
            assert name in (ROOT / path).read_text(encoding="utf-8"), item
