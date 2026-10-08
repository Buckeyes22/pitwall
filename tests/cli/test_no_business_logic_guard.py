"""Boundary guard: CLI modules parse arguments and print; they do not reach the database.

Repositories and database internals live behind service functions (for example
``pitwall.registry_admin``). A CLI module that imports a repository, the ``asyncpg`` driver, or a
repository-bearing ``pitwall.db`` submodule is doing the service layer's work.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parents[2] / "src" / "pitwall" / "cli"

_FORBIDDEN_MODULES = ("asyncpg",)
_FORBIDDEN_PREFIXES = (
    "pitwall.db.repository",
    "pitwall.db.quota_repository",
    "pitwall.db.kill_log",
    "pitwall.db.drill_evidence",
)


def _violations(source: str, filename: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            names = [(alias.name, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [(module, f"{module}.{alias.name}") for alias in node.names]
        else:
            continue
        for module, full in names:
            if (
                module.split(".")[0] in _FORBIDDEN_MODULES
                or any(module == p or module.startswith(p + ".") for p in _FORBIDDEN_PREFIXES)
                or full.rsplit(".", 1)[-1].endswith("Repository")
            ):
                found.append(f"{filename}:{node.lineno}: imports {full}")
    return found


def test_cli_modules_import_no_repositories_or_database_internals() -> None:
    files = sorted(CLI_DIR.glob("*.py"))
    assert files, "no CLI modules found"
    violations = [
        line for path in files for line in _violations(path.read_text(encoding="utf-8"), path.name)
    ]
    assert violations == []


def test_the_guard_flags_a_repository_import() -> None:
    source = "def f():\n    from pitwall.db.repository import LeaseRepository\n"
    assert _violations(source, "example.py") != []
    assert _violations("import asyncpg\n", "example.py") != []
    assert _violations("from pitwall.db import get_pool\n", "example.py") == []
