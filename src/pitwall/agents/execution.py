"""Resolve child execution without rediscovering a source checkout."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

DIST_NAME = "pitwall"


class ExecutionError(ValueError):
    """Raised when an execution handoff is absent or cannot be verified."""


@dataclass(frozen=True, slots=True)
class ExecutionDescriptor:
    """The stable command prefix for a recursive Agent Routing invocation."""

    mode: str
    argv: tuple[str, ...]


def _running_interpreter() -> Path:
    candidate = Path(os.path.abspath(sys.executable))
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ExecutionError(f"running Python interpreter is not executable: {candidate}")
    return candidate


def child_execution(env: Mapping[str, str]) -> ExecutionDescriptor:
    """Build the only supported recursive command descriptor."""

    del env
    return ExecutionDescriptor("artifact", (str(_running_interpreter()), "-m", "pitwall.agents"))


def distribution_version(env: Mapping[str, str]) -> str:
    """Read the installed distribution's version."""

    del env
    try:
        return metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError as exc:
        raise ExecutionError(f"installed distribution metadata is missing for {DIST_NAME}") from exc
