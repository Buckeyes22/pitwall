#!/usr/bin/env python3
"""Validate the model facts files against their schemas, their sources, and the registry."""

from __future__ import annotations

import argparse
import datetime
import importlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.agents.model_facts_common import (  # noqa: E402  # reason: module setup (ROOT or sys.path) must run before the package imports
    CACHE_DIR,
    COPIED_RUN_WORDS,
    EXTRACTED_KEYS,
    FACTS_DIR,
    HARNESS_FACT_TYPES,
    HOST_FACT_TYPES,
    MAX_QUOTE_WORDS,
    MAX_TEXT_CHARS,
    MODEL_FACT_TYPES,
    REGISTRY,
    REQUIRED_PAGE_TYPES,
    STALE_DAYS,
    STOP_DAYS,
    FactsError,
    cached_text,
    facts_root,
    load_facts,
    load_sources,
    read_json,
    unit_kind,
    unit_names,
)

DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


@dataclass(frozen=True)
class Problem:
    level: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


def _schema(name: str, root: Path) -> Any:
    return read_json(facts_root(root) / "schemas" / f"{name}.schema.json")


def _schema_problems(document: Any, name: str, where: str, root: Path) -> list[Problem]:
    jsonschema = importlib.import_module("jsonschema")
    validator = jsonschema.Draft202012Validator(_schema(name, root))
    problems = []
    for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path)):
        path = "/".join(str(part) for part in error.absolute_path) or "(root)"
        problems.append(Problem("error", f"{where} {path}", error.message))
    return problems


def _type_ok(kind: str, value: Any, key: str = "") -> bool:
    if kind == "int":
        return type(value) is int and value > 0
    if kind == "int-or-unlimited":
        return value == "unlimited" or (type(value) is int and value > 0)
    if kind == "list":
        return (
            isinstance(value, list)
            and (bool(value) or key == "effortValues")
            and all(isinstance(item, str) and item for item in value)
        )
    if kind == "str":
        return isinstance(value, str) and bool(value)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "date":
        return isinstance(value, str) and DATE.fullmatch(value) is not None
    if kind == "sampling":
        return (
            isinstance(value, dict)
            and bool(value)
            and set(value) <= {"temperature", "top_p", "top_k"}
            and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value.values()
            )
        )
    if kind == "license":
        return (
            isinstance(value, dict)
            and set(value) == {"name", "url"}
            and isinstance(value["name"], str)
            and (
                value["url"] is None
                or (isinstance(value["url"], str) and value["url"].startswith("https://"))
            )
        )
    if kind == "effort-on-other":
        if not isinstance(value, dict):
            return False
        behaviour = value.get("behaviour")
        if behaviour == "coerced":
            mapping = value.get("map")
            return (
                set(value) == {"behaviour", "map"}
                and isinstance(mapping, dict)
                and bool(mapping)
                and all(
                    isinstance(key, str) and isinstance(item, str) for key, item in mapping.items()
                )
            )
        return behaviour in {"error", "unknown"} and set(value) == {"behaviour"}
    return False


def _fact_table(kind: str) -> dict[str, str]:
    return {
        "families": MODEL_FACT_TYPES,
        "hosts": HOST_FACT_TYPES,
        "harnesses": HARNESS_FACT_TYPES,
    }[kind]


def _check_fact(
    key: str,
    fact: dict[str, Any],
    table: dict[str, str],
    ids: dict[str, dict[str, Any]],
    where: str,
) -> list[Problem]:
    problems: list[Problem] = []
    if key not in table:
        return [Problem("error", where, f"{key} is not a fact key for this unit")]
    if not _type_ok(table[key], fact["value"], key):
        problems.append(Problem("error", where, f"{key} value has the wrong type for {table[key]}"))
    for evidence in fact["evidence"]:
        source = ids.get(evidence["source"])
        if source is None:
            problems.append(
                Problem(
                    "error", where, f"evidence source {evidence['source']} is not in sources.json"
                )
            )
        elif fact["method"] == "extracted" and source["fetch"] != "huggingface":
            problems.append(
                Problem(
                    "error",
                    where,
                    f"{key} is extracted but {source['id']} is not a Hugging Face source",
                )
            )
        if "quote" in evidence and len(evidence["quote"].split()) > MAX_QUOTE_WORDS:
            problems.append(
                Problem("error", where, f"quote is longer than {MAX_QUOTE_WORDS} words")
            )
    if fact["method"] == "extracted" and key not in EXTRACTED_KEYS:
        problems.append(
            Problem(
                "error", where, f"{key} cannot be extracted; only {', '.join(EXTRACTED_KEYS)} can"
            )
        )
    if any(evidence.get("disagrees") for evidence in fact["evidence"]) and "note" not in fact:
        problems.append(Problem("error", where, f"{key} has a disagreeing source but no note"))
    return problems


