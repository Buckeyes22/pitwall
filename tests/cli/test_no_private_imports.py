"""``pitwall.cli`` modules must import only public names from other pitwall modules."""

from __future__ import annotations

import ast
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parents[2] / "src" / "pitwall" / "cli"


def test_cli_imports_no_private_symbols() -> None:
    private: list[str] = []
    for path in sorted(CLI_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        private.extend(
            f"{path.name}: {node.module}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.split(".")[0] == "pitwall"
            for alias in node.names
            if alias.name.startswith("_") and not alias.name.startswith("__")
        )
    assert private == []
