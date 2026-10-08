#!/usr/bin/env python3
"""Write the generated shims and a `pitwall` stand-in into a directory, for the CI shim checks.

`<dir>/scripts/*.sh` are the shims `pitwall agents install` writes; `<dir>/bin/pitwall` runs this
checkout's agents runtime, so the shims can be exercised without installing anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pitwall.agents.installation import (  # noqa: E402  # reason: sys.path bootstrap
    shim_names,
    shim_script,
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: write_shims.py <directory>", file=sys.stderr)
        return 64
    target = Path(argv[1])
    scripts = target / "scripts"
    bin_dir = target / "bin"
    scripts.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in shim_names():
        (scripts / name).write_text(shim_script(name), encoding="utf-8")
        (scripts / name).chmod(0o755)
    stub = bin_dir / "pitwall"
    stub.write_text(
        f'#!/bin/sh\n[ "$1" = agents ] && shift\nexec "{sys.executable}" -m pitwall.agents "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
