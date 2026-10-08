"""docs/sdlc references code as module:symbol, never as path:line."""

from __future__ import annotations

import re
from pathlib import Path

SDLC = Path(__file__).resolve().parents[2] / "docs" / "sdlc"
LINE_REFERENCE = re.compile(r"[\w/.\-]+\.(?:py|toml|ts|js|sh|json|md|ya?ml|sql):\d+")


def test_sdlc_has_no_file_line_references() -> None:
    offenders = [
        f"{page.name}:{number}: {match.group(0)}"
        for page in sorted(SDLC.glob("*.md"))
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1)
        for match in LINE_REFERENCE.finditer(line)
    ]
    assert not offenders, "\n".join(offenders[:20])
