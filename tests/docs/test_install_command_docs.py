"""The README and this release's notes give the install command pitwall.install_hint spells."""

from __future__ import annotations

import re
from pathlib import Path

from pitwall import __version__
from pitwall.install_hint import install_command

ROOT = Path(__file__).resolve().parents[2]
_WHEEL_INSTALL = re.compile(r"uv tool install [^\n`]*\.whl")


def test_wheel_install_commands_match_install_hint() -> None:
    expected = install_command()
    for path in (ROOT / "README.md", ROOT / f"docs/releases/v{__version__}.md"):
        found = _WHEEL_INSTALL.findall(path.read_text(encoding="utf-8"))
        assert found, f"{path.name} gives no wheel install command"
        assert set(found) == {expected}, f"{path.name}: {found} != {expected}"
