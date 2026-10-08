"""``pitwall dashboard``."""

from __future__ import annotations

import argparse
import os
import sys

from pitwall.runpod_credentials import resolve_runpod_api_key


def cmd_dashboard(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pitwall dashboard",
        description="Launch the Textual operator console.",
    )
    parser.parse_args(argv)

    from pitwall.personal.backend import select_backend

    if select_backend() == "personal" and resolve_runpod_api_key(os.environ)[0] is None:
        if sys.stdin.isatty():
            from pitwall.cli.personal import run_personal_setup

            run_personal_setup()
        else:
            print(
                "no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY",
                file=sys.stderr,
            )
            return 2

    from pitwall.tui import PitwallApp

    PitwallApp().run()
    return 0
