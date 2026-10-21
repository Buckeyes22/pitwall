"""The shim and steer-gate paths start without the service dependencies.

Harness shims and the steering hook run once per tool call, so ``pitwall agents _shim`` and
``pitwall agents _steer-gate`` must not pay for the API, database, or TUI stacks.
"""

from __future__ import annotations

import select
import subprocess
import sys
import time
from pathlib import Path

FORBIDDEN = (
    "fastapi",
    "uvicorn",
    "asyncpg",
    "redis",
    "arq",
    "textual",
    "runpod",
    "mcp",
    "prometheus_client",
)


def _imported_modules(argv: list[str], home: Path) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-X", "importtime", "-m", "pitwall", *argv],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        check=False,
    )
    modules = set()
    for line in result.stderr.splitlines():
        if line.startswith("import time:") and "|" in line:
            modules.add(line.rsplit("|", 1)[1].strip())
    assert modules, result.stderr[-400:]
    return modules


def test_shim_path_imports_no_heavy_modules(tmp_path: Path) -> None:
    for command in ("_shim", "_steer-gate"):
        modules = _imported_modules(["agents", command, "--help"], tmp_path)
        loaded = sorted(name for name in modules if name.split(".")[0] in FORBIDDEN)
        assert loaded == [], command
        assert "pitwall.agents.dispatch" in modules, command


def test_inbox_watch_imports_no_heavy_modules(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-X", "importtime", "-m", "pitwall", "agents", "inbox", "--watch"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    stderr_lines: list[str] = []
    try:
        assert process.stderr is not None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stderr], [], [], 0.1)
            if not ready:
                continue
            line = process.stderr.readline()
            if not line:
                break
            stderr_lines.append(line)
            if "| pitwall.agents.ask_watch" in line:
                break
    finally:
        if process.poll() is None:
            process.terminate()
        _, remaining = process.communicate(timeout=5)
        stderr_lines.append(remaining)
    stderr = "".join(stderr_lines)
    modules = {
        line.rsplit("|", 1)[1].strip()
        for line in stderr.splitlines()
        if line.startswith("import time:") and "|" in line
    }
    assert "pitwall.agents.ask_watch" in modules, stderr[-1200:]
    loaded = sorted(name for name in modules if name.split(".")[0] in FORBIDDEN)
    assert loaded == []
