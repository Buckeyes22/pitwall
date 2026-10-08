"""AST guard that keeps TUI widget methods at their source-adapter boundary."""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_WIDGET_CALLS = {
    "get_pool",
    "RunpodGraphQLClient",
    "fit_options",
    "serve_model",
    "run_launch",
    "run_teardown",
    "BudgetGate",
    "CapabilityRepository",
    "ProviderRepository",
    "LeaseRepository",
    "ProductionRoutingService",
}
_TUI_DIR = Path(__file__).parents[2] / "src" / "pitwall" / "tui"


def _terminal_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _widget_methods(class_node: ast.ClassDef) -> list[ast.AST]:
    return [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def test_widget_methods_do_not_call_business_logic() -> None:
    violations: list[str] = []
    for path in sorted(_TUI_DIR.glob("*.py")):
        module = ast.parse(path.read_text(), filename=str(path))
        for class_node in ast.walk(module):
            if not isinstance(class_node, ast.ClassDef) or not class_node.name.endswith(
                ("Screen", "Modal", "Panel")
            ):
                continue
            for method in _widget_methods(class_node):
                for node in ast.walk(method):
                    if (
                        isinstance(node, ast.Call)
                        and _terminal_name(node) in _FORBIDDEN_WIDGET_CALLS
                    ):
                        violations.append(
                            f"{path.name}:{class_node.name}.{method.name}: {_terminal_name(node)}"
                        )

    assert violations == []


def test_serve_preview_screen_delegates_to_injected_action_source() -> None:
    module = ast.parse((_TUI_DIR / "serve.py").read_text())
    screen = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.ClassDef) and node.name == "ServePreviewScreen"
    )
    called_attributes = {
        node.func.attr
        for method in _widget_methods(screen)
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert {"preview", "launch"} <= called_attributes


def test_routing_jobs_panel_delegates_to_injected_source() -> None:
    module = ast.parse((_TUI_DIR / "routing_jobs.py").read_text())
    panel = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.ClassDef) and node.name == "RoutingJobsPanel"
    )
    called_attributes = {
        node.func.attr
        for method in _widget_methods(panel)
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "execute_routing_job" in called_attributes
