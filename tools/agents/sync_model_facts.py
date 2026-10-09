#!/usr/bin/env python3
"""Generate the registry fields, skill blocks, ledger blocks, and facts sheets from the model facts.

The generator places text. It never writes text: facts become fixed-format lines, and guidance
is copied word for word from facts.json.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGEN_COMMAND = "uv run --frozen python tools/agents/sync_model_facts.py"
sys.path.insert(0, str(ROOT))

from tools.agents.model_facts_common import (  # noqa: E402  # reason: module setup (ROOT or sys.path) must run before the package imports
    HOSTS,
    LEDGER,
    REFERENCE,
    REGISTRY,
    SKILL,
    FactsError,
    facts_root,
    load_facts,
    load_sources,
    read_json,
    unit_names,
)

MARKER = re.compile(r"<!-- MODEL-FACTS:(?P<name>[a-z0-9.-]+) (?P<edge>START|END)(?P<rest>[^>]*)-->")
START_TEXT = "<!-- MODEL-FACTS:{name} START (generated from docs/agents/model-facts/families/{name}; edit facts.json, not this block) -->"
END_TEXT = "<!-- MODEL-FACTS:{name} END -->"
CLASS_LABEL = {
    "vendor": "Stated by vendor",
    "harness": "Stated by harness",
    "host": "Stated by host",
    "artifact": "Shown by artifact",
}
MODEL_KEYS = ("displayName", "effortValues", "provenance")
FAMILY_KEYS = (
    "contextWindow",
    "samplingDefaults",
    "license",
    "modelCardUrl",
    "provenance",
)


class GenerateError(Exception):
    """The generator refused to write because a target is not in the expected shape."""


def family_names(root: Path) -> list[str]:
    """Families are the capability cards; each card owns one family."""
    return sorted(path.stem for path in (root / LEDGER).glob("*.md"))


def _number(value: int) -> str:
    return f"{value:,}"


def _value(model: dict[str, Any], key: str) -> Any:
    fact = model["facts"].get(key)
    return None if fact is None else fact["value"]


def _live(facts: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (model_id, model)
        for model_id, model in facts.get("models", {}).items()
        if model.get("status") != "retired"
    ]


def _listed(facts: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Live models a host agent may route to.

    A model is left out when every route it has is unregistered on a harness the family registers
    for another model: that harness rejects it, so the card must not offer it. Models routed only
    through hosts or model-agnostic harnesses, and models with no route, stay listed.
    """
    registered = registered_harnesses(facts)
    return [
        (model_id, model)
        for model_id, model in _live(facts)
        if not (
            model.get("routes")
            and all(
                not route.get("register") and route["harness"] in registered
                for route in model["routes"]
            )
        )
    ]


def _grouped(models: list[tuple[str, dict[str, Any]]], render: Any) -> str | None:
    """One value when every model agrees, otherwise the value for each group of models."""
    rendered = [(model_id, render(model)) for model_id, model in models]
    present = [(model_id, text) for model_id, text in rendered if text]
    if not present:
        return None
    if len(present) == len(rendered) and len({text for _, text in present}) == 1:
        return str(present[0][1])
    groups: dict[str, list[str]] = {}
    for model_id, text in present:
        groups.setdefault(text, []).append(model_id)
    return "; ".join(
        ", ".join(f"`{model_id}`" for model_id in ids) + f": {text}" for text, ids in groups.items()
    )


def _context(model: dict[str, Any]) -> str | None:
    window, output = _value(model, "contextWindow"), _value(model, "maxOutput")
    parts = []
    if window:
        parts.append(f"{_number(window)} tokens")
    if output == "unlimited":
        parts.append("no output limit")
    elif output:
        parts.append(f"output {_number(output)}")
    return ", ".join(parts) or None


def _effort(model: dict[str, Any]) -> str | None:
    values, default = _value(model, "effortValues"), _value(model, "effortDefault")
    can_disable = _value(model, "thinkingCanDisable")
    if not values:
        return None
    text = ", ".join(values)
    if default:
        text += f" (default {default})"
    if can_disable is False:
        text += ", cannot be disabled"
    return text


def _effort_other(model: dict[str, Any]) -> str | None:
    other = _value(model, "effortOnOther")
    if not other or other["behaviour"] == "unknown":
        return None
    if other["behaviour"] == "error":
        return "other values are rejected"
    return ", ".join(
        f"any other value runs as {actual}" if asked == "*" else f"{asked} runs as {actual}"
        for asked, actual in other["map"].items()
    )


def _sampling(model: dict[str, Any]) -> str | None:
    sampling, locked = _value(model, "samplingDefaults"), _value(model, "samplingLocked")
    if not sampling:
        return "custom values are ignored" if locked else None
    text = ", ".join(f"{key} {value}" for key, value in sampling.items())
    return text + ("; custom values are ignored" if locked else "")


