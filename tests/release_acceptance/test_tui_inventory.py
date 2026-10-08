"""Tests for :mod:`tools.release_acceptance.tui_inventory`.

Expectations are derived independently by parsing the real ``src/pitwall/tui``
tree with :mod:`ast` in this test module. Confirmation tiers are exercised against a
fixture ``confirmation.py`` written into a copied tree, because the product has no
confirmation tier table; the extractor still reads one when a tree declares it.
They never call the
extractor's private helpers and never assert that a declaration exists merely
because the extractor produced a row: a row must match the source declaration
found here, and a dynamic declaration must produce an explicit issue instead of
false coverage.
"""

from __future__ import annotations

import ast
import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import tui_inventory

REPO_ROOT = Path(__file__).resolve().parents[2]
TUI_DIR = REPO_ROOT / tui_inventory.TUI_REL_PATH
APP_PATH = REPO_ROOT / tui_inventory.APP_REL_PATH

APP_BASES = {"App"}
SCREEN_BASES = {"Screen", "ModalScreen", "MarkdownViewer"}
MODAL_BASES = {"ModalScreen"}
WIDGET_BASES = {
    "Horizontal",
    "Vertical",
    "VerticalScroll",
    "DataTable",
    "ListView",
    "Static",
    "Markdown",
}
CATEGORIES = {
    "app",
    "view",
    "widget",
    "action",
    "binding",
    "button",
    "event-handler",
    "registration",
    "confirmation",
}


def _modules() -> dict[str, ast.Module]:
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        for path in sorted(TUI_DIR.glob("*.py"))
        if path.name != "__init__.py"
    }


def _classes() -> dict[str, ast.ClassDef]:
    classes: dict[str, ast.ClassDef] = {}
    for tree in _modules().values():
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                classes[node.name] = node
    return classes


def _base_ids(node: ast.ClassDef) -> set[str]:
    ids: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Subscript):
            base = base.value
        if isinstance(base, ast.Name):
            ids.add(base.id)
        elif isinstance(base, ast.Attribute):
            ids.add(base.attr)
    return ids


def _kinds() -> dict[str, str]:
    classes = _classes()
    resolved: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for name, node in classes.items():
            if name in resolved:
                continue
            for base in _base_ids(node):
                if base in APP_BASES:
                    resolved[name] = "app"
                elif base in SCREEN_BASES:
                    resolved[name] = "view"
                elif base in WIDGET_BASES:
                    resolved[name] = "widget"
                elif base in resolved:
                    resolved[name] = resolved[base]
                if name in resolved:
                    changed = True
                    break
    return resolved


