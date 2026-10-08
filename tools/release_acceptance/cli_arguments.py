"""Compact argparse declaration inventory for release acceptance.

``discover(root)`` parses — never imports, executes, or runtime-introspects —
``src/pitwall/cli*.py``, ``src/pitwall/config.py``, and every module under
``src/pitwall/agents``. Declarations become
``kind == "cli"`` rows in the existing cli_inventory shape:
``cli:parser:*`` (``ArgumentParser(...)``), ``cli:argument:*``
(``add_argument(...)``), ``cli:subcommand:*`` (``add_parser("name", ...)``),
and ``cli:subparsers:*`` (``add_subparsers(...)``). Surface ids carry file,
qualified enclosing scope (``Class.method`` or ``outer.inner``), literal
receiver variable, and literal option/command name, so same-named parsers in
different functions or classes never collide.

Every positional and keyword argument is preserved as a literal value or a
source expression; nothing is evaluated. All literal positional aliases are
kept (``add_argument("-v", "--verbose")`` records both and keys on the
first). A same-function literal binding is resolved in source order
(``parser = ArgumentParser(...)``, ``sub = parser.add_subparsers(...)``,
``cmd = sub.add_parser("name")``, including transparent mutually-exclusive
groups), so an option is never attributed to a later reassignment of the same
variable name. A dynamic option or command name is an explicit UNRESOLVED
issue and no row is fabricated; an unresolved or cyclic receiver keeps its
literal row but is marked unresolved. No full command path is assembled from
dynamic segments.

``contract_digest`` hashes source-AST declaration inputs (file, qualified
scope, operation, normalized ``ast.unparse`` of the call), never line numbers,
so a changed default or choices drifts the digest while comments, formatting,
and line moves do not. A repeated surface id is kept once with an explicit
``duplicate_surface_id`` issue naming every occurrence.

Not exhaustive: aliased or factory-built parsers, options added through helper
functions, runtime argparse behavior, and non-target files are UNRESOLVED.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-cli-arguments.v1"
CLI_GLOB_DIR = "src/pitwall/cli"
CLI_GLOB = "*.py"
CONFIG_MODULE = "src/pitwall/config.py"
ROUTING_DIR = "src/pitwall/agents"
_CONSTRUCTORS = {
    "ArgumentParser": "parser",
    "add_argument": "argument",
    "add_parser": "subcommand",
    "add_subparsers": "subparsers",
    "add_mutually_exclusive_group": "group",
    "add_argument_group": "group",
}
ISSUE_DYNAMIC_OPTION = "dynamic_option_name"
ISSUE_DYNAMIC_COMMAND = "dynamic_command_name"
ISSUE_UNBOUND_PARSER = "unresolved_parser_binding"
ISSUE_DUPLICATE = "duplicate_surface_id"
UNRESOLVED_BINDING = ISSUE_UNBOUND_PARSER
UNRESOLVED_PARENT = "dynamic_parent_command"
_UNRESOLVED = (
    ("aliased_constructors", "aliased or factory-built ArgumentParser construction"),
    ("helper_added_arguments", "options added through helper functions are not attributed"),
    ("runtime_behavior", "runtime argparse parsing, defaults resolution, and executed behavior"),
    ("non_target_files", "CLI surfaces in files outside the listed targets"),
)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_Binding = tuple[int, str, ast.Call | None]


def _literal(node: ast.AST) -> tuple[Any, bool]:
    """Return a JSON-safe literal value, or ``(None, False)`` when dynamic."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value, True
        return None, False
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[Any] = []
        for element in node.elts:
            value, literal = _literal(element)
            if not literal:
                return None, False
            values.append(value)
        return values, True
    if isinstance(node, ast.Dict):
        mapping: dict[str, Any] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            if key_node is None:
                return None, False
            key, key_literal = _literal(key_node)
            value, value_literal = _literal(value_node)
            if not (key_literal and value_literal) or not isinstance(key, str):
                return None, False
            mapping[key] = value
        return mapping, True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = node.operand
        if isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float)):
            return -operand.value, True
    return None, False


def _strings(node: ast.AST) -> list[str] | None:
    """Return literal strings in ``node``, or None if any part is not literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        found: list[str] = []
        for element in node.elts:
            values = _strings(element)
            if values is None:
                return None
            found += values
        return found
    return None


def _flags(args: list[ast.expr]) -> list[str] | None:
    """Return all literal positional option strings, or None if any is dynamic."""
    found: list[str] = []
    for arg in args:
        values = _strings(arg)
        if values is None:
            return None
        found += values
    return found or None


def _value(text: str, node: ast.AST) -> Any:
    value, literal = _literal(node)
    if literal:
        return value
    return {
        "expression": ast.get_source_segment(text, node) or ast.unparse(node),
        "unresolved": True,
    }


def _kwargs(text: str, call: ast.Call) -> dict[str, Any]:
    return {keyword.arg or "<dynamic>": _value(text, keyword.value) for keyword in call.keywords}


def _kind(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return "parser" if func.id == "ArgumentParser" else None
    return _CONSTRUCTORS.get(func.attr) if isinstance(func, ast.Attribute) else None


def _receiver(node: ast.Call) -> tuple[str | None, str]:
    """Return the literal receiver variable name and its source expression."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None, ""
    if isinstance(func.value, ast.Name):
        return func.value.id, func.value.id
    return None, ast.unparse(func.value)