def _status(model_id: str, model: dict[str, Any]) -> str:
    retires = _value(model, "retires")
    return (
        f"`{model_id}` (retires {retires})"
        if model.get("status") == "retiring" and retires
        else f"`{model_id}`"
    )


def _checked(sources: dict[str, Any]) -> str | None:
    dates = [source["reviewedAt"] for source in sources["sources"] if "reviewedAt" in source]
    return max(dates) if dates else None


def registered_harnesses(facts: dict[str, Any]) -> set[str]:
    return {
        route["harness"]
        for model in facts.get("models", {}).values()
        for route in model.get("routes", [])
        if route.get("register")
    }


def render_block(
    facts: dict[str, Any] | None,
    sources: dict[str, Any] | None,
    surface: str,
    harness_guidance: list[dict[str, Any]] | None = None,
) -> list[str]:
    """The lines between one family's markers on one surface.

    Models the family's registered harnesses reject are left out; FACTS.md keeps every live model.
    """
    if facts is None or sources is None:
        return []
    models = _listed(facts)
    lines: list[str] = []
    if models:
        lines.append(
            "- **Models:** " + ", ".join(_status(model_id, model) for model_id, model in models)
        )
    for label, render in (
        ("Context", _context),
        ("Effort", _effort),
        ("Effort on other values", _effort_other),
        ("Sampling", _sampling),
    ):
        text = _grouped(models, render)
        if text:
            lines.append(f"- **{label}:** {text}.")
    returning = [model_id for model_id, model in models if _value(model, "mustReturnReasoning")]
    if returning:
        lines.append(
            "- **Reasoning:** pass the whole assistant message, reasoning included, back on every turn ("
            + ", ".join(f"`{model_id}`" for model_id in returning)
            + ")."
        )
    # The card carries facts only; the reference shows every statement marked for it or the card.
    shown = {"reference": {"reference", "card"}, "ledger": {"ledger"}}.get(surface, set())
    for statement in [*facts["guidance"], *(harness_guidance or [])]:
        if shown & set(statement["surfaces"]):
            scope = (
                ""
                if statement["applies"] == ["*"]
                else " (" + ", ".join(f"`{m}`" for m in statement["applies"]) + ")"
            )
            lines.append(f"- **{CLASS_LABEL[statement['class']]}{scope}:** {statement['text']}")
    checked = _checked(sources)
    if lines and checked:
        lines.append(
            f"- **Facts checked:** {checked}. Sources: `docs/agents/model-facts/families/{facts['unit'].split('/', 1)[1]}/FACTS.md`."
        )
    return lines


def replace_blocks(text: str, blocks: dict[str, list[str]], known: set[str], where: str) -> str:
    """Replace the body of every marker pair in `text`. Nothing outside a pair changes."""
    out: list[str] = []
    position = 0
    seen: set[str] = set()
    matches = list(MARKER.finditer(text))
    index = 0
    while index < len(matches):
        start = matches[index]
        name = start["name"]
        if start["edge"] != "START":
            raise GenerateError(f"{where}: MODEL-FACTS:{name} END has no START")
        if name not in known:
            raise GenerateError(f"{where}: MODEL-FACTS:{name} does not name a capability card")
        if name in seen:
            raise GenerateError(f"{where}: MODEL-FACTS:{name} appears twice")
        if index + 1 >= len(matches):
            raise GenerateError(f"{where}: MODEL-FACTS:{name} START has no END")
        end = matches[index + 1]
        if end["edge"] != "END" or end["name"] != name:
            raise GenerateError(
                f"{where}: MODEL-FACTS:{name} START is followed by MODEL-FACTS:{end['name']} {end['edge']}"
            )
        seen.add(name)
        body = "".join(line + "\n" for line in blocks.get(name, []))
        out.append(text[position : start.start()])
        out.append(START_TEXT.format(name=name) + "\n" + body + END_TEXT.format(name=name))
        position = end.end()
        index += 2
    out.append(text[position:])
    return "".join(out)


def _registry_family(registry: dict[str, Any], name: str) -> str | None:
    card = f"{LEDGER}/{name}.md"
    for family_id, family in registry.get("modelFamilies", {}).items():
        if family["capabilityCard"] == card:
            return str(family_id)
    return None


def _repo_relative(prompt_reference: str) -> str:
    """Facts store the path under docs/ (`prompting/x.md`); the registry stores it from the repository root."""
    return prompt_reference if prompt_reference.startswith("docs/") else f"docs/{prompt_reference}"


