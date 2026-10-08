from __future__ import annotations

import subprocess
from pathlib import Path

from tools.ci import check_markdown_links
from tools.ci.check_markdown_links import anchors, check_internal, link_targets, markdown_files


def test_link_targets_support_inline_images_and_references() -> None:
    text = "[doc](guide.md#start) ![image](asset.png)\n[policy]: POLICY.md\n"
    assert link_targets(text) == ["guide.md#start", "asset.png", "POLICY.md"]


def test_anchors_follow_github_style_duplicate_suffixes(tmp_path: Path) -> None:
    page = tmp_path / "page.md"
    page.write_text('# Hello, World!\n## Hello World\n<a id="manual"></a>\n', encoding="utf-8")
    assert anchors(page) == {"hello-world", "hello-world-1", "manual"}


def test_internal_check_reports_missing_file_and_anchor(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    target = tmp_path / "guide.md"
    source.write_text("[ok](guide.md#start) [bad](guide.md#missing) [gone](gone.md)\n")
    target.write_text("# Start\n")

    failures = check_internal(tmp_path, [source, target])

    assert len(failures) == 2
    assert any("missing anchor #missing" in failure for failure in failures)
    assert any("missing target 'gone.md'" in failure for failure in failures)


def test_markdown_inventory_uses_tracked_and_nonignored_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# README\n", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=b"README.md\0docs/guide.md\0src/module.py\0",
        stderr=b"",
    )
    monkeypatch.setattr(
        check_markdown_links.subprocess,
        "run",
        lambda *_args, **_kwargs: completed,
    )

    assert markdown_files(tmp_path) == [tmp_path / "README.md", tmp_path / "docs/guide.md"]


def test_own_repo_blob_and_tree_urls_map_to_checkout_paths() -> None:
    from tools.ci.check_markdown_links import _own_repo_path

    assert (
        _own_repo_path("https://github.com/Buckeyes22/pitwall/blob/main/README.md") == "README.md"
    )
    assert (
        _own_repo_path("https://github.com/Buckeyes22/pitwall/tree/main/docs/sdlc") == "docs/sdlc"
    )
    assert (
        _own_repo_path("https://github.com/Buckeyes22/pitwall/blob/main/README.md#quick-start")
        == "README.md"
    )


def test_other_own_repo_pages_are_not_fetched() -> None:
    from tools.ci.check_markdown_links import _own_repo_path

    pages = (
        "https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml/badge.svg",
        "https://github.com/Buckeyes22/pitwall/issues/new/choose",
        "https://github.com/Buckeyes22/pitwall/pull/30",
        "https://github.com/Buckeyes22/pitwall/discussions",
    )
    for url in pages:
        assert _own_repo_path(url) == ""
    assert _own_repo_path("https://github.com/astral-sh/uv") is None


def test_own_repo_file_links_are_checked_against_the_checkout(tmp_path: Path) -> None:
    (tmp_path / "exists.md").write_text("# Exists\n")
    source = tmp_path / "doc.md"
    source.write_text(
        "[ok](https://github.com/Buckeyes22/pitwall/blob/main/exists.md) "
        "[gone](https://github.com/Buckeyes22/pitwall/blob/main/missing.md) "
        "[badge](https://github.com/Buckeyes22/pitwall/actions/workflows/ci.yml/badge.svg) "
        "[other](https://example.test/page)\n"
    )
    failures = check_internal(tmp_path, [source])
    assert failures == [
        "doc.md: missing target 'https://github.com/Buckeyes22/pitwall/blob/main/missing.md'"
    ]
    from tools.ci.check_markdown_links import _external_urls

    assert _external_urls([source]) == ["https://example.test/page"]
