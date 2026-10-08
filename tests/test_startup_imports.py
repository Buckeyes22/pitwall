"""The shim and steer-gate paths start without the service dependencies.

Harness shims and the steering hook run once per tool call, so ``pitwall agents _shim`` and
``pitwall agents _steer-gate`` must not pay for the API, database, or TUI stacks.
"""

from __future__ import annotations

import subprocess
import sys
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
