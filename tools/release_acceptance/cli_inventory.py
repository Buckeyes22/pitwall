"""Root CLI command and console-entrypoint inventory for release acceptance.

``discover(root)`` parses — never imports, executes, or runtime-introspects —
``src/pitwall/cli/__init__.py`` (``ast``) and both literal ``[project.scripts]``
manifests (``tomllib``). Every row is ``kind == "cli"`` and carries the
original surface class as ``metadata["category"]``: ``cli:pitwall:<command>``
(with ``aliases``), ``cli:pitwall:flag:<flag>``, and ``cli:pitwall:default``
from ``main``'s literal group/flag comparisons and the literal ``GROUPS`` dispatch
table (one command per ``(group, module, function)`` entry, sourced at that
entry's line), plus ``cli:entrypoint:<name>``
from the manifests. Sources are ``path:line`` references to the literal
declaration location.

Only literal comparisons made directly in the root ``main`` body count: nested
function definitions and comparisons against unrelated operands are ignored. A
recognized ``group`` predicate whose comparator is not fully literal (for
example ``if group == expected:``) is reported by ``discover_with_issues`` as
an UNRESOLVED ``dynamic_group_predicate`` issue instead of being silently
dropped; there is no general AST evaluation.

Not exhaustive: nested subcommands, per-command options, confirmation prompts,
and output modes are UNRESOLVED, as is anything behind a dynamic predicate.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-cli-inventory.v1"
CLI_MODULE = "src/pitwall/cli/__init__.py"
MANIFESTS = ("pyproject.toml",)
_SCRIPTS_TABLE = "[project.scripts]"
_SCRIPTS_KEY = re.compile(r"^\s*project\.scripts(?:\.[^=\s]+)?\s*=")
_FLAGS = ("-h", "--help", "help", "-V", "--version")
_UNRESOLVED = (
    ("subcommands", "nested subcommands"),
    ("options", "per-command options and flags"),
    ("confirmation", "confirmation prompts"),
    ("output_modes", "command output modes"),
)


def _row(
    surface_id: str, operation: str, source: str, category: str, **metadata: Any
) -> dict[str, Any]:
    return {
        "surface_id": surface_id,
        "kind": "cli",
        "operation": operation,
        "source": source,
        "metadata": {"category": category, **metadata},
    }


def _own_nodes(func: ast.FunctionDef) -> list[ast.AST]:
    """Return nodes of ``func``'s own body, skipping nested function scopes."""
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = list(func.body)
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _literal_strings(node: ast.AST) -> list[str] | None:
    """Return string literals in ``node``, or None if any part is not literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        literals: list[str] = []
        for element in node.elts:
            inner = _literal_strings(element)
            if inner is None:
                return None
            literals += inner
        return literals
    return None


def _is_group(left: ast.AST) -> bool:
    """True for the ``group`` value or its ``args[0]`` equivalent."""
    if isinstance(left, ast.Name):
        return left.id == "group"
    return (
        isinstance(left, ast.Subscript)
        and isinstance(left.value, ast.Name)
        and left.value.id == "args"
        and isinstance(left.slice, ast.Constant)
        and left.slice.value == 0
    )


def _compares(
    main: ast.FunctionDef,
) -> tuple[list[tuple[int, list[str]]], list[tuple[int, str]], list[int]]:
    """Return literal group comparisons, flag comparisons, and dynamic group lines."""
    commands: list[tuple[int, list[str]]] = []
    flags: list[tuple[int, str]] = []
    dynamic: list[int] = []
    for node in _own_nodes(main):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        is_group = _is_group(left)
        is_args = isinstance(left, ast.Name) and left.id == "args"
        if not (is_group or is_args):
            continue
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            if not isinstance(operator, (ast.Eq, ast.In)):
                continue
            literals = _literal_strings(comparator)
            if is_group:
                if literals is None:
                    dynamic.append(node.lineno)
                elif literals:
                    commands.append((node.lineno, literals))
            elif literals is not None:
                flags += [(node.lineno, name) for name in literals if name in _FLAGS]
    return sorted(commands), sorted(flags), sorted(set(dynamic))


def _module_value(tree: ast.Module, name: str) -> ast.expr | None:
    """Return the value assigned to module-level ``name``."""
    for node in tree.body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return getattr(node, "value", None)
    return None


def _table_commands(tree: ast.Module) -> list[tuple[int, list[str]]]:
    """Return ``(line, [group])`` for each literal ``GROUPS`` entry."""
    value = _module_value(tree, "GROUPS")
    if not isinstance(value, (ast.Tuple, ast.List)):
        return []
    entries = [(e.lineno, _literal_strings(e)) for e in value.elts]
    return [(line, lits[:1]) for line, lits in entries if lits]


def _default_handler(tree: ast.Module) -> str:
    """Return the handler named by a literal ``_DEFAULT_GROUP`` entry, if any."""
    lits = _literal_strings(_module_value(tree, "_DEFAULT_GROUP") or ast.Pass())
    return lits[2] if lits and len(lits) == 3 else ""


def _default_row(main: ast.FunctionDef, default_handler: str = "") -> list[dict[str, Any]]:
    """Return the no-argument dashboard default row, if ``main`` has that branch."""
    for node in _own_nodes(main):
        if not isinstance(node, ast.If):
            continue
        test = getattr(node, "test", None)
        operand = getattr(test, "operand", None)
        if not isinstance(test, ast.UnaryOp) or not isinstance(test.op, ast.Not):
            continue
        if not isinstance(operand, ast.Name) or operand.id != "args":
            continue
        for statement in getattr(node, "body", []):
            if not isinstance(statement, ast.Return) or not isinstance(statement.value, ast.Call):
                continue
            handler = getattr(statement.value.func, "id", "") or getattr(
                statement.value.func, "attr", ""
            )
            if not (isinstance(handler, str) and handler.startswith("cmd_")):
                handler = default_handler
            if isinstance(handler, str) and handler.startswith("cmd_"):
                return [
                    _row(
                        "cli:pitwall:default",
                        handler.removeprefix("cmd_"),
                        f"{CLI_MODULE}:{node.lineno}",
                        "default",
                        handler=handler,
                    )
                ]
    return []


def _declaration_line(text: str) -> int:
    """Return the 1-based line of the literal ``[project.scripts]`` declaration."""
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() == _SCRIPTS_TABLE or _SCRIPTS_KEY.match(line):
            return number
    return 1


def _entrypoint_rows(root: Path) -> list[dict[str, Any]]:
    """Return literal console-script rows from both manifests, sourced as ``path:line``."""
    rows: list[dict[str, Any]] = []
    for manifest in MANIFESTS:
        path = root / manifest
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        scripts = tomllib.loads(text).get("project", {}).get("scripts", {})
        if not scripts:
            continue
        line = _declaration_line(text)
        rows += [
            _row(
                f"cli:entrypoint:{name}",
                str(target),
                f"{manifest}:{line}",
                "entrypoint",
                name=name,
                target=str(target),
                manifest=manifest,
            )
            for name, target in sorted(scripts.items())
        ]
    return rows


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate surface_ids, merging unique command aliases."""
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = merged.get(row["surface_id"])
        if existing is None:
            merged[row["surface_id"]] = row
            continue
        if existing["metadata"]["category"] == "command":
            aliases = existing["metadata"]["aliases"]
            aliases += [alias for alias in row["metadata"]["aliases"] if alias not in aliases]
    return list(merged.values())


