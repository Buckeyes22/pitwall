"""Disposable fixture the acceptance runners repair (port of ``create-fixture.ts`` and the runners' shared checks)."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

PRESERVED_CONTENT = "USER_UNRELATED_CONTENT_MUST_SURVIVE\n"
ADD_SOURCE = "export const add = (a, b) => a - b;\n"
ADD_TEST_SOURCE = (
    "import {test} from 'node:test';\n"
    "import assert from 'node:assert/strict';\n"
    "import {add} from './add.mjs';\n"
    "test('adds positive and negative numbers', () => "
    "{ assert.equal(add(2, 3), 5); assert.equal(add(-2, 3), 1); });\n"
)
# A 64x64 image whose left half is red and right half is blue (tests/workbench/fixtures/colors.png).
COLORS_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAUElEQVR4nO3PsQkAAAzDsPz/dHpFliLw"
    "bFCaTBvvu94DAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPAAcuOzw"
    "4j9XSHcAAAAASUVORK5CYII="
)

FixtureSnapshot = dict[str, str]


class FixtureError(RuntimeError):
    """A fixture is not the designated disposable fixture, or changed outside its target."""


def node_executable() -> str:
    """The ``node`` on PATH that runs the fixture's own tests."""
    node = shutil.which("node")
    if node is None:
        raise FixtureError("node is not installed")
    return node


def write_fixture_files(directory: Path, *, with_image: bool) -> None:
    """Write the failing ``add`` repair task into an existing empty directory."""
    (directory / "add.mjs").write_text(ADD_SOURCE)
    (directory / "add.test.mjs").write_text(ADD_TEST_SOURCE)
    (directory / "preserve.txt").write_text(PRESERVED_CONTENT)
    if with_image:
        (directory / "colors.png").write_bytes(COLORS_PNG)


def create_fixture(path: Path | str) -> Path:
    """Create a new disposable fixture directory; any existing path is refused, never replaced."""
    directory = Path(os.path.abspath(path))
    directory.mkdir(mode=0o700)
    write_fixture_files(directory, with_image=True)
    return directory


def create_temporary_fixture(root: Path) -> Path:
    """Create a fresh fixture (without the image) under ``root``."""
    directory = Path(tempfile.mkdtemp(prefix="fixture-", dir=root))
    write_fixture_files(directory, with_image=False)
    return directory


def snapshot_fixture(
    root: Path, relative: str = "", snapshot: FixtureSnapshot | None = None
) -> FixtureSnapshot:
    """Fingerprint every fixture entry except ``add.mjs``, the one file the repair may change."""
    result: FixtureSnapshot = {} if snapshot is None else snapshot
    directory = root / relative if relative else root
    for entry in sorted(os.listdir(directory)):
        name = f"{relative}/{entry}" if relative else entry
        if name == "add.mjs":
            continue
        path = root / name
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            result[name] = f"symlink:{os.readlink(path)}"
        elif stat.S_ISDIR(info.st_mode):
            result[name] = f"directory:{info.st_mode}"
            snapshot_fixture(root, name, result)
        elif stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result[name] = f"file:{info.st_mode}:{digest}"
        else:
            result[name] = f"other:{info.st_mode}:{info.st_size}"
    return result


def assert_fixture_unchanged(before: FixtureSnapshot, after: FixtureSnapshot) -> None:
    changed = sorted(name for name in {*before, *after} if before.get(name) != after.get(name))
    if changed:
        raise FixtureError(f"fixture changed outside add.mjs: {', '.join(changed[:8])}")


@dataclass(frozen=True)
class FixtureCheck:
    exit_code: int | None
    timed_out: bool


# A hang guard, not a latency gate: a trivial ``node --test`` that is merely slow on a busy machine
# must not fail an acceptance gate. Only a wedged run reaches it.
FIXTURE_CHECK_TIMEOUT_S = 120.0


def fixture_check(cwd: Path, timeout_s: float = FIXTURE_CHECK_TIMEOUT_S) -> FixtureCheck:
    """Run the fixture's own ``node --test add.test.mjs`` independently of the model."""
    try:
        completed = subprocess.run(  # noqa: S603  # reason: fixed argv, node resolved from PATH, no shell
            [node_executable(), "--test", "add.test.mjs"],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return FixtureCheck(None, True)
    return FixtureCheck(completed.returncode, False)