def _own_nodes(node: ast.AST) -> list[ast.AST]:
    """Return ``node``'s own nodes in source order, skipping nested scopes."""
    found: list[ast.AST] = []

    def visit(child: ast.AST) -> None:
        found.append(child)
        if not isinstance(child, _SCOPES):
            for grand in ast.iter_child_nodes(child):
                visit(grand)

    for child in getattr(node, "body", []):
        visit(child)
    return found


def _scope_names(module: ast.Module) -> tuple[dict[int, str], dict[int, str | None]]:
    """Return qualified names per scope node and the enclosing class per function."""
    qualified: dict[int, str] = {}
    enclosing: dict[int, str | None] = {}

    def walk(node: ast.AST, prefix: str, klass: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, prefix, klass)
                continue
            name = prefix + child.name
            qualified[id(child)] = name
            if isinstance(child, ast.ClassDef):
                walk(child, name + ".", name)
            else:
                enclosing[id(child)] = klass
                walk(child, name + ".", klass)

    walk(module, "", None)
    return qualified, enclosing


def _bindings(nodes: list[ast.AST]) -> list[_Binding]:
    """Return ``(line, name, call)`` for recognized builder assignments, in order."""
    return sorted(
        (
            node.lineno,
            node.targets[0].id,
            node.value
            if isinstance(node.value, ast.Call) and _kind(node.value.func) is not None
            else None,
        )
        for node in nodes
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    )


def _bound(bindings: list[_Binding], name: str | None, before: int) -> ast.Call | None:
    """Return the source-order binding of ``name`` in effect before ``before``."""
    call: ast.Call | None = None
    for line, bound_name, bound_call in bindings:
        if line >= before:
            break
        if bound_name == name:
            call = bound_call
    return call


def _parent(
    bindings: list[_Binding], receiver: str | None, line: int, seen: frozenset[str] = frozenset()
) -> tuple[str | None, bool, bool]:
    """Return (literal parent command, binding known, parent command literal)."""
    if receiver is None or receiver in seen:
        return None, False, False
    call = _bound(bindings, receiver, line)
    if call is None:
        return None, False, False
    operation = _kind(call.func)
    if operation == "group":
        return _parent(bindings, _receiver(call)[0], call.lineno, seen | {receiver})
    if operation in ("parser", "subparsers"):
        return None, True, True
    if operation == "subcommand" and call.args:
        names = _strings(call.args[0])
        return (names[0] if names else None), True, bool(names)
    return None, False, False


