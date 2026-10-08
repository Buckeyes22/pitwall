"""Task artifact containment, translated from packages/pi-workbench/tests/artifact-containment.test.ts."""

from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.workbench.task_record import assert_task_artifact_path

pytestmark = pytest.mark.parity

TASK_ID = "task-artifact-fixture"
VALID = f"{TASK_ID}.result-0123456789abcdef.txt"


def artifact(root: Path, kind: str = "result") -> Path:
    return root / f"{TASK_ID}.{kind}-0123456789abcdef.txt"


def check(path: Path, root: Path, kind: str = "result") -> None:
    assert_task_artifact_path(str(path), root=str(root), task_id=TASK_ID, kind=kind)


def test_accepts_an_existing_regular_artifact_owned_by_the_task(tmp_path: Path) -> None:
    """Source: artifact-containment.test.ts 'accepts an existing regular artifact owned by the task'."""
    path = artifact(tmp_path)
    path.write_text("result\n")
    check(path, tmp_path)


def test_canonicalizes_a_task_directory_symlink_before_checking_containment(tmp_path: Path) -> None:
    """Source: 'canonicalizes a task directory symlink before checking containment'."""
    actual = tmp_path / "actual"
    actual.mkdir()
    root = tmp_path / "records"
    root.symlink_to(actual)
    path = artifact(root)
    path.write_text("result\n")
    check(path, root)


def test_rejects_traversal_and_another_tasks_artifact(tmp_path: Path) -> None:
    """Source: 'rejects traversal and another task's artifact'."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / VALID).write_text("result\n")
    (outside / VALID).write_text("outside\n")
    with pytest.raises(ValueError, match="escapes|owned"):
        check(root / ".." / "outside" / VALID, root)
    with pytest.raises(ValueError, match="owned"):
        check(root / "task-other.result-0123456789abcdef.txt", root)


def test_rejects_a_filename_not_produced_by_the_artifact_writer(tmp_path: Path) -> None:
    """Source: 'rejects an owned-directory filename that was not produced by the artifact writer'."""
    path = tmp_path / f"{TASK_ID}.result-arbitrary.txt"
    path.write_text("result\n")
    with pytest.raises(ValueError, match="owned"):
        check(path, tmp_path)


def test_rejects_a_final_symlink_that_points_outside_the_record_directory(tmp_path: Path) -> None:
    """Source: 'rejects a final symlink that points outside the task record directory'."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = outside / "outside.txt"
    target.write_text("outside\n")
    path = artifact(root)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink|regular"):
        check(path, root)


def test_rejects_an_intermediate_symlink_that_escapes(tmp_path: Path) -> None:
    """Source: 'rejects an intermediate symlink that escapes before the artifact filename'."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / VALID).write_text("outside\n")
    (root / "nested").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink|escapes"):
        check(root / "nested" / VALID, root)


def test_rejects_an_internal_symlink_redirected_to_another_task_artifact(tmp_path: Path) -> None:
    """Source: 'rejects an internal symlink redirected to another task artifact'."""
    other = tmp_path / "other-task"
    other.mkdir()
    (other / VALID).write_text("other task\n")
    (tmp_path / "nested").symlink_to(other)
    with pytest.raises(ValueError, match="owned"):
        check(tmp_path / "nested" / VALID, tmp_path)


def test_rejects_a_missing_or_non_artifact_path(tmp_path: Path) -> None:
    """Source: 'rejects a missing or non-artifact path'."""
    with pytest.raises(ValueError, match="unavailable"):
        check(artifact(tmp_path), tmp_path)
    directory = artifact(tmp_path, "summary")
    directory.mkdir()
    with pytest.raises(ValueError, match="regular|owned"):
        check(directory, tmp_path, "summary")
