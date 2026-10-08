"""Narrowing helpers for the untyped JSON that Pi events and session files carry."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping

JsonObject = dict[str, object]


def as_object(value: object) -> JsonObject | None:
    """The value as a JSON object, or ``None``."""
    return value if isinstance(value, dict) else None


def as_list(value: object) -> list[object] | None:
    """The value as a JSON array, or ``None``."""
    return value if isinstance(value, list) else None


def as_str(value: object) -> str | None:
    """The value when it is a string, or ``None``."""
    return value if isinstance(value, str) else None


def as_number(value: object) -> int | float | None:
    """The value when it is a finite number (never a bool), or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if math.isfinite(value) else None


def non_negative(value: object) -> int | float | None:
    """The value when it is a finite, non-negative number, or ``None``."""
    number = as_number(value)
    return number if number is not None and number >= 0 else None


def first_non_negative(record: Mapping[str, object], *keys: str) -> int | float | None:
    """The first of ``keys`` holding a finite, non-negative number."""
    for key in keys:
        value = non_negative(record.get(key))
        if value is not None:
            return value
    return None


def dig(value: object, *path: str | int) -> object | None:
    """Follow object keys and array indexes; anything missing along the way is ``None``."""
    current: object | None = value
    for step in path:
        if isinstance(step, int):
            items = as_list(current)
            current = (
                items[step] if items is not None and -len(items) <= step < len(items) else None
            )
        else:
            record = as_object(current)
            current = record.get(step) if record is not None else None
    return current


def get_str(record: object, key: str) -> str | None:
    """``record[key]`` when ``record`` is an object holding a string there."""
    item = as_object(record)
    return as_str(item.get(key)) if item is not None else None


def get_number(record: object, key: str) -> int | float | None:
    """``record[key]`` when ``record`` is an object holding a finite number there."""
    item = as_object(record)
    return as_number(item.get(key)) if item is not None else None


def get_object(record: object, key: str) -> JsonObject | None:
    """``record[key]`` when it is an object."""
    item = as_object(record)
    return as_object(item.get(key)) if item is not None else None


def get_list(record: object, key: str) -> list[object] | None:
    """``record[key]`` when it is an array."""
    item = as_object(record)
    return as_list(item.get(key)) if item is not None else None


def parse_json(text: str) -> object | None:
    """Parse JSON text; malformed text is ``None`` (evidence stays unavailable)."""
    try:
        parsed: object = json.loads(text)
    except ValueError:
        # Malformed or truncated evidence is treated as absent, never as a match.
        return None
    return parsed


def parse_json_object(text: str) -> JsonObject | None:
    """Parse JSON text that must be an object."""
    return as_object(parse_json(text))


def jsonl_objects(content: str) -> list[JsonObject]:
    """Every line of ``content`` that parses to a JSON object, in order."""
    found: list[JsonObject] = []
    for line in content.split("\n"):
        value = parse_json_object(line)
        if value is not None:
            found.append(value)
    return found


def result_text(value: object) -> str:
    """Flatten a tool result to its text, following only text, content, and output keys."""
    if isinstance(value, str):
        return value
    items = as_list(value)
    if items is not None:
        return "\n".join(result_text(item) for item in items)
    record = as_object(value)
    if record is not None:
        return "\n".join(
            result_text(item) if key in ("text", "content", "output") else ""
            for key, item in record.items()
        )
    return ""


def all_text(value: object) -> str:
    """Flatten every string reachable from a persisted record (offline reevaluation)."""
    if isinstance(value, str):
        return value
    items = as_list(value)
    if items is not None:
        return "\n".join(all_text(item) for item in items)
    record = as_object(value)
    if record is not None:
        return "\n".join(all_text(item) for item in record.values())
    return ""