def _words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def copied_run(text: str, source: str, length: int = COPIED_RUN_WORDS) -> str | None:
    """The first run of `length` consecutive words `text` shares with `source`, if any."""
    words = _words(text)
    if len(words) < length:
        return None
    haystack = " " + " ".join(_words(source)) + " "
    for start in range(len(words) - length + 1):
        run = " ".join(words[start : start + length])
        if f" {run} " in haystack:
            return run
    return None


def validate_unit(
    unit: str, root: Path, today: datetime.date
) -> tuple[list[Problem], dict[str, Any] | None]:
    kind = unit_kind(unit)
    sources = load_sources(unit, root)
    problems = _schema_problems(sources, "sources", f"{unit}/sources.json", root)
    facts = load_facts(unit, root)
    if facts is not None:
        problems += _schema_problems(facts, "facts", f"{unit}/facts.json", root)
    if problems:
        return problems, None
    if sources["unit"] != unit or (facts is not None and facts["unit"] != unit):
        problems.append(Problem("error", unit, "the unit field does not match the directory"))
    ids: dict[str, dict[str, Any]] = {}
    for source in sources["sources"]:
        if source["id"] in ids:
            problems.append(
                Problem("error", f"{unit}/sources.json", f"source id {source['id']} is repeated")
            )
        ids[source["id"]] = source
        if (
            source.get("reviewedHash")
            and source.get("observedHash")
            and source["reviewedHash"] != source["observedHash"]
        ):
            problems.append(
                Problem(
                    "warning", f"{unit} {source['id']}", "pending: changed since it was reviewed"
                )
            )
        if source["fetch"] == "manual":
            reviewed = source.get("reviewedAt")
            if (
                reviewed is None
                or (today - datetime.date.fromisoformat(reviewed)).days > STALE_DAYS
            ):
                problems.append(
                    Problem(
                        "warning",
                        f"{unit} {source['id']}",
                        f"stale: manual source reviewed {reviewed or 'never'}",
                    )
                )
    covered = {source["pageType"] for source in sources["sources"]} | {
        item["pageType"] for item in sources["notPublished"]
    }
    for page_type in REQUIRED_PAGE_TYPES[kind]:
        if page_type not in covered:
            problems.append(
                Problem(
                    "error",
                    f"{unit}/sources.json",
                    f"page type {page_type} has no source and no notPublished entry",
                )
            )
    if facts is None:
        return problems, None
    table = _fact_table(kind)
    for key, fact in facts.get("facts", {}).items():
        problems += _check_fact(key, fact, table, ids, f"{unit} facts.{key}")
    model_ids = set(facts.get("models", {}))
    for model_id, model in facts.get("models", {}).items():
        where = f"{unit} models.{model_id}"
        for key, fact in model["facts"].items():
            problems += _check_fact(key, fact, table, ids, f"{where}.{key}")
        if model.get("status") in {"retiring", "retired"} and "retires" not in model["facts"]:
            problems.append(
                Problem("error", where, f"status {model['status']} needs a retires fact")
            )
        if kind == "hosts" and "of" not in model:
            problems.append(
                Problem("error", where, "a host model names the family model it serves with `of`")
            )
    for statement in facts["guidance"]:
        where = f"{unit} guidance.{statement['id']}"
        source = ids.get(statement["source"])
        if source is None:
            problems.append(
                Problem("error", where, f"source {statement['source']} is not in sources.json")
            )
        elif statement["class"] != source["kind"]:
            problems.append(
                Problem(
                    "error",
                    where,
                    f"class {statement['class']} is not the source kind {source['kind']}",
                )
            )
        if len(statement["text"]) > MAX_TEXT_CHARS:
            problems.append(
                Problem("error", where, f"text is longer than {MAX_TEXT_CHARS} characters")
            )
        if "quote" in statement and len(statement["quote"].split()) > MAX_QUOTE_WORDS:
            problems.append(
                Problem("error", where, f"quote is longer than {MAX_QUOTE_WORDS} words")
            )
        unknown = [item for item in statement["applies"] if item != "*" and item not in model_ids]
        if unknown:
            problems.append(
                Problem("error", where, f"applies to unknown models: {', '.join(unknown)}")
            )
        cached = cached_text(unit, statement["source"], root)
        if cached is not None:
            run = copied_run(statement["text"], cached)
            if run:
                problems.append(Problem("error", where, f'text copies its source: "{run}"'))
    return problems, facts


def _effort_values(model: dict[str, Any], route: dict[str, Any]) -> list[str] | None:
    if "effortValues" in route:
        return list(route["effortValues"])
    fact = model["facts"].get("effortValues")
    return list(fact["value"]) if fact else None


