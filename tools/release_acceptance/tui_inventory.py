"""TUI-surface inventory for the Pitwall Textual operator console.

Extracts deterministic surface rows from the real source of ``src/pitwall/tui``
using the standard-library :mod:`ast` module only. Modules are read and parsed
as text: no application or screen object is constructed, no credentials are
read, and no network call is made. Every row is ``kind == "tui"`` and carries a
``tui:``-namespaced ``surface_id``; the original surface class is recorded as
``metadata["category"]``:

* ``tui:app:<ClassName>`` - an ``App`` subclass (the shell). Its ``BINDINGS``
  and ``action_*`` methods are inventoried exactly like screen bindings.
* ``tui:view:<name-or-class>`` - a screen class. ``<name>`` is the registered
  name declared through ``super().__init__(name=...)`` when present, otherwise
  the class name. There is no fixed view-name whitelist: every class that
  resolves to ``App``/``Screen``/``ModalScreen``/``MarkdownViewer`` (directly or
  through an in-repo base) is inventoried, and ``install_screen`` registrations
  are derived from every literal call in the package, including names this
  module has never seen before.
* ``tui:widget:<ClassName>`` - an in-package widget class.
* ``tui:action:<ClassName>:<name>`` - an ``action_*`` method. ``operation`` is
  ``exposed-ui-action`` only when a literal binding declared on the class or on
  an in-repo base names the action; otherwise the row is ``internal-helper``.
* ``tui:binding:<ClassName>:<key>:<action>`` - a resolved literal ``BINDINGS``
  entry; ``inherited`` marks declarations from in-repo base classes and
  ``shadowed`` marks entries whose key is re-bound later, so nothing is dropped.
* ``tui:button:<ClassName>:<button-id>`` - a literal ``Button(..., id=...)``
  declaration in a class body, with the exact source segment digest.
* ``tui:event:<ClassName>:<method>`` - an event handler declared as an ``on_*``
  method or decorated with ``@on(...)``. The row records the literal decorator
  event and selector, plus any literal ``event.button.id`` / ``event.input.id``
  checks (direct comparisons, aliases, and literal dispatch-dict keys) found in
  the handler body. No causal association is inferred that the source does not
  literally show.
* ``tui:registration:<name>`` - one literal ``install_screen(..., "<name>")``
  call, with the literal ``push_screen``/``switch_screen`` names that reach it.
* ``tui:confirmation:<action-id>`` - one entry of ``confirmation._ACTION_TIERS``.
  This is a declaration-only policy table: it exports no operator button mapping
  and does not establish runtime enforcement, so the row records the tier and the
  entry/table source digests and claims nothing beyond that. The absence of
  policy references is itself reported as an explicit issue. A tree without
  ``confirmation.py`` declares no tiers and yields no confirmation rows.

Source bytes drive the contracts: file-, class-, action-, binding-, button-,
handler-, registration-, and confirmation-entry digests are sha256 over the
exact original source segments (``_segment``/``_span``), so any edit to a
declaration changes the inventory.

``discover`` returns the surface rows; ``discover_with_issues`` additionally
returns explicit issues for declarations this narrow extractor cannot resolve
to a literal (non-literal ``BINDINGS``, dynamic screen registration, dynamic
button ids, non-literal ``@on`` selectors, direct self-bindings, orphaned
binding actions, navigations without a registration, registrations without a
class or without navigation, unclassified classes that declare TUI surface
parts) and for the scopes owned by other units. Nothing is invented: a
declaration that cannot be read literally yields an issue, never a fabricated
row, and unknown classes never produce false coverage.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

TUI_REL_PATH = Path("src/pitwall/tui")
CONFIRMATION_REL_PATH = TUI_REL_PATH / "confirmation.py"
APP_REL_PATH = TUI_REL_PATH / "app.py"
SUPPORTED_SCHEMA_VERSION = 2

KIND = "tui"

CATEGORY_APP = "app"
CATEGORY_VIEW = "view"
CATEGORY_WIDGET = "widget"
CATEGORY_ACTION = "action"
CATEGORY_BINDING = "binding"
CATEGORY_BUTTON = "button"
CATEGORY_EVENT_HANDLER = "event-handler"
CATEGORY_REGISTRATION = "registration"
CATEGORY_CONFIRMATION = "confirmation"

OP_APP_SHELL = "app-shell"
OP_SCREEN = "screen"
OP_MODAL_SCREEN = "modal-screen"
OP_WIDGET = "widget"
OP_EXPOSED_ACTION = "exposed-ui-action"
OP_INTERNAL_HELPER = "internal-helper"
OP_KEY_BINDING = "key-binding"
OP_INHERITED_BINDING = "inherited-key-binding"
OP_BUTTON = "button"
OP_EVENT_HANDLER = "event-handler"
OP_SCREEN_REGISTRATION = "screen-registration"
OP_CONFIRMATION_TIER = "confirmation-tier"

ISSUE_NON_LITERAL_BINDINGS = "non-literal-bindings"
ISSUE_DYNAMIC_SCREEN_REGISTRATION = "dynamic-screen-registration"
ISSUE_DIRECT_SELF_BINDING = "direct-self-binding"
ISSUE_ORPHAN_BINDING_ACTION = "orphan-binding-action"
ISSUE_MISSING_SCREEN_CLASS = "missing-screen-class"
ISSUE_UNRESOLVED_NAVIGATION = "unresolved-screen-navigation"
ISSUE_DYNAMIC_CONFIRMATION_TIERS = "dynamic-confirmation-tiers"
ISSUE_UNRESOLVED_CONFIRMATION_DEFAULT = "unresolved-confirmation-default"
ISSUE_DYNAMIC_BUTTON_ID = "dynamic-button-id"
ISSUE_DYNAMIC_ON_SELECTOR = "dynamic-on-selector"
ISSUE_UNCLASSIFIED_TUI_CLASS = "unclassified-tui-class"
ISSUE_UNREFERENCED_REGISTRATION = "unreferenced-screen-registration"
ISSUE_ON_SELECTOR_MISSING_BUTTON = "on-selector-missing-button"
ISSUE_CONFIRMATION_POLICY_DECLARATION_ONLY = "confirmation-policy-declaration-only"
ISSUE_SEVERITY = "warning"
UNRESOLVED_SEVERITY = "unresolved"

APP_BASES = frozenset({"App"})
SCREEN_BASES = frozenset({"Screen", "ModalScreen", "MarkdownViewer"})
MODAL_BASES = frozenset({"ModalScreen"})
WIDGET_BASES = frozenset(
    {
        "Horizontal",
        "Vertical",
        "VerticalScroll",
        "DataTable",
        "ListView",
        "Static",
        "Markdown",
    }
)
BUTTON_PRESSED = "Button.Pressed"
TEXTUAL_BUILTIN_ACTIONS = frozenset({"quit", "bell"})
CONFIRMATION_REF_NAMES = frozenset({"confirm_tier_for_action", "ConfirmTier"})

_UNRESOLVED_SCOPES: tuple[tuple[str, str], ...] = (
    (
        "unresolved-textual-base-bindings",
        "BINDINGS inherited from Textual base classes outside src/pitwall/tui (App and "
        "Screen defaults) are not enumerated; only in-repo declarations are resolved",
    ),
    (
        "unresolved-runtime-construction",
        "widgets, buttons, and screens constructed by runtime factories instead of literal "
        "declarations are not enumerated; non-literal declarations yield explicit issues",
    ),
    (
        "unresolved-handler-behaviour",
        "event-handler bodies are not analyzed for effects; only the declaration, its literal "
        "@on selector, and literal event.button.id / event.input.id checks are recorded",
    ),
)


class TuiInventoryError(ValueError):
    """Raised when the TUI package is missing, unparsable, or ambiguous.

    The message names the offending path or class so the caller can fix the
    declaration rather than receive a silently incomplete inventory.
    """


@dataclass(frozen=True, slots=True)
class _BindingDecl:
    key: str
    action: str
    description: str | None
    show: bool
    lineno: int
    source_sha256: str

    def __hash__(self) -> int:
        return hash((self.key, self.action, self.source_sha256, self.lineno))


@dataclass(frozen=True, slots=True)
class _ActionInfo:
    name: str
    method: str
    lineno: int
    is_async: bool
    source_sha256: str


@dataclass(frozen=True, slots=True)
class _ButtonInfo:
    button_id: str
    label: str | None
    lineno: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class _OnDecorator:
    event: str
    selector: str | None
    literal: bool
    lineno: int


@dataclass(frozen=True, slots=True)
class _HandlerDecl:
    method: str
    lineno: int
    is_on_method: bool
    decorators: tuple[_OnDecorator, ...]
    button_id_checks: tuple[str, ...]
    input_id_checks: tuple[str, ...]
    is_async: bool
    source_sha256: str


@dataclass(slots=True)
class _ClassInfo:
    name: str
    module_rel: str
    lineno: int
    bases: tuple[str, ...]
    kind: str | None
    is_modal: bool
    app_name: str | None
    dom_id: str | None
    actions: dict[str, _ActionInfo]
    binding_decls: list[_BindingDecl]
    buttons: dict[str, _ButtonInfo]
    handlers: list[_HandlerDecl]
    source_sha256: str = ""

    def declares_tui_parts(self) -> bool:
        return bool(self.actions or self.binding_decls or self.buttons or self.handlers)


@dataclass(frozen=True, slots=True)
class _Module:
    rel_path: str
    path: Path
    source: str
    lines: tuple[str, ...]
    file_sha256: str
    tree: ast.Module


@dataclass(frozen=True, slots=True)
class _Registration:
    name: str
    screen_class: str
    module_rel: str
    lineno: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class _Navigation:
    name: str
    module_rel: str
    lineno: int
    call: str


@dataclass(frozen=True, slots=True)
class _ConfirmationEntry:
    action_id: str
    tier: str
    lineno: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class _ConfirmationTable:
    entries: dict[str, _ConfirmationEntry]
    default_tier: str | None
    source_sha256: str
    reference_sites: tuple[str, ...]


def discover(root: str | Path) -> list[dict[str, Any]]:
    """Return the deterministic, ``surface_id``-sorted surface rows for ``root``."""
    return cast(list[dict[str, Any]], discover_with_issues(root)["surfaces"])


def discover_with_issues(root: str | Path) -> dict[str, Any]:
    """Return ``{"surfaces", "issues", "schema_version"}`` for ``root``.

    Raises :class:`TuiInventoryError` when the TUI package is missing, a module
    cannot be parsed, two classes claim the same
    identifier, two registrations claim the same name, or two rows claim the
    same ``surface_id``.
    """
    root = Path(root)
    tui_dir = root / TUI_REL_PATH
    if not tui_dir.is_dir():
        raise TuiInventoryError(f"{TUI_REL_PATH.as_posix()}: package not found at {tui_dir}")
    module_paths = sorted(path for path in tui_dir.rglob("*.py") if "__pycache__" not in path.parts)
    if not module_paths:
        raise TuiInventoryError(f"{TUI_REL_PATH.as_posix()}: no module files under {tui_dir}")
    modules = [_load_module(root, path) for path in module_paths]
    issues: list[dict[str, Any]] = []
    classes = _collect_classes(modules, issues)
    registrations, navigation = _collect_registrations(modules, issues)
    confirmation = _parse_confirmation(modules, issues)

    surfaces: list[dict[str, Any]] = []
    surfaces.extend(_class_surfaces(classes))
    surfaces.extend(_action_surfaces(classes, issues))
    surfaces.extend(_binding_surfaces(classes))
    surfaces.extend(_button_surfaces(classes))
    surfaces.extend(_event_surfaces(classes, issues))
    surfaces.extend(_registration_surfaces(registrations, navigation))
    surfaces.extend(_confirmation_surfaces(confirmation, issues))
    _report_unresolved_navigation(registrations, navigation, issues)
    _report_missing_screen_classes(registrations, classes, issues)
    _report_unreferenced_registrations(registrations, navigation, issues)
    _ensure_unique_surface_ids(surfaces)

    for code, message in _UNRESOLVED_SCOPES:
        issues.append(
            {
                "code": code,
                "message": message,
                "severity": UNRESOLVED_SEVERITY,
                "surface_ids": [],
            }
        )

    surfaces.sort(key=lambda surface: surface["surface_id"])
    issues.sort(key=lambda issue: (issue["code"], ",".join(issue["surface_ids"]), issue["message"]))
    return {
        "surfaces": surfaces,
        "issues": issues,
        "schema_version": SUPPORTED_SCHEMA_VERSION,
    }


def _row(
    surface_id: str, operation: str, source: str, category: str, **metadata: Any
) -> dict[str, Any]:
    return {
        "surface_id": surface_id,
        "kind": KIND,
        "operation": operation,
        "source": source,
        "metadata": {"category": category, **metadata},
    }


def _load_module(root: Path, path: Path) -> _Module:
    data = path.read_bytes()
    try:
        source = data.decode("utf-8")
        tree = ast.parse(source, filename=path.as_posix())
    except (UnicodeDecodeError, SyntaxError) as exc:
        line = getattr(exc, "lineno", None) or 1
        message = getattr(exc, "msg", None) or str(exc)
        raise TuiInventoryError(
            f"{path.relative_to(root).as_posix()}: unparsable at line {line}: {message}"
        ) from exc
    return _Module(
        rel_path=path.relative_to(root).as_posix(),
        path=path,
        source=source,
        lines=tuple(source.splitlines()),
        file_sha256=_digest_bytes(data),
        tree=tree,
    )


def _collect_classes(modules: list[_Module], issues: list[dict[str, Any]]) -> dict[str, _ClassInfo]:
    classes: dict[str, _ClassInfo] = {}
    modules_by_class: dict[str, str] = {}
    for module in modules:
        for node in module.tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name in classes:
                raise TuiInventoryError(
                    f"duplicate class name {node.name!r} in {module.rel_path} and "
                    f"{modules_by_class[node.name]}; surface ids require unique class names"
                )
            classes[node.name] = _class_info(module, node, issues)
            modules_by_class[node.name] = module.rel_path
    _resolve_class_kinds(classes)
    for info in classes.values():
        if info.kind is None and info.declares_tui_parts():
            issues.append(
                _issue(
                    ISSUE_UNCLASSIFIED_TUI_CLASS,
                    f"class {info.name!r} declares bindings/actions/buttons/handlers but no "
                    "App, Screen, or widget base could be resolved in src/pitwall/tui; its "
                    "declarations are not inventoried",
                    f"{info.module_rel}:{info.lineno}",
                    _class_issue_id(info.name),
                )
            )
    return classes


def _class_info(module: _Module, node: ast.ClassDef, issues: list[dict[str, Any]]) -> _ClassInfo:
    bases = tuple(_base_name(base) for base in node.bases)
    is_modal = any(base in MODAL_BASES for base in bases)
    app_name, dom_id = _constructor_identity(node)
    actions: dict[str, _ActionInfo] = {}
    for child in node.body:
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name.startswith(
            "action_"
        ):
            action_name = child.name[len("action_") :]
            actions[action_name] = _ActionInfo(
                name=action_name,
                method=child.name,
                lineno=child.lineno,
                is_async=isinstance(child, ast.AsyncFunctionDef),
                source_sha256=_digest(_segment(module, child)),
            )
    binding_decls: list[_BindingDecl] = []
    for child in node.body:
        if not isinstance(child, ast.Assign) or not _assigns_bindings(child):
            continue
        _collect_binding_decls(module, node, child.value, binding_decls, issues)
    buttons = _collect_buttons(module, node, issues)
    handlers = _collect_handlers(module, node, issues)
    return _ClassInfo(
        name=node.name,
        module_rel=module.rel_path,
        lineno=node.lineno,
        bases=bases,
        kind=None,
        is_modal=is_modal,
        app_name=app_name,
        dom_id=dom_id,
        actions=actions,
        binding_decls=binding_decls,
        buttons=buttons,
        handlers=handlers,
        source_sha256=_digest(_segment(module, node)),
    )


def _assigns_bindings(node: ast.Assign) -> bool:
    return any(isinstance(target, ast.Name) and target.id == "BINDINGS" for target in node.targets)


def _constructor_identity(node: ast.ClassDef) -> tuple[str | None, str | None]:
    name: str | None = None
    dom_id: str | None = None
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
            continue
        if child.func.attr != "__init__":
            continue
        if not isinstance(child.func.value, ast.Call):
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
                name = keyword.value.value
            if (
                keyword.arg == "id"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                dom_id = keyword.value.value
    return name, dom_id


def _resolve_class_kinds(classes: dict[str, _ClassInfo]) -> None:
    changed = True
    while changed:
        changed = False
        for info in classes.values():
            if info.kind is not None:
                continue
            for base in info.bases:
                if base in APP_BASES:
                    info.kind = CATEGORY_APP
                elif base in SCREEN_BASES:
                    info.kind = CATEGORY_VIEW
                    info.is_modal = info.is_modal or base in MODAL_BASES
                elif base in WIDGET_BASES:
                    info.kind = CATEGORY_WIDGET
                else:
                    parent = classes.get(base)
                    if parent is not None and parent.kind is not None:
                        info.kind = parent.kind
                        info.is_modal = info.is_modal or parent.is_modal
                if info.kind is not None:
                    changed = True
                    break


def _collect_binding_decls(
    module: _Module,
    owner: ast.ClassDef,
    value: ast.expr,
    decls: list[_BindingDecl],
    issues: list[dict[str, Any]],
) -> None:
    if not isinstance(value, ast.List | ast.Tuple):
        issues.append(
            _issue(
                ISSUE_NON_LITERAL_BINDINGS,
                f"class {owner.name!r} declares BINDINGS with a non-literal expression; "
                "its key bindings are not enumerable",
                f"{module.rel_path}:{value.lineno}",
                _class_issue_id(owner.name),
            )
        )
        return
    for element in value.elts:
        decl = _binding_decl(module, element)
        if decl is None:
            issues.append(
                _issue(
                    ISSUE_NON_LITERAL_BINDINGS,
                    f"class {owner.name!r} declares a BINDINGS entry that is not a literal "
                    "Binding(key, action, ...) call or string tuple",
                    f"{module.rel_path}:{element.lineno}",
                    _class_issue_id(owner.name),
                )
            )
            continue
        decls.append(decl)


def _binding_decl(module: _Module, element: ast.expr) -> _BindingDecl | None:
    decl = _literal_binding(element)
    if decl is None:
        return None
    key, action, description, show = decl
    return _BindingDecl(
        key=key,
        action=action,
        description=description,
        show=show,
        lineno=element.lineno,
        source_sha256=_digest(_segment(module, element)),
    )


def _literal_binding(element: ast.expr) -> tuple[str, str, str | None, bool] | None:
    if isinstance(element, ast.Call):
        func = element.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "Binding":
            return None
        key: str | None = None
        action: str | None = None
        description: str | None = None
        show = True
        for index, arg in enumerate(element.args):
            if index == 0 and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                key = arg.value
            elif index == 1 and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                action = arg.value
            elif index == 2 and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                description = arg.value
        for keyword in element.keywords:
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                if keyword.arg == "key":
                    key = keyword.value.value
                elif keyword.arg == "action":
                    action = keyword.value.value
                elif keyword.arg == "description":
                    description = keyword.value.value
            if keyword.arg == "show" and isinstance(keyword.value, ast.Constant):
                show = bool(keyword.value.value)
        if key is None or action is None:
            return None
        return key, action, description, show
    if isinstance(element, ast.Tuple) and 2 <= len(element.elts) <= 3:
        literals = [elt.value for elt in element.elts if isinstance(elt, ast.Constant)]
        if len(literals) != len(element.elts):
            return None
        if not all(isinstance(value, str) for value in literals):
            return None
        return (
            cast(str, literals[0]),
            cast(str, literals[1]),
            cast(str, literals[2]) if len(literals) == 3 else None,
            True,
        )
    return None


def _collect_buttons(
    module: _Module, node: ast.ClassDef, issues: list[dict[str, Any]]
) -> dict[str, _ButtonInfo]:
    buttons: dict[str, _ButtonInfo] = {}
    for child in ast.walk(node):
        if not isinstance(child, ast.Call) or _call_name(child.func) != "Button":
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
        if (
            child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ):
            label = child.args[0].value
        if button_id is None:
            issues.append(
                _issue(
                    ISSUE_DYNAMIC_BUTTON_ID,
                    f"class {node.name!r} constructs Button(...) without a literal id=; the "
                    "button is not enumerable by tui_inventory",
                    f"{module.rel_path}:{child.lineno}",
                    _class_issue_id(node.name),
                )
            )
            continue
        if button_id in buttons:
            issues.append(
                _issue(
                    ISSUE_DYNAMIC_BUTTON_ID,
                    f"class {node.name!r} declares Button id {button_id!r} more than once; "
                    "only the first literal declaration is inventoried",
                    f"{module.rel_path}:{child.lineno}",
                    f"tui:button:{node.name}:{button_id}",
                )
            )
            continue
        buttons[button_id] = _ButtonInfo(
            button_id=button_id,
            label=label,
            lineno=child.lineno,
            source_sha256=_digest(_segment(module, child)),
        )
    return buttons


def _collect_handlers(
    module: _Module, node: ast.ClassDef, issues: list[dict[str, Any]]
) -> list[_HandlerDecl]:
    handlers: list[_HandlerDecl] = []
    for child in node.body:
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        is_on_method = child.name.startswith("on_")
        decorators: list[_OnDecorator] = []
        for decorator in child.decorator_list:
            parsed = _on_decorator(decorator)
            if parsed is None:
                continue
            if not parsed.literal:
                issues.append(
                    _issue(
                        ISSUE_DYNAMIC_ON_SELECTOR,
                        f"class {node.name!r} handler {child.name!r} declares "
                        f"@on({parsed.event}, <non-literal selector>); the selector is not "
                        "enumerable by tui_inventory",
                        f"{module.rel_path}:{parsed.lineno}",
                        f"tui:event:{node.name}:{child.name}",
                    )
                )
            decorators.append(parsed)
        if not is_on_method and not decorators:
            continue
        handlers.append(
            _HandlerDecl(
                method=child.name,
                lineno=child.lineno,
                is_on_method=is_on_method,
                decorators=tuple(decorators),
                button_id_checks=tuple(sorted(_id_checks(child, "button"))),
                input_id_checks=tuple(sorted(_id_checks(child, "input"))),
                is_async=isinstance(child, ast.AsyncFunctionDef),
                source_sha256=_digest(
                    _span(module, _declaration_start(child), child.end_lineno or child.lineno)
                ),
            )
        )
    return handlers


def _on_decorator(decorator: ast.expr) -> _OnDecorator | None:
    if not isinstance(decorator, ast.Call):
        return None
    if isinstance(decorator.func, ast.Attribute):
        if decorator.func.attr != "on":
            return None
    elif isinstance(decorator.func, ast.Name):
        if decorator.func.id != "on":
            return None
    else:
        return None
    if not decorator.args:
        return _OnDecorator(event="", selector=None, literal=False, lineno=decorator.lineno)
    event = ast.unparse(decorator.args[0])
    if len(decorator.args) < 2:
        return _OnDecorator(event=event, selector=None, literal=False, lineno=decorator.lineno)
    selector_node = decorator.args[1]
    if isinstance(selector_node, ast.Constant) and isinstance(selector_node.value, str):
        return _OnDecorator(
            event=event, selector=selector_node.value, literal=True, lineno=decorator.lineno
        )
    return _OnDecorator(event=event, selector=None, literal=False, lineno=decorator.lineno)


def _id_checks(function: ast.AST, owner_attr: str) -> set[str]:
    """Return literal ids compared against ``event.<owner_attr>.id`` in ``function``.

    Only source-proven forms count: a direct ``event.button.id == "x"`` comparison, a
    comparison of a local alias assigned from ``event.button.id``, or a ``.get(...)``
    lookup on a literal dict whose value is keyed by ``event.button.id``.
    """
    dict_keys: dict[str, list[str]] = {}
    aliases: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Dict):
                keys = [
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                ]
                if keys and len(keys) == len(node.value.keys):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            dict_keys[target.id] = keys
            if _is_owner_id(node.value, owner_attr):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        aliases.add(target.id)
    checks: set[str] = set()
    used_dicts: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Compare) and (
            _is_owner_id(node.left, owner_attr)
            or (isinstance(node.left, ast.Name) and node.left.id in aliases)
        ):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    checks.add(comparator.value)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in dict_keys
            and any(_contains_owner_id(arg, owner_attr) for arg in node.args)
        ):
            used_dicts.add(node.func.value.id)
    for name in used_dicts:
        checks.update(dict_keys[name])
    return checks


def _is_owner_id(node: ast.expr, owner_attr: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "id"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == owner_attr
    )


def _contains_owner_id(node: ast.AST, owner_attr: str) -> bool:
    return any(
        isinstance(child, ast.expr) and _is_owner_id(child, owner_attr) for child in ast.walk(node)
    )


def _resolve_bindings(
    info: _ClassInfo, classes: dict[str, _ClassInfo]
) -> list[tuple[_BindingDecl, str, bool, bool, int]]:
    """Resolve BINDINGS for ``info``, most-derived first, one row per key/action.

    The return tuple is ``(declaration, declaring_class, inherited, shadowed,
    declaration_count)``. A base row is ``shadowed`` when a more-derived class
    binds the same key. Base declarations remain visible in the inventory.
    """
    ordered: list[tuple[str, _BindingDecl]] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        current = classes.get(name)
        if current is None:
            return
        for decl in current.binding_decls:
            ordered.append((name, decl))
        for base in current.bases:
            visit(base)

    visit(info.name)
    rows: list[dict[str, Any]] = []
    index_by_identity: dict[tuple[str, str], int] = {}
    for decl_class, decl in ordered:
        identity = (decl.key, decl.action)
        index = index_by_identity.get(identity)
        if index is None:
            index_by_identity[identity] = len(rows)
            rows.append({"decl": decl, "decl_class": decl_class, "count": 1, "shadowed": False})
        else:
            rows[index]["count"] += 1
    for position, row in enumerate(rows):
        for earlier in rows[:position]:
            if (
                earlier["decl"].key == row["decl"].key
                and earlier["decl_class"] != row["decl_class"]
            ):
                row["shadowed"] = True
                break
    return [
        (
            row["decl"],
            row["decl_class"],
            row["decl_class"] != info.name,
            row["shadowed"],
            row["count"],
        )
        for row in rows
    ]


def _class_surfaces(classes: dict[str, _ClassInfo]) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for info in sorted(classes.values(), key=lambda item: item.name):
        if info.kind is None:
            continue
        if info.kind == CATEGORY_APP:
            surface_id = f"tui:app:{info.name}"
            operation = OP_APP_SHELL
        elif info.kind == CATEGORY_VIEW:
            surface_id = f"tui:view:{info.app_name or info.name}"
            operation = OP_MODAL_SCREEN if info.is_modal else OP_SCREEN
        else:
            surface_id = f"tui:widget:{info.name}"
            operation = OP_WIDGET
        surfaces.append(
            _row(
                surface_id,
                operation,
                f"{info.module_rel}:{info.lineno}",
                info.kind,
                class_name=info.name,
                module=info.module_rel,
                bases=list(info.bases),
                app_name=info.app_name,
                dom_id=info.dom_id,
                external_bases=[base for base in info.bases if base not in classes],
                action_count=len(info.actions),
                binding_declaration_count=len(info.binding_decls),
                button_count=len(info.buttons),
                handler_count=len(info.handlers),
                sha256=info.source_sha256,
            )
        )
    return surfaces


def _action_surfaces(
    classes: dict[str, _ClassInfo], issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    all_actions = {action.name for info in classes.values() for action in info.actions.values()}
    surfaces: list[dict[str, Any]] = []
    for info in sorted(classes.values(), key=lambda item: item.name):
        if info.kind is None:
            continue
        resolved = _resolve_bindings(info, classes)
        for action in sorted(info.actions.values(), key=lambda item: item.name):
            association = _action_association(resolved, action.name)
            exposed = association != "none"
            surfaces.append(
                _row(
                    f"tui:action:{info.name}:{action.name}",
                    OP_EXPOSED_ACTION if exposed else OP_INTERNAL_HELPER,
                    f"{info.module_rel}:{action.lineno}",
                    CATEGORY_ACTION,
                    owner_class=info.name,
                    action_name=action.name,
                    method=action.method,
                    exposed=exposed,
                    association=association,
                    is_async=action.is_async,
                    sha256=action.source_sha256,
                )
            )
    for info in sorted(classes.values(), key=lambda item: item.name):
        if info.kind is None:
            continue
        for decl, _decl_class, _inherited, _shadowed, _count in _resolve_bindings(info, classes):
            if decl.action in all_actions or decl.action in TEXTUAL_BUILTIN_ACTIONS:
                continue
            issues.append(
                _issue(
                    ISSUE_ORPHAN_BINDING_ACTION,
                    f"class {info.name!r} binds key {decl.key!r} to action {decl.action!r} "
                    "but no action_* method exists in src/pitwall/tui",
                    f"{info.module_rel}:{decl.lineno}",
                    f"tui:binding:{info.name}:{decl.key}:{decl.action}",
                )
            )
    return surfaces


def _action_association(
    resolved: list[tuple[_BindingDecl, str, bool, bool, int]], action_name: str
) -> str:
    for decl, _decl_class, inherited, shadowed, _count in resolved:
        if decl.action != action_name or shadowed:
            continue
        return "inherited-binding" if inherited else "class-binding"
    return "none"


def _binding_surfaces(classes: dict[str, _ClassInfo]) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for info in sorted(classes.values(), key=lambda item: item.name):
        if info.kind is None:
            continue
        for decl, decl_class, inherited, shadowed, count in _resolve_bindings(info, classes):
            surfaces.append(
                _row(
                    f"tui:binding:{info.name}:{decl.key}:{decl.action}",
                    OP_INHERITED_BINDING if inherited else OP_KEY_BINDING,
                    f"{classes[decl_class].module_rel}:{decl.lineno}",
                    CATEGORY_BINDING,
                    owner_class=info.name,
                    declaration_class=decl_class,
                    key=decl.key,
                    action=decl.action,
                    description=decl.description,
                    show=decl.show,
                    inherited=inherited,
                    shadowed=shadowed,
                    declaration_count=count,
                    sha256=decl.source_sha256,
                )
            )
    return surfaces


def _button_surfaces(classes: dict[str, _ClassInfo]) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for info in sorted(classes.values(), key=lambda item: item.name):
        if info.kind is None:
            continue
        for button in sorted(info.buttons.values(), key=lambda item: item.button_id):
            surfaces.append(
                _row(
                    f"tui:button:{info.name}:{button.button_id}",
                    OP_BUTTON,
                    f"{info.module_rel}:{button.lineno}",
                    CATEGORY_BUTTON,
                    owner_class=info.name,
                    button_id=button.button_id,
                    label=button.label,
                    declaration_source=f"{info.module_rel}:{button.lineno}",
                    sha256=button.source_sha256,
                )
            )
    return surfaces


def _event_surfaces(
    classes: dict[str, _ClassInfo], issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for info in sorted(classes.values(), key=lambda item: item.name):
        if info.kind is None:
            continue
        for handler in info.handlers:
            for decorator in handler.decorators:
                if (
                    decorator.event == BUTTON_PRESSED
                    and decorator.selector is not None
                    and decorator.selector.startswith("#")
                ):
                    button_id = decorator.selector[1:]
                    if button_id not in info.buttons:
                        issues.append(
                            _issue(
                                ISSUE_ON_SELECTOR_MISSING_BUTTON,
                                f"class {info.name!r} handler {handler.method!r} selects button "
                                f"#{button_id} but no literal Button with that id is declared "
                                "in the class",
                                f"{info.module_rel}:{decorator.lineno}",
                                f"tui:event:{info.name}:{handler.method}",
                            )
                        )
            kinds = []
            if handler.is_on_method:
                kinds.append("on-method")
            if handler.decorators:
                kinds.append("on-decorator")
            surfaces.append(
                _row(
                    f"tui:event:{info.name}:{handler.method}",
                    OP_EVENT_HANDLER,
                    f"{info.module_rel}:{handler.lineno}",
                    CATEGORY_EVENT_HANDLER,
                    owner_class=info.name,
                    method=handler.method,
                    declaration_kinds=kinds,
                    event_name=handler.method[len("on_") :] if handler.is_on_method else None,
                    events=sorted({decorator.event for decorator in handler.decorators}),
                    selectors=sorted(
                        {
                            decorator.selector
                            for decorator in handler.decorators
                            if decorator.selector is not None
                        }
                    ),
                    button_id_checks=list(handler.button_id_checks),
                    input_id_checks=list(handler.input_id_checks),
                    is_async=handler.is_async,
                    sha256=handler.source_sha256,
                )
            )
    return surfaces


def _collect_registrations(
    modules: list[_Module], issues: list[dict[str, Any]]
) -> tuple[dict[str, _Registration], list[_Navigation]]:
    registrations: dict[str, _Registration] = {}
    navigation: list[_Navigation] = []
    for module in modules:
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "install_screen":
                registration = _register_screen(module, node, issues)
                if registration is not None:
                    if registration.name in registrations:
                        raise TuiInventoryError(
                            f"duplicate install_screen name {registration.name!r} in "
                            f"{module.rel_path}:{node.lineno}"
                        )
                    registrations[registration.name] = registration
            elif node.func.attr in {"push_screen", "switch_screen"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    navigation.append(
                        _Navigation(first.value, module.rel_path, node.lineno, node.func.attr)
                    )
            elif node.func.attr in {"bind", "bind_key", "add_key"}:
                issues.append(
                    _issue(
                        ISSUE_DIRECT_SELF_BINDING,
                        f"{node.func.attr}(...) adds a binding imperatively; it is not "
                        "enumerated by tui_inventory",
                        f"{module.rel_path}:{node.lineno}",
                        _class_issue_id("dynamic"),
                    )
                )
    return registrations, navigation


def _register_screen(
    module: _Module, node: ast.Call, issues: list[dict[str, Any]]
) -> _Registration | None:
    if len(node.args) < 2:
        issues.append(
            _issue(
                ISSUE_DYNAMIC_SCREEN_REGISTRATION,
                "install_screen(...) call does not provide a positional name argument",
                f"{module.rel_path}:{node.lineno}",
                _class_issue_id("dynamic"),
            )
        )
        return None
    screen_arg, name_arg = node.args[0], node.args[1]
    screen_class = (
        screen_arg.func.id
        if isinstance(screen_arg, ast.Call) and isinstance(screen_arg.func, ast.Name)
        else ""
    )
    if not (
        isinstance(name_arg, ast.Constant)
        and isinstance(name_arg.value, str)
        and screen_class[:1].isupper()
    ):
        issues.append(
            _issue(
                ISSUE_DYNAMIC_SCREEN_REGISTRATION,
                "install_screen(...) uses a non-literal name or a callable that is not a "
                "capitalized screen class; the registration is not enumerable by "
                "tui_inventory",
                f"{module.rel_path}:{node.lineno}",
                _class_issue_id("dynamic"),
            )
        )
        return None
    return _Registration(
        name=name_arg.value,
        screen_class=screen_class,
        module_rel=module.rel_path,
        lineno=node.lineno,
        source_sha256=_digest(_segment(module, node)),
    )


def _report_unresolved_navigation(
    registrations: dict[str, _Registration],
    navigation: list[_Navigation],
    issues: list[dict[str, Any]],
) -> None:
    for item in sorted(navigation, key=lambda nav: (nav.name, nav.module_rel, nav.lineno)):
        if item.name in registrations:
            continue
        issues.append(
            _issue(
                ISSUE_UNRESOLVED_NAVIGATION,
                f"{item.call}({item.name!r}) has no matching literal install_screen(..., "
                f"{item.name!r}) registration in src/pitwall/tui",
                f"{item.module_rel}:{item.lineno}",
                f"tui:registration:{item.name}",
            )
        )


def _report_missing_screen_classes(
    registrations: dict[str, _Registration],
    classes: dict[str, _ClassInfo],
    issues: list[dict[str, Any]],
) -> None:
    for name in sorted(registrations):
        registration = registrations[name]
        if registration.screen_class in classes:
            continue
        issues.append(
            _issue(
                ISSUE_MISSING_SCREEN_CLASS,
                f"install_screen(..., {name!r}) registers class "
                f"{registration.screen_class!r}, which is not defined in src/pitwall/tui",
                f"{registration.module_rel}:{registration.lineno}",
                f"tui:registration:{name}",
            )
        )


def _report_unreferenced_registrations(
    registrations: dict[str, _Registration],
    navigation: list[_Navigation],
    issues: list[dict[str, Any]],
) -> None:
    referenced = {item.name for item in navigation}
    for name in sorted(registrations):
        if name in referenced:
            continue
        registration = registrations[name]
        issues.append(
            _issue(
                ISSUE_UNREFERENCED_REGISTRATION,
                f"install_screen(..., {name!r}) is never reached by a literal "
                "push_screen/switch_screen call in src/pitwall/tui",
                f"{registration.module_rel}:{registration.lineno}",
                f"tui:registration:{name}",
            )
        )


def _registration_surfaces(
    registrations: dict[str, _Registration],
    navigation: list[_Navigation],
) -> list[dict[str, Any]]:
    navigation_refs: dict[str, list[str]] = {}
    for item in navigation:
        navigation_refs.setdefault(item.name, []).append(f"{item.module_rel}:{item.lineno}")
    surfaces: list[dict[str, Any]] = []
    for name in sorted(registrations):
        registration = registrations[name]
        refs = sorted(navigation_refs.get(name, []))
        surfaces.append(
            _row(
                f"tui:registration:{name}",
                OP_SCREEN_REGISTRATION,
                f"{registration.module_rel}:{registration.lineno}",
                CATEGORY_REGISTRATION,
                name=name,
                screen_class=registration.screen_class,
                module=registration.module_rel,
                navigation_refs=refs,
                referenced=bool(refs),
                sha256=registration.source_sha256,
            )
        )
    return surfaces


def _parse_confirmation(modules: list[_Module], issues: list[dict[str, Any]]) -> _ConfirmationTable:
    module = next(
        (item for item in modules if item.rel_path == CONFIRMATION_REL_PATH.as_posix()), None
    )
    if module is None:
        return _ConfirmationTable(
            entries={}, default_tier=None, source_sha256="", reference_sites=()
        )
    reference_sites = _confirmation_reference_sites(modules)
    enum_values = _confirmation_enum_values(module)
    entries: dict[str, _ConfirmationEntry] = {}
    table_assignment = _find_assignment(module.tree, "_ACTION_TIERS")
    if table_assignment is None or not isinstance(table_assignment.value, ast.Dict):
        issues.append(
            _issue(
                ISSUE_DYNAMIC_CONFIRMATION_TIERS,
                "_ACTION_TIERS is not a literal dict in confirmation.py; confirmation tiers "
                "are not enumerable by tui_inventory",
                f"{module.rel_path}:1",
                _class_issue_id("dynamic"),
            )
        )
        return _ConfirmationTable(
            entries={},
            default_tier=None,
            source_sha256=_digest(""),
            reference_sites=reference_sites,
        )
    table = table_assignment.value
    for key_node, value_node in zip(table.keys, table.values, strict=True):
        if not (
            isinstance(key_node, ast.Constant)
            and isinstance(key_node.value, str)
            and isinstance(value_node, ast.Attribute)
        ):
            issues.append(
                _issue(
                    ISSUE_DYNAMIC_CONFIRMATION_TIERS,
                    "an _ACTION_TIERS entry is not a literal action id mapped to a ConfirmTier "
                    "member",
                    f"{module.rel_path}:{key_node.lineno if key_node else 1}",
                    _class_issue_id("dynamic"),
                )
            )
            continue
        tier = enum_values.get(value_node.attr)
        if tier is None:
            issues.append(
                _issue(
                    ISSUE_DYNAMIC_CONFIRMATION_TIERS,
                    f"_ACTION_TIERS entry {key_node.value!r} references unknown ConfirmTier "
                    f"member {value_node.attr!r}",
                    f"{module.rel_path}:{value_node.lineno}",
                    f"tui:confirmation:{key_node.value}",
                )
            )
            continue
        entries[key_node.value] = _ConfirmationEntry(
            action_id=key_node.value,
            tier=tier,
            lineno=key_node.lineno,
            source_sha256=_digest(
                _span(module, key_node.lineno, value_node.end_lineno or value_node.lineno)
            ),
        )
    default_tier = _confirmation_default(module, enum_values, issues)
    return _ConfirmationTable(
        entries=entries,
        default_tier=default_tier,
        source_sha256=_digest(_segment(module, table)),
        reference_sites=reference_sites,
    )


def _confirmation_reference_sites(modules: list[_Module]) -> tuple[str, ...]:
    refs: set[str] = set()
    for module in modules:
        if module.rel_path == CONFIRMATION_REL_PATH.as_posix():
            continue
        for node in ast.walk(module.tree):
            if (
                isinstance(node, ast.Name)
                and node.id in CONFIRMATION_REF_NAMES
                or isinstance(node, ast.Attribute)
                and node.attr in CONFIRMATION_REF_NAMES
            ):
                refs.add(f"{module.rel_path}:{node.lineno}")
    return tuple(sorted(refs))


def _confirmation_enum_values(module: _Module) -> dict[str, str]:
    for node in module.tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ConfirmTier":
            continue
        values: dict[str, str] = {}
        for child in node.body:
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if (
                        isinstance(target, ast.Name)
                        and isinstance(child.value, ast.Constant)
                        and isinstance(child.value.value, str)
                    ):
                        values[target.id] = child.value.value
        return values
    return {}


def _confirmation_default(
    module: _Module, enum_values: dict[str, str], issues: list[dict[str, Any]]
) -> str | None:
    function = next(
        (
            node
            for node in module.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "confirm_tier_for_action"
        ),
        None,
    )
    if function is not None:
        for child in ast.walk(function):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr != "get" or len(child.args) < 2:
                continue
            fallback = child.args[1]
            if isinstance(fallback, ast.Attribute):
                tier = enum_values.get(fallback.attr)
                if tier is not None:
                    return tier
    issues.append(
        _issue(
            ISSUE_UNRESOLVED_CONFIRMATION_DEFAULT,
            "confirm_tier_for_action does not expose a literal ConfirmTier fallback; the "
            "default tier is not enumerable by tui_inventory",
            f"{module.rel_path}:1",
            _class_issue_id("dynamic"),
        )
    )
    return None


def _confirmation_surfaces(
    confirmation: _ConfirmationTable, issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    surfaces: list[dict[str, Any]] = []
    for action_id in sorted(confirmation.entries):
        entry = confirmation.entries[action_id]
        surfaces.append(
            _row(
                f"tui:confirmation:{action_id}",
                OP_CONFIRMATION_TIER,
                _confirmation_source(entry.lineno),
                CATEGORY_CONFIRMATION,
                action_id=action_id,
                tier=entry.tier,
                default_tier=confirmation.default_tier,
                entry_sha256=entry.source_sha256,
                sha256=entry.source_sha256,
                table_sha256=confirmation.source_sha256,
                policy_references=list(confirmation.reference_sites),
                enforcement_proven=False,
                policy="declaration-only; source references alone do not establish enforcement",
            )
        )
    if confirmation.entries and not confirmation.reference_sites:
        issues.append(
            {
                "code": ISSUE_CONFIRMATION_POLICY_DECLARATION_ONLY,
                "message": (
                    f"{CONFIRMATION_REL_PATH.as_posix()}: _ACTION_TIERS is a declaration-only "
                    "policy table; no caller of confirm_tier_for_action or ConfirmTier exists "
                    "in src/pitwall/tui, so tui_inventory records tiers without claiming any "
                    "operator button mapping or enforcement"
                ),
                "severity": UNRESOLVED_SEVERITY,
                "surface_ids": [
                    f"tui:confirmation:{action_id}" for action_id in sorted(confirmation.entries)
                ],
            }
        )
    return surfaces


def _confirmation_source(lineno: int) -> str:
    return f"{CONFIRMATION_REL_PATH.as_posix()}:{lineno}"


def _find_assignment(tree: ast.Module, name: str) -> ast.Assign | ast.AnnAssign | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node
    return None


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _class_issue_id(class_name: str) -> str:
    return f"tui:class:{class_name}"


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return ""


def _declaration_start(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    starts = [node.lineno, *(decorator.lineno for decorator in node.decorator_list)]
    return min(starts)


def _segment(module: _Module, node: ast.AST) -> str:
    if not isinstance(node, (ast.stmt, ast.expr)):
        raise TuiInventoryError("source segment requires a statement or expression")
    start = node.lineno - 1
    end = getattr(node, "end_lineno", None) or (start + 1)
    return "\n".join(module.lines[start:end])


def _span(module: _Module, first_lineno: int, last_lineno: int) -> str:
    return "\n".join(module.lines[first_lineno - 1 : last_lineno])


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ensure_unique_surface_ids(surfaces: list[dict[str, Any]]) -> None:
    seen: dict[str, str] = {}
    for surface in surfaces:
        surface_id = surface["surface_id"]
        source = surface["source"]
        if surface_id in seen:
            raise TuiInventoryError(
                f"duplicate surface_id {surface_id!r} produced by {source} and {seen[surface_id]}"
            )
        seen[surface_id] = source


def _issue(code: str, message: str, source: str, surface_id: str) -> dict[str, Any]:
    return {
        "code": code,
        "message": f"{source}: {message}",
        "severity": ISSUE_SEVERITY,
        "surface_ids": [surface_id] if surface_id else [],
    }
