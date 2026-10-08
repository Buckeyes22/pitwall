"""The pinned Pi versions, read from the installer catalogue that drives `pitwall agents setup pi`."""

from __future__ import annotations

import json
from importlib import resources

PI_PACKAGE = "@earendil-works/pi-coding-agent"
SUBAGENTS_PACKAGE = "@tintinweb/pi-subagents"


def _load() -> tuple[str, str]:
    text = (
        resources.files("pitwall")
        .joinpath("agents/resources/config/harness-installers.json")
        .read_text(encoding="utf-8")
    )
    pi = json.loads(text)["harnesses"]["pi"]["platforms"]["linux"]
    (extra,) = pi["extraPackages"]
    if pi["package"] != PI_PACKAGE or extra["package"] != SUBAGENTS_PACKAGE:
        raise RuntimeError("harness-installers.json pi entry does not name the Workbench packages")
    return str(pi["version"]), str(extra["version"])


PINNED_PI_VERSION, PINNED_SUBAGENTS_VERSION = _load()
