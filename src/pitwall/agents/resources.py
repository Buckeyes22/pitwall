"""Access canonical JSON data shipped with :mod:`pitwall.agents`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from pathlib import Path, PurePosixPath
from typing import Any


class ResourceError(ValueError):
    """A packaged resource name or payload is invalid."""


def _resource(relative: str) -> Traversable:
    candidate = PurePosixPath(relative)
    if (
        candidate.is_absolute()
        or len(candidate.parts) != 2
        or candidate.parts[0] not in {"config", "schemas"}
        or candidate.suffix != ".json"
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ResourceError(f"invalid packaged resource name: {relative!r}")
    resource = files("pitwall.agents").joinpath("resources", *candidate.parts)
    if not resource.is_file():
        raise ResourceError(f"packaged resource does not exist: {relative}")
    return resource


def read_resource_text(relative: str) -> str:
    """Read one canonical UTF-8 packaged resource."""

    try:
        return _resource(relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceError(f"cannot read packaged resource {relative}: {exc}") from exc


def read_resource_json(relative: str) -> Any:
    """Decode one canonical packaged JSON resource."""

    try:
        return json.loads(read_resource_text(relative))
    except json.JSONDecodeError as exc:
        raise ResourceError(
            f"packaged resource {relative} is malformed at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc


@contextmanager
def resource_path(relative: str) -> Iterator[Path]:
    """Materialize one packaged resource for a path-only consumer."""

    with as_file(_resource(relative)) as path:
        yield path
