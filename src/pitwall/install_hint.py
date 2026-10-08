"""The one place that spells the command users run to install this release of Pitwall."""

from __future__ import annotations

from pitwall import __version__

_RELEASES = "https://github.com/Buckeyes22/pitwall/releases/download/"


def wheel_url(version: str | None = None) -> str:
    """The GitHub release wheel for *version* (default: the running version)."""

    version = version or __version__
    return f"{_RELEASES}v{version}/pitwall-{version}-py3-none-any.whl"


def install_command(version: str | None = None) -> str:
    """The ``uv tool install`` command for *version* (default: the running version)."""

    return f"uv tool install --python 3.14 {wheel_url(version)}"
