"""Policy document loading helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # reason: PyYAML ships no type stubs
from pydantic import ValidationError

from pitwall.policy.schema import Policy, PolicySet


class PolicyLoadError(ValueError):
    """Raised when a policy document cannot be parsed or validated."""


def load_policy_file(path: str | Path) -> PolicySet:
    """Load one JSON/YAML policy document from *path*."""

    policy_path = Path(path)
    text = policy_path.read_text(encoding="utf-8")
    return _load_policy_text(text, source=str(policy_path))


def load_policy_files(paths: Iterable[str | Path]) -> PolicySet:
    """Load and merge policy documents from *paths* in caller-provided order."""

    return merge_policy_sets(load_policy_file(path) for path in paths)


def load_default_policy_set() -> PolicySet:
    """Load the packaged example policies used by the audit gate."""

    examples = files("pitwall.policy").joinpath("examples")
    documents = [
        _load_policy_text(resource.read_text(encoding="utf-8"), source=resource.name)
        for resource in sorted(examples.iterdir(), key=lambda item: item.name)
        if resource.name.endswith((".json", ".yaml", ".yml"))
    ]
    return merge_policy_sets(documents)


def merge_policy_sets(policy_sets: Iterable[PolicySet]) -> PolicySet:
    """Merge policy documents into one deterministic policy set."""

    policies: list[Policy] = []
    version = 1
    for policy_set in policy_sets:
        version = max(version, policy_set.version)
        policies.extend(policy_set.policies)
    return PolicySet(version=version, policies=tuple(policies))


def _load_policy_text(text: str, *, source: str) -> PolicySet:
    try:
        payload = _parse_policy_payload(text)
    except (
        yaml.YAMLError,
        ValueError,
    ) as exc:  # reason: PyYAML's timestamp constructor raises a plain ValueError
        raise PolicyLoadError(f"{source}: invalid policy document: {_yaml_problem(exc)}") from None
    if not isinstance(payload, Mapping):
        raise PolicyLoadError(f"{source}: top-level policy document must be an object")
    try:
        return PolicySet.model_validate(payload)
    except ValidationError as exc:
        problems = _validation_problems(exc)
        raise PolicyLoadError(f"{source}: invalid policy document: {problems}") from None


def _validation_problems(exc: ValidationError) -> str:
    """``loc: msg`` lines; pydantic's own text includes input values, which can quote the file."""
    return "\n".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors(include_input=False, include_url=False)
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


def _parse_policy_payload(text: str) -> Any:
    """Parse a JSON or YAML policy document; YAML 1.2 flow syntax covers JSON."""
    payload = yaml.safe_load(text)
    return {} if payload is None else payload


__all__ = [
    "PolicyLoadError",
    "load_default_policy_set",
    "load_policy_file",
    "load_policy_files",
    "merge_policy_sets",
]
