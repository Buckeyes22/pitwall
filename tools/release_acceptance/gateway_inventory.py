"""Python gateway surface inventory for release acceptance.

``discover(root)`` parses - never imports, executes, or runtime-introspects - the literal
declarations of ``src/pitwall/gateway/`` with ``ast``. The surface IDs are the ones the retired
Node gateway inventory used, so a surface that survived the port keeps its identity:

* ``gateway:route:<path>:<METHOD>`` - each literal entry of the ``self._routes`` dispatch table in
  ``app.py`` (``path -> (method, handler, rate-limited)``).
* ``gateway:inbound-shape:<name>`` - each literal of ``INBOUND_SHAPES`` in ``translation.py``.
* ``gateway:compression-mode:<name>`` - each literal of the ``CompressionPolicy`` alias in
  ``compression.py``.

Rows use kind ``gateway`` with the surface class in ``metadata["category"]`` and carry the
sha256 of the declaring source segment, so a changed method, handler, or rate-limit flag
changes the digest. A declaration that stops being literal raises ``InventoryError`` rather than
silently dropping rows. The ``pitwall gateway serve`` command is a CLI surface and is discovered
by the CLI inventories.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-gateway-inventory.v1"
APP = "src/pitwall/gateway/app.py"
TRANSLATION = "src/pitwall/gateway/translation.py"
COMPRESSION = "src/pitwall/gateway/compression.py"


class InventoryError(ValueError):
    """Raised when a gateway declaration is missing or no longer literal."""


def _parse(root: Path, relative: str) -> tuple[ast.Module, str]:
    path = root / relative
    if not path.is_file():
        raise InventoryError(f"{relative}: required source file not found under {root}")
    text = path.read_text(encoding="utf-8")
    return ast.parse(text), text


def _strings(node: ast.AST, where: str) -> list[tuple[str, int]]:
    """Return ``(value, line)`` for each string literal of a literal collection."""
    elements = node.elts if isinstance(node, (ast.Tuple, ast.List)) else [node]
    found = [
        (e.value, e.lineno)
        for e in elements
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    ]
    if len(found) != len(elements) or not found:
        raise InventoryError(f"{where} is not a literal string collection")
    return found


def _row(
    surface_id: str,
    category: str,
    operation: str,
    source: str,
    line: int,
    segment: str,
    **metadata: Any,
) -> dict[str, Any]:
    digest = hashlib.sha256(f"{source}\0{surface_id}\0{segment}".encode()).hexdigest()
    return {
        "surface_id": surface_id,
        "kind": "gateway",
        "operation": operation,
        "source": f"{source}:{line}",
        "metadata": {"category": category, "declaration_sha256": digest, **metadata},
    }


def _routes(root: Path) -> list[dict[str, Any]]:
    tree, text = _parse(root, APP)
    for node in ast.walk(tree):
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if not any(
            isinstance(t, ast.Attribute) and t.attr == "_routes" and isinstance(t.value, ast.Name)
            for t in targets
        ):
            continue
        table = getattr(node, "value", None)
        if not isinstance(table, ast.Dict):
            continue
        rows = []
        for key, value in zip(table.keys, table.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                raise InventoryError(f"{APP}: route table key is not a string literal")
            elements = value.elts if isinstance(value, ast.Tuple) else []
            method = elements[0] if elements else None
            if not (isinstance(method, ast.Constant) and isinstance(method.value, str)):
                raise InventoryError(f"{APP}:{key.lineno}: route {key.value} has no literal method")
            handler = ast.unparse(elements[1]) if len(elements) > 1 else None
            rate_limited = ast.unparse(elements[2]) if len(elements) > 2 else None
            rows.append(
                _row(
                    f"gateway:route:{key.value}:{method.value}",
                    "route",
                    method.value,
                    APP,
                    key.lineno,
                    (ast.get_source_segment(text, key) or "")
                    + (ast.get_source_segment(text, value) or ""),
                    path=key.value,
                    methods=[method.value],
                    handler=handler,
                    rate_limited=rate_limited,
                    dispatch="route-table",
                )
            )
        return rows
    raise InventoryError(f"{APP} declares no literal self._routes dispatch table")


def _assigned(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return getattr(node, "value", None)
    return None


def _shapes(root: Path) -> list[dict[str, Any]]:
    tree, _ = _parse(root, TRANSLATION)
    value = _assigned(tree, "INBOUND_SHAPES")
    if value is None:
        raise InventoryError(f"{TRANSLATION} declares no INBOUND_SHAPES")
    return [
        _row(
            f"gateway:inbound-shape:{name}",
            "inbound-shape",
            name,
            TRANSLATION,
            line,
            name,
            shape=name,
        )
        for name, line in _strings(value, f"{TRANSLATION} INBOUND_SHAPES")
    ]


def _modes(root: Path) -> list[dict[str, Any]]:
    tree, _ = _parse(root, COMPRESSION)
    value = _assigned(tree, "CompressionPolicy")
    literal = value.slice if isinstance(value, ast.Subscript) else None
    if literal is None:
        raise InventoryError(f"{COMPRESSION} declares no CompressionPolicy Literal alias")
    return [
        _row(
            f"gateway:compression-mode:{name}",
            "compression-mode",
            name,
            COMPRESSION,
            line,
            name,
            mode=name,
        )
        for name, line in _strings(literal, f"{COMPRESSION} CompressionPolicy")
    ]


def discover(root: str | Path) -> list[dict[str, Any]]:
    """Return the deterministic, ``surface_id``-sorted gateway rows for ``root``."""
    base = Path(root)
    rows = _routes(base) + _shapes(base) + _modes(base)
    return sorted(rows, key=lambda row: row["surface_id"])


def discover_with_issues(root: str | Path) -> dict[str, Any]:
    """Return the discovery report shape shared by the domain inventories."""
    return {"schema_version": SCHEMA_VERSION, "surfaces": discover(root), "issues": []}
