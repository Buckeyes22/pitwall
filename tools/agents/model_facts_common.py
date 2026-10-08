#!/usr/bin/env python3
"""Shared constants and file helpers for the model facts pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FACTS_DIR = "docs/agents/model-facts"
CACHE_DIR = ".cache"
UNIT_KINDS = ("families", "harnesses", "hosts")
REQUIRED_PAGE_TYPES = {
    "families": ("model", "effort", "guidance", "changes"),
    "harnesses": ("harness", "changes"),
    "hosts": ("model", "changes"),
}
NOTICE_DAYS = 45
STOP_DAYS = 30
STALE_DAYS = 45
MAX_TEXT_CHARS = 240
MAX_QUOTE_WORDS = 25
COPIED_RUN_WORDS = 12
REGISTRY = "src/pitwall/agents/resources/config/harness-registry.json"
CATALOG = "src/pitwall/agents/resources/config/model-catalog.json"
HOSTS = ("claude", "codex", "copilot")
SKILL = "plugins/{host}/skills/subagent-model-routing/SKILL.md"
REFERENCE = "plugins/{host}/skills/subagent-model-routing/references/model-prompting.md"
LEDGER = "plugins/claude/skills/subagent-model-routing/ledger"

MODEL_FACT_TYPES: dict[str, str] = {
    "contextWindow": "int",
    "maxInput": "int",
    "maxOutput": "int-or-unlimited",
    "inputModalities": "list",
    "knowledgeCutoff": "str",
    "effortValues": "list",
    "effortDefault": "str",
    "effortOnOther": "effort-on-other",
    "thinkingCanDisable": "bool",
    "mustReturnReasoning": "bool",
    "samplingDefaults": "sampling",
    "samplingLocked": "bool",
    "license": "license",
    "released": "date",
    "retires": "date",
    "successor": "str",
    "revision": "str",
    "parameters": "int",
}
HOST_FACT_TYPES: dict[str, str] = {**MODEL_FACT_TYPES, "webSearch": "bool", "batch": "bool"}
HARNESS_FACT_TYPES: dict[str, str] = {
    "headlessFlag": "str",
    "outputFormats": "list",
    "effortFlag": "str",
    "permissionDefault": "str",
    "sandboxDefault": "str",
    "instructionFiles": "list",
    "subagents": "bool",
}
EXTRACTED_KEYS = ("revision", "parameters", "license", "contextWindow", "samplingDefaults")


class FactsError(Exception):
    """A model facts file is missing or cannot be read."""


def facts_root(root: Path = ROOT) -> Path:
    return root / FACTS_DIR


def unit_names(root: Path = ROOT) -> list[str]:
    """Every unit that has a source list, as `<kind>/<name>`, sorted."""
    names: list[str] = []
    for kind in UNIT_KINDS:
        for path in sorted((facts_root(root) / kind).glob("*/sources.json")):
            names.append(f"{kind}/{path.parent.name}")
    return names


def unit_kind(unit: str) -> str:
    kind = unit.split("/", 1)[0]
    if kind not in UNIT_KINDS or "/" not in unit:
        raise FactsError(f"not a unit name: {unit}")
    return kind


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FactsError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FactsError(f"{path}: {exc}") from exc


def write_text_atomic(path: Path, text: str) -> None:
    """Write through a temporary file so an interrupted run leaves no partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def write_json(path: Path, document: Any) -> None:
    write_text_atomic(path, json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def load_sources(unit: str, root: Path = ROOT) -> dict[str, Any]:
    document = read_json(facts_root(root) / unit / "sources.json")
    if not isinstance(document, dict):
        raise FactsError(f"{unit}/sources.json is not an object")
    return document


def load_facts(unit: str, root: Path = ROOT) -> dict[str, Any] | None:
    """The facts file of a unit, or None when the unit has no facts yet."""
    path = facts_root(root) / unit / "facts.json"
    if not path.exists():
        return None
    document = read_json(path)
    if not isinstance(document, dict):
        raise FactsError(f"{unit}/facts.json is not an object")
    return document


def cache_path(unit: str, source_id: str, root: Path = ROOT) -> Path:
    return facts_root(root) / CACHE_DIR / unit / source_id


def cache_file(unit: str, source_id: str, root: Path = ROOT) -> Path:
    """Where the normalised text of a one-file source is kept. Identifiers may contain dots."""
    base = cache_path(unit, source_id, root)
    return base.parent / f"{base.name}.txt"


def cached_text(unit: str, source_id: str, root: Path = ROOT) -> str | None:
    """The cached text of a source, or None when it has not been fetched here."""
    base = cache_path(unit, source_id, root)
    if base.is_dir():
        parts = [
            path.read_text(encoding="utf-8") for path in sorted(base.iterdir()) if path.is_file()
        ]
        return "\n".join(parts)
    single = cache_file(unit, source_id, root)
    return single.read_text(encoding="utf-8") if single.exists() else None
