"""Bounded source-declaration inventory of the checkout's operations surface.

``discover(root)`` reads the operational declarations actually checked into the
repository and never executes, interpolates, or contacts anything:

* ``install:dockerfile:<dockerfile>:<stage>`` — each ``FROM`` stage with its
  build-arg-resolved base image, or the raw ``$ARG`` expression plus an explicit
  ``base_unresolved`` marker when the file declares no default;
  ``service:entrypoint:<dockerfile>:<stage>:<cmd|entrypoint>`` records literal
  ``CMD``/``ENTRYPOINT`` argv text.
* ``service:compose:<file>:<service>`` — services in the root
  ``docker-compose*.yml``/``compose*.yml`` files plus literal ``include:``
  targets that stay inside the root, with image/build references, dependency
  names and conditions, ports, volumes, profiles, networks, restart policy,
  command, entrypoint, and environment variable *names* only;
  ``service:compose-include:`` records the includes, ``ops:volume``/``ops:network``
  the top-level named resources.
* ``install:migration:<path>`` — each ``db/migrations/*.sql`` with the sha256 of
  its exact bytes, so content drift at an unchanged path is visible.
* ``ops:script:<path>`` — ``scripts/release/`` files and any file under
  ``scripts/`` or ``src/pitwall/ops/`` named backup/restore/retention/archive.

Every row carries ``source`` as ``path:line``, ``metadata["source_sha256"]``
(sha256 of the raw file bytes) and a canonical ``metadata["contract_sha256"]``
binding identity, kind, operation, location, and raw source, so a changed
declaration drifts the contract with no runtime inspection.

Compose ``${...}`` text (including ``$${...}`` shell form) is recorded raw with
an explicit UNRESOLVED ``dynamic_compose_expression`` issue; it is never
interpolated and no ambient environment value is read. Environment values are
never recorded, only variable names. URL userinfo and recognizable
credential-shaped literal values are redacted from recorded strings; other
literal values remain source declarations and secret detection is unresolved.
Malformed known YAML fails discovery: a syntax error, non-mapping document, or non-mapping
``services``/``volumes``/``networks`` section raises, as does a missing root
``docker-compose.yml``.

Not exhaustive: docker/compose execution and runtime argv, Compose substitution,
environment values, docs/runbook procedures and host-level backup/retention
jobs, and every other inventory unit's operations surface are UNRESOLVED.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # reason: PyYAML ships no type stubs

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-ops-inventory.v1"
COMPOSE_MANDATORY = "docker-compose.yml"
COMPOSE_GLOBS = (
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose*.yml",
    "compose*.yaml",
)
DOCKERFILE_GLOB = "docker/Dockerfile*"
MIGRATIONS_DIR = "db/migrations"
RELEASE_SCRIPTS_DIR = "scripts/release"
SCRIPTS_DIRS = ("scripts", "src/pitwall/ops")

_OPS_NAME = re.compile(r"(backup|restore|retention|archive)", re.IGNORECASE)
_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]+@")
_SECRET_KEY = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?P<name>(?<![A-Za-z0-9])(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)(?![A-Za-z0-9]))"
    r"(?P<separator>\s*[:=]\s*)(?P<quote>[\"']?)(?P<value>[^\s,\]}\"']+)"
    r"(?P=quote)",
    re.IGNORECASE,
)
_SECRET_FLAG = re.compile(
    r"(?P<flag>--(?:password|passwd|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credential))"
    r"(?:\s*[\"']?\s*[,=:]\s*[\"']?|\s+)"
    r"(?P<quote>[\"']?)(?P<value>[^\s,\]}\"']+)(?P=quote)",
    re.IGNORECASE,
)
_SECRET_FLAG_ONLY = re.compile(
    r"^--(?:password|passwd|secret|token|api[-_]?key|access[-_]?key|private[-_]?key|credential)$",
    re.IGNORECASE,
)
_ARG_REFERENCE = re.compile(
    r"^\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}$|^\$(?P<plain>[A-Za-z_][A-Za-z0-9_]*)$"
)
_FROM = re.compile(r"^FROM\s+(?P<ref>\S+)(?:\s+AS\s+(?P<alias>\S+))?\s*$", re.IGNORECASE)
_ARG = re.compile(r"^ARG\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:=(?P<value>.*))?$", re.IGNORECASE)
_CMD = re.compile(r"^(?P<op>CMD|ENTRYPOINT)\s+(?P<body>.+)$", re.IGNORECASE)
_SEQUENCE = re.compile(r"(?P<sequence>\d+)")

_UNRESOLVED_TOPICS = (
    ("runtime_execution", "docker/compose execution, container behavior, and runtime argv are"),
    (
        "runtime_substitution",
        "Compose substitution and container shell expansion are never evaluated; raw expressions are",
    ),
    (
        "secret_values",
        "Compose environment values and URL userinfo are never recorded; known credential-shaped literal values are redacted and other literal values are",
    ),
    ("docs_runbooks", "docs/runbook procedures and host-level backup/restore/retention jobs are"),
    (
        "other_inventories",
        "every other inventory unit and project remains separate; its operations surface is",
    ),
)

_SCOPE_REASON = (
    "Dockerfile stages/base images/entrypoints, root Compose "
    "services/dependencies/ports/volumes/profiles, migration files, and checked-in "
    "scripts/release plus backup/restore/retention/archive-named scripts only; not exhaustive"
)


class OpsInventoryError(ValueError):
    """Raised when a known compose file is missing, malformed, or unrecognized."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OpsInventoryError(f"cannot read {path.name}: {exc}") from exc


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise OpsInventoryError(f"cannot read {path.name}: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _redact(value: str) -> str:
    value = _USERINFO.sub(lambda match: f"{match.group('scheme')}<redacted>@", value)
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}<redacted>", value
    )
    return _SECRET_FLAG.sub(lambda match: f"{match.group('flag')}=<redacted>", value)