def update_registry(
    registry: dict[str, Any], units: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    """Only the keys in MODEL_KEYS and FAMILY_KEYS change. No model is removed; no default changes."""
    updated = copy.deepcopy(registry)
    for name, (facts, sources) in units.items():
        provenance = f"model-facts:families/{name}"
        for model in facts.get("models", {}).values():
            for route in model.get("routes", []):
                if not route.get("register"):
                    continue
                harness = updated["harnesses"].get(route["harness"])
                if harness is None:
                    raise GenerateError(
                        f"families/{name}: harness {route['harness']} is not a registry harness"
                    )
                values = (
                    route["effortValues"]
                    if "effortValues" in route
                    else _value(model, "effortValues") or []
                )
                entry = harness["models"].get(route["model"])
                if entry is None:
                    if model.get("status", "current") != "current":
                        continue
                    for key in ("promptReference", "runtimeReference"):
                        if key not in facts:
                            raise GenerateError(
                                f"families/{name}: {key} is needed to register {route['model']}"
                            )
                    harness["models"][route["model"]] = {
                        "displayName": model.get("displayName", route["model"]),
                        "aliases": [],
                        "effortValues": list(values),
                        "promptReference": _repo_relative(facts["promptReference"]),
                        "runtimeReference": facts["runtimeReference"],
                        "capabilityCard": f"{LEDGER}/{name}.md",
                        "provenance": provenance,
                    }
                    continue
                if "displayName" in model:
                    entry["displayName"] = model["displayName"]
                if "effortValues" in route or values:
                    entry["effortValues"] = list(values)
                entry["provenance"] = provenance
        family_id = _registry_family(updated, name)
        if family_id is None:
            continue
        family = updated["modelFamilies"][family_id]
        unit_facts = {"facts": facts.get("facts", {})}
        window = _value(unit_facts, "contextWindow")
        if window:
            family["contextWindow"] = window
        sampling = _value(unit_facts, "samplingDefaults")
        if sampling:
            family["samplingDefaults"] = sampling
        licence = _value(unit_facts, "license")
        if licence:
            family["license"] = licence["name"]
        cards = [source["url"] for source in sources["sources"] if source["fetch"] == "huggingface"]
        if cards:
            family["modelCardUrl"] = cards[0]
        family["provenance"] = provenance
    return updated


def _route_rows(name: str, facts: dict[str, Any], hosts: dict[str, dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    for model_id, model in _live(facts):
        for route in model.get("routes", []):
            served = model
            host = route.get("host")
            if host and host in hosts:
                for host_model in hosts[host].get("models", {}).values():
                    if host_model.get("of") == f"families/{name}#{model_id}":
                        served = {"facts": {**model["facts"], **host_model["facts"]}}
            web, batch = _value(served, "webSearch"), _value(served, "batch")
            cells = [
                f"`{model_id}`",
                route["harness"],
                host or "direct",
                f"`{route['model']}`",
                _context(served) or "",
                _effort(served) or "",
                "" if web is None else ("yes" if web else "no"),
                "" if batch is None else ("yes" if batch else "no"),
            ]
            rows.append("| " + " | ".join(cells) + " |")
    return rows


def _fact_rows(facts: dict[str, Any]) -> list[str]:
    rows = []
    for key, fact in facts.get("facts", {}).items():
        sources = ", ".join(evidence["source"] for evidence in fact["evidence"])
        rows.append(f"| {key} | `{json.dumps(fact['value'], ensure_ascii=False)}` | {sources} |")
    for model_id, model in facts.get("models", {}).items():
        label = f"{model_id} (serves {model['of']})" if "of" in model else model_id
        for key, fact in model["facts"].items():
            sources = ", ".join(evidence["source"] for evidence in fact["evidence"])
            rows.append(
                f"| {label}: {key} | `{json.dumps(fact['value'], ensure_ascii=False)}` | {sources} |"
            )
    return rows


def render_sheet(
    unit: str, facts: dict[str, Any], sources: dict[str, Any], hosts: dict[str, dict[str, Any]]
) -> str:
    kind, name = unit.split("/", 1)
    lines = [
        f"# Model facts: {name}",
        "",
        f"<!-- Generated by tools/agents/sync_model_facts.py from docs/agents/model-facts/{unit}; edit facts.json and sources.json. -->",
        "",
    ]
    if kind == "families":
        lines += [
            "## Routes",
            "",
            "| Model | Harness | Host | Model id on the route | Context | Effort | Web search | Batch |",
            "|---|---|---|---|---|---|---|---|",
            *_route_rows(name, facts, hosts),
            "",
        ]
    else:
        lines += [
            "## Facts",
            "",
            "| Fact | Value | Sources |",
            "|---|---|---|",
            *(_fact_rows(facts) or ["| none | | |"]),
            "",
        ]
    lines += ["## Statements", ""]
    for statement in facts["guidance"]:
        lines.append(
            f"- {CLASS_LABEL[statement['class']]} ({statement['source']}, {statement['locator']}): {statement['text']}"
        )
    if not facts["guidance"]:
        lines.append("None recorded.")
    lines += ["", "## Disagreements", ""]
    disagreements = []
    for model_id, model in facts.get("models", {}).items():
        for key, fact in model["facts"].items():
            for evidence in fact["evidence"]:
                if evidence.get("disagrees"):
                    disagreements.append(
                        f"- `{model_id}` {key}: {evidence['source']} states "
                        f"`{json.dumps(evidence['states'])}`; kept `{json.dumps(fact['value'])}`. {fact['note']}"
                    )
    lines += disagreements or ["None recorded."]
    lines += ["", "## Pending sources", ""]
    pending = [
        f"- {source['id']}: changed since it was reviewed"
        for source in sources["sources"]
        if source.get("observedHash") and source.get("observedHash") != source.get("reviewedHash")
    ]
    lines += pending or ["None."]
    lines += ["", "## Not published", ""]
    lines += [
        f"- {item['pageType']} ({item['kind']}): searched {item['searched']} on {item['searchedAt']}."
        for item in sources["notPublished"]
    ] or ["None recorded."]
    lines += [
        "",
        "## Sources",
        "",
        "| Id | Kind | Page type | Reviewed | Link |",
        "|---|---|---|---|---|",
    ]
    for source in sources["sources"]:
        lines.append(
            f"| {source['id']} | {source['kind']} | {source['pageType']} | "
            f"{source.get('reviewedAt', 'not yet')} | <{source['url']}> |"
        )
    return "\n".join(lines) + "\n"


def generated_files(root: Path = ROOT) -> dict[Path, str]:
    names = family_names(root)
    known = set(names)
    units: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    others: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    hosts: dict[str, dict[str, Any]] = {}
    harness_guidance: dict[str, list[dict[str, Any]]] = {}
    for unit in unit_names(root):
        facts = load_facts(unit, root)
        if facts is None:
            continue
        kind, name = unit.split("/", 1)
        if kind == "families":
            if name not in known:
                raise GenerateError(f"{unit} has no capability card {LEDGER}/{name}.md")
            units[name] = (facts, load_sources(unit, root))
            continue
        others[unit] = (facts, load_sources(unit, root))
        if kind == "hosts":
            hosts[name] = facts
        else:
            harness_guidance[name] = facts["guidance"]
    outputs: dict[Path, str] = {}
    registry_path = root / REGISTRY
    registry = read_json(registry_path)
    outputs[registry_path] = (
        json.dumps(update_registry(registry, units), indent=2, ensure_ascii=False) + "\n"
    )
    surfaces: list[tuple[Path, str]] = []
    for host in HOSTS:
        surfaces.append((root / SKILL.format(host=host), "card"))
        surfaces.append((root / REFERENCE.format(host=host), "reference"))
    surfaces += [(root / LEDGER / f"{name}.md", "ledger") for name in names]
    for path, surface in surfaces:
        blocks: dict[str, list[str]] = {}
        for name in names:
            facts, sources = units.get(name, (None, None))
            extra = [
                statement
                for harness in sorted(registered_harnesses(facts or {}))
                for statement in harness_guidance.get(harness, [])
            ]
            blocks[name] = render_block(facts, sources, surface, extra)
        text = path.read_text(encoding="utf-8")
        outputs[path] = replace_blocks(text, blocks, known, str(path.relative_to(root)))
    for name, (facts, sources) in units.items():
        outputs[facts_root(root) / "families" / name / "FACTS.md"] = render_sheet(
            f"families/{name}", facts, sources, hosts
        )
    for unit, (facts, sources) in others.items():
        outputs[facts_root(root) / unit / "FACTS.md"] = render_sheet(unit, facts, sources, hosts)
    return outputs


def synchronize(root: Path = ROOT, *, check: bool) -> list[Path]:
    changed: list[Path] = []
    for path, content in generated_files(root).items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        changed.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail instead of writing stale outputs"
    )
    args = parser.parse_args(argv)
    base = root or ROOT
    try:
        changed = synchronize(base, check=args.check)
    except (GenerateError, FactsError) as exc:
        print(f"model facts not generated: {exc}", file=sys.stderr)
        return 2
    for path in changed:
        verb = "stale" if args.check else "generated"
        print(f"{verb} {path.relative_to(base)}", file=sys.stderr if args.check else sys.stdout)
    if changed and args.check:
        print(
            f"fix: run `{REGEN_COMMAND}` (or `make regen`) and commit the result", file=sys.stderr
        )
    if not changed:
        print("model facts outputs are current")
    return 1 if changed and args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
