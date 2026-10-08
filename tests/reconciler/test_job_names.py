"""Every job name a producer enqueues is a name the reconciler worker registers."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

_SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"


def _enqueued_job_names() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "enqueue_job"
                and node.args
            ):
                continue
            first = node.args[0]
            assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
                f"{path.relative_to(_SRC)}:{node.lineno} enqueues a non-literal job name"
            )
            found.append((path.name, first.value))
    return found


def test_every_enqueued_job_name_is_registered() -> None:
    from arq.worker import func

    from pitwall.reconciler import WorkerSettings

    registered = {func(function).name for function in WorkerSettings.functions}
    enqueued = _enqueued_job_names()

    assert enqueued, "the AST scan found no enqueue_job call; the scan is broken"
    unregistered = [(file, name) for file, name in enqueued if name not in registered]
    assert unregistered == []
