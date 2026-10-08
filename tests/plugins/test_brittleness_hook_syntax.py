"""Plugin hook scripts run under the user's system python3 (macOS ships 3.9)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PLUGINS = Path(__file__).resolve().parents[2] / "plugins"
HOOKS = sorted(PLUGINS.rglob("*.py"))


def test_plugin_hooks_were_found() -> None:
    assert HOOKS, "no plugin hook scripts found under plugins/"


@pytest.mark.parametrize("path", HOOKS, ids=lambda p: str(p.relative_to(PLUGINS)))
def test_plugin_hook_parses_on_python_39(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path), feature_version=(3, 9))
    except SyntaxError as exc:
        pytest.fail(
            f"{path.relative_to(PLUGINS)}:{exc.lineno}: {exc.msg}; "
            "hooks run under the system python3 (3.9 on macOS), so parenthesize "
            "`except (A, B):`"
        )


# Standard-library modules newer than Python 3.9 that a hook must not import unguarded.
_NEWER_THAN_39 = {"tomllib"}


@pytest.mark.parametrize("path", HOOKS, ids=lambda p: str(p.relative_to(PLUGINS)))
def test_plugin_hook_does_not_import_post_39_stdlib_unguarded(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:  # top level only: a try/except ImportError wrapper is not a top-level Import
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        bad = sorted(set(names) & _NEWER_THAN_39)
        assert not bad, (
            f"{path.relative_to(PLUGINS)}:{node.lineno} imports {bad} unguarded; "
            "the system python3 may be 3.9. Wrap it in try/except ImportError."
        )
