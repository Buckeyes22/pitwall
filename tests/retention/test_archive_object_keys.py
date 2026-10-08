"""Archive and purge behaviour around object-storage keys, commits, and lock scope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pitwall.retention import archive
from pitwall.retention.archive import archive_workloads_to_jsonl
from tests.retention._fakes import KEY, FakePool, workload


async def _run(pool: FakePool, output_dir: Path, **kwargs: Any) -> dict[str, Any]:
    return await archive_workloads_to_jsonl(
        pool,
        output_dir,
        encryption_key=KEY,
        key_version="v1",
        **kwargs,
    )


async def test_rows_with_object_keys_skipped_without_adapter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    plain = workload("wl_plain")
    keyed = workload("wl_keyed", result={"r2_key": "results/wl_keyed.bin"})
    pool = FakePool([plain, keyed])

    with caplog.at_level("WARNING", logger="pitwall.retention.archive"):
        manifest = await _run(pool, tmp_path, purge=True)

    assert manifest["workload_count"] == 1
    assert manifest["deleted_count"] == 1
    assert manifest["skipped_object_key_count"] == 1
    assert pool.deleted_ids == {"wl_plain"}
    assert "skipped 1" in caplog.text


async def test_no_orphan_directory_when_nothing_archived(tmp_path: Path) -> None:
    output = tmp_path / "archive"
    pool = FakePool([workload("wl_keyed", result={"object_key": "a/b"})])

    manifest = await _run(pool, output, purge=True)

    assert manifest["workload_count"] == 0
    assert manifest["skipped_object_key_count"] == 1
    assert not output.exists()

    empty = await _run(FakePool([]), output, purge=True)
    assert empty["workload_count"] == 0
    assert not output.exists()


async def test_object_delete_after_commit(tmp_path: Path) -> None:
    pool = FakePool([workload("wl_keyed", result={"r2_key": "results/one.bin"})])
    seen: list[tuple[list[str], bool, bool]] = []

    async def object_delete(keys: list[str]) -> None:
        committed = ("commit", False) in pool.events
        seen.append((keys, pool.in_transaction, committed))

    manifest = await _run(pool, tmp_path, purge=True, object_delete=object_delete)

    assert seen == [(["results/one.bin"], False, True)]
    assert manifest["deleted_count"] == 1
    commit = json.loads(next(tmp_path.glob("ret_*/commit.json")).read_text(encoding="utf-8"))
    assert commit["pending_object_keys"] == []


async def test_failed_object_delete_is_recorded_and_retried(tmp_path: Path) -> None:
    pool = FakePool([workload("wl_keyed", result={"r2_key": "results/one.bin"})])

    async def failing(keys: list[str]) -> None:
        raise OSError("storage down")

    manifest = await _run(pool, tmp_path, purge=True, object_delete=failing)
    assert manifest["database_committed"] is True
    assert manifest["pending_object_keys"] == ["results/one.bin"]
    commit_path = next(tmp_path.glob("ret_*/commit.json"))
    assert json.loads(commit_path.read_text(encoding="utf-8"))["pending_object_keys"] == [
        "results/one.bin"
    ]

    retried: list[list[str]] = []

    async def working(keys: list[str]) -> None:
        retried.append(keys)

    await _run(FakePool([]), tmp_path, purge=True, object_delete=working)
    assert retried == [["results/one.bin"]]
    assert json.loads(commit_path.read_text(encoding="utf-8"))["pending_object_keys"] == []


async def test_non_purge_rows_not_rearchived(tmp_path: Path) -> None:
    pool = FakePool([workload("wl_a"), workload("wl_b")])
    manifest = await _run(pool, tmp_path, purge=False)
    assert manifest["workload_count"] == 2
    assert pool.deleted_ids == set()

    audit = [args for sql, args in pool.executed if "config_audit" in sql]
    assert len(audit) == 1
    assert json.loads(audit[0][2])["workload_ids"] == ["wl_a", "wl_b"]

    # The selection query excludes ids recorded by earlier archive-only runs.
    assert "retention_run" in archive._SELECT_PAGE
    assert "workload_ids" in archive._SELECT_PAGE


async def test_locks_released_during_encryption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = FakePool([workload("wl_a")])
    io_in_transaction: list[bool] = []
    real_write = archive._atomic_write

    def spying_write(path: Path, data: bytes) -> None:
        io_in_transaction.append(pool.in_transaction)
        real_write(path, data)

    monkeypatch.setattr(archive, "_atomic_write", spying_write)
    await _run(pool, tmp_path, purge=True)

    assert io_in_transaction
    assert not any(io_in_transaction)
    # Rows are re-locked by id in a single later transaction before the purge.
    assert [name for name, _ in pool.events].count("begin") == 1
    assert ("fetch", True) in pool.events
    assert pool.deleted_ids == {"wl_a"}
