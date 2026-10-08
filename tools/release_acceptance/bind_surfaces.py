"""Bind every discovered surface to the test node that exercises it.

``release_acceptance/reviewed-bindings.json`` holds one binding per (surface, test node):
the node's source line and file hash, the oracle the node asserts, and its proof lane.
The source-reviewed bindings already in the file are kept; this module adds the
journey bindings from the mapping rules below, which name for each surface family the
test that drives it and derive the oracle from that test's pinned fixture. Run it after
a bound test file changes: a binding whose file hash no longer matches is stale, and the
matrix refuses it until the rules have been re-read against the edited test.

A hand-reviewed binding keeps its oracle, but its ``source`` line follows the bound test's
current definition line whenever its file hash is current (or accepted). The per-family
review records (``reviewed-bindings-*.json``) get the same treatment.

    uv run --frozen python -m tools.release_acceptance.bind_surfaces [--accept-reviewed]
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from tools.release_acceptance import discovery, test_index

ROOT = Path(__file__).resolve().parents[2]
BINDINGS = ROOT / "release_acceptance" / "reviewed-bindings.json"
EXPLICIT = ROOT / "release_acceptance" / "surface-test-map.json"
FAMILY_GLOB = "reviewed-bindings-*.json"
REVIEWER = "root journey review (journey-coverage plan, Task 14)"
REVIEW_STATE = "journey_bound_execution_by_harness"
GENERATED = "journey-rule"

J34 = "tests/release/test_mcp_all_tools_journey.py"
J35 = "tests/release/test_rest_all_operations_journey.py"
J36 = "tests/release/test_cli_all_commands_journey.py"
J37 = "tests/release/test_config_keys_journey.py"
J43 = "tests/integration/test_upgrade_from_release.py"
AR_JOURNEYS = "tests/agents/test_journeys.py"

REGEN_COMMAND = "uv run --frozen python -m tools.release_acceptance.bind_surfaces --accept-reviewed"
REGEN_HINT = f"run `{REGEN_COMMAND}` (or `make regen-bindings`)"

Rule = tuple[str, str, str, str]  # (test node id, framework, proof lane, oracle)


def _fixtures(relative: str) -> dict[str, Any]:
    return dict(json.loads((ROOT / "tests" / "release" / relative).read_text(encoding="utf-8")))


def _mcp_rules() -> Iterator[tuple[str, Rule]]:
    for name, fixture in _fixtures("mcp_tool_fixtures.json").items():
        if "expect_keys" in fixture:
            outcome = f"returns top-level keys {sorted(fixture['expect_keys'])}"
        else:
            outcome = f"returns the typed code {fixture['expect_error']}"
        oracle = (
            f"over real stdio against a seeded database, a valid call {outcome}; an invalid "
            "or undeclared argument is refused as invalid_tool_arguments without echoing input"
        )
        yield f"mcp:{name}", (f"{J34}::test_tool_over_stdio[{name}]", "pytest", "release", oracle)


def _rest_rules(surfaces: list[str]) -> Iterator[tuple[str, Rule]]:
    known = set(surfaces)
    for operation, fixture in _fixtures("rest_operation_fixtures.json").items():
        method, path = operation.split(" ", 1)
        outcome = f"HTTP {fixture['status']}"
        if fixture.get("expect_keys"):
            outcome += f" with keys {sorted(fixture['expect_keys'])}"
        if fixture.get("expect_error"):
            outcome += f" with the typed code {fixture['expect_error']}"
        oracle = (
            f"on a real server, anonymous gets 401 and a wrong-scope token 403 before any "
            f"effect; the {fixture['scope']} token gets {outcome}"
        )
        node = f"{J35}::test_operation[{operation}]"
        # Starlette path converters (``{name:path}``) are absent from the OpenAPI path.
        candidates = [f"rest:{method}:{path}"] + [
            f"rest:{method}:{path.replace('{' + name + '}', '{' + name + ':path}')}"
            for name in ("object_key", "path")
        ]
        [surface] = [candidate for candidate in dict.fromkeys(candidates) if candidate in known]
        yield surface, (node, "pytest", "release", oracle)


def _cli_rules() -> Iterator[tuple[str, Rule]]:
    fixtures = _fixtures("cli_fixtures.json")
    for key, fixture in fixtures.items():
        prog, _, path = key.partition("|")
        command = " ".join([prog, *fixture["argv"]])
        if fixture.get("daemon"):
            oracle = f"`{command}` starts and stays up until stopped"
        else:
            oracle = f"`{command}` exits {fixture['exit']}"
        if fixture.get("json_keys"):
            oracle += f" with JSON keys {sorted(fixture['json_keys'])}"
        node = f"{J36}::test_command[{key}]"
        if prog == "pitwall" and path and " " not in path:
            yield f"cli:pitwall:{path}", (node, "pytest", "release", oracle)
        if not path:
            yield f"cli:entrypoint:{prog}", (node, "pytest", "release", oracle)
            if prog == "pitwall":
                yield "cli:pitwall:default", (node, "pytest", "release", oracle)


def _config_rules() -> Iterator[tuple[str, Rule]]:
    functions = {
        "field": "test_setting_field",
        "agents": "test_agents_table_field",
        "weight": "test_routing_weight",
        "alias": "test_env_example_key_sets_its_fields",
        "consumer": "test_env_example_key_reaches_its_consumer",
        "compose": "test_compose_only_key_reaches_every_published_port",
    }
    oracles = {
        "field": "the environment value parses to the pinned value, pitwall.toml applies it, "
        "the environment wins, and an invalid value exits EX_CONFIG naming the variable "
        "without echoing it",
        "agents": "the [agents] table value parses to the pinned model, pitwall config check "
        "accepts it, and a wrong-typed value exits EX_CONFIG naming the [agents] table",
        "weight": "a PITWALL_ROUTING_WEIGHTS entry parses to the pinned weight and an invalid "
        "weight exits EX_CONFIG naming the variable",
        "alias": "the .env.example key sets every settings field it aliases to the pinned value",
        "consumer": "the consumer that reads the key directly returns the pinned result with the "
        "key set and the pinned default without it",
        "compose": "docker compose config binds every published port to the key's value",
    }
    for key, fixture in _fixtures("config_fixtures.json").items():
        kind = fixture["kind"]
        yield key, (f"{J37}::{functions[kind]}[{key}]", "pytest", "release", oracles[kind])


def _argument_rules(surfaces: list[str]) -> Iterator[tuple[str, Rule]]:
    oracles = {
        "argument": "the argument is registered at its source line on the parser of the command "
        "that owns it, and that command's --help names it",
        "parser": "the parser is constructed at its source line when its command path runs",
        "subparsers": "the subcommand group is added at its source line when its parent parses",
        "subcommand": "the subcommand is registered at its source line under its parent command",
    }
    node = f"{J36}::test_every_argument_surface_is_registered_and_documented"
    for surface in surfaces:
        operation = surface.split(":", 2)[1]
        if surface.startswith("cli:") and operation in oracles:
            yield surface, (node, "pytest", "release", oracles[operation])


def _migration_rules(surfaces: list[str]) -> Iterator[tuple[str, Rule]]:
    oracle = (
        "a v0.1.0a2 database upgrades in place: each migration applies exactly once, db status "
        "reports none pending, and every seeded value survives"
    )
    node = f"{J43}::test_last_release_database_upgrades_in_place"
    for surface in surfaces:
        if surface.startswith("install:migration:"):
            yield surface, (node, "pytest", "integration", oracle)


def _shim_rules(surfaces: list[str]) -> Iterator[tuple[str, Rule]]:
    for surface in surfaces:
        if not surface.startswith("shim:"):
            continue
        if surface == "shim:route":
            node = f"{AR_JOURNEYS}::J40ShimsJourney::test_route_shim"
            oracle = "route-shim resolves the named route to its harness and model, ends SHIM-DONE, and receipts the exit"
        else:
            node = f"{AR_JOURNEYS}::J40ShimsJourney::test_every_harness_shim"
            oracle = "the harness receives the adapter's pinned argv; SHIM-DONE and the parsed SHIM-RESULT receipt carry exit 0, then 3 when the harness fails"
        yield surface, (node, "unittest", "hermetic", oracle)


def _explicit_rules(surfaces: list[str]) -> Iterator[tuple[str, Rule]]:
    explicit = json.loads(EXPLICIT.read_text(encoding="utf-8"))["bindings"]
    for surface, row in explicit.items():
        yield (
            surface,
            (row["test_node_id"], row["framework"], row["proof_lane"], row["reviewed_oracle"]),
        )


RULE_SETS: list[Callable[[list[str]], Iterator[tuple[str, Rule]]]] = [
    lambda surfaces: _mcp_rules(),
    _rest_rules,
    lambda surfaces: _cli_rules(),
    lambda surfaces: _config_rules(),
    _argument_rules,
    _migration_rules,
    _shim_rules,
    _explicit_rules,
]


def _sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def _node_lines(index: dict[str, Any]) -> dict[str, int]:
    lines = {str(node["node_id"]): int(node["line"]) for node in index["nodes"]}
    for template in index["templates"]:
        template_id = str(template["template_id"])
        if "::" in template_id:
            base = template_id.removeprefix("template:").rsplit(":", 1)[0]
            lines.setdefault(base, int(template["line"]))
    return lines


def _refresh_reviewed(
    bindings: list[dict[str, Any]], lines: dict[str, int], *, accept_reviewed: bool
) -> list[str]:
    """Re-line hand-reviewed bindings in place; return the ones whose test needs a re-read.

    A binding whose file hash is current (or is accepted) takes its test's current definition
    line. One whose test changed is listed unless ``accept_reviewed``. One whose test file or
    node is gone (deleted or renamed) stops the run: only a reviewer can re-bind it.
    """
    stale: list[str] = []
    gone: list[str] = []
    for binding in bindings:
        base = str(binding["test_node_id"]).split("[", 1)[0]
        path = base.split("::", 1)[0]
        label = f"{binding['surface_id']} -> {binding['test_node_id']}"
        if not (ROOT / path).is_file():
            gone.append(f"{label}: test file {path} is gone")
            continue
        if base not in lines:
            gone.append(f"{label}: test node is gone from {path}")
            continue
        current = _sha(path)
        if binding.get("test_source_sha256") != current:
            if not accept_reviewed:
                stale.append(label)
                continue
            binding["test_source_sha256"] = current
        binding["source"] = f"{path}:{lines[base]}"
    if gone:
        raise SystemExit(
            "reviewed bindings name a missing test. Point each binding's test_node_id at the"
            " test that now covers the surface (in reviewed-bindings.json and its per-family"
            " record), re-read that test, then rerun with --accept-reviewed"
            f" ({REGEN_HINT}):\n  " + "\n  ".join(gone)
        )
    return stale


def build_families(
    *, accept_reviewed: bool = False
) -> tuple[dict[Path, dict[str, Any]], list[str]]:
    """Return each per-family review record re-lined, and its bindings that need a re-read."""
    lines = _node_lines(test_index.build_report(ROOT))
    payloads: dict[Path, dict[str, Any]] = {}
    stale: list[str] = []
    for path in sorted(BINDINGS.parent.glob(FAMILY_GLOB)):
        payload = json.loads(path.read_text(encoding="utf-8"))
        stale += [
            f"{path.name}: {item}"
            for item in _refresh_reviewed(
                payload["bindings"], lines, accept_reviewed=accept_reviewed
            )
        ]
        payloads[path] = payload
    return payloads, sorted(stale)


def build(*, accept_reviewed: bool = False) -> tuple[dict[str, Any], list[str], list[str]]:
    """Return the bindings payload, the unmapped surfaces, and the stale reviewed bindings.

    Rule and map bindings take their test file's current hash: regenerating is the review
    of an edited test against the rule or map entry that binds it. A hand-reviewed binding
    whose test changed is reported instead, and re-hashed only with ``accept_reviewed``.
    """
    report = discovery.build_report(ROOT)
    surfaces = [str(row["surface_id"]) for row in report["surfaces"]]
    known = set(surfaces)
    lines = _node_lines(test_index.build_report(ROOT))
    payload = json.loads(BINDINGS.read_text(encoding="utf-8"))
    kept = [binding for binding in payload["bindings"] if binding.get("generated") != GENERATED]
    stale_reviewed = _refresh_reviewed(kept, lines, accept_reviewed=accept_reviewed)
    generated: list[dict[str, Any]] = []
    covered: set[str] = set()
    absent: set[str] = set()
    for rules in RULE_SETS:
        for surface, (node, framework, lane, oracle) in rules(surfaces):
            if surface not in known:
                raise SystemExit(f"rule names {surface}, which discovery does not report")
            base = node.split("[", 1)[0]
            path = base.split("::", 1)[0]
            if path.startswith("tests/release/") and lane != "release":
                raise SystemExit(f"{node} runs only under -m release; bind it in the release lane")
            if base not in lines:
                absent.add(base)
                continue
            generated.append(
                {
                    "surface_id": surface,
                    "framework": framework,
                    "source": f"{path}:{lines[base]}",
                    "test_source_sha256": _sha(path),
                    "reviewed_oracle": oracle,
                    "proof_lane": lane,
                    "limits": "hermetic fixtures and loopback fakes; no paid provider or live RunPod call",
                    "reviewer": REVIEWER,
                    "review_state": REVIEW_STATE,
                    "status": "not_run",
                    "test_node_id": node,
                    "generated": GENERATED,
                }
            )
            covered.add(surface)
    if absent:
        raise SystemExit(
            "bound nodes absent from the static test index:\n  " + "\n  ".join(sorted(absent))
        )
    payload["bindings"] = kept + sorted(
        generated, key=lambda b: (b["surface_id"], b["test_node_id"])
    )
    unmapped = sorted(known - covered - {str(b["surface_id"]) for b in kept})
    return payload, unmapped, sorted(stale_reviewed)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    accept = "--accept-reviewed" in args
    payload, unmapped, stale = build(accept_reviewed=accept)
    BINDINGS.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    families, family_stale = build_families(accept_reviewed=accept)
    for path, family in families.items():
        path.write_text(json.dumps(family, indent=2) + "\n", encoding="utf-8")
    stale += family_stale
    print(f"{len(payload['bindings'])} bindings; {len(unmapped)} surfaces unmapped")
    for surface in unmapped:
        print(f"  unmapped: {surface}")
    for binding in stale:
        print(f"  reviewed binding's test changed; re-read it, then --accept-reviewed: {binding}")
    if unmapped:
        print("bind each unmapped surface (fixture rule or surface-test-map.json), then rerun")
    if stale:
        print(f"after re-reading the listed tests: {REGEN_HINT}")
    return 1 if unmapped or stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
