"""The install command shown to users comes from one function keyed on the running version."""

from __future__ import annotations

from pitwall import __version__
from pitwall.install_hint import install_command, wheel_url


def test_install_command_is_derived_from_the_running_version() -> None:
    assert install_command() == (
        "uv tool install --python 3.14 "
        f"https://github.com/Buckeyes22/pitwall/releases/download/v{__version__}/"
        f"pitwall-{__version__}-py3-none-any.whl"
    )


def test_install_command_accepts_an_explicit_version() -> None:
    assert wheel_url("1.2.3").endswith("/v1.2.3/pitwall-1.2.3-py3-none-any.whl")
    assert install_command("1.2.3").startswith("uv tool install --python 3.14 https://")
