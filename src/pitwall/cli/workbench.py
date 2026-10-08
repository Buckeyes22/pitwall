"""``pitwall workbench``: thin entry for :func:`pitwall.workbench.cli.main`."""

from __future__ import annotations


def cmd_workbench(argv: list[str]) -> int:
    from pitwall.workbench.cli import main

    return main(argv)
