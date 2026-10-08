"""The audit actors and actions the code writes must be the ones the schema allows.

``insert_audit`` writes are validated only by Postgres' ``config_audit_actor_check``
and ``config_audit_action_check`` constraints. Unit tests drive that path through
fakes, which accept any string, so a new actor or action can pass every hermetic
gate and still fail at runtime against a real database. This test closes the gap by
comparing the source to the migrated schema directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SRC_DIR = _REPO_ROOT / "src" / "pitwall"

_COLUMNS = ("actor", "action")


async def _allowed(db_catalog: Catalog, column: str) -> set[str]:
    """A column's allow-list as the migrated database enforces it."""

    check = await db_catalog.constraint("config_audit", f"config_audit_{column}_check")
    assert check.kind == "check" and check.columns == (column,)
    return set(check.literals)


def _literals(node: ast.expr) -> list[str]:
    """String values a keyword argument can take, seeing through ``a if c else b``."""

    if isinstance(node, ast.Constant):
        return [node.value] if isinstance(node.value, str) else []
    if isinstance(node, ast.IfExp):
        return _literals(node.body) + _literals(node.orelse)
    return []


#: Keyword arguments that carry an audit actor into a config_audit write somewhere below the
#: call: lease mutations (renew_lease, patch_lease_settings), the RunPod control plane and
#: volume-file services, onboarding, and budget limits all forward ``actor``/``audit_actor``.
_ACTOR_KEYWORDS = ("actor", "audit_actor")
_ACTOR_LITERAL_ALIASES = ("RunPodActor", "AuditActor")


def _written(column: str) -> list[tuple[str, int, str]]:
    """Every literal value the package can write to ``column``.

    For ``action``: values passed as ``action=`` to ``insert_audit``. For ``actor``: every
    literal passed as an ``actor=`` or ``audit_actor=`` keyword to any call (the lease
    mutation service, for example, forwards it to its own audit insert), plus the members of
    the services' actor ``Literal`` aliases.
    """

    found: list[tuple[str, int, str]] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text())
        rel = str(path.relative_to(_REPO_ROOT))
        for node in ast.walk(tree):
            if (
                column == "actor"
                and isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id in _ACTOR_LITERAL_ALIASES
                    for target in node.targets
                )
                and isinstance(node.value, ast.Subscript)
            ):
                members = node.value.slice
                values = members.elts if isinstance(members, ast.Tuple) else [members]
                found.extend((rel, node.lineno, v) for m in values for v in _literals(m))
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if column == "actor":
                keywords = [k for k in node.keywords if k.arg in _ACTOR_KEYWORDS]
            elif name == "insert_audit":
                keywords = [k for k in node.keywords if k.arg == column]
            else:
                continue
            for keyword in keywords:
                found.extend((rel, node.lineno, value) for value in _literals(keyword.value))
    return found


async def test_lease_renewal_actors_from_the_cli_and_reconciler_are_seen_and_allowed(
    db_catalog: Catalog,
) -> None:
    """The renewal actors that once failed the check on Postgres (fixed by 0042)."""
    written = {value for _path, _line, value in _written("actor")}
    assert {"cli:lease", "reconciler:activity"} <= written
    assert {"cli:lease", "reconciler:activity"} <= await _allowed(db_catalog, "actor")


@pytest.mark.parametrize("column", _COLUMNS)
async def test_every_audit_value_written_is_allowed_by_the_constraint(
    db_catalog: Catalog, column: str
) -> None:
    allowed = await _allowed(db_catalog, column)
    written = _written(column)

    assert written, f"expected to find insert_audit call sites writing {column}"

    rejected = [entry for entry in written if entry[2] not in allowed]
    detail = "\n".join(
        f"  {path}:{line} writes {column}={value!r}" for path, line, value in rejected
    )
    assert not rejected, (
        f"these insert_audit calls write values config_audit_{column}_check rejects:\n{detail}\n"
        f"allowed: {sorted(allowed)}\n"
        f"Add a migration extending the constraint before writing a new {column}."
    )
