"""pitwall.agents talks to the broker over HTTP; it never imports broker internals."""

from __future__ import annotations

import ast
from pathlib import Path

AGENTS_ROOT = Path(__file__).resolve().parents[1] / "src" / "pitwall" / "agents"
FORBIDDEN_PREFIXES = ("pitwall.db", "pitwall.api.leases")
FORBIDDEN_PARTS = frozenset({"repository", "repositories", "services", "service"})


def _imported_modules(tree: ast.AST, package: str) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = parts[: len(parts) - node.level + 1]
                name = ".".join([*base, *([node.module] if node.module else [])])
            else:
                name = node.module or ""
            modules.append(name)
            modules.extend(f"{name}.{alias.name}" for alias in node.names)
    return modules


def _is_forbidden(module: str) -> bool:
    if not module.startswith("pitwall.") or module.startswith("pitwall.agents"):
        return False
    if any(module == p or module.startswith(f"{p}.") for p in FORBIDDEN_PREFIXES):
        return True
    return bool(FORBIDDEN_PARTS.intersection(module.split(".")[1:]))


def _violations(root: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root.parent.parent)
        package = ".".join(rel.with_suffix("").parts)
        if path.name != "__init__.py":
            package = package.rsplit(".", 1)[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend(
            f"{path.relative_to(root)}: {module}"
            for module in _imported_modules(tree, package)
            if _is_forbidden(module)
        )
    return found


def test_agents_never_imports_broker_internals() -> None:
    assert AGENTS_ROOT.is_dir()
    assert _violations(AGENTS_ROOT) == []


def test_guard_detects_forbidden_imports(tmp_path: Path) -> None:
    pkg = tmp_path / "pitwall" / "agents"
    pkg.mkdir(parents=True)
    (pkg / "bad.py").write_text(
        "import pitwall.db\n"
        "from pitwall.api.leases import x\n"
        "from pitwall.core import repository\n"
        "from pitwall.api.schemas.serve import ServeCreate\n"
        "from .sibling import y\n",
        encoding="utf-8",
    )
    assert _violations(pkg) == [
        "bad.py: pitwall.db",
        "bad.py: pitwall.api.leases",
        "bad.py: pitwall.api.leases.x",
        "bad.py: pitwall.core.repository",
    ]
