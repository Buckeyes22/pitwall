"""The backup drill runs pg_dump and pg_restore without blocking the event loop."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest

from pitwall.ops import backup_drill

SOURCE = "postgresql://operator:secret@db.example:5432/source"  # pragma: allowlist secret
TARGET = "postgresql://operator:secret@db.example:5432/restore"  # pragma: allowlist secret


def _fake_tool(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


async def test_drill_does_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slow_dump = (
        "import sys, time\n"
        "time.sleep(1.0)\n"
        "path = sys.argv[sys.argv.index('--file') + 1]\n"
        "open(path, 'wb').write(b'dump')\n"
    )
    _fake_tool(tmp_path, "pg_dump", slow_dump)
    _fake_tool(tmp_path, "pg_restore", "import time\ntime.sleep(0.3)\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    ticks = 0
    stop = asyncio.Event()

    async def ticker() -> None:
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.01)
            ticks += 1

    async def fake_connect(url: str) -> object:
        return object()

    monkeypatch.setattr("asyncpg.connect", fake_connect)
    task = asyncio.create_task(ticker())
    await backup_drill._restore_schema_and_data(SOURCE, TARGET, "pitwall")
    stop.set()
    await task

    # A blocked loop would tick at most once while the 1.3 s of fake tools ran.
    assert ticks >= 30


async def test_dump_failure_and_timeout_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tool(tmp_path, "pg_dump", "import sys\nsys.stderr.write('boom')\nsys.exit(2)\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(RuntimeError, match="pg_dump failed: boom"):
        await backup_drill._restore_schema_and_data(SOURCE, TARGET, "pitwall")

    _fake_tool(tmp_path, "pg_dump", "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(backup_drill, "PG_TOOL_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(RuntimeError, match="pg_dump timed out"):
        await backup_drill._restore_schema_and_data(SOURCE, TARGET, "pitwall")
