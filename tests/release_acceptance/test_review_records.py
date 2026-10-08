"""The source-review records follow unchanged code and refuse changed code.

A comment, blank line, or edit elsewhere in a reviewed file only moves line numbers, so the
refresh re-lines entries with no human edit. A changed reviewed definition is listed for a
human re-read and nothing is written until ``--accept-reviewed``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import review_records as rr

SOURCE = '''"""Settings."""

import os


class Settings:
    alpha: str = Field(default_factory=lambda: os.environ["A"])
    beta: str = Field(default_factory=lambda: os.environ["B"])


def build(parser, names):
    for name in ("status", "result"):
        parser.add_parser(name)
'''
FILE = "pkg/config.py"


def _issue(line: int) -> dict[str, Any]:
    return {"code": "dynamic_default", "domain": "config", "source": f"{FILE}:{line}"}


def _entry(root: Path, line: int) -> dict[str, Any]:
    return {
        "issue_code": "dynamic_default",
        "domain": "config",
        "source": f"{FILE}:{line}",
        "span_sha256": rr.span_digest(root, f"{FILE}:{line}"),
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / FILE).write_text(SOURCE, encoding="utf-8")
    return tmp_path


def _rewrite(root: Path, old: str, new: str) -> None:
    path = root / FILE
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_comment_and_blank_lines_reline_entries_without_review(root: Path) -> None:
    entries = [_entry(root, 7), _entry(root, 8), _entry(root, 13)]
    _rewrite(root, '"""Settings."""\n', '"""Settings."""\n\n# a new comment line\n')
    _rewrite(root, "    beta:", "    # note\n\n    beta:")
    issues = [_issue(9), _issue(12), _issue(17)]

    pending = rr.refresh_entries(root, entries, issues, accept_reviewed=False)

    assert pending == []
    assert [entry["source"] for entry in entries] == [f"{FILE}:{n}" for n in (9, 12, 17)]


def test_edit_to_another_definition_does_not_touch_an_entry(root: Path) -> None:
    entries = [_entry(root, 7)]
    _rewrite(root, 'os.environ["B"]', 'os.environ["B2"]')
    assert rr.refresh_entries(root, entries, [_issue(7)], accept_reviewed=False) == []


def test_changed_reviewed_definition_is_refused_and_listed(root: Path) -> None:
    entries = [_entry(root, 7), _entry(root, 8)]
    before = [dict(entry) for entry in entries]
    _rewrite(root, 'os.environ["A"]', 'os.environ.get("A", "x")')

    pending = rr.refresh_entries(root, entries, [_issue(7), _issue(8)], accept_reviewed=False)

    assert len(pending) == 1 and f"{FILE}:7" in pending[0]
    assert entries[0] == before[0]  # a refused entry is not modified
    assert entries[1]["source"] == f"{FILE}:8"


def test_accept_reviewed_records_a_changed_definition(root: Path) -> None:
    entries = [_entry(root, 7)]
    _rewrite(root, 'os.environ["A"]', 'os.environ.get("A", "x")')
    issues = [_issue(7)]

    assert rr.refresh_entries(root, entries, issues, accept_reviewed=True) == []
    assert entries[0]["span_sha256"] == rr.span_digest(root, f"{FILE}:7")
    assert rr.refresh_entries(root, entries, issues, accept_reviewed=False) == []


def test_loop_header_is_part_of_the_reviewed_definition(root: Path) -> None:
    entries = [_entry(root, 13)]
    _rewrite(root, '("status", "result")', '("status", "result", "follow")')
    pending = rr.refresh_entries(root, entries, [_issue(13)], accept_reviewed=False)
    assert len(pending) == 1


def test_dependency_pins_ignore_comments_and_flag_code_changes(root: Path) -> None:
    dependency = {"path": FILE, "normalized_sha256": rr.file_digest(root, FILE)}
    _rewrite(root, "import os", "import os  # comment\n\n")
    assert rr.refresh_dependencies(root, [dependency], accept_reviewed=False) == []
    _rewrite(root, "import os", "import os\nimport sys")
    assert len(rr.refresh_dependencies(root, [dependency], accept_reviewed=False)) == 1
    assert rr.refresh_dependencies(root, [dependency], accept_reviewed=True) == []
    assert dependency["normalized_sha256"] == rr.file_digest(root, FILE)


def test_refresh_command_is_named_in_the_hints() -> None:
    assert "tools.release_acceptance.review_records" in rr.REFRESH_HINT
    assert "--accept-reviewed" in rr.ACCEPT_HINT


def test_committed_records_use_the_normalized_pins() -> None:
    review = json.loads((rr.ROOT / rr.DISCOVERY_REVIEW).read_text(encoding="utf-8"))
    assert all("source_sha256" not in entry for entry in review["entries"])
    for relative in rr.DEPENDENCY_RECORDS:
        record = json.loads((rr.ROOT / relative).read_text(encoding="utf-8"))
        assert all("sha256" not in dependency for dependency in record["dependencies"])