def _scan(root: Path) -> tuple[list[dict[str, Any]], list[int]]:
    """Return deduplicated CLI rows sorted by surface_id plus dynamic predicate lines."""
    root = Path(root).resolve()
    tree = ast.parse((root / CLI_MODULE).read_text(encoding="utf-8"))
    main = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    if main is None:
        raise ValueError(f"{CLI_MODULE} has no top-level main() function")
    commands, flags, dynamic = _compares(main)
    commands = sorted(commands + _table_commands(tree))
    rows = [
        _row(
            f"cli:pitwall:{names[0]}",
            names[0],
            f"{CLI_MODULE}:{line}",
            "command",
            name=names[0],
            aliases=names[1:],
        )
        for line, names in commands
    ]
    rows += [
        _row(f"cli:pitwall:flag:{flag}", flag, f"{CLI_MODULE}:{line}", "flag", flag=flag)
        for line, flag in flags
    ]
    rows += _default_row(main, _default_handler(tree)) + _entrypoint_rows(root)
    rows = _dedupe(rows)
    rows.sort(key=lambda row: row["surface_id"])
    return rows, dynamic


def discover(root: Path) -> list[dict[str, Any]]:
    """Return root dispatcher commands, aliases, flags, default, and console entrypoints."""
    return _scan(root)[0]


def discover_with_issues(root: Path) -> dict[str, Any]:
    """Return ``discover(root)`` plus the explicitly unresolved CLI topics."""
    surfaces, dynamic = _scan(root)
    issues = [
        {"topic": topic, "status": "unresolved", "reason": f"{label} are UNRESOLVED"}
        for topic, label in _UNRESOLVED
    ]
    issues += [
        {
            "topic": "dynamic_group_predicate",
            "status": "unresolved",
            "source": f"{CLI_MODULE}:{line}",
            "reason": f"command group predicate at {CLI_MODULE}:{line} is not literal; UNRESOLVED",
        }
        for line in dynamic
    ]
    issues.append(
        {
            "topic": "scope",
            "status": "partial",
            "reason": "root commands and literal console entrypoints only; not exhaustive",
        }
    )
    return {"schema_version": SCHEMA_VERSION, "surfaces": surfaces, "issues": issues}
