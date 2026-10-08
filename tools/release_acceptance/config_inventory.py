"""Settings-field and environment-example configuration inventory.

``discover(root)`` parses — never imports, instantiates, executes, or
runtime-introspects — ``src/pitwall/config.py`` (``ast``) and ``.env.example``
(plain text). Every annotated class-body assignment on ``PitwallSettings`` and
its nested ``_StrictBase`` config models becomes a
``config:field:<Class>:<name>`` row, so same-named fields on different models
can never collide. Each field row records the declared type and default source
expressions, the literal validation alias and its source expression, exact
``Field()``/``Field(...)``/``Field(default=...)`` required semantics, literal
``Field`` constraints, an exact ``path:line`` source, a SHA256 of the
declaration segment, and a stable contract digest over the declared contract
alone, including the dynamic alias source expression.

``config:env:<NAME>`` rows carry every literal ``NAME=`` key documented in
``.env.example``. Example values are never recorded — only key names — while
``source_sha256`` hashes the full declaration line so a changed example default
drifts the row without leaking the value.

A field or env name declared more than once is reported with an explicit
``duplicate_field_declaration`` / ``duplicate_env_declaration`` issue naming
every occurrence source; the first row is kept deterministically.

Non-literal defaults, aliases, and constraints stay as their source expression
plus an explicit ``*_unresolved`` marker and an UNRESOLVED issue; they are
never evaluated.

Not exhaustive: runtime environment binding, config-file and per-operation
behavior, and every other project's configuration surface are UNRESOLVED.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-config-inventory.v1"
CONFIG_MODULE = "src/pitwall/config.py"
ENV_EXAMPLE = ".env.example"
SETTINGS_CLASS = "PitwallSettings"
_MODEL_BASES = frozenset({"_StrictBase", "BaseSettings"})
_ALIAS_KEYS = ("validation_alias", "alias")
_CONSTRAINT_KEYS = frozenset(
    {
        "allow_inf_nan",
        "exclude",
        "frozen",
        "ge",
        "gt",
        "le",
        "lt",
        "max_length",
        "min_length",
        "multiple_of",
        "pattern",
        "repr",
        "strict",
    }
)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
type Row = dict[str, Any]
type Issue = dict[str, Any]
_UNRESOLVED = (
    ("runtime_env_binding", "runtime environment binding, precedence, and validation"),
    ("config_files", "config-file loading and per-operation settings behavior"),
    ("other_projects", "every other project's configuration surface"),
)


class ConfigInventoryError(ValueError):
    """Raised when the settings module cannot be read, parsed, or recognized."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigInventoryError(f"cannot read {path.name}: {exc}") from exc


