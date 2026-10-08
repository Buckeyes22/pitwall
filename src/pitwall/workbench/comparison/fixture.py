"""Fresh disposable fixtures and host-side checks for the comparison runner."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pitwall.workbench.comparison.reasoning_fixture import (
    ComparisonChildReasoningFixture,
    write_comparison_child_reasoning_fixture,
)
from pitwall.workbench.hosted.fixture import node_executable

MODULE_COUNT = 180
CALLER_COUNT = 30
TEST_COUNT = 20
GIT_DATE = "2020-01-01T00:00:00Z"
ORACLE_SOURCE = "export function add(left, right) {\n  return left + right;\n}\nexport const sector = 'sector-7';\n"
NEIGHBOR_SOURCE = "export const neighbor = 'not-the-oracle';\n"


@dataclass(frozen=True)
class MediumStats:
    files: int
    lines: int
    bytes: int


@dataclass(frozen=True)
class ComparisonFixture:
    cwd: Path
    oracle_path: Path
    revision: str
    medium_stats: MediumStats
    child_reasoning_fixture: ComparisonChildReasoningFixture


@dataclass(frozen=True)
class NodeRun:
    code: int | None
    output: str
    duration_ms: int


def sha256_text(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _count(source: str) -> tuple[int, int]:
    return source.count("\n"), len(source.encode())


def build_fixture(root: Path, child_reasoning_level: str = "low") -> ComparisonFixture:
    """Create a deterministic git fixture with a 230-file medium tree and one search oracle."""
    cwd = Path(tempfile.mkdtemp(prefix="fixture-", dir=root))
    (cwd / "preserve.txt").write_text("COMPARISON_PROTECTED_CONTENT\n")
    (cwd / "child-probe.txt").write_text("CHILD_ORACLE_7F31\n")
    medium = cwd / "medium"
    (medium / "sector-7" / "node").mkdir(parents=True)
    lines = size = 0

    def write(path: Path, source: str) -> None:
        nonlocal lines, size
        path.write_text(source)
        added_lines, added_bytes = _count(source)
        lines += added_lines
        size += added_bytes

    for i in range(MODULE_COUNT):
        decoy = (
            "export function add(left, right) {\n  return left - right;\n}\n" if i % 30 == 0 else ""
        )
        source = (
            f"// deterministic domain module {i}\n"
            f'export const module{i}Name = "module-{i}";\n'
            f"export function normalize{i}(value) {{\n  return String(value).trim().toLowerCase();\n}}\n"
            f'export function summarize{i}(values) {{\n  return values.map(normalize{i}).join(",");\n}}\n'
            f'export const module{i}Metadata = {{\n  index: {i},\n  sector: "{i % 9}",\n'
            f'  kind: "implementation",\n}};\n{decoy}'
        )
        write(medium / f"module-{i:03d}.mjs", source)
    (medium / "apps" / "sector-7").mkdir(parents=True)
    (medium / "tests").mkdir(parents=True)
    for i in range(CALLER_COUNT):
        source = (
            f'import {{ normalize{i} }} from "../../module-{i:03d}.mjs";\n'
            f"export function call{i}(value) {{\n  return normalize{i}(value);\n}}\n"
        )
        write(medium / "apps" / "sector-7" / f"caller-{i:03d}.mjs", source)
    for i in range(TEST_COUNT):
        source = (
            'import { test } from "node:test";\nimport assert from "node:assert/strict";\n'
            f'import {{ call{i} }} from "../apps/sector-7/caller-{i:03d}.mjs";\n'
            f'test("caller-{i}", () => {{ assert.equal(call{i}(" Value "), "value"); }});\n'
        )
        write(medium / "tests" / f"caller-{i:03d}.test.mjs", source)
    oracle_path = medium / "sector-7" / "node" / "target.mjs"
    write(oracle_path, ORACLE_SOURCE)
    write(medium / "sector-7" / "node" / "neighbor.mjs", NEIGHBOR_SOURCE)
    reasoning = write_comparison_child_reasoning_fixture(cwd, child_reasoning_level)
    git_env = {**os.environ, "GIT_AUTHOR_DATE": GIT_DATE, "GIT_COMMITTER_DATE": GIT_DATE}

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603  # reason: fixed git argv in a disposable fixture, no shell
            ["git", *args], cwd=cwd, env=git_env, check=True, capture_output=True, text=True
        ).stdout

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=comparison",
        "-c",
        "user.email=comparison@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    revision = git("rev-parse", "HEAD").strip()
    stats = MediumStats(MODULE_COUNT + CALLER_COUNT + TEST_COUNT + 2, lines, size)
    return ComparisonFixture(cwd, oracle_path, revision, stats, reasoning)


def file_snapshot(root: Path) -> dict[str, str]:
    """Relative path to content hash for every regular file under ``root``."""
    return {
        path.relative_to(root).as_posix(): sha256_text(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def run_node(cwd: Path, args: list[str], timeout_s: float = 10.0) -> NodeRun:
    """Run ``node <args>`` in ``cwd`` with output captured and a bounded runtime."""
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603  # reason: fixed node argv against the disposable fixture, no shell
            [node_executable(), *args],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return NodeRun(None, "timeout", int((time.monotonic() - started) * 1000))
    return NodeRun(
        completed.returncode,
        completed.stdout + completed.stderr,
        int((time.monotonic() - started) * 1000),
    )
