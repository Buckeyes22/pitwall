"""The pitwall-channel entry of a YAML harness config, written as one managed line.

Writes are line-based so the user's formatting and comments survive: the entry is one
``  pitwall-channel: {...}`` JSON-flow line (JSON is valid YAML) under a top-level block mapping,
and the section header above it is added only when missing. Reads and the post-write check use
:func:`yaml.safe_load`, so an entry that goose rewrote in block style, or that carries comments or
quoting, reads back as the harness would see it. A write that would not read back as intended, or
a file PyYAML cannot parse, is refused and the file is left unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

MARKER = "  # managed by pitwall\n"
#: Written after a section header that pitwall added, so removal never drops a user's own header.
HEADER_MARKER = "  # managed by pitwall"
_TOP_LEVEL = re.compile(r"^[^\s#-][^:]*:(?:\s|$)")
#: Characters YAML cannot hold literally in a quoted scalar (line breaks, non-printables, surrogates).
_YAML_ESCAPE = re.compile("[\x7f-\x9f\u2028\u2029\ud800-\udfff\ufeff\ufffe\uffff]")


class YamlChannelError(ValueError):
    """The YAML file cannot be edited safely."""


def _section(lines: list[str], section: str) -> tuple[int, int] | None:
    """``(header, end)`` of the top-level block mapping *section*, or None when absent."""
    header = f"{section}:"
    starts = [i for i, line in enumerate(lines) if line.split("#", 1)[0].rstrip() == header]
    if len(starts) > 1:
        raise YamlChannelError(f"duplicate top-level {section!r} sections")
    if not starts:
        if any(line.startswith(header) for line in lines):
            raise YamlChannelError(f"top-level {section!r} is not a block mapping")
        return None
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.startswith(("#", "---", " ")):
            continue
        if _TOP_LEVEL.match(line):
            end = index
            break
        raise YamlChannelError(
            f"cannot find where the {section!r} section ends: line {index + 1} is not "
            "a plain top-level key, so it will not be edited"
        )
    for line in lines[start + 1 : end]:
        text = line.strip()
        if text and not text.startswith("#"):
            indent = len(line) - len(line.lstrip(" "))
            if indent != 2:
                raise YamlChannelError(
                    f"{section!r} entries are indented {indent} spaces; "
                    "re-indent them with 2 spaces"
                )
            break
    return start, end


def _entry_span(lines: list[str], start: int, end: int, name: str) -> tuple[int, int, int] | None:
    """``(first, key, finish)`` of the *name* entry: its marker, key line, and children."""
    key = f"  {name}:"
    for index in range(start + 1, end):
        line = lines[index]
        if not line.startswith(key) or line[len(key) : len(key) + 1] not in ("", " ", "\r", "\n"):
            continue
        first = index - 1 if lines[index - 1].rstrip("\r\n") == MARKER.rstrip("\n") else index
        finish = index + 1
        while finish < end and (lines[finish].startswith("   ") or not lines[finish].strip()):
            finish += 1
        while finish > index + 1 and not lines[finish - 1].strip():
            finish -= 1
        return first, index, finish
    return None


def _parse_problem(exc: Exception) -> str:
    """A YAML error's class and position only: PyYAML's own text can quote the file."""
    name = type(exc).__name__
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        return f"invalid YAML ({name}) at line {mark.line + 1}, column {mark.column + 1}"
    position = getattr(exc, "position", None)
    where = f" at character {position + 1}" if isinstance(position, int) else ""
    return f"invalid YAML ({name}){where}"


def _load(text: str) -> dict[str, Any]:
    """The YAML document as a mapping; an empty document is ``{}``."""
    import yaml  # type: ignore[import-untyped]  # reason: PyYAML ships no type stubs

    try:
        data = yaml.safe_load(text)
    except (
        yaml.YAMLError,
        ValueError,
    ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
        raise YamlChannelError(_parse_problem(exc)) from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise YamlChannelError("the file's top level is not a mapping")
    return data


def _section_map(data: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    value = data.get(section)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise YamlChannelError(f"top-level {section!r} is not a mapping")
    return value


def read_entry(text: str, section: str, name: str) -> dict[str, Any] | None:
    """The *name* entry under *section*: ``{}`` when present but not a mapping, None when absent.

    Raises :class:`YamlChannelError` when the text is not valid YAML or *section* is not a mapping.
    """
    found = _section_map(_load(text), section)
    if name not in found:
        return None
    value = found[name]
    return dict(value) if isinstance(value, Mapping) else {}


def _without_entry(data: Mapping[str, Any], section: str, name: str) -> dict[str, Any]:
    """*data* minus the *name* entry, and minus *section* itself once it is empty."""
    rest = dict(data)
    kept = {key: value for key, value in _section_map(data, section).items() if key != name}
    if kept:
        rest[section] = kept
    else:
        rest.pop(section, None)
    return rest


def _check_result(
    before: str, after: str, section: str, name: str, entry: Mapping[str, Any] | None
) -> None:
    """Refuse a planned write that does not read back as intended."""
    expected = None if entry is None else json.loads(json.dumps(dict(entry)))
    loaded = _load(after)
    got = _section_map(loaded, section).get(name)
    if got != expected or _without_entry(loaded, section, name) != _without_entry(
        _load(before), section, name
    ):
        raise YamlChannelError("the planned edit would not read back as intended; not editing")


def plan_text(
    before: str,
    section: str,
    name: str,
    entry: Mapping[str, Any] | None,
    is_ours: Callable[[Mapping[str, Any]], bool],
) -> str:
    """*before* with the *name* entry set to *entry*, or removed when *entry* is None."""
    if before and not before.endswith("\n"):
        raise YamlChannelError("the file must end with a newline before it can be edited safely")
    existing = read_entry(before, section, name)
    lines = before.splitlines(keepends=True)
    bounds = _section(lines, section)
    span = _entry_span(lines, *bounds, name) if bounds is not None else None
    if (span is None) != (existing is None):
        raise YamlChannelError(f"cannot locate the existing {name} entry line to edit it")
    if span is None and entry is None:
        return before
    if span is not None:
        if not is_ours(existing or {}):
            raise YamlChannelError(
                f"an existing {name} entry is not managed by pitwall; rename or remove it"
            )
        first, _, finish = span
        del lines[first:finish]
        bounds = _section(lines, section)
    if entry is None:
        if (
            bounds is not None
            and HEADER_MARKER in lines[bounds[0]]
            and not any(line.strip() for line in lines[bounds[0] + 1 : bounds[1]])
        ):
            del lines[bounds[0] : bounds[1]]
            while lines and not lines[-1].strip() and bounds[0] >= len(lines):
                lines.pop()
        after = "".join(lines)
        _check_result(before, after, section, name, None)
        return after
    flow = _YAML_ESCAPE.sub(
        lambda match: f"\\u{ord(match.group()):04x}", json.dumps(dict(entry), ensure_ascii=False)
    )
    rendered = [MARKER, f"  {name}: {flow}\n"]
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend([f"{section}:{HEADER_MARKER}\n", *rendered])
    else:
        start, end = bounds
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = rendered
    after = "".join(lines)
    _check_result(before, after, section, name, entry)
    return after