def _parse_module(text: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        raise ConfigInventoryError(f"{CONFIG_MODULE}: invalid Python: {exc}") from exc


def _segment(text: str, node: ast.AST) -> str:
    return ast.get_source_segment(text, node) or ast.unparse(node)


def _literal(node: ast.AST | None) -> tuple[Any, bool]:
    """Return a JSON-safe literal value, or ``(None, False)`` when dynamic."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return node.value, True
        return None, False
    if isinstance(node, (ast.List, ast.Tuple)):
        values: list[Any] = []
        for element in node.elts:
            value, literal = _literal(element)
            if not literal:
                return None, False
            values.append(value)
        return values, True
    if isinstance(node, ast.Dict):
        mapping: dict[str, Any] = {}
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            key, key_literal = _literal(key_node)
            value, value_literal = _literal(value_node)
            if not (key_literal and value_literal) or not isinstance(key, str):
                return None, False
            mapping[key] = value
        return mapping, True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = node.operand
        if isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float)):
            return -operand.value, True
    return None, False


def _contract_digest(contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_ellipsis(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _annotation_constraints(text: str, annotation: ast.AST) -> dict[str, Any]:
    if not (
        isinstance(annotation, ast.Subscript)
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "Annotated"
    ):
        return {}
    elements = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else []
    constraints: dict[str, Any] = {}
    for element in elements:
        if not (
            isinstance(element, ast.Call)
            and isinstance(element.func, ast.Name)
            and element.func.id == "Field"
        ):
            continue
        for keyword in element.keywords:
            if keyword.arg not in _CONSTRAINT_KEYS:
                continue
            value, literal = _literal(keyword.value)
            constraints[keyword.arg] = (
                value
                if literal
                else {"expression": _segment(text, keyword.value), "unresolved": True}
            )
    return constraints


def _field_row(text: str, model: str, node: ast.AnnAssign) -> tuple[Row, list[Issue]]:
    if not isinstance(node.target, ast.Name):
        raise ConfigInventoryError(f"{CONFIG_MODULE}: field target must be a name")
    field = node.target.id
    surface_id = f"config:field:{model}:{field}"
    type_expression = _segment(text, node.annotation)
    call = node.value if isinstance(node.value, ast.Call) else None
    if call is not None and not (isinstance(call.func, ast.Name) and call.func.id == "Field"):
        call = None
    kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None} if call else {}
    positional = list(call.args) if call else []
    unresolved: list[str] = []

    default_expression: str | None = None
    default_unresolved = False
    required = False
    ellipsis_default = ("default" in kwargs and _is_ellipsis(kwargs["default"])) or (
        bool(positional) and _is_ellipsis(positional[0])
    )
    if ellipsis_default:
        # ``Field(...)`` / ``Field(default=...)``: the ellipsis sentinel means
        # the field is required, not that it has an unresolved default.
        required = True
    elif "default" in kwargs:
        default_expression = _segment(text, kwargs["default"])
        _, literal = _literal(kwargs["default"])
        default_unresolved = not literal
    elif "default_factory" in kwargs:
        default_expression = _segment(text, kwargs["default_factory"])
        default_unresolved = True
    elif positional:
        default_expression = _segment(text, positional[0])
        _, literal = _literal(positional[0])
        default_unresolved = not literal
    elif node.value is not None and call is None:
        default_expression = _segment(text, node.value)
        _, literal = _literal(node.value)
        default_unresolved = not literal
    else:
        # A bare annotation with no default is required.
        required = True
    if default_unresolved:
        unresolved.append("dynamic_default")

    alias: str | None = None
    alias_expression: str | None = None
    alias_unresolved = False
    for key in _ALIAS_KEYS:
        alias_node = kwargs.get(key)
        if alias_node is None:
            continue
        alias_expression = _segment(text, alias_node)
        value, literal = _literal(alias_node)
        if literal and isinstance(value, str):
            alias = value
        else:
            alias_unresolved = True
            unresolved.append("dynamic_alias")
        break

    constraints = _annotation_constraints(text, node.annotation)
    for key, value_node in kwargs.items():
        if key not in _CONSTRAINT_KEYS:
            continue
        value, literal = _literal(value_node)
        constraints[key] = (
            value if literal else {"expression": _segment(text, value_node), "unresolved": True}
        )
    if any(isinstance(value, dict) and value.get("unresolved") for value in constraints.values()):
        unresolved.append("dynamic_constraint")

    contract = {
        "surface_id": surface_id,
        "kind": "config",
        "operation": "setting",
        "model": model,
        "field": field,
        "type_expression": type_expression,
        "default_expression": default_expression,
        "default_unresolved": default_unresolved,
        "required": required,
        "alias": alias,
        "alias_expression": alias_expression,
        "alias_unresolved": alias_unresolved,
        "constraints": constraints,
    }
    segment = ast.get_source_segment(text, node) or _segment(text, node)
    metadata = {
        "category": "field",
        "model": model,
        "field": field,
        "type_expression": type_expression,
        "default_expression": default_expression,
        "default_unresolved": default_unresolved,
        "required": required,
        "alias": alias,
        "alias_expression": alias_expression,
        "alias_unresolved": alias_unresolved,
        "constraints": constraints,
        "unresolved": sorted(set(unresolved)),
        "source_sha256": hashlib.sha256(segment.encode("utf-8")).hexdigest(),
        "contract_digest": _contract_digest(contract),
    }
    row = {
        "surface_id": surface_id,
        "kind": "config",
        "operation": "setting",
        "source": f"{CONFIG_MODULE}:{node.lineno}",
        "metadata": metadata,
    }
    dynamic = [
        {
            "topic": topic,
            "status": "unresolved",
            "source": f"{CONFIG_MODULE}:{node.lineno}",
            "reason": (
                f"{model}.{field} {topic.removeprefix('dynamic_')} at "
                f"{CONFIG_MODULE}:{node.lineno} is not a literal expression; "
                "recorded as source text and never executed; UNRESOLVED"
            ),
        }
        for topic in sorted(set(unresolved))
    ]
    return row, dynamic


def _model_classes(module: ast.Module) -> list[ast.ClassDef]:
    classes: list[ast.ClassDef] = []
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {ast.unparse(base) for base in node.bases}
        if node.name == SETTINGS_CLASS or bases & _MODEL_BASES:
            classes.append(node)
    return classes


def _duplicate_issues(kind: str, occurrences: list[tuple[str, list[str]]]) -> list[Issue]:
    return [
        {
            "topic": f"duplicate_{kind}_declaration",
            "status": "unresolved",
            "reason": (
                f"{kind} {name} is declared {len(sources)} times at "
                f"{', '.join(sources)}; the first declaration is kept and every "
                "duplicate stays UNRESOLVED"
            ),
            "duplicate_sources": list(sources),
        }
        for name, sources in sorted(occurrences)
    ]


def _field_rows(text: str) -> tuple[list[Row], list[Issue]]:
    module = _parse_module(text)
    classes = _model_classes(module)
    if not any(cls.name == SETTINGS_CLASS for cls in classes):
        raise ConfigInventoryError(f"{CONFIG_MODULE}: no {SETTINGS_CLASS} settings class found")
    rows: list[Row] = []
    dynamic: list[Issue] = []
    declared: dict[str, list[str]] = {}
    for cls in classes:
        for node in cls.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            row, field_issues = _field_row(text, cls.name, node)
            rows.append(row)
            dynamic += field_issues
            declared.setdefault(row["surface_id"], []).append(row["source"])
    duplicates = [
        (surface_id, sources) for surface_id, sources in declared.items() if len(sources) > 1
    ]
    return rows, dynamic + _duplicate_issues("field", duplicates)


def _env_row(name: str, source: str, line: str) -> Row:
    surface_id = f"config:env:{name}"
    contract = {
        "surface_id": surface_id,
        "kind": "config",
        "operation": "setting",
        "env_name": name,
    }
    metadata = {
        "category": "env_example",
        "env_name": name,
        "example_value_redacted": True,
        "unresolved": [],
        # Hashes the full declaration line (value included) so changed example
        # defaults drift the metadata without ever recording the value.
        "source_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
        "contract_digest": _contract_digest(contract),
    }
    return {
        "surface_id": surface_id,
        "kind": "config",
        "operation": "setting",
        "source": source,
        "metadata": metadata,
    }


def _env_rows(path: Path) -> tuple[list[Row], list[Issue]]:
    rows: list[Row] = []
    declared: dict[str, list[str]] = {}
    for number, line in enumerate(_read_text(path).splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, _value = stripped.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            continue
        source = f"{ENV_EXAMPLE}:{number}"
        rows.append(_env_row(name, source, stripped))
        declared.setdefault(name, []).append(source)
    duplicates = [(name, sources) for name, sources in declared.items() if len(sources) > 1]
    return rows, _duplicate_issues("env", duplicates)


def _dedupe_sorted(rows: list[Row]) -> list[Row]:
    merged: dict[str, Row] = {}
    for row in rows:
        merged.setdefault(row["surface_id"], row)
    return [merged[key] for key in sorted(merged)]


def _scan(root: Path) -> tuple[list[Row], list[Issue]]:
    root = Path(root).resolve()
    rows, dynamic = _field_rows(_read_text(root / CONFIG_MODULE))
    env_path = root / ENV_EXAMPLE
    if env_path.is_file():
        env_rows, env_issues = _env_rows(env_path)
        rows += env_rows
        dynamic += env_issues
    rows = _dedupe_sorted(rows)
    issues = dynamic + [
        {
            "topic": topic,
            "status": "unresolved",
            "reason": f"{label} are UNRESOLVED",
        }
        for topic, label in _UNRESOLVED
    ]
    issues.append(
        {
            "topic": "scope",
            "status": "partial",
            "reason": (
                "annotated fields of config.py settings models and literal "
                ".env.example key names only; not exhaustive"
            ),
        }
    )
    issues.sort(key=lambda issue: (issue["topic"], issue.get("source", "")))
    return rows, issues


def discover(root: Path) -> list[Row]:
    """Return settings-field and env-example key rows for ``root``."""
    return _scan(root)[0]


def discover_with_issues(root: Path) -> Row:
    """Return ``discover(root)`` plus explicitly unresolved configuration topics."""
    surfaces, issues = _scan(root)
    return {"schema_version": SCHEMA_VERSION, "surfaces": surfaces, "issues": issues}
