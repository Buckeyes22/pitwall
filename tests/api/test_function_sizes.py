"""A-17, A-18, A-19, A-52, A-53: the named oversized functions stay split into short steps."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"
_MAX_LINES = 120

_NAMED_FUNCTIONS = [
    ("api/leases/launch.py", "_run_launch_runpod"),
    ("api/leases/teardown.py", "run_teardown"),
    ("onboarding.py", "_apply_locked"),
    ("core/cost_reporting.py", "recent_workloads_read"),
    ("config.py", "_explicit_env_settings_data"),
]


def _function_lengths(relative_path: str) -> dict[str, int]:
    tree = ast.parse((_SRC / relative_path).read_text(encoding="utf-8"))
    return {
        node.name: (node.end_lineno or node.lineno) - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


@pytest.mark.parametrize(("relative_path", "name"), _NAMED_FUNCTIONS)
def test_named_function_is_at_most_120_lines(relative_path: str, name: str) -> None:
    lengths = _function_lengths(relative_path)
    assert name in lengths
    assert lengths[name] <= _MAX_LINES, f"{name} is {lengths[name]} lines"


@pytest.mark.parametrize(("relative_path", "name"), _NAMED_FUNCTIONS)
def test_split_steps_are_at_most_120_lines(relative_path: str, name: str) -> None:
    prefix_steps = {
        node: length
        for node, length in _function_lengths(relative_path).items()
        if node.startswith((name, f"{name}_", f"_{name.lstrip('_')}_"))
    }
    assert all(length <= _MAX_LINES for length in prefix_steps.values()), prefix_steps
