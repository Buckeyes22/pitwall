"""Static test-node index for release acceptance.

``discover(root)`` enumerates - never imports, executes, or runtime-introspects -
the statically declared test nodes of the release candidate:

* Python ``tests/`` (which includes ``tests/agents/``) via :mod:`ast`:
  pytest functions and classes (nested class identities compose as
  ``pkg::Outer::Inner::test_x``), inherited methods from in-module base classes,
  and unittest methods. Collection mirrors pytest's default rules: ``test_*`` /
  ``*_test`` files except ``conftest.py``, module-level and class-level ``test*``
  names, classes that pytest refuses to collect (own ``__init__``, no ``Test``
  prefix outside a ``unittest.TestCase`` hierarchy, ``__test__ = False``) are
  reported as explicit issues instead of fabricated nodes.

Rows carry ``path``, ``line``, ``framework`` (``pytest``/``unittest``),
``node_id``, ``kind``, literal marker names, ``skip_indicated`` and
``expected_failure`` flags. Parameterized and dynamic cases are retained as
explicit unresolved ``templates`` with a deterministic ``template_id`` and an
``unresolved_reason`` (``parameterized`` / ``dynamic-test-name`` / ``unittest-load-tests``), never as
concrete pass nodes: ``pytest.mark.parametrize`` produces one template for the
concrete cases.

Literal HTTP paths, ``pitwall_*`` MCP tool names and CLI argv lists found inside
a test body are reported as ``suggested_associations`` on that node. They are
labelled ``review_status: unreviewed`` and are *not* coverage proof: a test that
mentions ``/v1/models`` may be exercising a fake, a stub, or the surface itself.

Known limitations (also emitted in the report under ``limitations``):

* Static extraction only. conftest hooks, plugins, ``load_tests`` protocols and
  runtime name generation cannot be resolved without executing code; they are
  reported as issues and templates.
* ``skipIf``/``skipUnless`` presence is recorded, not evaluated: the marker may
  or may not skip at runtime.
* Python search is limited to ``tests`` and
  pytest's default file/naming conventions; ``fixtures`` and ``conftest.py``
  are skipped deliberately.
* Cross-module and imported base classes are not resolved; the affected classes
  get an ``unknown-test-base`` issue and inherited methods may be missing.
* Duplicate ``node_id`` values are deduplicated and reported as an issue.
* Associations are literal-string hints, not verified request/CLI/tool calls.

Only the Python standard library is used for the implementation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-test-index.v1"
ASSOCIATIONS_LABEL = "unreviewed-suggestions-not-coverage"

PY_PACKAGES = (Path("tests"),)
_SKIP_DIRS = frozenset({"__pycache__", "fixtures", ".git"})

SKIP_MARKERS = frozenset({"skip", "skipif", "skipIf", "skipUnless"})
XFAIL_MARKERS = frozenset({"xfail", "expectedFailure"})

HTTP_PATH_PREFIXES = (
    "/v1",
    "/health",
    "/mcp",
    "/webhooks",
    "/internal",
    "/admin",
    "/embed",
    "/serve",
    "/runsync",
    "/run",
    "/openai",
)
_CLI_HINTS = ("pitwall",)
_HTTP_PATH = re.compile(r"^/[A-Za-z0-9._~/{}-]+$")
_MCP_TOOL = re.compile(r"^pitwall_[a-z0-9_]+$")

FRAMEWORK_PYTEST = "pytest"
FRAMEWORK_UNITTEST = "unittest"

REASON_PARAMETRIZED = "parameterized"
REASON_DYNAMIC_NAME = "dynamic-test-name"
REASON_LOAD_TESTS = "unittest-load-tests"

LIMITATIONS = (
    "Static source extraction only: no test, product module, or provider is imported or executed.",
    "Parameterized and dynamic cases are unresolved templates, never concrete executable nodes.",
    "Marker flags record literal declarations; conditional skips are not evaluated.",
    "Python scope is tests/ (including tests/agents/) with pytest default collection rules.",
    "Imported/cross-module base classes are not resolved; affected classes carry an unknown-test-base issue.",
    "Duplicate node_id values are deduplicated and reported as an issue.",
    "Suggested associations are literal-string hints labelled unreviewed; they are not coverage proof.",
)


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _issue(code: str, path: str, line: int, message: str) -> dict[str, Any]:
    return {"code": code, "path": path, "line": line, "message": message}


def _read(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover - environment dependent
        return None, str(exc)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bounded(value: str, limit: int = 160) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


# --------------------------------------------------------------------------- #
# shared row builders
# --------------------------------------------------------------------------- #


def _node(
    *,
    path: str,
    line: int,
    framework: str,
    node_id: str,
    kind: str,
    markers: list[str],
    skip_indicated: bool,
    expected_failure: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "path": path,
        "line": line,
        "framework": framework,
        "node_id": node_id,
        "kind": kind,
        "markers": sorted(set(markers)),
        "skip_indicated": skip_indicated,
        "expected_failure": expected_failure,
        "metadata": metadata,
    }


def _template(
    *,
    path: str,
    line: int,
    framework: str,
    template_id: str,
    kind: str,
    unresolved_reason: str,
    markers: list[str],
    skip_indicated: bool,
    expected_failure: bool,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "path": path,
        "line": line,
        "framework": framework,
        "template_id": template_id,
        "kind": kind,
        "unresolved_reason": unresolved_reason,
        "markers": sorted(set(markers)),
        "skip_indicated": skip_indicated,
        "expected_failure": expected_failure,
        "metadata": metadata,
    }


def _association(association_type: str, value: str) -> dict[str, Any]:
    return {
        "association_type": association_type,
        "value": value,
        "label": "unreviewed-suggestion",
        "review_status": "unreviewed",
        "basis": "literal-string-in-test-body",
        "note": "association only; not evidence this node exercises the surface",
    }


# --------------------------------------------------------------------------- #
# Python extraction
# --------------------------------------------------------------------------- #


def _python_files(root: Path) -> list[str]:
    found: list[str] = []
    for package in PY_PACKAGES:
        base = root / package
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            parts = path.relative_to(root).parts
            if any(part in _SKIP_DIRS for part in parts):
                continue
            stem = path.stem
            if stem == "conftest":
                continue
            if stem.startswith("test_") or stem.endswith("_test"):
                found.append(_rel(root, path))
    return sorted(set(found))


def _marker_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _decorators(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    return list(node.decorator_list)


def _decorator_markers(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [name for dec in _decorators(node) if (name := _marker_name(dec))]


def _parametrize_info(decorators: list[ast.expr]) -> dict[str, Any] | None:
    for dec in decorators:
        if isinstance(dec, ast.Call) and _marker_name(dec) == "parametrize":
            info: dict[str, Any] = {
                "parameter_argument_names": None,
                "parameter_values_literal": False,
                "parameter_count": None,
            }
            names: list[str] | None = None
            if dec.args:
                argnames = dec.args[0]
                if isinstance(argnames, ast.Constant) and isinstance(argnames.value, str):
                    names = [part.strip() for part in argnames.value.split(",") if part.strip()]
                elif isinstance(argnames, (ast.Tuple, ast.List)) and all(
                    isinstance(el, ast.Constant) and isinstance(el.value, str)
                    for el in argnames.elts
                ):
                    names = [cast(str, cast(ast.Constant, el).value) for el in argnames.elts]
            info["parameter_argument_names"] = names
            if len(dec.args) > 1:
                values = dec.args[1]
                if isinstance(values, (ast.List, ast.Tuple)) and not any(
                    isinstance(el, ast.Starred) for el in values.elts
                ):
                    info["parameter_values_literal"] = True
                    info["parameter_count"] = len(values.elts)
            info["argument_names_literal"] = names is not None
            return info
    return None


def _module_markers(tree: ast.Module) -> tuple[list[str], list[dict[str, Any]]]:
    markers: list[str] = []
    issues: list[dict[str, Any]] = []
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            continue
        values = stmt.value.elts if isinstance(stmt.value, (ast.List, ast.Tuple)) else [stmt.value]
        for value in values:
            if isinstance(value, ast.Call) and _marker_name(value) == "parametrize":
                issues.append(
                    _issue(
                        "module-parametrize",
                        "",
                        value.lineno,
                        "module-level pytestmark parametrize is unresolved; method nodes keep literal names",
                    )
                )
            name = _marker_name(value)
            if name is None:
                issues.append(
                    _issue(
                        "module-pytestmark-dynamic",
                        "",
                        stmt.lineno,
                        "pytestmark entry is not a literal marker expression",
                    )
                )
            else:
                markers.append(name)
    if not markers and not issues:
        for stmt in tree.body:
            if (
                isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets)
                and _marker_name(stmt.value) is None
            ):
                issues.append(
                    _issue(
                        "module-pytestmark-dynamic",
                        "",
                        stmt.lineno,
                        "pytestmark is not a literal marker expression",
                    )
                )
    pending: list[ast.AST] = list(tree.body)
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if (
            isinstance(item, ast.Call)
            and _getattr_chain(item.func) == "pytest.skip"
            and any(
                k.arg == "allow_module_level"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in item.keywords
            )
        ):
            markers.append("skipif")
            issues.append(
                _issue(
                    "module-skip-condition",
                    "",
                    item.lineno,
                    "module-level skip may prevent collection; condition is not evaluated",
                )
            )
        pending.extend(ast.iter_child_nodes(item))
    return markers, issues


def _disabled_by_test_attr(body: list[ast.stmt]) -> bool:
    for stmt in body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__test__" for t in stmt.targets):
            continue
        if isinstance(stmt.value, ast.Constant) and stmt.value.value is False:
            return True
    return False


def _dynamic_name_sites(body: list[ast.stmt], path: str) -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    for stmt in body:
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
            targets = [stmt.target]
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Call)
                and isinstance(target.value.func, ast.Name)
                and target.value.func.id == "globals"
            ):
                sites.append(
                    _issue(
                        "dynamic-test-name",
                        path,
                        stmt.lineno,
                        "runtime assignment into globals() can define tests this static index cannot name",
                    )
                )
        value = getattr(stmt, "value", None)
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id in {"setattr", "exec"}
        ):
            sites.append(
                _issue(
                    "dynamic-test-name",
                    path,
                    stmt.lineno,
                    f"module-level {value.func.id}() can define tests this static index cannot name",
                )
            )
    return sites


def _getattr_chain(expr: ast.expr) -> str:
    parts: list[str] = []
    current: ast.expr | None = expr
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


class _ClassFacts:
    """Static facts about one in-module class and its in-module base chain."""

    def __init__(
        self,
        cls: ast.ClassDef,
        classes: dict[str, ast.ClassDef],
        seen: frozenset[str],
        unittest_bases: frozenset[str],
    ):
        self.unittest_subclass = False
        self.has_init = False
        self.abstract = False
        self.unknown_bases: list[str] = []
        if cls.name in seen:
            self.unknown_bases.append(f"{cls.name} (cyclic base)")
            return
        for base in cls.bases:
            text = _getattr_chain(base)
            if text in unittest_bases:
                self.unittest_subclass = True
            elif isinstance(base, ast.Name) and base.id in classes:
                nested = _ClassFacts(classes[base.id], classes, seen | {cls.name}, unittest_bases)
                self.unittest_subclass |= nested.unittest_subclass
                self.has_init |= nested.has_init
                self.abstract |= nested.abstract
                self.unknown_bases.extend(nested.unknown_bases)
            elif text and text != "object":
                self.unknown_bases.append(text)
        self.has_init |= any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"
            for item in cls.body
        )
        self.abstract |= any(
            "abstractmethod" in _decorator_markers(item)
            for item in cls.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        )


def _class_collectable(cls: ast.ClassDef, facts: _ClassFacts) -> bool:
    if _disabled_by_test_attr(cls.body):
        return False
    if facts.has_init and not facts.unittest_subclass:
        return False
    return facts.unittest_subclass or cls.name.startswith("Test")


def _class_declares_tests(cls: ast.ClassDef) -> bool:
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith(
            "test"
        ):
            return True
        if isinstance(item, ast.ClassDef) and _class_declares_tests(item):
            return True
    return False


def _suggestions_python(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for child in ast.walk(func):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            value = child.value
            if (
                1 < len(value) <= 160
                and _HTTP_PATH.match(value)
                and value.startswith(HTTP_PATH_PREFIXES)
            ):
                found.setdefault(("http-path", value), _association("http-path", value))
            elif _MCP_TOOL.match(value):
                found.setdefault(("mcp-tool", value), _association("mcp-tool", value))
        if isinstance(child, (ast.List, ast.Tuple)) and child.elts:
            constants: list[str] = []
            for element in child.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    constants.append(element.value)
                else:
                    break
            if constants and any(hint in " ".join(constants) for hint in _CLI_HINTS):
                value = _bounded(" ".join(constants))
                found.setdefault(("cli", value), _association("cli", value))
    return [found[key] for key in sorted(found)]


def _python_function_row(
    *,
    path: str,
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    node_id: str,
    framework: str,
    module_markers: list[str],
    class_markers: list[str],
    class_parametrize: dict[str, Any] | None,
    inherited_from: str | None,
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    markers = [*module_markers, *class_markers, *_decorator_markers(func)]
    skip_indicated = bool(set(markers) & SKIP_MARKERS)
    expected_failure = bool(set(markers) & XFAIL_MARKERS)
    if "fixture" in markers:
        issues.append(
            _issue(
                "fixture-named-test",
                path,
                func.lineno,
                f"{node_id} is decorated as a fixture; pytest does not collect it as a test",
            )
        )
        return None, None
    metadata: dict[str, Any] = {
        "async": isinstance(func, ast.AsyncFunctionDef),
        "suggested_associations": _suggestions_python(func),
    }
    if inherited_from is not None:
        metadata["inherited_from"] = inherited_from
    if "parametrize" in module_markers:
        return None, _template(
            path=path,
            line=func.lineno,
            framework=framework,
            template_id=f"template:{node_id}:module-parametrize",
            kind="pytest-method" if node_id.count("::") > 1 else "pytest-function",
            unresolved_reason=REASON_PARAMETRIZED,
            markers=markers,
            skip_indicated=skip_indicated,
            expected_failure=expected_failure,
            metadata={**metadata, "parameterized_by": "module-pytestmark"},
        )
    if class_parametrize is not None:
        info = class_parametrize
        template = _template(
            path=path,
            line=func.lineno,
            framework=framework,
            template_id=f"template:{node_id}:class-parametrize",
            kind="pytest-method",
            unresolved_reason=REASON_PARAMETRIZED,
            markers=markers,
            skip_indicated=skip_indicated,
            expected_failure=expected_failure,
            metadata={**metadata, **info, "parameterized_by": "class-decorator"},
        )
        return None, template
    own = _parametrize_info(_decorators(func))
    if own is not None:
        template = _template(
            path=path,
            line=func.lineno,
            framework=framework,
            template_id=f"template:{node_id}:parametrize",
            kind="pytest-method" if "::" in node_id else "pytest-function",
            unresolved_reason=REASON_PARAMETRIZED,
            markers=markers,
            skip_indicated=skip_indicated,
            expected_failure=expected_failure,
            metadata={**metadata, "parameterized_by": "function-decorator", **own},
        )
        return None, template
    kind = (
        "unittest-method"
        if framework == FRAMEWORK_UNITTEST
        else "pytest-method"
        if node_id.count("::") > 1
        else "pytest-function"
    )
    node = _node(
        path=path,
        line=func.lineno,
        framework=framework,
        node_id=node_id,
        kind=kind,
        markers=markers,
        skip_indicated=skip_indicated,
        expected_failure=expected_failure,
        metadata=metadata,
    )
    return node, None


def _class_mro(
    cls: ast.ClassDef, classes: dict[str, ast.ClassDef], seen: frozenset[str] = frozenset()
) -> list[str]:
    """C3 ordering for declared local bases; external bases remain opaque names."""
    if cls.name in seen:
        raise ValueError("cyclic class hierarchy")
    bases = [_getattr_chain(base) for base in cls.bases]
    sequences = [
        _class_mro(classes[name], classes, seen | {cls.name}) if name in classes else [name]
        for name in bases
    ] + [bases.copy()]
    result = [cls.name]
    while any(sequences):
        sequences = [seq for seq in sequences if seq]
        candidate = next(
            (seq[0] for seq in sequences if not any(seq[0] in other[1:] for other in sequences)),
            None,
        )
        if candidate is None:
            raise ValueError("inconsistent class hierarchy")
        result.append(candidate)
        for seq in sequences:
            if seq[0] == candidate:
                seq.pop(0)
    return result


def _collect_class(
    *,
    root: Path,
    path: str,
    cls: ast.ClassDef,
    classes: dict[str, ast.ClassDef],
    module_markers: list[str],
    prefix: tuple[str, ...],
    inherited_from: str | None,
    issues: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    templates: list[dict[str, Any]],
    seen_classes: frozenset[str],
    unittest_bases: frozenset[str],
) -> None:
    facts = _ClassFacts(cls, classes, seen_classes, unittest_bases)
    class_markers = _decorator_markers(cls)
    class_parametrize = _parametrize_info(_decorators(cls))
    identity = prefix if inherited_from is not None else (*prefix, cls.name)
    collectable = _class_collectable(cls, facts)
    if _disabled_by_test_attr(cls.body):
        issues.append(
            _issue(
                "disabled-by-__test__",
                path,
                cls.lineno,
                f"{'::'.join(identity)} sets __test__ = False; pytest does not collect it",
            )
        )
        return
    if facts.has_init and not facts.unittest_subclass:
        if _class_declares_tests(cls):
            issues.append(
                _issue(
                    "class-has-init",
                    path,
                    cls.lineno,
                    f"{'::'.join(identity)} declares __init__; pytest does not collect its test methods",
                )
            )
        return
    if facts.abstract and _class_declares_tests(cls):
        issues.append(
            _issue(
                "abstract-test-class",
                path,
                cls.lineno,
                f"{'::'.join(identity)} declares abstract methods and is not statically collectable",
            )
        )
        return
    if not collectable:
        if _class_declares_tests(cls):
            issues.append(
                _issue(
                    "uncollectable-test-class",
                    path,
                    cls.lineno,
                    f"{'::'.join(identity)} does not match pytest's default class rules; "
                    "its test-prefixed methods are not collected",
                )
            )
        return
    if facts.unknown_bases:
        declared = _class_declares_tests(cls) or any(
            isinstance(item, ast.ClassDef) for item in cls.body
        )
        if declared:
            issues.append(
                _issue(
                    "unknown-test-base",
                    path,
                    cls.lineno,
                    f"{'::'.join(identity)} inherits {', '.join(sorted(set(facts.unknown_bases)))}; "
                    "methods inherited through it cannot be statically enumerated",
                )
            )
    framework = FRAMEWORK_UNITTEST if facts.unittest_subclass else FRAMEWORK_PYTEST
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith(
            "test"
        ):
            node_id = "::".join((*identity, item.name))
            node, template = _python_function_row(
                path=path,
                func=item,
                node_id=node_id,
                framework=framework,
                module_markers=module_markers,
                class_markers=class_markers,
                class_parametrize=class_parametrize,
                inherited_from=inherited_from,
                issues=issues,
            )
            if node is not None:
                nodes.append(node)
            if template is not None:
                templates.append(template)
        elif isinstance(item, ast.ClassDef):
            _collect_class(
                root=root,
                path=path,
                cls=item,
                classes=classes,
                module_markers=module_markers,
                prefix=identity,
                inherited_from=inherited_from,
                issues=issues,
                nodes=nodes,
                templates=templates,
                seen_classes=seen_classes | {cls.name},
                unittest_bases=unittest_bases,
            )
    try:
        mro = _class_mro(cls, classes)
    except ValueError as exc:
        issues.append(_issue("unresolved-class-bases", path, cls.lineno, str(exc)))
        return
    shadowed = {
        item.name
        for item in cls.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for base_name in mro[1:]:
        base_cls = classes.get(base_name)
        if base_cls is None:
            continue
        for item in base_cls.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name in shadowed:
                continue
            shadowed.add(item.name)
            if not item.name.startswith("test"):
                continue
            node, template = _python_function_row(
                path=path,
                func=item,
                node_id="::".join((*identity, item.name)),
                framework=framework,
                module_markers=module_markers,
                class_markers=class_markers,
                class_parametrize=class_parametrize,
                inherited_from=base_name,
                issues=issues,
            )
            if node is not None:
                nodes.append(node)
            if template is not None:
                templates.append(template)


def _analyze_python(
    rel: str, text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        return [], [], [_issue("unparseable-source", rel, exc.lineno or 1, str(exc))]
    if _disabled_by_test_attr(tree.body):
        return (
            [],
            [],
            [_issue("disabled-by-__test__", rel, 1, "module sets __test__ = False")],
        )
    marker_issues = _module_markers(tree)
    module_markers, marker_problems = marker_issues
    for problem in marker_problems:
        issues.append({**problem, "path": rel})
    dynamic = _dynamic_name_sites(tree.body, rel)
    for site in dynamic:
        issues.append(site)
        templates.append(
            _template(
                path=rel,
                line=site["line"],
                framework=FRAMEWORK_PYTEST,
                template_id=f"template:{rel}:{site['line']}:dynamic-test-name",
                kind="pytest-function",
                unresolved_reason=REASON_DYNAMIC_NAME,
                markers=[],
                skip_indicated=False,
                expected_failure=False,
                metadata={"detail": site["message"]},
            )
        )
    classes = {item.name: item for item in tree.body if isinstance(item, ast.ClassDef)}
    unittest_bases: set[str] = set()
    for item in tree.body:
        if isinstance(item, ast.Import):
            for alias in item.names:
                if alias.name == "unittest":
                    unittest_bases.add(f"{alias.asname or alias.name}.TestCase")
        elif isinstance(item, ast.ImportFrom) and item.module == "unittest":
            for alias in item.names:
                if alias.name == "TestCase":
                    unittest_bases.add(alias.asname or alias.name)
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name == "load_tests":
                issues.append(
                    _issue(
                        "unittest-load-tests",
                        rel,
                        stmt.lineno,
                        "load_tests protocol is resolved at runtime; listed nodes may not be authoritative",
                    )
                )
                templates.append(
                    _template(
                        path=rel,
                        line=stmt.lineno,
                        framework=FRAMEWORK_UNITTEST,
                        template_id=f"template:{rel}:{stmt.lineno}:load-tests",
                        kind="unittest-load-tests",
                        unresolved_reason=REASON_LOAD_TESTS,
                        markers=[],
                        skip_indicated=False,
                        expected_failure=False,
                        metadata={"detail": "module defines load_tests"},
                    )
                )
                continue
            if not stmt.name.startswith("test"):
                continue
            node, template = _python_function_row(
                path=rel,
                func=stmt,
                node_id=f"{rel}::{stmt.name}",
                framework=FRAMEWORK_PYTEST,
                module_markers=module_markers,
                class_markers=[],
                class_parametrize=None,
                inherited_from=None,
                issues=issues,
            )
            if node is not None:
                nodes.append(node)
            if template is not None:
                templates.append(template)
        elif isinstance(stmt, ast.ClassDef):
            _collect_class(
                root=Path("."),
                path=rel,
                cls=stmt,
                classes=classes,
                module_markers=module_markers,
                prefix=(rel,),
                inherited_from=None,
                issues=issues,
                nodes=nodes,
                templates=templates,
                seen_classes=frozenset(),
                unittest_bases=frozenset(unittest_bases),
            )
    return nodes, templates, issues


def _dedupe(
    rows: list[dict[str, Any]],
    key: str,
    issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = row[key]
        if identity in unique:
            issues.append(
                _issue(
                    "duplicate-node-id",
                    row["path"],
                    row["line"],
                    f"duplicate {key} {identity}; first seen at line {unique[identity]['line']}",
                )
            )
            continue
        unique[identity] = row
    return [unique[identity] for identity in sorted(unique)]


def discover_with_issues(root: Path | str) -> dict[str, Any]:
    root = Path(root)
    nodes: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for rel in _python_files(root):
        text, error = _read(root / rel)
        if text is None:
            issues.append(_issue("unreadable-source", rel, 1, error or "read failed"))
            continue
        file_nodes, file_templates, file_issues = _analyze_python(rel, text)
        nodes.extend(file_nodes)
        templates.extend(file_templates)
        issues.extend(file_issues)
        files.append(
            {
                "path": rel,
                "frameworks": sorted({row["framework"] for row in file_nodes + file_templates})
                or [FRAMEWORK_PYTEST],
                "sha256": _sha256(text),
                "nodes": len(file_nodes),
                "templates": len(file_templates),
            }
        )
    nodes = _dedupe(nodes, "node_id", issues)
    templates = _dedupe(templates, "template_id", issues)
    templates.sort(key=lambda row: (row["path"], row["template_id"]))
    issues.sort(key=lambda row: (row["path"], row["line"], row["code"], row["message"]))
    files.sort(key=lambda row: row["path"])
    return {
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "associations_label": ASSOCIATIONS_LABEL,
        "limitations": list(LIMITATIONS),
        "nodes": nodes,
        "templates": templates,
        "issues": issues,
        "files": files,
    }


def discover(root: Path | str) -> list[dict[str, Any]]:
    """Return the sorted static test-node rows for ``root``."""
    return cast(list[dict[str, Any]], discover_with_issues(root)["nodes"])


def build_report(root: Path | str) -> dict[str, Any]:
    return discover_with_issues(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="test_index",
        description="Statically index test nodes (Python AST) without executing anything.",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root to index.")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.root.is_dir():
        sys.stderr.write(f"test_index: root is not a directory: {args.root}\n")
        return 2
    report = build_report(args.root)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
