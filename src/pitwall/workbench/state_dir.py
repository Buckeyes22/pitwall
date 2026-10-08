"""Where the workbench keeps host-local state: ``$XDG_STATE_HOME`` when set, else ``~/.local/state``.

The Pi extensions compute the same directories (``shared-admission.ts``, ``account-budget.ts``),
so the Python side and the TypeScript side agree on one location.
"""

from __future__ import annotations

import os
from pathlib import Path


def workbench_state_dir(*parts: str) -> Path:
    """``<state home>/pitwall/pi-workbench/<parts...>``; an empty ``XDG_STATE_HOME`` is unset."""
    configured = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(configured) if configured else Path.home() / ".local" / "state"
    return base.joinpath("pitwall", "pi-workbench", *parts)


__all__ = ["workbench_state_dir"]
