"""Replace the committed SBOM with a fresh export only when its content changed.

A CycloneDX export carries a new serial number and timestamp on every run. Those two fields name
the generation event, so a regeneration that changes nothing else keeps the committed file.

    uv run --frozen python docs/sbom/refresh_sbom.py FRESH.json docs/sbom/pitwall-sbom.cdx.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _content(document: dict[str, Any]) -> dict[str, Any]:
    stable = dict(document)
    stable.pop("serialNumber", None)
    stable["metadata"] = {k: v for k, v in document["metadata"].items() if k != "timestamp"}
    return stable


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    fresh, committed = Path(argv[0]), Path(argv[1])
    new = json.loads(fresh.read_text(encoding="utf-8"))
    if committed.exists() and _content(
        json.loads(committed.read_text(encoding="utf-8"))
    ) == _content(new):
        print(f"{committed}: unchanged (only the serial number and timestamp differ)")
        return 0
    committed.write_text(fresh.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"{committed}: regenerated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