def _row(surface_id: str, rel: str, function: str, call: ast.Call, **extra: Any) -> dict[str, Any]:
    operation = _kind(call.func)
    digest = hashlib.sha256(
        json.dumps(
            {
                "file": rel,
                "scope": function,
                "operation": operation,
                "declaration": ast.unparse(call),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "surface_id": surface_id,
        "kind": "cli",
        "operation": operation,
        "source": f"{rel}:{call.lineno}",
        "metadata": {
            "category": operation,
            "file": rel,
            "class": extra.pop("klass", None),
            "function": function,
            "contract_digest": digest,
            **extra,
        },
    }


def _issue(topic: str, rel: str, lineno: int, reason: str, **extra: Any) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "topic": topic,
        "status": "unresolved",
        "source": f"{rel}:{lineno}",
        "reason": reason,
    }
    issue.update(extra)
    return issue


def _scan_scope(
    text: str, rel: str, function: str, klass: str | None, nodes: list[ast.AST]
) -> tuple[list[tuple[str, int, dict[str, Any]]], list[dict[str, Any]]]:
    entries: list[tuple[str, int, dict[str, Any]]] = []
    issues: list[dict[str, Any]] = []
    bindings = _bindings(nodes)
    variables = {id(call): name for _, name, call in bindings if call is not None}

    def add(surface_id: str, node: ast.Call, **extra: Any) -> None:
        entries.append(
            (surface_id, node.lineno, _row(surface_id, rel, function, node, klass=klass, **extra))
        )

    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        operation = _kind(node.func)
        if operation in (None, "group"):
            continue
        variable = variables.get(id(node))
        receiver, receiver_expression = _receiver(node)
        parent, known, parent_literal = _parent(bindings, receiver, node.lineno)
        unresolved = (
            [] if known and parent_literal else [UNRESOLVED_PARENT if known else UNRESOLVED_BINDING]
        )
        name = variable or receiver or "<unresolved>"
        if operation == "parser":
            add(
                f"cli:parser:{rel}:{function}:{variable or '<anonymous>'}",
                node,
                parser_variable=variable,
                kwargs=_kwargs(text, node),
                unresolved=[],
            )
            continue
        if operation == "argument":
            flags = _flags(node.args)
            if not flags:
                issues.append(
                    _issue(
                        ISSUE_DYNAMIC_OPTION,
                        rel,
                        node.lineno,
                        f"add_argument at {rel}:{node.lineno} has no fully literal "
                        "option name; recorded as UNRESOLVED, no row fabricated",
                    )
                )
                continue
            surface_id = f"cli:argument:{rel}:{function}:{name}:{flags[0]}"
            add(
                surface_id,
                node,
                parser_variable=receiver if known else None,
                receiver=receiver_expression,
                flags=flags,
                parent_command=parent,
                kwargs=_kwargs(text, node),
                unresolved=unresolved,
            )
            if not known:
                issues.append(
                    _issue(
                        ISSUE_UNBOUND_PARSER,
                        rel,
                        node.lineno,
                        f"parser binding {receiver_expression or '<expression>'} at "
                        f"{rel}:{node.lineno} is not a source-ordered same-scope literal "
                        "assignment; hierarchy UNRESOLVED",
                        surface_ids=[surface_id],
                    )
                )
            continue
        if operation == "subcommand":
            names = _strings(node.args[0]) if node.args else None
            if not names:
                issues.append(
                    _issue(
                        ISSUE_DYNAMIC_COMMAND,
                        rel,
                        node.lineno,
                        f"add_parser at {rel}:{node.lineno} has a non-literal "
                        "command name; no command row fabricated; UNRESOLVED",
                    )
                )
                continue
            add(
                f"cli:subcommand:{rel}:{function}:{name}:{names[0]}",
                node,
                parser_variable=variable,
                receiver=receiver_expression,
                parent_parser_variable=receiver,
                parent_command=parent,
                command=names[0],
                kwargs=_kwargs(text, node),
                unresolved=unresolved,
            )
            continue
        kwargs = _kwargs(text, node)
        dest = kwargs.get("dest") if isinstance(kwargs.get("dest"), str) else None
        add(
            f"cli:subparsers:{rel}:{function}:{name}:{dest or '<dynamic>'}",
            node,
            parser_variable=variable,
            receiver=receiver_expression,
            dest=dest,
            kwargs=kwargs,
            unresolved=unresolved,
        )
    return entries, issues


def _target_files(root: Path) -> list[Path]:
    files = set((root / CLI_GLOB_DIR).glob(CLI_GLOB))
    files.add(root / CONFIG_MODULE)
    routing = root / ROUTING_DIR
    if routing.is_dir():
        files.update(routing.rglob("*.py"))
    return sorted(
        (path for path in files if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _scan(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(root).resolve()
    entries: list[tuple[str, str, int, dict[str, Any]]] = []
    issues: list[dict[str, Any]] = []
    for path in _target_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        module = ast.parse(text)
        qualified, enclosing = _scope_names(module)
        scopes = [("<module>", None, _own_nodes(module))] + [
            (qualified[id(node)], enclosing[id(node)], _own_nodes(node))
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for function, klass, nodes in scopes:
            scope_entries, scope_issues = _scan_scope(text, rel, function, klass, nodes)
            entries += [(sid, rel, line, row) for sid, line, row in scope_entries]
            issues += scope_issues
    seen: dict[str, tuple[str, int, dict[str, Any]]] = {}
    duplicates: dict[str, list[str]] = {}
    for surface_id, rel, lineno, row in sorted(entries, key=lambda item: (item[0], item[2])):
        if surface_id in seen:
            first = seen[surface_id]
            duplicates.setdefault(surface_id, [f"{first[0]}:{first[1]}"]).append(f"{rel}:{lineno}")
            continue
        seen[surface_id] = (rel, lineno, row)
    issues += [
        {
            "topic": ISSUE_DUPLICATE,
            "status": "unresolved",
            "reason": (
                f"surface_id {surface_id} is declared {len(sources)} times at "
                f"{', '.join(sources)}; the first declaration is kept and every "
                "duplicate stays UNRESOLVED"
            ),
            "surface_ids": [surface_id],
            "duplicate_sources": sources,
        }
        for surface_id, sources in sorted(duplicates.items())
    ]
    issues += [
        {"topic": topic, "status": "unresolved", "reason": f"{label} are UNRESOLVED"}
        for topic, label in _UNRESOLVED
    ]
    issues.append(
        {
            "topic": "scope",
            "status": "partial",
            "reason": (
                "literal argparse declarations in the listed target files only; "
                "dynamic hierarchy remains open for review; not exhaustive"
            ),
        }
    )
    issues.sort(key=lambda issue: (issue["topic"], issue.get("source", ""), issue["reason"]))
    surfaces = [entry[2] for entry in seen.values()]
    surfaces.sort(key=lambda row: row["surface_id"])
    return surfaces, issues


def discover(root: Path) -> list[dict[str, Any]]:
    """Return literal argparse declaration rows for ``root``, sorted by surface id."""
    return _scan(root)[0]


def discover_with_issues(root: Path) -> dict[str, Any]:
    """Return ``discover(root)`` plus explicitly unresolved declaration hierarchy."""
    surfaces, issues = _scan(root)
    return {"schema_version": SCHEMA_VERSION, "surfaces": surfaces, "issues": issues}