def _compact(value: Any) -> Any:
    """Return a JSON-safe, URL-userinfo-redacted copy of ``value``."""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        compacted: list[Any] = []
        redact_next = False
        for item in value:
            if redact_next and isinstance(item, str):
                compacted.append("<redacted>")
                redact_next = False
                continue
            compacted.append(_compact(item))
            redact_next = isinstance(item, str) and bool(_SECRET_FLAG_ONLY.fullmatch(item.strip()))
        return compacted
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if _SECRET_KEY.search(str(key)) else _compact(child)
            for key, child in sorted(value.items())
        }
    return _redact(str(value))


def _as_list(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value] if value is not None else []
    return sorted(_redact(str(item)) for item in items)


def _contained_file(root: Path, path: Path) -> bool:
    """Return whether ``path`` resolves to a regular file beneath ``root``."""
    if not path.is_file():
        return False
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _source_containment_issue(source: str, declaration: str) -> dict[str, str]:
    return {
        "topic": "source_containment",
        "status": "unresolved",
        "source": source,
        "reason": (
            f"{declaration} is a known declaration path that does not resolve to a regular "
            "file beneath the checkout root; it was excluded from static inventory"
        ),
    }


def _contract(surface_id: str, kind: str, operation: str, source: str, source_sha256: str) -> str:
    payload = {
        "surface_id": surface_id,
        "kind": kind,
        "operation": operation,
        "source": source,
        "source_sha256": source_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _sha256(canonical.encode("utf-8"))


def _row(
    surface_id: str,
    kind: str,
    operation: str,
    source: str,
    source_sha256: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(metadata)
    metadata["source_sha256"] = source_sha256
    metadata["contract_sha256"] = _contract(surface_id, kind, operation, source, source_sha256)
    return {
        "surface_id": surface_id,
        "kind": kind,
        "operation": operation,
        "source": source,
        "metadata": metadata,
    }


def _declaration_line(text: str, section: str, name: str) -> int:
    """Return the line of ``name`` declared directly under top-level ``section``."""
    start: int | None = None
    indent: int | None = None
    for index, line in enumerate(text.splitlines()):
        if start is None:
            if re.match(rf"^{re.escape(section)}\s*:", line):
                start = index
            continue
        if line and not line[0].isspace():
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if indent is None:
            indent = len(line) - len(line.lstrip())
        if len(line) - len(line.lstrip()) == indent and re.match(
            rf"^{re.escape(name)}\s*:", stripped
        ):
            return index + 1
    return 1


def _text_line(text: str, needle: str) -> int:
    index = text.find(needle)
    return text.count("\n", 0, index) + 1 if index >= 0 else 1


def _env(environment: Any) -> tuple[list[str], list[str]]:
    """Return (sorted variable names, sorted names whose value holds a ``$`` expression)."""
    pairs: list[tuple[str, Any]] = []
    if isinstance(environment, dict):
        pairs = [(str(key), value) for key, value in environment.items()]
    elif isinstance(environment, list):
        for entry in environment:
            if isinstance(entry, str) and entry.strip():
                name, _, value = entry.strip().partition("=")
                pairs.append((name.strip(), value))
            elif isinstance(entry, dict):
                pairs.extend((str(key), value) for key, value in entry.items())
    names = sorted({name for name, _ in pairs})
    dynamic = sorted({name for name, value in pairs if isinstance(value, str) and "$" in value})
    return names, dynamic


def _dynamic_expressions(value: Any, prefix: str = "") -> dict[str, str]:
    found: dict[str, str] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) == "environment":
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            found.update(_dynamic_expressions(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(_dynamic_expressions(child, f"{prefix}[{index}]"))
    elif isinstance(value, str) and "$" in value:
        found[prefix] = _redact(value)
    return found


def _include_entries(data: dict[str, Any]) -> list[str]:
    include = data.get("include")
    entries: list[str] = []
    for item in include if isinstance(include, list) else [include]:
        if isinstance(item, str):
            entries.append(item)
        elif isinstance(item, dict) and isinstance(item.get("path"), str):
            entries.append(item["path"])
    return entries


class DuplicateKeyError(yaml.constructor.ConstructorError):  # type: ignore[misc]  # reason: installed PyYAML ConstructorError has no type stubs
    """A compose mapping repeats a key; the class name is all an error message reports."""


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]  # reason: installed PyYAML SafeLoader has no type stubs
    """SafeLoader variant that rejects duplicate keys in one YAML mapping."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        # PyYAML's SafeLoader gives the YAML merge key a special tag and only
        # resolves it after flatten_mapping; it is still a normal key for the
        # duplicate check at this point.
        key = (
            "<<"
            if key_node.tag == "tag:yaml.org,2002:merge"
            else loader.construct_object(key_node, deep=deep)
        )
        if key in seen:
            raise DuplicateKeyError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        seen.add(key)
    loader.flatten_mapping(node)
    return {
        loader.construct_object(key_node, deep=deep): loader.construct_object(value_node, deep=deep)
        for key_node, value_node in node.value
    }


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _yaml_problem(exc: Exception, first_line: int = 1) -> str:
    """A YAML error's class and position only: PyYAML's own text can quote the file."""
    name = type(exc).__name__
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        return f"invalid YAML ({name}) at line {mark.line + first_line}, column {mark.column + 1}"
    position = getattr(exc, "position", None)
    where = f" at character {position + 1}" if isinstance(position, int) else ""
    return f"invalid YAML ({name}){where}"


def _load_compose(root: Path, rel: str) -> tuple[dict[str, Any], str]:
    text = _read_text(root / rel)
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except (
        yaml.YAMLError,
        ValueError,
    ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
        raise OpsInventoryError(f"{rel}: {_yaml_problem(exc)}") from None
    if data is None:
        raise OpsInventoryError(f"{rel}: empty compose document")
    if not isinstance(data, dict):
        raise OpsInventoryError(f"{rel}: compose document must be a mapping")
    if not (data.get("services") or data.get("include")):
        raise OpsInventoryError(f"{rel}: declares no services or include entries")
    for section in ("services", "volumes", "networks"):
        declared = data.get(section)
        if declared is not None and not isinstance(declared, dict):
            raise OpsInventoryError(f"{rel}: {section} must be a mapping")
    return data, text


def _compose_file_list(root: Path) -> list[str]:
    """Return root compose files plus literal, root-contained ``include:`` targets."""
    files: list[str] = []
    for pattern in COMPOSE_GLOBS:
        files.extend(
            rel
            for path in sorted(root.glob(pattern))
            if _contained_file(root, path)
            and (rel := path.relative_to(root).as_posix()) not in files
        )
    if COMPOSE_MANDATORY not in files:
        raise OpsInventoryError(
            f"{COMPOSE_MANDATORY}: required compose file not found under {root}"
        )
    index = 0
    while index < len(files):
        data, _ = _load_compose(root, files[index])
        index += 1
        for entry in _include_entries(data):
            target = Path(os.path.normpath(Path(files[index - 1]).parent / entry))
            rel = target.as_posix()
            if (
                not target.is_absolute()
                and ".." not in target.parts
                and _contained_file(root, root / target)
                and rel not in files
            ):
                files.append(rel)
    return files


def _source_containment_issues(root: Path, compose_files: list[str]) -> list[dict[str, str]]:
    """Report known declaration paths that resolve outside the checkout."""
    issues: dict[tuple[str, str], dict[str, str]] = {}

    def add(source: str, declaration: str) -> None:
        issue = _source_containment_issue(source, declaration)
        issues[(source, declaration)] = issue

    for pattern in (*COMPOSE_GLOBS, DOCKERFILE_GLOB):
        for path in sorted(root.glob(pattern)):
            if (path.is_file() or path.is_symlink()) and not _contained_file(root, path):
                add(path.relative_to(root).as_posix() + ":1", path.relative_to(root).as_posix())

    docker_dir = root / "docker"
    if docker_dir.is_symlink() and not _contained_file(root, docker_dir):
        add("docker:1", "docker")

    migration_dir = root / MIGRATIONS_DIR
    if migration_dir.is_symlink() and not _contained_file(root, migration_dir):
        add(f"{MIGRATIONS_DIR}:1", MIGRATIONS_DIR)
    if migration_dir.is_dir():
        for path in sorted(migration_dir.glob("*.sql")):
            if (path.is_file() or path.is_symlink()) and not _contained_file(root, path):
                rel = path.relative_to(root).as_posix()
                add(f"{rel}:1", rel)

    for base in SCRIPTS_DIRS:
        directory = root / base
        if not directory.is_dir():
            continue
        if directory.is_symlink() and not _contained_file(root, directory):
            rel = directory.relative_to(root).as_posix()
            add(f"{rel}:1", rel)
        for path in sorted(directory.rglob("*")):
            if (path.is_file() or path.is_symlink()) and not _contained_file(root, path):
                rel = path.relative_to(root).as_posix()
                add(f"{rel}:1", rel)

    for rel in compose_files:
        data, text = _load_compose(root, rel)
        for entry in _include_entries(data):
            target = Path(os.path.normpath(Path(rel).parent / entry))
            candidate = target if target.is_absolute() else root / target
            if (candidate.is_file() or candidate.is_symlink()) and not _contained_file(
                root, candidate
            ):
                add(
                    f"{rel}:{_text_line(text, entry)}",
                    f"{rel} include target {entry!r}",
                )
    return sorted(issues.values(), key=lambda issue: (issue["source"], issue["reason"]))


def _compose_file_rows(
    rel: str, data: dict[str, Any], text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sha = _sha256(text.encode("utf-8"))
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for name, spec in sorted((data.get("services") or {}).items()):
        if not isinstance(spec, dict):
            raise OpsInventoryError(f"{rel}: service {name} must be a mapping")
        source = f"{rel}:{_declaration_line(text, 'services', str(name))}"
        dynamic = _dynamic_expressions(spec)
        env_names, dynamic_env = _env(spec.get("environment"))
        dynamic.update(
            {f"environment.{env_name}": "<redacted expression value>" for env_name in dynamic_env}
        )
        dynamic = dict(sorted(dynamic.items()))
        depends_on = spec.get("depends_on")
        dependencies = (
            sorted(str(item) for item in depends_on)
            if isinstance(depends_on, list)
            else sorted(str(key) for key in depends_on)
            if isinstance(depends_on, dict)
            else []
        )
        conditions = {
            str(key): str(value["condition"])
            for key, value in (depends_on or {}).items()
            if isinstance(depends_on, dict) and isinstance(value, dict) and value.get("condition")
        }
        rows.append(
            _row(
                f"service:compose:{rel}:{name}",
                "service",
                "service",
                source,
                sha,
                {
                    "category": "compose-service",
                    "compose_file": rel,
                    "image": _compact(spec.get("image")),
                    "build": _compact(spec.get("build")),
                    "depends_on": dependencies,
                    "depends_on_conditions": conditions,
                    "ports": _as_list(spec.get("ports")),
                    "volumes": _as_list(spec.get("volumes")),
                    "profiles": _as_list(spec.get("profiles")),
                    "networks": _as_list(spec.get("networks")),
                    "restart": _compact(spec.get("restart")),
                    "command": _compact(spec.get("command")),
                    "entrypoint": _compact(spec.get("entrypoint")),
                    "env_names": env_names,
                    "dynamic_expressions": dynamic,
                    "dynamic_unresolved": bool(dynamic),
                },
            )
        )
        if dynamic:
            issues.append(
                {
                    "topic": "dynamic_compose_expression",
                    "status": "unresolved",
                    "source": source,
                    "reason": (
                        f"{rel} service {name} has raw expressions at "
                        f"{', '.join(dynamic)}; no Compose substitution or runtime value is evaluated"
                    ),
                }
            )
    for entry in _include_entries(data):
        target = Path(os.path.normpath(Path(rel).parent / entry)).as_posix()
        source = f"{rel}:{_text_line(text, entry)}"
        rows.append(
            _row(
                f"service:compose-include:{rel}:{target}",
                "service",
                "include",
                source,
                sha,
                {"category": "compose-include", "compose_file": rel, "target": target},
            )
        )
        issues.append(
            {
                "topic": "compose_include",
                "status": "unresolved",
                "source": source,
                "reason": (
                    f"{rel} includes {target}; a root-contained readable target is inventoried "
                    "from its own definitions, while include override/merge semantics are not evaluated"
                ),
            }
        )
    for section, operation in (("volumes", "volume"), ("networks", "network")):
        for name, spec in sorted((data.get(section) or {}).items()):
            rows.append(
                _row(
                    f"ops:{operation}:{rel}:{name}",
                    "ops",
                    operation,
                    f"{rel}:{_declaration_line(text, section, str(name))}",
                    sha,
                    {
                        "category": operation,
                        "compose_file": rel,
                        "name": str(name),
                        "external": bool(isinstance(spec, dict) and spec.get("external")),
                    },
                )
            )
    return rows, issues


def _dockerfile_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob(DOCKERFILE_GLOB)):
        if not _contained_file(root, path):
            continue
        rel = path.relative_to(root).as_posix()
        text = _read_text(path)
        sha = _sha256(text.encode("utf-8"))
        global_args: dict[str, str] = {}
        stage_args: dict[str, str] = {}
        stage: str | None = None
        index = 0
        for number, raw_line in enumerate(text.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _ARG.match(line)
            if match is not None:
                target = global_args if stage is None else stage_args
                target[match.group("name")] = (match.group("value") or "").strip()
                continue
            match = _FROM.match(line)
            if match is not None:
                index += 1
                stage = match.group("alias") or f"stage{index}"
                expression = match.group("ref")
                ref = _ARG_REFERENCE.match(expression)
                arg_name = (ref.group("braced") or ref.group("plain")) if ref is not None else None
                # Only global ARG declarations are in scope for FROM. ARGs declared
                # inside an earlier stage must not resolve a later stage's base.
                if arg_name is not None:
                    resolved = global_args.get(arg_name)
                    unresolved = resolved is None
                elif "$" in expression:
                    resolved = None
                    unresolved = True
                else:
                    resolved = expression
                    unresolved = False
                stage_args = dict(global_args)
                rows.append(
                    _row(
                        f"install:dockerfile:{rel}:{stage}",
                        "install",
                        "stage",
                        f"{rel}:{number}",
                        sha,
                        {
                            "category": "stage",
                            "stage": stage,
                            "stage_index": index,
                            "base_image": _redact(resolved) if resolved is not None else None,
                            "base_expression": _redact(expression),
                            "base_unresolved": unresolved,
                        },
                    )
                )
                continue
            match = _CMD.match(line)
            if match is not None and stage is not None:
                instruction = match.group("op").lower()
                body = match.group("body")
                rows.append(
                    _row(
                        f"service:entrypoint:{rel}:{stage}:{instruction}",
                        "service",
                        "entrypoint",
                        f"{rel}:{number}",
                        sha,
                        {
                            "category": "entrypoint",
                            "instruction": instruction,
                            "argv": _redact(body),
                            "stage": stage,
                            "dynamic_unresolved": "$" in body,
                        },
                    )
                )
    return rows


def _migration_rows(root: Path) -> list[dict[str, Any]]:
    directory = root / MIGRATIONS_DIR
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.sql")) if directory.is_dir() else []:
        if not _contained_file(root, path):
            continue
        rel = path.relative_to(root).as_posix()
        data = _read_bytes(path)
        sequence = _SEQUENCE.match(path.name)
        rows.append(
            _row(
                f"install:migration:{rel}",
                "install",
                "migration",
                f"{rel}:1",
                _sha256(data),
                {
                    "category": "migration",
                    "filename": path.name,
                    "sequence": int(sequence.group("sequence")) if sequence else None,
                    "bytes": len(data),
                },
            )
        )
    return rows


def _script_rows(root: Path) -> list[dict[str, Any]]:
    selected: dict[str, str] = {}
    for base in SCRIPTS_DIRS:
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not _contained_file(root, path) or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            operational = bool(_OPS_NAME.search(path.name))
            if operational or rel.startswith(f"{RELEASE_SCRIPTS_DIR}/"):
                selected[rel] = "operations" if operational else "release"
    return [
        _row(
            f"ops:script:{rel}",
            "ops",
            "script",
            f"{rel}:1",
            _sha256(data := _read_bytes(root / rel)),
            {"category": category, "path": rel, "bytes": len(data)},
        )
        for rel, category in sorted(selected.items())
    ]


def _scan(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(root).resolve()
    rows = _dockerfile_rows(root)
    compose_files = _compose_file_list(root)
    issues: list[dict[str, Any]] = _source_containment_issues(root, compose_files)
    for rel in compose_files:
        data, text = _load_compose(root, rel)
        file_rows, file_issues = _compose_file_rows(rel, data, text)
        rows += file_rows
        issues += file_issues
    rows += _migration_rows(root)
    rows += _script_rows(root)
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        merged.setdefault(row["surface_id"], row)
    rows = [merged[key] for key in sorted(merged)]
    issues += [
        {"topic": topic, "status": "unresolved", "reason": f"{label} UNRESOLVED"}
        for topic, label in _UNRESOLVED_TOPICS
    ]
    issues.append({"topic": "scope", "status": "partial", "reason": _SCOPE_REASON})
    issues.sort(key=lambda issue: (issue["topic"], issue.get("source", "")))
    return rows, issues


def discover(root: Path) -> list[dict[str, Any]]:
    """Return the deterministic, ``surface_id``-sorted operations rows for ``root``."""
    return _scan(root)[0]


def discover_with_issues(root: Path) -> dict[str, Any]:
    """Return ``discover(root)`` plus explicitly unresolved operations topics."""
    surfaces, issues = _scan(root)
    return {"schema_version": SCHEMA_VERSION, "surfaces": surfaces, "issues": issues}
