"""Read and rewrite the ``[agents.profiles]`` tables of a pitwall.toml file (stdlib only).

Only ``[agents.profiles...]`` sections are replaced on a save. Everything else in the file is kept
as written, and a save that would change any other value is refused.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from typing import Any

TABLE = ("agents", "profiles")
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_HEADER = re.compile(r"^\s*\[(?!\[)(?P<inner>.+?)\]\s*(?:#.*)?$")
_SECTION_ORDER = ("defaults", "endpoints", "harnesses", "models", "pitwall")


class ProfilesTomlError(ValueError):
    """The file cannot be rewritten without changing something other than the profiles."""


def _key(name: str) -> str:
    return name if _BARE_KEY.fullmatch(name) else _string(name)


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_scalar(item) for item in value) + "]"
    raise ProfilesTomlError(f"cannot write a {type(value).__name__} value to TOML")


def _emit(path: tuple[str, ...], table: Mapping[str, Any], out: list[str], *, force: bool) -> None:
    scalars = [
        (key, value)
        for key, value in sorted(table.items())
        if value is not None and value != [] and not isinstance(value, Mapping)
    ]
    if scalars or force:
        out.append("[" + ".".join(_key(part) for part in path) + "]")
        out.extend(f"{_key(key)} = {_scalar(value)}" for key, value in scalars)
        out.append("")
    for key, value in sorted(table.items()):
        if isinstance(value, Mapping) and value:
            _emit((*path, key), value, out, force=False)


def render(document: Mapping[str, Any]) -> str:
    """The ``[agents.profiles...]`` tables for a validated profiles document."""
    body = {key: value for key, value in document.items() if key != "schemaVersion"}
    out: list[str] = []
    for section in sorted(body, key=lambda name: _SECTION_ORDER.index(name)):
        value = body[section]
        if isinstance(value, Mapping):
            _emit((*TABLE, section), value, out, force=section == "defaults")
    return "\n".join(out).rstrip("\n") + "\n"


def _header_path(line: str) -> tuple[str, ...] | None:
    match = _HEADER.match(line)
    if match is None:
        return None
    try:
        parsed: Any = tomllib.loads(f"[{match.group('inner')}]\n")
    except tomllib.TOMLDecodeError:
        return None
    path: list[str] = []
    while isinstance(parsed, dict) and len(parsed) == 1:
        (name, parsed), *_ = parsed.items()
        path.append(name)
    return tuple(path)


def _without_profiles(text: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("["):
            path = _header_path(line)
            if path is not None or stripped.startswith("[["):
                skipping = path is not None and path[: len(TABLE)] == TABLE
        if not skipping:
            kept.append(line)
    return "".join(kept)


def _without_table(data: Mapping[str, Any]) -> dict[str, Any]:
    rest = json.loads(json.dumps(data, default=str))
    agents = rest.get("agents")
    if isinstance(agents, dict):
        agents.pop(TABLE[1], None)
        if not agents:
            del rest["agents"]
    return dict(rest)


def _toml_problem(exc: tomllib.TOMLDecodeError) -> str:
    """A TOML error's position only: tomllib's own text can quote keys from the file."""
    return f"invalid TOML at line {exc.lineno}, column {exc.colno}"


def parse(text: str) -> dict[str, Any]:
    """The parsed ``[agents.profiles]`` table of ``text`` (empty when absent)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfilesTomlError(_toml_problem(exc)) from None
    agents = data.get("agents", {})
    if not isinstance(agents, dict):
        raise ProfilesTomlError("[agents] must be a table")
    profiles = agents.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ProfilesTomlError("[agents.profiles] must be a table")
    return dict(profiles)


def replace_profiles(text: str, document: Mapping[str, Any]) -> str:
    """``text`` with its ``[agents.profiles]`` tables replaced by ``document``'s."""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfilesTomlError(f"the existing file is {_toml_problem(exc)}") from None
    rest = _without_profiles(text).rstrip()
    rendered = render(document)
    result = f"{rest}\n\n{rendered}" if rest else rendered
    try:
        after = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise ProfilesTomlError(f"the rewritten file would be {_toml_problem(exc)}") from None
    if _without_table(after) != _without_table(before):
        raise ProfilesTomlError("the rewrite would change settings outside [agents.profiles]")
    return result
