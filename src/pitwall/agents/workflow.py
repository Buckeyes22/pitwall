"""Phase 6 workflow document loader, semantic validator, and digest.

The workflow document is a versioned JSON graph that names dispatch tasks,
their routes, prompts, dependencies, retry policy, and optional verification
commands. ``validate_workflow`` performs the structural and semantic checks
described in section 6.2 of the Phase 6 implementation plan and returns a
fully explicit normalized dictionary plus any advisory warnings (currently
limited to unknown model IDs when the harness opts in). ``load_workflow``
reads and validates a workflow file from disk. ``workflow_digest`` produces a
deterministic SHA-256 over the canonical JSON of the normalized output.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .harnesses import get_adapter


class WorkflowError(ValueError):
    """Raised when a workflow document violates the runtime contract."""


_TASK_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ARTIFACT_VALUES = {"stdout", "stderr", "result", "patch", "diffstat"}
_RETRY_TRIGGERS = {"timeout", "transport-error"}
_HOSTS = {"claude", "codex", "copilot"}
_WORKSPACES = {"shared", "isolated", "auto"}
_MODES = {"read", "write"}
_FAILURE_POLICIES = {"fail-fast", "continue"}
_ROUTE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")


def _fail(message: str) -> None:
    raise WorkflowError(message)


def _require_keys(value: Mapping[str, Any], field: str, allowed: set[str]) -> None:
    extras = set(value) - allowed
    if extras:
        joined = ", ".join(sorted(extras))
        _fail(f"{field} has unknown fields: {joined}")


def _resolve_alias(harness_def: Mapping[str, Any], model: str) -> tuple[str, bool]:
    """Resolve ``model`` against ``harness_def`` registry, case-insensitively.

    Returns ``(canonical_model, found)``. ``found`` is False when the model is
    not declared under the harness's ``models`` map or any of its aliases;
    callers decide whether unknown models are tolerated.
    """

    if not isinstance(harness_def, Mapping):
        return model, False
    models = harness_def.get("models")
    if not isinstance(models, Mapping):
        return model, False
    if model in models:
        return model, True
    lower = model.lower()
    for canonical, entry in models.items():
        if not isinstance(entry, Mapping):
            continue
        aliases = entry.get("aliases") or []
        for alias in aliases:
            if isinstance(alias, str) and alias and alias.lower() == lower:
                return canonical, True
    return model, False


def _validate_prompt_file(file_value: str, workflow_dir: Path, field: str) -> str:
    if not file_value:
        _fail(f"{field} must be a non-empty string")
    candidate = Path(file_value)
    if candidate.is_absolute():
        _fail(f"{field} must be a relative path: {file_value!r}")
    parts = candidate.parts
    if any(part == ".." for part in parts):
        _fail(f"{field} must not contain a parent path segment: {file_value!r}")
    real_dir = workflow_dir.resolve()
    resolved = (real_dir / file_value).resolve()
    try:
        resolved.relative_to(real_dir)
    except ValueError:
        _fail(f"{field} escapes workflow directory: {file_value!r}")
    if not resolved.is_file():
        _fail(f"{field} is not a regular file: {file_value!r}")
    return file_value


def _find_cycle(deps: Mapping[str, list[str]]) -> list[str] | None:
    """Return a cycle path like ``['a', 'b', 'c', 'a']`` or ``None``."""

    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(deps, WHITE)

    for start in deps:
        if color[start] != WHITE:
            continue
        path = [start]
        color[start] = GRAY
        stack = [(start, iter(deps.get(start, [])))]
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if not isinstance(child, str):
                    continue
                if child not in color:
                    _fail(f"dependency references unknown task: {child!r}")
                if color[child] == GRAY:
                    if child in path:
                        idx = path.index(child)
                        return path[idx:] + [child]
                    return [child, node, child]
                if color[child] == WHITE:
                    color[child] = GRAY
                    path.append(child)
                    stack.append((child, iter(deps.get(child, []))))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                path.pop()
                stack.pop()
    return None


def _validate_prompt(prompt: Any, task_name: str, workflow_dir: Path) -> dict[str, Any]:
    if not isinstance(prompt, Mapping):
        _fail(f"tasks.{task_name}.prompt must be an object")
    keys = set(prompt)
    if keys != {"file"} and keys != {"text"}:
        _fail(f"tasks.{task_name}.prompt must contain exactly one of 'file' or 'text'")
    if "file" in prompt:
        file_value = prompt["file"]
        if not isinstance(file_value, str) or not file_value:
            _fail(f"tasks.{task_name}.prompt.file must be a non-empty string")
        return {
            "file": _validate_prompt_file(
                file_value, workflow_dir, f"tasks.{task_name}.prompt.file"
            )
        }
    text_value = prompt["text"]
    if not isinstance(text_value, str) or not text_value:
        _fail(f"tasks.{task_name}.prompt.text must be a non-empty string")
    return {"text": text_value}


def _normalize_route(
    route: Mapping[str, Any],
    *,
    task_name: str,
    registry_harnesses: Mapping[str, Any],
    native_harnesses: set[str],
    host: str,
    warnings: list[str],
) -> dict[str, Any]:
    if not isinstance(route, Mapping):
        _fail(f"tasks.{task_name}.route must be an object")
    _require_keys(route, f"tasks.{task_name}.route", {"name", "provider", "model", "effort"})

    route_name = route.get("name")
    if route_name is not None and (
        not isinstance(route_name, str) or _ROUTE_NAME_PATTERN.fullmatch(route_name) is None
    ):
        _fail(f"tasks.{task_name}.route.name must match [A-Za-z][A-Za-z0-9._-]{{0,63}}")

    harness = route.get("provider")
    if not isinstance(harness, str) or not harness:
        _fail(f"tasks.{task_name}.route.harness must be a non-empty string")
    assert isinstance(harness, str)
    if harness not in registry_harnesses:
        _fail(f"tasks.{task_name}.route.harness {harness!r} is not a registered harness")
    if harness in native_harnesses:
        _fail(
            f"tasks.{task_name}.route.harness {harness!r} is native to host {host!r}; "
            "keep that work native to the host"
        )
    try:
        adapter = get_adapter(harness)
    except ValueError:
        _fail(f"tasks.{task_name}.route.harness {harness!r} has no harness adapter")
    model = route.get("model")
    if not isinstance(model, str) or not model:
        _fail(f"tasks.{task_name}.route.model must be a non-empty string")
    assert isinstance(model, str)

    harness_def = registry_harnesses.get(harness, {})
    canonical_model, found = _resolve_alias(harness_def, model)
    allow_unknown = (
        bool(harness_def.get("allowUnknownModels", False))
        if isinstance(harness_def, Mapping)
        else False
    )
    if not found and adapter.model_bound:
        _fail(
            f"tasks.{task_name}.route.model {model!r} is not a model of model-bound "
            f"harness {harness!r}"
        )
    if not found:
        if allow_unknown:
            warnings.append(
                f"tasks.{task_name}.route.model {model!r} is not declared by harness {harness!r}; "
                "passing through because allowUnknownModels is true"
            )
        else:
            _fail(
                f"tasks.{task_name}.route.model {model!r} is not a known model of harness {harness!r}"
            )

    effort = route.get("effort")
    if effort is not None:
        if not isinstance(effort, str) or not effort:
            _fail(f"tasks.{task_name}.route.effort must be a non-empty string")
        if adapter.supported_efforts is None:
            _fail(
                f"tasks.{task_name}.route.effort {effort!r} is not supported: "
                f"harness {harness!r} has no effort control"
            )
        effort_def = harness_def.get("effort") if isinstance(harness_def, Mapping) else None
        allowed = list(effort_def.get("values", [])) if isinstance(effort_def, Mapping) else []
        if effort not in allowed:
            _fail(
                f"tasks.{task_name}.route.effort {effort!r} is not allowed for harness {harness!r}"
            )
        if found and isinstance(harness_def, Mapping):
            models = harness_def.get("models")
            if isinstance(models, Mapping):
                entry = models.get(canonical_model)
                if isinstance(entry, Mapping):
                    model_effort = entry.get("effortValues") or []
                    if model_effort and effort not in model_effort:
                        _fail(
                            f"tasks.{task_name}.route.effort {effort!r} is not allowed for "
                            f"model {canonical_model!r} of harness {harness!r}"
                        )

    normalized: dict[str, Any] = {"provider": harness, "model": canonical_model}
    if route_name is not None:
        normalized["name"] = route_name
    if effort is not None:
        normalized["effort"] = effort
    return dict(sorted(normalized.items()))


def _normalize_retry(retry: Any, task_name: str) -> dict[str, Any]:
    if retry is None:
        retry = {}
    if not isinstance(retry, Mapping):
        _fail(f"tasks.{task_name}.retry must be an object")
    _require_keys(retry, f"tasks.{task_name}.retry", {"maxAttempts", "backoffSeconds", "on"})
    max_attempts = retry.get("maxAttempts", 1)
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 1:
        _fail(f"tasks.{task_name}.retry.maxAttempts must be a positive integer")
    backoff = retry.get("backoffSeconds", 0)
    if not isinstance(backoff, (int, float)) or isinstance(backoff, bool) or backoff < 0:
        _fail(f"tasks.{task_name}.retry.backoffSeconds must be a non-negative number")
    on = retry.get("on", [])
    if not isinstance(on, list):
        _fail(f"tasks.{task_name}.retry.on must be a list")
    seen: set[str] = set()
    normalized_on: list[str] = []
    for trigger in on:
        if not isinstance(trigger, str):
            _fail(f"tasks.{task_name}.retry.on entries must be strings")
        if trigger not in _RETRY_TRIGGERS:
            _fail(
                f"tasks.{task_name}.retry.on {trigger!r} must be one of {sorted(_RETRY_TRIGGERS)}"
            )
        if trigger in seen:
            _fail(f"tasks.{task_name}.retry.on duplicates {trigger!r}")
        seen.add(trigger)
        normalized_on.append(trigger)
    return {"maxAttempts": max_attempts, "backoffSeconds": backoff, "on": normalized_on}


def _normalize_verify(verify: Any, task_name: str) -> list[list[str]]:
    if verify is None:
        return []
    if not isinstance(verify, list):
        _fail(f"tasks.{task_name}.verify must be a list")
    normalized: list[list[str]] = []
    for index, command in enumerate(verify):
        if not isinstance(command, list) or not command:
            _fail(f"tasks.{task_name}.verify[{index}] must be a non-empty array of strings")
        argv: list[str] = []
        for arg_index, arg in enumerate(command):
            if not isinstance(arg, str) or not arg:
                _fail(f"tasks.{task_name}.verify[{index}][{arg_index}] must be a non-empty string")
            argv.append(arg)
        normalized.append(argv)
    return normalized


def _normalize_context_from(
    context_from: Any,
    *,
    task_name: str,
    direct_deps: set[str],
) -> list[dict[str, Any]]:
    if context_from is None:
        return []
    if not isinstance(context_from, list):
        _fail(f"tasks.{task_name}.contextFrom must be a list")
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(context_from):
        if not isinstance(entry, Mapping):
            _fail(f"tasks.{task_name}.contextFrom[{index}] must be an object")
        _require_keys(
            entry, f"tasks.{task_name}.contextFrom[{index}]", {"task", "artifact", "maxBytes"}
        )
        ref = entry.get("task")
        if not isinstance(ref, str) or not ref:
            _fail(f"tasks.{task_name}.contextFrom[{index}].task must be a non-empty string")
        artifact = entry.get("artifact")
        if artifact not in _ARTIFACT_VALUES:
            _fail(
                f"tasks.{task_name}.contextFrom[{index}].artifact must be one of "
                f"{sorted(_ARTIFACT_VALUES)}"
            )
        if ref not in direct_deps:
            _fail(f"tasks.{task_name}.contextFrom[{index}].task {ref!r} is not a direct dependency")
        max_bytes = entry.get("maxBytes", 50000)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            _fail(f"tasks.{task_name}.contextFrom[{index}].maxBytes must be a positive integer")
        normalized.append({"task": ref, "artifact": artifact, "maxBytes": max_bytes})
    return normalized


def _normalize_task(
    task_name: str,
    task_def: Any,
    *,
    defaults: Mapping[str, Any],
    workflow_dir: Path,
    registry_harnesses: Mapping[str, Any],
    native_harnesses: set[str],
    host: str,
    warnings: list[str],
    task_names: set[str],
) -> dict[str, Any]:
    if not isinstance(task_def, Mapping):
        _fail(f"tasks.{task_name} must be an object")
    _require_keys(
        task_def,
        f"tasks.{task_name}",
        {
            "route",
            "mode",
            "prompt",
            "dependsOn",
            "contextFrom",
            "timeoutSeconds",
            "workspace",
            "retry",
            "verify",
            "askSupport",
            "maxAsks",
        },
    )

    ask_support = task_def.get("askSupport")
    if ask_support is not None and not isinstance(ask_support, bool):
        _fail(f"tasks.{task_name}.askSupport must be a boolean")

    max_asks = task_def.get("maxAsks")
    if max_asks is not None:
        if isinstance(max_asks, bool) or not isinstance(max_asks, int) or not 1 <= max_asks <= 50:
            _fail(f"tasks.{task_name}.maxAsks must be an integer 1..50")
        effective_ask_support = (
            ask_support if ask_support is not None else defaults.get("askSupport", False)
        )
        if not effective_ask_support:
            _fail(f"tasks.{task_name}.maxAsks requires askSupport")

    mode = task_def.get("mode")
    if mode not in _MODES:
        _fail(f"tasks.{task_name}.mode must be one of {sorted(_MODES)}")

    route = _normalize_route(
        task_def.get("route"),
        task_name=task_name,
        registry_harnesses=registry_harnesses,
        native_harnesses=native_harnesses,
        host=host,
        warnings=warnings,
    )

    prompt = _validate_prompt(task_def.get("prompt"), task_name, workflow_dir)

    depends_on_raw = task_def.get("dependsOn", [])
    if depends_on_raw is None:
        depends_on_raw = []
    if not isinstance(depends_on_raw, list):
        _fail(f"tasks.{task_name}.dependsOn must be a list")
    seen_dep: set[str] = set()
    normalized_deps: list[str] = []
    for dep in depends_on_raw:
        if not isinstance(dep, str) or not dep:
            _fail(f"tasks.{task_name}.dependsOn entries must be non-empty strings")
        if dep == task_name:
            _fail(f"task {task_name!r} depends on itself")
        if dep not in task_names:
            _fail(f"tasks.{task_name}.dependsOn references unknown task {dep!r}")
        if dep in seen_dep:
            _fail(f"tasks.{task_name}.dependsOn duplicates {dep!r}")
        seen_dep.add(dep)
        normalized_deps.append(dep)
    normalized_deps.sort()

    direct_deps = set(normalized_deps)
    context_from = _normalize_context_from(
        task_def.get("contextFrom"),
        task_name=task_name,
        direct_deps=direct_deps,
    )

    timeout_seconds = task_def.get("timeoutSeconds", defaults["timeoutSeconds"])
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        _fail(f"tasks.{task_name}.timeoutSeconds must be a positive number")

    workspace = task_def.get("workspace", defaults["workspace"])
    if workspace not in _WORKSPACES:
        _fail(f"tasks.{task_name}.workspace must be one of {sorted(_WORKSPACES)}")
    if mode == "write" and workspace == "shared":
        _fail(f"write task {task_name!r} cannot use workspace 'shared'")

    retry = _normalize_retry(task_def.get("retry"), task_name)
    verify = _normalize_verify(task_def.get("verify"), task_name)

    normalized_task = {
        "route": route,
        "mode": mode,
        "prompt": prompt,
        "dependsOn": normalized_deps,
        "contextFrom": context_from,
        "timeoutSeconds": timeout_seconds,
        "workspace": workspace,
        "retry": retry,
        "verify": verify,
    }
    # Decision 15: channel keys appear in the normalized task only when set.
    effective_ask_support = ask_support if ask_support is not None else defaults.get("askSupport")
    if effective_ask_support:
        normalized_task["askSupport"] = True
    if max_asks is not None:
        normalized_task["maxAsks"] = max_asks
    return normalized_task


def _normalize_defaults(raw_defaults: Any, registry_harnesses: Mapping[str, Any]) -> dict[str, Any]:
    if raw_defaults is None:
        return {
            "maxConcurrency": 2,
            "providerConcurrency": {},
            "timeoutSeconds": 1140,
            "workspace": "auto",
            "failurePolicy": "fail-fast",
        }
    if not isinstance(raw_defaults, Mapping):
        _fail("workflow.defaults must be an object")
    _require_keys(
        raw_defaults,
        "workflow.defaults",
        {
            "maxConcurrency",
            "providerConcurrency",
            "timeoutSeconds",
            "workspace",
            "failurePolicy",
            "askSupport",
            "autoAnswer",
        },
    )

    max_concurrency = raw_defaults.get("maxConcurrency", 2)
    if (
        isinstance(max_concurrency, bool)
        or not isinstance(max_concurrency, int)
        or max_concurrency < 1
    ):
        _fail("workflow.defaults.maxConcurrency must be a positive integer")

    harness_concurrency = raw_defaults.get("providerConcurrency", {})
    if harness_concurrency is None:
        harness_concurrency = {}
    if not isinstance(harness_concurrency, Mapping):
        _fail("workflow.defaults.providerConcurrency must be an object")
    normalized_harness_concurrency: dict[str, int] = {}
    for harness, value in harness_concurrency.items():
        if not isinstance(harness, str) or not harness:
            _fail("workflow.defaults.providerConcurrency keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail(f"workflow.defaults.providerConcurrency.{harness} must be a positive integer")
        if harness not in registry_harnesses:
            _fail(f"workflow.defaults.providerConcurrency names unknown harness {harness!r}")
        normalized_harness_concurrency[harness] = value
    normalized_harness_concurrency = dict(sorted(normalized_harness_concurrency.items()))

    timeout_seconds = raw_defaults.get("timeoutSeconds", 1140)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        _fail("workflow.defaults.timeoutSeconds must be a positive number")

    workspace = raw_defaults.get("workspace", "auto")
    if workspace not in _WORKSPACES:
        _fail(f"workflow.defaults.workspace must be one of {sorted(_WORKSPACES)}")

    failure_policy = raw_defaults.get("failurePolicy", "fail-fast")
    if failure_policy not in _FAILURE_POLICIES:
        _fail(f"workflow.defaults.failurePolicy must be one of {sorted(_FAILURE_POLICIES)}")

    ask_support = raw_defaults.get("askSupport")
    if ask_support is not None and not isinstance(ask_support, bool):
        _fail("workflow.defaults.askSupport must be a boolean")

    auto_answer = raw_defaults.get("autoAnswer")
    normalized_auto_answer: dict[str, Any] | None = None
    if auto_answer is not None:
        if not isinstance(auto_answer, Mapping):
            _fail("workflow.defaults.autoAnswer must be an object")
        _require_keys(auto_answer, "workflow.defaults.autoAnswer", {"route", "timeoutSeconds"})
        route_name = auto_answer.get("route")
        if not isinstance(route_name, str) or not re.fullmatch(
            r"[A-Za-z][A-Za-z0-9._-]{0,63}", route_name
        ):
            _fail("workflow.defaults.autoAnswer.route must name a route")
        timeout_seconds_auto = auto_answer.get("timeoutSeconds", 30)
        if (
            isinstance(timeout_seconds_auto, bool)
            or not isinstance(timeout_seconds_auto, (int, float))
            or not 1 <= timeout_seconds_auto <= 120
        ):
            _fail("workflow.defaults.autoAnswer.timeoutSeconds must be a number 1..120")
        normalized_auto_answer = {"route": route_name, "timeoutSeconds": timeout_seconds_auto}

    normalized_defaults = {
        "maxConcurrency": max_concurrency,
        "providerConcurrency": normalized_harness_concurrency,
        "timeoutSeconds": timeout_seconds,
        "workspace": workspace,
        "failurePolicy": failure_policy,
    }
    if ask_support:
        normalized_defaults["askSupport"] = True
    if normalized_auto_answer is not None:
        normalized_defaults["autoAnswer"] = normalized_auto_answer
    return normalized_defaults


def validate_workflow(
    data: Any,
    *,
    source_path: Path,
    repo_root: Path,
    registry: Mapping[str, Any],
    host: str,
) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalize a workflow document.

    ``source_path`` is the resolved path of the workflow JSON file; its parent
    directory anchors prompt file references. ``repo_root`` is accepted for
    API symmetry with ``load_registry`` but is not required for the current
    validation rules. Returns the normalized, deterministic workflow document
    plus a list of advisory warnings.
    """

    del repo_root  # currently unused; reserved for future contract checks

    warnings: list[str] = []

    if not isinstance(data, Mapping):
        _fail("workflow must be an object")

    if data.get("schemaVersion") != 1:
        _fail("workflow.schemaVersion must be 1")
    _require_keys(data, "workflow", {"schemaVersion", "name", "defaults", "tasks"})

    if host not in _HOSTS:
        _fail(f"workflow host must be one of {sorted(_HOSTS)}: {host!r}")

    name = data.get("name")
    if not isinstance(name, str) or not name:
        _fail("workflow.name must be a non-empty string")
    if len(name) > 128:
        _fail("workflow.name must be at most 128 characters")

    registry_harnesses = registry.get("harnesses", {}) if isinstance(registry, Mapping) else {}
    if not isinstance(registry_harnesses, Mapping):
        _fail("registry.harnesses must be an object")

    defaults = _normalize_defaults(data.get("defaults"), registry_harnesses)

    registry_hosts = registry.get("hosts", {}) if isinstance(registry, Mapping) else {}
    if not isinstance(registry_hosts, Mapping):
        _fail("registry.hosts must be an object")
    host_def = registry_hosts.get(host, {})
    native_harnesses: set[str] = set()
    if isinstance(host_def, Mapping):
        native = host_def.get("nativeHarnesses", [])
        if isinstance(native, list):
            native_harnesses = {entry for entry in native if isinstance(entry, str)}

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, Mapping) or not raw_tasks:
        _fail("workflow.tasks must be a non-empty object")

    workflow_dir = source_path.parent

    task_names: set[str] = set()
    for task_name in raw_tasks:
        if not isinstance(task_name, str) or _TASK_NAME_PATTERN.fullmatch(task_name) is None:
            _fail(f"task name {task_name!r} must match [A-Za-z][A-Za-z0-9_-]{{0,63}}")
        if task_name in task_names:
            _fail(f"workflow.tasks duplicates task name {task_name!r}")
        task_names.add(task_name)

    normalized_tasks: dict[str, dict[str, Any]] = {}
    for task_name in sorted(task_names):
        task_def = raw_tasks.get(task_name)
        normalized_tasks[task_name] = _normalize_task(
            task_name,
            task_def,
            defaults=defaults,
            workflow_dir=workflow_dir,
            registry_harnesses=registry_harnesses,
            native_harnesses=native_harnesses,
            host=host,
            warnings=warnings,
            task_names=task_names,
        )

    deps_for_cycle = {name: task["dependsOn"] for name, task in normalized_tasks.items()}
    cycle = _find_cycle(deps_for_cycle)
    if cycle:
        path_str = " -> ".join(cycle)
        _fail(f"dependency cycle detected: {path_str}")

    return (
        {
            "schemaVersion": 1,
            "name": name,
            "defaults": defaults,
            "tasks": normalized_tasks,
        },
        warnings,
    )


def load_workflow(
    path: Path,
    *,
    repo_root: Path,
    registry: Mapping[str, Any],
    host: str,
) -> dict[str, Any]:
    """Read, parse, validate, and normalize a workflow JSON document."""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkflowError(f"cannot read workflow file {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"workflow file {path} is not valid JSON: {exc}") from exc

    resolved = path.resolve()
    normalized, _warnings = validate_workflow(
        data,
        source_path=resolved,
        repo_root=repo_root,
        registry=registry,
        host=host,
    )
    return normalized


def workflow_digest(normalized: Mapping[str, Any]) -> str:
    """Return a deterministic lowercase SHA-256 of the normalized workflow."""

    canonical = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