def _app_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for name, node in _classes().items():
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr != "__init__" or not isinstance(child.func.value, ast.Call):
                continue
            if not (
                isinstance(child.func.value.func, ast.Name) and child.func.value.func.id == "super"
            ):
                continue
            for keyword in child.keywords:
                if (
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    names[name] = keyword.value.value
    return names


def _actions() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name, node in _classes().items():
        for child in node.body:
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name.startswith(
                "action_"
            ):
                found.setdefault(name, set()).add(child.name[len("action_") :])
    return found


def _literal_bindings() -> dict[str, list[tuple[str, str]]]:
    found: dict[str, list[tuple[str, str]]] = {}
    for name, node in _classes().items():
        entries: list[tuple[str, str]] = []
        for child in node.body:
            if not isinstance(child, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "BINDINGS" for target in child.targets
            ):
                continue
            if not isinstance(child.value, ast.List | ast.Tuple):
                continue
            for element in child.value.elts:
                if (
                    isinstance(element, ast.Call)
                    and isinstance(element.func, ast.Name)
                    and element.func.id == "Binding"
                    and len(element.args) >= 2
                    and isinstance(element.args[0], ast.Constant)
                    and isinstance(element.args[1], ast.Constant)
                ):
                    entries.append((str(element.args[0].value), str(element.args[1].value)))
                elif (
                    isinstance(element, ast.Tuple)
                    and len(element.elts) >= 2
                    and all(isinstance(elt, ast.Constant) for elt in element.elts)
                ):
                    entries.append((str(element.elts[0].value), str(element.elts[1].value)))
        if entries:
            found[name] = entries
    return found


def _ancestors(name: str) -> list[str]:
    classes = _classes()
    ordered: list[str] = []
    stack = list(_base_ids(classes[name]))
    while stack:
        base = stack.pop(0)
        if base in classes and base not in ordered:
            ordered.append(base)
            stack.extend(_base_ids(classes[base]))
    return ordered


def _expected_binding_rows(cls: str) -> dict[tuple[str, str], tuple[str, bool, bool]]:
    order: list[tuple[str, str, str]] = []
    seen_classes: set[str] = set()
    for owner in [cls, *_ancestors(cls)]:
        if owner in seen_classes:
            continue
        seen_classes.add(owner)
        for key, action in _literal_bindings().get(owner, []):
            order.append((action, key, owner))
    unique: list[tuple[str, str, str]] = []
    for action, key, owner in order:
        if not any(entry[0] == action and entry[1] == key for entry in unique):
            unique.append((action, key, owner))
    rows: dict[tuple[str, str], tuple[str, bool, bool]] = {}
    for index, (action, key, owner) in enumerate(unique):
        shadowed = any(other_key == key for _a, other_key, _o in unique[index + 1 :])
        rows[(key, action)] = (owner, owner != cls, shadowed)
    return rows


def _buttons() -> dict[str, dict[str, str | None]]:
    found: dict[str, dict[str, str | None]] = {}
    for name, node in _classes().items():
        per_class: dict[str, str | None] = {}
        for child in ast.walk(node):
            if not (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "Button"
            ):
                continue
            button_id: str | None = None
            label: str | None = None
            for keyword in child.keywords:
                if (
                    keyword.arg == "id"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    button_id = keyword.value.value
                if (
                    keyword.arg == "label"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    label = keyword.value.value
            if child.args and isinstance(child.args[0], ast.Constant):
                label = str(child.args[0].value)
            if button_id is not None:
                per_class[button_id] = label
        if per_class:
            found[name] = per_class
    return found


def _handlers() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for name, node in _classes().items():
        for child in node.body:
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            decorators = [
                decorator
                for decorator in child.decorator_list
                if isinstance(decorator, ast.Call)
                and (
                    (isinstance(decorator.func, ast.Name) and decorator.func.id == "on")
                    or (isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "on")
                )
            ]
            if child.name.startswith("on_") or decorators:
                found.setdefault(name, set()).add(child.name)
    return found


def _literal_on_selectors() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for name, node in _classes().items():
        for child in node.body:
            if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in child.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "on"
                    and len(decorator.args) >= 2
                    and isinstance(decorator.args[1], ast.Constant)
                    and isinstance(decorator.args[1].value, str)
                ):
                    continue
                found.setdefault((name, child.name), set()).add(decorator.args[1].value)
    return found


def _literal_button_id_mentions(cls: str, method: str) -> set[str]:
    """Independently derive literal ids compared against ``event.button.id``."""
    node = _classes()[cls]
    function = next(
        child
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name == method
    )
    aliases: set[str] = set()
    for child in ast.walk(function):
        if isinstance(child, ast.Assign) and _is_button_id(child.value):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    mentions: set[str] = set()
    for child in ast.walk(function):
        if isinstance(child, ast.Compare) and (
            _is_button_id(child.left)
            or (isinstance(child.left, ast.Name) and child.left.id in aliases)
        ):
            for comparator in child.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    mentions.add(comparator.value)
    return mentions


def _is_button_id(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "button"
    )


def _registrations() -> dict[str, str]:
    found: dict[str, str] = {}
    for tree in _modules().values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "install_screen" or len(node.args) < 2:
                continue
            screen, name = node.args[0], node.args[1]
            if (
                isinstance(screen, ast.Call)
                and isinstance(screen.func, ast.Name)
                and isinstance(name, ast.Constant)
                and isinstance(name.value, str)
            ):
                found[name.value] = screen.func.id
    return found


def _navigations() -> set[str]:
    found: set[str] = set()
    for tree in _modules().values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"push_screen", "switch_screen"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.add(first.value)
    return found


def _surface(report: dict[str, Any], surface_id: str) -> dict[str, Any]:
    for surface in report["surfaces"]:
        if surface["surface_id"] == surface_id:
            return surface
    raise AssertionError(f"missing surface {surface_id!r}")


def _issues(report: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [issue for issue in report["issues"] if issue["code"] == code]


def _copy_tui_tree(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    source_dir = root / tui_inventory.TUI_REL_PATH
    source_dir.mkdir(parents=True)
    for path in sorted(TUI_DIR.glob("*.py")):
        shutil.copy2(path, source_dir / path.name)
    return root


FIXTURE_TIERS = {
    "overview.refresh": "none",
    "lease.renew": "confirm",
    "lease.terminate": "type_to_confirm",
    "provider.disable": "double_confirm",
}

FIXTURE_CONFIRMATION = """\"\"\"Fixture confirmation tier table.\"\"\"

from enum import StrEnum


class ConfirmTier(StrEnum):
    NONE = "none"
    CONFIRM = "confirm"
    TYPE_TO_CONFIRM = "type_to_confirm"
    DOUBLE_CONFIRM = "double_confirm"


_ACTION_TIERS: dict[str, ConfirmTier] = {
    "overview.refresh": ConfirmTier.NONE,
    "lease.renew": ConfirmTier.CONFIRM,
    "lease.terminate": ConfirmTier.TYPE_TO_CONFIRM,
    "provider.disable": ConfirmTier.DOUBLE_CONFIRM,
}


def confirm_tier_for_action(action: str) -> ConfirmTier:
    return _ACTION_TIERS.get(action, ConfirmTier.CONFIRM)
"""


def _copy_tui_tree_with_confirmation(tmp_path: Path, name: str = "repo") -> Path:
    root = _copy_tui_tree(tmp_path, name)
    (root / tui_inventory.CONFIRMATION_REL_PATH).write_text(FIXTURE_CONFIRMATION, encoding="utf-8")
    return root


def _rewrite(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    assert old in source, f"fixture anchor not found in {path}: {old!r}"
    path.write_text(source.replace(old, new), encoding="utf-8")


def test_every_row_is_tui_kind_with_tui_id_and_category() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    assert report["schema_version"] == tui_inventory.SUPPORTED_SCHEMA_VERSION
    for surface in report["surfaces"]:
        assert surface["kind"] == "tui", surface["surface_id"]
        assert surface["surface_id"].startswith("tui:") or surface["surface_id"].startswith(
            "tui:class:"
        ), surface["surface_id"]
        assert surface["metadata"]["category"] in CATEGORIES, surface["surface_id"]
        assert set(surface) == {"surface_id", "kind", "operation", "source", "metadata"}
    assert not hasattr(tui_inventory, "DOCUMENTED_VIEW_NAMES")
    ids = [surface["surface_id"] for surface in report["surfaces"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


def test_app_views_widgets_and_view_names_are_derived_from_source() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    kinds = _kinds()
    app_names = _app_names()

    observed = {
        surface["metadata"]["category"]: surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] in {"app", "view", "widget"}
    }
    for surface in report["surfaces"]:
        category = surface["metadata"]["category"]
        if category not in {"app", "view", "widget"}:
            continue
        class_name = surface["metadata"]["class_name"]
        assert kinds[class_name] == category, class_name

    app_surfaces = {
        surface["metadata"]["class_name"]: surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "app"
    }
    assert set(app_surfaces) == {name for name, kind in kinds.items() if kind == "app"}
    for class_name in app_surfaces:
        assert class_name == "PitwallApp"

    view_surfaces = {
        surface["metadata"]["class_name"]: surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "view"
    }
    assert set(view_surfaces) == {name for name, kind in kinds.items() if kind == "view"}
    for class_name, surface in view_surfaces.items():
        expected_name = app_names.get(class_name, class_name)
        assert surface["surface_id"] == f"tui:view:{expected_name}"
        assert surface["metadata"]["app_name"] == app_names.get(class_name)
        assert surface["operation"] in {
            tui_inventory.OP_SCREEN,
            tui_inventory.OP_MODAL_SCREEN,
        }

    widget_surfaces = {
        surface["metadata"]["class_name"]
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "widget"
    }
    assert widget_surfaces == {name for name, kind in kinds.items() if kind == "widget"}
    assert observed  # every category produced at least one row


def test_actions_cover_every_real_action_method_and_association() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    actions = _actions()
    kinds = _kinds()

    declared = {
        (surface["metadata"]["owner_class"], surface["metadata"]["action_name"]): surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "action"
    }
    expected = {
        (owner, action) for owner, names in actions.items() for action in names if owner in kinds
    }
    assert set(declared) == expected
    for (owner, action), surface in declared.items():
        expected_rows = _expected_binding_rows(owner)
        associations = [row[0] for (key, name), row in expected_rows.items() if name == action]
        if associations:
            assert surface["operation"] == tui_inventory.OP_EXPOSED_ACTION, surface["surface_id"]
            assert surface["metadata"]["association"] == (
                "inherited-binding" if associations[0] != owner else "class-binding"
            )
        else:
            assert surface["operation"] == tui_inventory.OP_INTERNAL_HELPER, surface["surface_id"]
            assert surface["metadata"]["association"] == "none"
        assert surface["metadata"]["exposed"] == (
            surface["operation"] == tui_inventory.OP_EXPOSED_ACTION
        )

    toggle = _surface(report, "tui:action:HardwareFitScreen:toggle_details")
    assert toggle["operation"] == tui_inventory.OP_EXPOSED_ACTION
    assert toggle["metadata"]["association"] == "class-binding"

    refresh = _surface(report, "tui:action:OverviewScreen:refresh")
    assert refresh["metadata"]["method"] == "action_refresh"
    assert refresh["metadata"]["is_async"] is True


def test_bindings_cover_every_literal_binding_including_inherited_and_shadowed() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    kinds = _kinds()
    observed = {
        surface["surface_id"]: surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "binding"
    }
    expected_count = 0
    for owner in kinds:
        for (key, action), (decl_class, inherited, shadowed) in _expected_binding_rows(
            owner
        ).items():
            surface_id = f"tui:binding:{owner}:{key}:{action}"
            surface = observed[surface_id]
            assert surface["metadata"]["declaration_class"] == decl_class, surface_id
            assert surface["metadata"]["inherited"] is inherited, surface_id
            assert surface["metadata"]["shadowed"] is shadowed, surface_id
            assert surface["operation"] == (
                tui_inventory.OP_INHERITED_BINDING if inherited else tui_inventory.OP_KEY_BINDING
            ), surface_id
            expected_count += 1
    assert len(observed) == expected_count
    assert not _issues(report, tui_inventory.ISSUE_ORPHAN_BINDING_ACTION)

    app_row = _surface(report, "tui:binding:PitwallApp:o:show_overview")
    assert app_row["metadata"]["inherited"] is False
    assert app_row["metadata"]["key"] == "o"


def test_buttons_cover_every_literal_button_declaration() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    buttons = _buttons()
    observed = {
        (surface["metadata"]["owner_class"], surface["metadata"]["button_id"]): surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "button"
    }
    expected = {
        (owner, button_id) for owner, per_class in buttons.items() for button_id in per_class
    }
    assert set(observed) == expected
    for (owner, button_id), surface in observed.items():
        assert surface["surface_id"] == f"tui:button:{owner}:{button_id}"
        assert surface["metadata"]["label"] == buttons[owner][button_id]
        assert surface["metadata"]["declaration_source"] == surface["source"]
        assert len(surface["metadata"]["sha256"]) == 64

    submit = _surface(report, "tui:button:RoutingJobsPanel:routing-submit")
    assert submit["metadata"]["label"] == "Submit job"


def test_event_handlers_on_selectors_and_literal_id_checks() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    kinds = _kinds()
    handlers = _handlers()
    expected = {
        (owner, method)
        for owner, methods in handlers.items()
        for method in methods
        if owner in kinds
    }
    observed = {
        (surface["metadata"]["owner_class"], surface["metadata"]["method"]): surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "event-handler"
    }
    assert set(observed) == expected

    selectors = _literal_on_selectors()
    for (owner, method), surface in observed.items():
        expected_selectors = selectors.get((owner, method), set())
        assert set(surface["metadata"]["selectors"]) == expected_selectors, surface["surface_id"]
        expected_events = {
            decorator for decorator in {_event_for(owner, method)} if decorator is not None
        }
        assert set(surface["metadata"]["events"]) == expected_events, surface["surface_id"]
        assert surface["metadata"]["declaration_kinds"], surface["surface_id"]
        assert len(surface["metadata"]["sha256"]) == 64

    guardrail = _surface(report, "tui:event:OperationsScreen:on_button_pressed")
    assert guardrail["metadata"]["button_id_checks"] == ["guardrail-preview-button"]

    routing = _surface(report, "tui:event:RoutingJobsPanel:on_button_pressed")
    assert set(routing["metadata"]["button_id_checks"]) >= {
        "routing-plan",
        "routing-submit",
        "routing-status",
        "routing-result",
        "routing-history",
        "routing-follow",
        "routing-cancel",
        "routing-stop",
    }

    volume = _surface(report, "tui:event:VolumeFilesOperationsPanel:on_button_pressed")
    assert set(volume["metadata"]["button_id_checks"]) >= {
        "volume-files-cancel",
        "volume-files-apply",
        "volume-files-list",
    }

    serve = _surface(report, "tui:event:ConfirmLaunchModal:enable_exact_confirmation")
    assert serve["metadata"]["selectors"] == ["#confirm-text"]
    assert serve["metadata"]["input_id_checks"] == []

    onboarding = _surface(report, "tui:event:RunPodOnboardingPanel:on_input_changed")
    assert onboarding["metadata"]["input_id_checks"] == ["onboarding-request"]
    routing_confirm = _surface(report, "tui:event:ConfirmRoutingJobModal:on_input_changed")
    assert routing_confirm["metadata"]["input_id_checks"] == ["routing-confirm-text"]

    checked = _literal_button_id_mentions("OperationsScreen", "on_button_pressed")
    assert guardrail["metadata"]["button_id_checks"] == sorted(checked)


def _event_for(owner: str, method: str) -> str | None:
    node = _classes()[owner]
    function = next(
        child
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name == method
    )
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call) and decorator.args:
            return ast.unparse(decorator.args[0])
    return None


def test_registrations_derive_from_source_not_a_view_whitelist(tmp_path: Path) -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    registrations = _registrations()
    observed = {
        surface["metadata"]["name"]: surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "registration"
    }
    assert set(observed) == set(registrations)
    for name, class_name in registrations.items():
        surface = observed[name]
        assert surface["surface_id"] == f"tui:registration:{name}"
        assert surface["metadata"]["screen_class"] == class_name
        assert surface["metadata"]["referenced"] is (name in _navigations())
    assert _navigations() <= set(registrations)
    assert not _issues(report, tui_inventory.ISSUE_UNRESOLVED_NAVIGATION)
    assert not _issues(report, tui_inventory.ISSUE_MISSING_SCREEN_CLASS)

    root = _copy_tui_tree(tmp_path, "eleventh")
    app_path = root / tui_inventory.APP_REL_PATH
    _rewrite(
        app_path,
        'self.install_screen(CostScreen(source), "cost")',
        'self.install_screen(CostScreen(source), "new11thview")',
    )
    _rewrite(
        app_path, 'await self.switch_screen("cost")', 'await self.switch_screen("new11thview")'
    )
    mutated = tui_inventory.discover_with_issues(root)
    ids = {surface["surface_id"] for surface in mutated["surfaces"]}
    assert "tui:registration:new11thview" in ids
    assert "tui:registration:cost" not in ids
    row = _surface(mutated, "tui:registration:new11thview")
    assert row["metadata"]["screen_class"] == "CostScreen"
    assert row["metadata"]["referenced"] is True
    assert len(row["metadata"]["navigation_refs"]) == 1
    assert row["metadata"]["navigation_refs"][0].startswith(tui_inventory.APP_REL_PATH.as_posix())
    assert len(row["metadata"]["sha256"]) == 64
    assert not _issues(mutated, tui_inventory.ISSUE_UNRESOLVED_NAVIGATION)


def test_app_bindings_and_actions_are_inventoried_including_in_repo_bases(tmp_path: Path) -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    app = _surface(report, "tui:app:PitwallApp")
    assert app["metadata"]["category"] == "app"
    assert app["metadata"]["binding_declaration_count"] == len(_literal_bindings()["PitwallApp"])
    for key, action in _literal_bindings()["PitwallApp"]:
        _surface(report, f"tui:binding:PitwallApp:{key}:{action}")

    root = _copy_tui_tree(tmp_path, "appbinding")
    app_path = root / tui_inventory.APP_REL_PATH
    _rewrite(
        app_path,
        "class PitwallApp(App[None]):",
        "class PitwallBaseApp(App[None]):\n"
        "    BINDINGS = [Binding('w', 'wave', 'Wave')]\n\n"
        "    async def action_wave(self) -> None:\n"
        "        pass\n\n\n"
        "class PitwallApp(PitwallBaseApp):",
    )
    _rewrite(
        app_path,
        '        Binding("e", "show_resources", "Resources"),\n',
        '        Binding("e", "show_resources", "Resources"),\n'
        '        Binding("x", "show_extra", "Extra"),\n',
    )
    _rewrite(
        app_path,
        "    async def action_show_help(self) -> None:",
        "    async def action_show_extra(self) -> None:\n"
        "        await self.push_screen(HelpScreen(self.screen))\n\n"
        "    async def action_show_help(self) -> None:",
    )
    mutated = tui_inventory.discover_with_issues(root)

    inherited = _surface(mutated, "tui:binding:PitwallApp:w:wave")
    assert inherited["metadata"]["inherited"] is True
    assert inherited["metadata"]["declaration_class"] == "PitwallBaseApp"
    assert inherited["operation"] == tui_inventory.OP_INHERITED_BINDING
    assert _surface(mutated, "tui:app:PitwallBaseApp")["metadata"]["category"] == "app"
    assert (
        _surface(mutated, "tui:action:PitwallBaseApp:wave")["operation"]
        == tui_inventory.OP_EXPOSED_ACTION
    )

    extra = _surface(mutated, "tui:binding:PitwallApp:x:show_extra")
    assert extra["metadata"]["inherited"] is False
    assert (
        _surface(mutated, "tui:action:PitwallApp:show_extra")["operation"]
        == tui_inventory.OP_EXPOSED_ACTION
    )
    assert not _issues(mutated, tui_inventory.ISSUE_ORPHAN_BINDING_ACTION)

    _rewrite(app_path, '        Binding("m", "show_models", "Models"),\n', "")
    dropped = tui_inventory.discover_with_issues(root)
    ids = {surface["surface_id"] for surface in dropped["surfaces"]}
    assert "tui:binding:PitwallApp:m:show_models" not in ids
    assert "tui:binding:PitwallApp:x:show_extra" in ids
    assert _surface(dropped, "tui:action:PitwallApp:show_models")["operation"] == (
        tui_inventory.OP_INTERNAL_HELPER
    )
    assert not _issues(dropped, tui_inventory.ISSUE_ORPHAN_BINDING_ACTION)


def test_the_product_tree_declares_no_confirmation_tiers() -> None:
    report = tui_inventory.discover_with_issues(REPO_ROOT)
    assert not (REPO_ROOT / tui_inventory.CONFIRMATION_REL_PATH).exists()
    assert not [s for s in report["surfaces"] if s["metadata"]["category"] == "confirmation"]
    assert not _issues(report, tui_inventory.ISSUE_CONFIRMATION_POLICY_DECLARATION_ONLY)


def test_confirmation_is_declaration_only_with_no_fabricated_workflow(tmp_path: Path) -> None:
    report = tui_inventory.discover_with_issues(_copy_tui_tree_with_confirmation(tmp_path))
    assert not hasattr(tui_inventory, "DECLARED_OPERATION_WORKFLOWS")

    surfaces = {
        surface["surface_id"]: surface
        for surface in report["surfaces"]
        if surface["metadata"]["category"] == "confirmation"
    }
    assert set(surfaces) == {f"tui:confirmation:{action_id}" for action_id in FIXTURE_TIERS}
    for action_id, tier in FIXTURE_TIERS.items():
        surface = surfaces[f"tui:confirmation:{action_id}"]
        assert surface["metadata"]["tier"] == tier
        assert surface["metadata"]["default_tier"] == "confirm"
        assert surface["metadata"]["enforcement_proven"] is False
        assert surface["metadata"]["policy_references"] == []
        assert "workflow_button_id" not in surface["metadata"]
        assert "workflow_owner_class" not in surface["metadata"]
        assert "workflow_handler" not in surface["metadata"]
        assert surface["metadata"]["policy"].startswith("declaration-only")
        assert len(surface["metadata"]["entry_sha256"]) == 64
        assert len(surface["metadata"]["table_sha256"]) == 64

    declaration_only = _issues(report, tui_inventory.ISSUE_CONFIRMATION_POLICY_DECLARATION_ONLY)
    assert len(declaration_only) == 1
    assert declaration_only[0]["severity"] == tui_inventory.UNRESOLVED_SEVERITY
    assert declaration_only[0]["surface_ids"] == [
        f"tui:confirmation:{action_id}" for action_id in sorted(FIXTURE_TIERS)
    ]


def test_policy_fixture_mutation_changes_only_confirmation_rows(tmp_path: Path) -> None:
    root = _copy_tui_tree_with_confirmation(tmp_path, "policy")
    before = _surface(tui_inventory.discover_with_issues(root), "tui:confirmation:lease.renew")[
        "metadata"
    ]

    confirmation_path = root / tui_inventory.CONFIRMATION_REL_PATH
    _rewrite(
        confirmation_path,
        '"lease.renew": ConfirmTier.CONFIRM,',
        '"lease.renew": ConfirmTier.DOUBLE_CONFIRM,',
    )
    report = tui_inventory.discover_with_issues(root)
    after = _surface(report, "tui:confirmation:lease.renew")["metadata"]
    assert after["tier"] == "double_confirm"
    assert after["entry_sha256"] != before["entry_sha256"]
    assert after["table_sha256"] != before["table_sha256"]
    assert _surface(report, "tui:confirmation:lease.renew")["source"].startswith(
        tui_inventory.CONFIRMATION_REL_PATH.as_posix()
    )

    _rewrite(confirmation_path, '"lease.renew": ConfirmTier.DOUBLE_CONFIRM,\n', "")
    report = tui_inventory.discover_with_issues(root)
    ids = {surface["surface_id"] for surface in report["surfaces"]}
    assert "tui:confirmation:lease.renew" not in ids
    assert "tui:confirmation:provider.disable" in ids


def test_dynamic_declarations_emit_explicit_issues_not_fabricated_rows(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "dynamic")
    cost_path = root / tui_inventory.TUI_REL_PATH / "cost.py"
    _rewrite(
        cost_path,
        '    BINDINGS = [\n        Binding("r", "refresh", "Refresh"),\n    ]',
        '    BINDINGS = [\n        Binding("r", "refresh", "Refresh"),\n'
        "        *EXTRA_BINDINGS,\n    ]",
    )

    app_path = root / tui_inventory.APP_REL_PATH
    _rewrite(
        app_path,
        '        self.install_screen(CostScreen(source), "cost")',
        '        self.install_screen(_cost_screen(), "cost")',
    )
    _rewrite(
        app_path,
        '        self.install_screen(OverviewScreen(source), "overview")',
        '        provider_registry_name = "overview"\n'
        "        self.install_screen(OverviewScreen(source), provider_registry_name)",
    )

    resources_path = root / tui_inventory.TUI_REL_PATH / "resources.py"
    _rewrite(
        resources_path,
        'yield Button("Lookup / Hub search", id="resources-query")',
        'yield Button("Lookup / Hub search")',
    )

    serve_path = root / tui_inventory.TUI_REL_PATH / "serve.py"
    _rewrite(
        serve_path,
        '@on(Button.Pressed, "#confirm-cancel")',
        "@on(Button.Pressed, SELECTOR)",
    )

    report = tui_inventory.discover_with_issues(root)
    ids = {surface["surface_id"] for surface in report["surfaces"]}
    assert "tui:registration:cost" not in ids
    assert "tui:registration:overview" not in ids
    assert "tui:binding:CostScreen:r:refresh" in ids
    assert "tui:binding:CostScreen:extra:starred" not in ids
    assert "tui:button:ResourcesScreen:resources-query" not in ids
    assert all(not surface["surface_id"].startswith("tui:class:") for surface in report["surfaces"])

    non_literal = _issues(report, tui_inventory.ISSUE_NON_LITERAL_BINDINGS)
    assert any("CostScreen" in issue["message"] for issue in non_literal)
    assert any("starred" not in issue["message"] for issue in non_literal)

    dynamic_registration = _issues(report, tui_inventory.ISSUE_DYNAMIC_SCREEN_REGISTRATION)
    assert dynamic_registration and all(
        issue["severity"] == tui_inventory.ISSUE_SEVERITY for issue in dynamic_registration
    )
    assert {issue["surface_ids"][0] for issue in dynamic_registration}

    dynamic_button = _issues(report, tui_inventory.ISSUE_DYNAMIC_BUTTON_ID)
    assert any("ResourcesScreen" in issue["message"] for issue in dynamic_button)

    dynamic_selector = _issues(report, tui_inventory.ISSUE_DYNAMIC_ON_SELECTOR)
    assert any("#confirm-cancel" not in issue["message"] for issue in dynamic_selector)
    cancel = _surface(report, "tui:event:ConfirmLaunchModal:cancel_button")
    assert cancel["metadata"]["selectors"] == []

    unresolved = {
        issue["code"]
        for issue in report["issues"]
        if issue["severity"] == tui_inventory.UNRESOLVED_SEVERITY
    }
    assert unresolved == {code for code, _message in tui_inventory._UNRESOLVED_SCOPES}


def test_unclassified_class_declarations_are_reported_not_inventoried(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "unclassified")
    cost_path = root / tui_inventory.TUI_REL_PATH / "cost.py"
    _rewrite(
        cost_path,
        "class CostScreen(Screen[None]):",
        "class FloatingThing:\n"
        "    BINDINGS = [Binding('k', 'kick', 'Kick')]\n\n"
        "    def action_kick(self) -> None:\n"
        "        pass\n\n\n"
        "class CostScreen(Screen[None]):",
    )
    report = tui_inventory.discover_with_issues(root)
    issues = _issues(report, tui_inventory.ISSUE_UNCLASSIFIED_TUI_CLASS)
    assert any("FloatingThing" in issue["message"] for issue in issues)
    ids = {surface["surface_id"] for surface in report["surfaces"]}
    assert "tui:action:FloatingThing:kick" not in ids
    assert "tui:binding:FloatingThing:k:kick" not in ids


def test_inherited_bindings_survive_and_shadow_without_being_dropped(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "inherit")
    cost_path = root / tui_inventory.TUI_REL_PATH / "cost.py"
    _rewrite(
        cost_path,
        "class CostScreen(Screen[None]):",
        "class CostBase(Screen[None]):\n"
        "    BINDINGS = [Binding('z', 'zen', 'Zen')]\n\n"
        "    async def action_zen(self) -> None:\n"
        "        pass\n\n\n"
        "class CostScreen(CostBase):",
    )
    report = tui_inventory.discover_with_issues(root)
    inherited = _surface(report, "tui:binding:CostScreen:z:zen")
    assert inherited["metadata"]["inherited"] is True
    assert inherited["metadata"]["declaration_class"] == "CostBase"
    assert (
        _surface(report, "tui:action:CostBase:zen")["operation"] == tui_inventory.OP_EXPOSED_ACTION
    )

    _rewrite(
        cost_path,
        '    BINDINGS = [\n        Binding("r", "refresh", "Refresh"),\n    ]',
        '    BINDINGS = [\n        Binding("r", "refresh", "Refresh"),\n'
        '        Binding("z", "refresh", "Zed"),\n    ]',
    )
    report = tui_inventory.discover_with_issues(root)
    sub_row = _surface(report, "tui:binding:CostScreen:z:refresh")
    base_row = _surface(report, "tui:binding:CostScreen:z:zen")
    assert sub_row["metadata"]["inherited"] is False
    assert sub_row["metadata"]["shadowed"] is False
    assert base_row["metadata"]["shadowed"] is True


def test_binding_shadowing_and_orphan_actions_are_recorded(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "shadow")
    cost_path = root / tui_inventory.TUI_REL_PATH / "cost.py"
    _rewrite(
        cost_path,
        '    BINDINGS = [\n        Binding("r", "refresh", "Refresh"),\n    ]',
        '    BINDINGS = [\n        Binding("r", "refresh", "Refresh"),\n'
        '        Binding("r", "reload", "Refresh again"),\n    ]',
    )
    report = tui_inventory.discover_with_issues(root)
    refresh_row = _surface(report, "tui:binding:CostScreen:r:refresh")
    reload_row = _surface(report, "tui:binding:CostScreen:r:reload")
    assert refresh_row["metadata"]["shadowed"] is False
    assert reload_row["metadata"]["shadowed"] is False
    assert _issues(report, tui_inventory.ISSUE_ORPHAN_BINDING_ACTION)


def test_changed_bindings_and_key_renames_change_rows(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "rename")
    overview_path = root / tui_inventory.TUI_REL_PATH / "overview.py"
    _rewrite(overview_path, 'Binding("r", "refresh"', 'Binding("R", "refresh"')
    ids = {
        surface["surface_id"] for surface in tui_inventory.discover_with_issues(root)["surfaces"]
    }
    assert "tui:binding:OverviewScreen:r:refresh" not in ids
    assert "tui:binding:OverviewScreen:R:refresh" in ids


def test_unresolved_navigation_missing_class_and_unreferenced_registration(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "nav")
    app_path = root / tui_inventory.APP_REL_PATH
    _rewrite(app_path, 'await self.push_screen("serve")', 'await self.push_screen("ghost")')
    report = tui_inventory.discover_with_issues(root)
    unresolved = _issues(report, tui_inventory.ISSUE_UNRESOLVED_NAVIGATION)
    assert any(issue["surface_ids"] == ["tui:registration:ghost"] for issue in unresolved)

    cost_path = root / tui_inventory.TUI_REL_PATH / "cost.py"
    cost_path.rename(root / tui_inventory.TUI_REL_PATH / "cost.py.disabled")
    report = tui_inventory.discover_with_issues(root)
    missing = _issues(report, tui_inventory.ISSUE_MISSING_SCREEN_CLASS)
    assert any(issue["surface_ids"] == ["tui:registration:cost"] for issue in missing)
    assert "tui:view:cost" not in {surface["surface_id"] for surface in report["surfaces"]}
    registration = _surface(report, "tui:registration:cost")
    assert registration["metadata"]["screen_class"] == "CostScreen"

    _rewrite(
        app_path,
        'self.install_screen(CostScreen(source), "cost")',
        'self.install_screen(CostScreen(source), "ghost2")',
    )
    report = tui_inventory.discover_with_issues(root)
    unreferenced = _issues(report, tui_inventory.ISSUE_UNREFERENCED_REGISTRATION)
    assert any(issue["surface_ids"] == ["tui:registration:ghost2"] for issue in unreferenced)


def test_on_selector_without_matching_button_is_an_issue(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "onbutton")
    resources_path = root / tui_inventory.TUI_REL_PATH / "resources.py"
    _rewrite(
        resources_path,
        '@on(Button.Pressed, "#resources-query")',
        '@on(Button.Pressed, "#ghost-button")',
    )
    report = tui_inventory.discover_with_issues(root)
    missing = _issues(report, tui_inventory.ISSUE_ON_SELECTOR_MISSING_BUTTON)
    assert any("ghost-button" in issue["message"] for issue in missing)
    surface = _surface(report, "tui:event:ResourcesScreen:query_button")
    assert surface["metadata"]["selectors"] == ["#ghost-button"]


def test_source_digests_are_stable_and_content_sensitive() -> None:
    first = tui_inventory.discover_with_issues(REPO_ROOT)
    second = tui_inventory.discover_with_issues(REPO_ROOT)
    assert first == second

    action = _surface(first, "tui:action:HardwareFitScreen:refresh")
    module_path = TUI_DIR / "hardware_fit.py"
    lineno = int(action["source"].rsplit(":", 1)[-1])
    assert "action_refresh" in module_path.read_text(encoding="utf-8").splitlines()[lineno - 1]

    class_row = _surface(first, "tui:view:hardware-fit")
    class_source = module_path.read_text(encoding="utf-8")
    class_lines = class_source.splitlines()
    tree = ast.parse(class_source)
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HardwareFitScreen"
    )
    expected_digest = hashlib.sha256(
        "\n".join(class_lines[class_node.lineno - 1 : class_node.end_lineno]).encode("utf-8")
    ).hexdigest()
    assert class_row["metadata"]["sha256"] == expected_digest
    assert class_row["source"] == f"src/pitwall/tui/hardware_fit.py:{class_node.lineno}"

    for surface in first["surfaces"]:
        digest = surface["metadata"].get("sha256")
        assert isinstance(digest, str) and len(digest) == 64, surface["surface_id"]


def test_missing_package_and_unparsable_module_raise(tmp_path: Path) -> None:
    with pytest.raises(tui_inventory.TuiInventoryError, match="package not found"):
        tui_inventory.discover_with_issues(tmp_path / "absent")

    root = _copy_tui_tree(tmp_path, "unparsable")
    (root / tui_inventory.TUI_REL_PATH / "cost.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(tui_inventory.TuiInventoryError, match="unparsable"):
        tui_inventory.discover_with_issues(root)

    root = _copy_tui_tree_with_confirmation(tmp_path, "confirmation-removed")
    (root / tui_inventory.CONFIRMATION_REL_PATH).unlink()
    report = tui_inventory.discover_with_issues(root)
    assert not [s for s in report["surfaces"] if s["metadata"]["category"] == "confirmation"]


def test_no_application_instantiation_or_network_in_source() -> None:
    source = Path(tui_inventory.__file__).read_text(encoding="utf-8")
    for forbidden in ("PitwallApp(", "httpx", "asyncpg", "socket", "import pitwall"):
        assert forbidden not in source


def test_nested_package_and_init_declarations_are_discovered(tmp_path: Path) -> None:
    root = _copy_tui_tree(tmp_path, "nested")
    nested = root / tui_inventory.TUI_REL_PATH / "new_views"
    nested.mkdir()
    (nested / "__init__.py").write_text(
        "class NestedScreen(Screen):\n"
        "    BINDINGS = [('n', 'nested', 'Nested')]\n"
        "    def action_nested(self): pass\n"
        "def register(app): app.install_screen(NestedScreen(), 'eleventh')\n"
    )
    report = tui_inventory.discover_with_issues(root)
    assert (
        _surface(report, "tui:registration:eleventh")["metadata"]["screen_class"] == "NestedScreen"
    )
    assert _surface(report, "tui:action:NestedScreen:nested")["source"].startswith(
        "src/pitwall/tui/new_views/__init__.py:"
    )


def test_policy_reference_does_not_prove_enforcement(tmp_path: Path) -> None:
    root = _copy_tui_tree_with_confirmation(tmp_path, "reference")
    (root / tui_inventory.TUI_REL_PATH / "unused.py").write_text(
        "from .confirmation import ConfirmTier\nunused = ConfirmTier.CONFIRM\n"
    )
    row = _surface(tui_inventory.discover_with_issues(root), "tui:confirmation:lease.renew")
    assert row["metadata"]["policy_references"]
    assert row["metadata"]["enforcement_proven"] is False


def test_binding_override_matches_installed_textual(tmp_path: Path) -> None:
    from textual.screen import Screen

    class Parent(Screen):
        BINDINGS = [("r", "parent", "Parent")]

    class Child(Parent):
        BINDINGS = [("r", "first", "First"), ("r", "second", "Second")]

    assert [b.action for b in Child._merge_bindings().key_to_bindings["r"]] == ["first", "second"]
    root = _copy_tui_tree(tmp_path, "textual")
    (root / tui_inventory.TUI_REL_PATH / "inherit_fixture.py").write_text(
        "class Parent(Screen):\n    BINDINGS = [('r', 'parent', 'Parent')]\n"
        "class Child(Parent):\n    BINDINGS = [('r', 'first', 'First'), ('r', 'second', 'Second')]\n"
    )
    report = tui_inventory.discover_with_issues(root)
    assert _surface(report, "tui:binding:Child:r:parent")["metadata"]["shadowed"] is True
    for action in ("first", "second"):
        assert _surface(report, f"tui:binding:Child:r:{action}")["metadata"]["shadowed"] is False
