"""Extract OmniRoute's free-tier catalog from its TypeScript sources without Node.

Regex-parses ``open-sse/config/freeModelCatalog.data.ts``,
``src/shared/constants/config.ts`` and
``open-sse/config/providers/registry/<id>/index.ts`` into the JSON payload that
``pitwall.gateway_catalog.sync`` consumes. Inputs are a mapping of package-relative
path to source text, so the same code reads a tarball held in memory or a directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

DATA_PATH = "open-sse/config/freeModelCatalog.data.ts"
ENDPOINTS_PATH = "src/shared/constants/config.ts"
REGISTRY_PREFIX = "open-sse/config/providers/registry/"
REGISTRY_SUFFIX = "/index.ts"

_REGISTRY_FIELDS = ("baseUrl", "format", "executor", "authType")
_NOT_LINE_END = r"[^\n\r  ]"
_FIELD_RE = re.compile(rf"^(\w+):\s*({_NOT_LINE_END}*)\Z", re.ASCII)
_INT_RE = re.compile(r"-?[0-9]+")
_ENDPOINT_RE = re.compile(r'("?[\w-]+"?)\s*:\s*"([^"]*)"\s*', re.ASCII)


def is_registry_index(path: str) -> bool:
    """True for ``open-sse/config/providers/registry/<id>/index.ts`` with a single-segment id."""
    if not (path.startswith(REGISTRY_PREFIX) and path.endswith(REGISTRY_SUFFIX)):
        return False
    provider = path[len(REGISTRY_PREFIX) : -len(REGISTRY_SUFFIX)]
    return bool(provider) and "/" not in provider


def wanted_paths(paths: Iterator[str] | list[str]) -> list[str]:
    """The subset of package-relative *paths* the extractor reads."""
    return [p for p in paths if p in (DATA_PATH, ENDPOINTS_PATH) or is_registry_index(p)]


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*[\s\S]*?\*/", " ", src)
    return re.sub(r"(^|[^:])//[^\n]*", r"\1 ", src)


def _parse_string_literal(text: str, key: str) -> str:
    match = re.search(rf'export const {re.escape(key)}\s*=\s*"([^"]*)"', text)
    if match is None:
        raise ValueError(f"cannot find {key}")
    return match.group(1)


def _split_top_level(body: str, quotes: str) -> list[str]:
    """Split on commas outside quoted strings (no escape handling, as upstream writes none)."""
    out: list[str] = []
    buf: list[str] = []
    in_str: str | None = None
    for char in body:
        if in_str is not None:
            buf.append(char)
            if char == in_str:
                in_str = None
            continue
        if char in quotes:
            in_str = char
            buf.append(char)
            continue
        if char == ",":
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if value in ("true", "false"):
        return value == "true"
    if value == "null":
        return None
    if _INT_RE.fullmatch(value):
        return int(value)
    if len(value) >= 1 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _parse_row(raw: str) -> dict[str, Any]:
    trimmed = re.sub(r"[\s,}]+\Z", "", re.sub(r"^[\s,{]+", "", raw))
    row: dict[str, Any] = {}
    for part in _split_top_level(trimmed, "\"'"):
        match = _FIELD_RE.match(part)
        if match is None:
            continue
        row[match.group(1)] = _parse_value(match.group(2))
    return row


def _parse_budgets(text: str) -> list[dict[str, Any]]:
    start = text.find("export const FREE_MODEL_BUDGETS")
    if start == -1:
        raise ValueError("FREE_MODEL_BUDGETS not found")
    # The type annotation carries a `[]`; the array literal opens with the second `[`.
    arr_start = text.find("[", text.find("[", start) + 1)
    arr_end = text.find("]", arr_start)
    body = text[arr_start + 1 : arr_end]
    rows: list[dict[str, Any]] = []
    buf: list[str] = []
    depth = 0
    in_str: str | None = None
    for char in body:
        if in_str is not None:
            buf.append(char)
            if char == in_str:
                in_str = None
            continue
        if char in "\"'`":
            in_str = char
            buf.append(char)
            continue
        if char == "{":
            depth += 1
        if char == "}":
            depth -= 1
            buf.append(char)
            if depth == 0:
                rows.append(_parse_row("".join(buf).strip()))
                buf = []
            continue
        buf.append(char)
    return rows


def _parse_endpoints(text: str) -> dict[str, str]:
    start = text.find("export const PROVIDER_ENDPOINTS")
    if start == -1:
        raise ValueError("PROVIDER_ENDPOINTS not found")
    obj_start = text.find("{", start)
    obj_end = text.find("}", obj_start)
    endpoints: dict[str, str] = {}
    for entry in _split_top_level(text[obj_start + 1 : obj_end], "\"'`"):
        match = _ENDPOINT_RE.fullmatch(entry)
        if match is None:
            continue
        endpoints[match.group(1).strip('"')] = match.group(2)
    return endpoints


def _parse_registry_entry(text: str) -> dict[str, str]:
    entry: dict[str, str] = {}
    for field in _REGISTRY_FIELDS:
        match = re.search(rf'^\s+{field}:\s*"([^"]*)"', text, re.MULTILINE)
        if match is not None:
            entry[field] = match.group(1)
    return entry


def extract_catalog(files: Mapping[str, str]) -> dict[str, Any]:
    """Build the sync payload from package-relative source texts."""
    for required in (DATA_PATH, ENDPOINTS_PATH):
        if required not in files:
            raise ValueError(f"upstream package is missing {required}")
    data_text = _strip_comments(files[DATA_PATH])
    endpoints_text = _strip_comments(files[ENDPOINTS_PATH])
    registry = {
        path[len(REGISTRY_PREFIX) : -len(REGISTRY_SUFFIX)]: _parse_registry_entry(
            _strip_comments(text)
        )
        for path, text in sorted(files.items())
        if is_registry_index(path)
    }
    return {
        "curatedAt": _parse_string_literal(data_text, "FREE_CATALOG_CURATED_AT"),
        "budgets": _parse_budgets(data_text),
        "endpoints": _parse_endpoints(endpoints_text),
        "registry": registry,
    }


def read_package_dir(root: Path) -> dict[str, str]:
    """Load the files the extractor reads from an unpacked package directory."""
    files: dict[str, str] = {}
    for rel in (DATA_PATH, ENDPOINTS_PATH):
        files[rel] = (root / rel).read_text(encoding="utf-8")
    registry = root / REGISTRY_PREFIX.rstrip("/")
    if registry.is_dir():
        for index in sorted(registry.glob("*/index.ts")):
            files[index.relative_to(root).as_posix()] = index.read_text(encoding="utf-8")
    return files
