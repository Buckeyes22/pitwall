"""AST-based facts about audited code, without executing or importing it.

Audit checks that used to search ``inspect.getsource`` text for tokens read these
structural facts instead: which names a function references, the order in which it
first references them, which calls it awaits, and which string literals it holds.
Comments, docstrings, and unrelated substrings cannot satisfy a check.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodeFacts:
    """Names, call order, awaited calls, and string constants found in one code unit."""

    names: frozenset[str]
    strings: tuple[str, ...]
    first_line: dict[str, int] = field(default_factory=dict)
    awaited: frozenset[str] = frozenset()

    def references(self, name: str) -> bool:
        return name in self.names

    def before(self, first: str, second: str) -> bool:
        """Whether ``first`` is referenced before ``second``; both must be referenced."""
        first_line = self.first_line.get(first)
        second_line = self.first_line.get(second)
        return first_line is not None and second_line is not None and first_line < second_line

    def has_string_containing(self, fragment: str) -> bool:
        return any(fragment in value for value in self.strings)


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def facts_from_tree(tree: ast.AST) -> CodeFacts:
    """Collect facts from a parsed function, class, or module."""
    names: set[str] = set()
    strings: list[str] = []
    first_line: dict[str, int] = {}
    awaited: set[str] = set()
    docstring_nodes = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            name = _name_of(node)
            if name is not None:
                names.add(name)
                first_line[name] = min(first_line.get(name, node.lineno), node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstring_nodes:
                strings.append(node.value)
        elif isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            called = _name_of(node.value.func)
            if called is not None:
                awaited.add(called)
    return CodeFacts(
        names=frozenset(names),
        strings=tuple(strings),
        first_line=first_line,
        awaited=frozenset(awaited),
    )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                found.add(id(body[0].value))
    return found


def facts_of(*objects: Any) -> CodeFacts:
    """Facts for live functions or classes, merged; line order is per object, so pass one
    object when the order of references matters."""
    trees = [ast.parse(textwrap.dedent(inspect.getsource(obj))) for obj in objects]
    module = ast.Module(body=[stmt for tree in trees for stmt in tree.body], type_ignores=[])
    return facts_from_tree(module)


def facts_of_module_file(module_name: str) -> tuple[CodeFacts, ast.Module]:
    """Facts for an importable module read from its file, without importing the module.

    ``find_spec`` on a top-level submodule imports only its parent package, so modules
    with import-time side effects (fail-closed environment checks) are not executed.
    """
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError(module_name)
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    return facts_from_tree(tree), tree


def route_handlers(tree: ast.Module, method: str, paths: set[str]) -> list[ast.AsyncFunctionDef]:
    """Async handlers decorated ``@<app>.<method>("<path>")`` for any of ``paths``."""
    handlers: list[ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == method
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and decorator.args[0].value in paths
            ):
                handlers.append(node)
                break
    return handlers


__all__ = [
    "CodeFacts",
    "facts_from_tree",
    "facts_of",
    "facts_of_module_file",
    "route_handlers",
]