def validate_registry(
    all_facts: dict[str, dict[str, Any]], root: Path, today: datetime.date
) -> list[Problem]:
    registry = read_json(root / REGISTRY)
    problems: list[Problem] = []
    retiring: dict[tuple[str, str], tuple[str, str]] = {}
    for unit, facts in all_facts.items():
        for model_id, model in facts.get("models", {}).items():
            retires = model["facts"].get("retires", {}).get("value")
            for route in model.get("routes", []):
                harness = registry["harnesses"].get(route["harness"])
                if harness is None:
                    problems.append(
                        Problem(
                            "error",
                            f"{unit} models.{model_id}",
                            f"harness {route['harness']} is not a registry harness",
                        )
                    )
                    continue
                if retires:
                    retiring[(route["harness"], route["model"])] = (
                        retires,
                        model.get("status", "current"),
                    )
                values = _effort_values(model, route) if route.get("register") else None
                allowed = harness["effort"]["values"]
                if values and harness["effort"]["kind"] == "none":
                    problems.append(
                        Problem(
                            "error",
                            f"{unit} models.{model_id}",
                            f"harness {route['harness']} has no effort control; "
                            'give this route "effortValues": []',
                        )
                    )
                elif values:
                    missing = [value for value in values if value not in allowed]
                    if missing:
                        problems.append(
                            Problem(
                                "error",
                                f"{unit} models.{model_id}",
                                f"harness {route['harness']} effort.values lacks {', '.join(missing)}",
                            )
                        )
    for harness_id, harness in registry["harnesses"].items():
        default = harness["defaultModel"].get("fallback")
        entry = retiring.get((harness_id, default)) if default else None
        if entry is None:
            continue
        retires, status = entry
        days = (datetime.date.fromisoformat(retires) - today).days
        if status == "retired" or days <= STOP_DAYS:
            problems.append(
                Problem(
                    "error",
                    f"registry harnesses.{harness_id}.defaultModel",
                    f"{default} retires {retires}; choose another default",
                )
            )
    return problems


def validate_ownership(all_facts: dict[str, dict[str, Any]]) -> list[Problem]:
    """A model identifier or artifact belongs to one family."""
    owners: dict[str, str] = {}
    problems: list[Problem] = []
    for unit, facts in all_facts.items():
        if not unit.startswith("families/"):
            continue
        for model_id, model in facts.get("models", {}).items():
            names = {model_id.lower()} | (
                {model["artifact"].lower()} if "artifact" in model else set()
            )
            for name in names:
                owner = owners.setdefault(name, unit)
                if owner != unit:
                    problems.append(Problem("error", unit, f"{name} is also claimed by {owner}"))
    for unit, facts in all_facts.items():
        if not unit.startswith("hosts/"):
            continue
        for model_id, model in facts.get("models", {}).items():
            family, _, served = model["of"].partition("#")
            target = all_facts.get(family)
            if target is None or served not in target.get("models", {}):
                problems.append(
                    Problem(
                        "error",
                        f"{unit} models.{model_id}",
                        f"of {model['of']} does not name a family model",
                    )
                )
    return problems


def validate_cache_untracked(root: Path) -> list[Problem]:
    try:
        tracked = subprocess.run(
            ["git", "ls-files", f"{FACTS_DIR}/{CACHE_DIR}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
    except OSError:
        return []
    return [
        Problem("error", path, "fetched source text must never be committed") for path in tracked
    ]


def validate(
    root: Path = ROOT, today: datetime.date | None = None, units: list[str] | None = None
) -> list[Problem]:
    today = today or datetime.date.today()
    problems: list[Problem] = []
    all_facts: dict[str, dict[str, Any]] = {}
    for unit in units or unit_names(root):
        try:
            found, facts = validate_unit(unit, root, today)
        except FactsError as exc:
            found, facts = [Problem("error", unit, str(exc))], None
        problems += found
        if facts is not None:
            all_facts[unit] = facts
    # Cross-unit checks need every unit's facts, even when only some units are being validated:
    # a host model's `of` names a family, and ownership compares all families.
    context = dict(all_facts)
    if units:
        for unit in unit_names(root):
            if unit not in context:
                try:
                    facts = load_facts(unit, root)
                except FactsError:
                    continue
                if facts is not None:
                    context[unit] = facts
    selected = set(all_facts)
    problems += [
        problem
        for problem in validate_ownership(context)
        if any(problem.where == unit or problem.where.startswith(f"{unit} ") for unit in selected)
    ]
    problems += validate_registry(all_facts, root, today)
    problems += validate_cache_untracked(root)
    return problems


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("units", nargs="*")
    args = parser.parse_args(argv)
    problems = validate(root or ROOT, units=args.units or None)
    for problem in problems:
        print(problem, file=sys.stderr if problem.level == "error" else sys.stdout)
    errors = sum(1 for problem in problems if problem.level == "error")
    warnings = len(problems) - errors
    print(f"model facts: {errors} errors, {warnings} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
