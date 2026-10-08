"""Owned native-child role profiles for the Tintin subagent backend (port of ``native-profile.ts``)."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # reason: PyYAML ships no type stubs

from pitwall.workbench.pi_pin import PINNED_SUBAGENTS_VERSION
from pitwall.workbench.profile import CompiledProfile, ProfileError, compile_profile
from pitwall.workbench.runtime_settings import enforce_runtime_settings, js_equal

PROFILE_FILE = "native-profile.json"
SCOUT_NAME = "workbench-scout"
MANAGED_NAMES = frozenset({"workbench-scout", "workbench-worker", "workbench-reviewer"})
PLANNING_TOOLS = ("read", "grep", "find", "ls")
NATIVE_BACKEND_SETTINGS: dict[str, Any] = {
    "schedulingEnabled": False,
    "workflowsEnabled": False,
    "agentMentions": "off",
    "disableDefaultAgents": True,
    "fallbackSubagent": False,
    "maxSubagentDepth": 0,
}
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.DOTALL | re.MULTILINE)


class _Yaml12Loader(yaml.SafeLoader):  # type: ignore[misc]  # reason: yaml is untyped, so its loader base is Any
    """SafeLoader with YAML 1.2 booleans: ``off``, ``on``, ``yes``, and ``no`` stay strings.

    Role files declare ``isolation: off``; Pi parses them with a YAML 1.2 reader.
    """


_Yaml12Loader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


class NativeProfileError(ProfileError):
    """A native profile file, role, or project setting conflicts with Workbench policy."""


@dataclass(frozen=True)
class NativeProfilePaths:
    profile_path: Path
    agent_path: Path
    env: dict[str, str]


def _yaml_problem(exc: Exception, first_line: int = 1) -> str:
    """A YAML error's class and position only: PyYAML's own text can quote the file."""
    name = type(exc).__name__
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        return f"invalid YAML ({name}) at line {mark.line + first_line}, column {mark.column + 1}"
    position = getattr(exc, "position", None)
    where = f" at character {position + 1}" if isinstance(position, int) else ""
    return f"invalid YAML ({name}){where}"


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Return the YAML frontmatter mapping, or an empty mapping when there is none."""
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}
    try:
        value = yaml.load(match.group(1), Loader=_Yaml12Loader)  # noqa: S506  # reason: _Yaml12Loader subclasses SafeLoader
    except (
        yaml.YAMLError,
        ValueError,
    ) as error:  # reason: PyYAML's timestamp constructor raises a plain ValueError
        problem = _yaml_problem(error, first_line=text.count("\n", 0, match.start(1)) + 1)
        raise NativeProfileError(f"invalid frontmatter: {problem}") from None
    return value if isinstance(value, dict) else {}


def _model(compiled: CompiledProfile) -> str:
    return json.dumps(f"{compiled.profile['provider']}/{compiled.profile['modelId']}")


def _scout_frontmatter(compiled: CompiledProfile) -> str:
    return f"""---
name: {SCOUT_NAME}
description: Read-only native scout
model: {_model(compiled)}
tools: read, grep, find, ls
extensions: false
skills: false
inherit_context: false
isolated: true
allowed_subagents: none
isolation: off
---
Perform the assigned bounded read-only investigation and report evidence.
"""


def _role_frontmatter(
    compiled: CompiledProfile, name: str, description: str, tools: str, planning: bool
) -> str:
    body = (
        "Planning policy: read-only investigation only. Do not edit, write, run shell commands, "
        "commit, reset, switch branches, or modify files."
        if planning
        else "Complete only the assigned bounded task. Do not commit, reset, switch branches, "
        "or modify files outside the supplied workspace."
    )
    return f"""---
name: {name}
description: {description}
model: {_model(compiled)}
tools: {tools}
extensions: false
skills: false
inherit_context: false
isolated: true
allowed_subagents: none
isolation: off
---
{body}
"""


def validate_planning_role_profiles(agent_dir: Path | str, expected_model: str) -> None:
    """Fail closed unless every managed role is read-only, isolated, and pinned to the model.

    The generated files are owned, but a profile can also be supplied directly through
    ``PITWALL_WORKBENCH_NATIVE_PROFILE``; ``planning`` alone must not make a writable role look safe.
    """
    agents_dir = Path(os.path.abspath(agent_dir)) / "agents"
    for name in ("workbench-scout", "workbench-worker", "workbench-reviewer"):
        path = agents_dir / f"{name}.md"
        try:
            if path.is_symlink():
                raise NativeProfileError("managed role must not be a symlink")
            text = path.read_text(encoding="utf-8")
        except (OSError, NativeProfileError) as error:
            raise NativeProfileError(
                f"planning native profile requires managed role {path}: {error}"
            ) from error
        try:
            frontmatter = parse_frontmatter(text)
        except NativeProfileError as error:
            raise NativeProfileError(
                f"planning native profile has invalid role {path}: {error}"
            ) from error
        tools = frontmatter.get("tools")
        actual = (
            [item.strip() for item in tools.split(",") if item.strip()]
            if isinstance(tools, str)
            else []
        )
        identity = frontmatter.get("name") == name and frontmatter.get("model") == expected_model
        safe = (
            identity
            and actual == list(PLANNING_TOOLS)
            and frontmatter.get("extensions") is False
            and frontmatter.get("skills") is False
            and frontmatter.get("inherit_context") is False
            and frontmatter.get("isolated") is True
            and frontmatter.get("allowed_subagents") == "none"
            and frontmatter.get("isolation") == "off"
        )
        if not identity:
            raise NativeProfileError(
                f"planning native profile role {path} must declare name {name} "
                f"and exact model {expected_model}"
            )
        if not safe:
            raise NativeProfileError(
                f"planning native profile role {path} must expose only read, grep, find, and ls "
                "with isolated context and no subagents"
            )


def _owned_file(path: Path, contents: str) -> None:
    try:
        if path.is_symlink():
            raise NativeProfileError(f"native profile file must not be a symlink: {path}")
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(contents)
        return
    if existing != contents:
        raise NativeProfileError(f"native profile file conflict: {path}")


def _reject_project_backend_conflicts(cwd: Path) -> None:
    path = cwd / ".pi" / "subagents.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError) as error:
        raise NativeProfileError(f"invalid project subagent settings: {error}") from error
    if not isinstance(raw, dict):
        raise NativeProfileError("invalid project subagent settings")
    for key in raw:
        if key not in NATIVE_BACKEND_SETTINGS:
            raise NativeProfileError(
                f"unknown project subagent setting {key}; "
                "Workbench native policy does not accept ignored backend keys"
            )
    for key, expected in NATIVE_BACKEND_SETTINGS.items():
        if key in raw and not js_equal(raw[key], expected):
            raise NativeProfileError(
                f"project subagent setting {key} conflicts with Workbench native policy"
            )


def _reject_agent_collisions(agent_dir: Path, cwd: Path, owned: set[Path], planning: bool) -> None:
    for directory in (agent_dir / "agents", cwd / ".pi" / "agents", cwd / ".agents" / "agents"):
        try:
            files = sorted(name for name in os.listdir(directory) if name.endswith(".md"))
        except FileNotFoundError:
            continue
        for file in files:
            path = directory / file
            if path in owned:
                continue
            if planning:
                raise NativeProfileError(f"planning native profile refuses unmanaged agent: {path}")
            declared = parse_frontmatter(path.read_text(encoding="utf-8")).get("name")
            declared = declared.strip() if isinstance(declared, str) else None
            if file[:-3] in MANAGED_NAMES or (declared and declared in MANAGED_NAMES):
                kind = (
                    "native scout"
                    if declared == SCOUT_NAME or file == f"{SCOUT_NAME}.md"
                    else "native role"
                )
                raise NativeProfileError(f"{kind} collision: {path}")


def configure_native_profile(
    compiled: CompiledProfile,
    cwd: Path | str | None = None,
    *,
    planning: bool = False,
) -> NativeProfilePaths:
    """Write the owned native profile and managed role files under ``compiled.agent_dir``.

    ``planning`` starts the coordinator and every native child with read-only tools.
    """
    project = Path(os.path.abspath(cwd if cwd is not None else os.getcwd()))
    agent_dir = Path(os.path.abspath(compiled.agent_dir))
    compiled = compile_profile(compiled.name, compiled.profile, agent_dir)
    enforce_runtime_settings(agent_dir, project, compiled.profile)
    _reject_project_backend_conflicts(project)
    agents_dir = agent_dir / "agents"
    try:
        if not stat.S_ISDIR(agents_dir.lstat().st_mode):
            raise NativeProfileError("native agents directory must be a real directory")
    except FileNotFoundError:
        agents_dir.mkdir(mode=0o700, parents=True)
    profile_path = agent_dir / PROFILE_FILE
    agent_path = agents_dir / f"{SCOUT_NAME}.md"
    worker_path = agents_dir / "workbench-worker.md"
    reviewer_path = agents_dir / "workbench-reviewer.md"
    _reject_agent_collisions(agent_dir, project, {agent_path, worker_path, reviewer_path}, planning)
    profile = {
        "schemaVersion": 1,
        "backend": "@tintinweb/pi-subagents",
        "version": PINNED_SUBAGENTS_VERSION,
        "profileName": compiled.name,
        **({"planning": True} if planning else {}),
        "profile": compiled.profile,
        "providerConfig": compiled.provider,
    }
    _owned_file(agent_dir / "subagents.json", json.dumps(NATIVE_BACKEND_SETTINGS, indent=2) + "\n")
    _owned_file(profile_path, json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
    _owned_file(agent_path, _scout_frontmatter(compiled))
    planning_tools = ", ".join(PLANNING_TOOLS)
    _owned_file(
        worker_path,
        _role_frontmatter(
            compiled,
            "workbench-worker",
            "Read-only planning worker"
            if planning
            else "Bounded writer in an isolated handoff workspace",
            planning_tools if planning else "read, edit, write, bash",
            planning,
        ),
    )
    _owned_file(
        reviewer_path,
        _role_frontmatter(
            compiled, "workbench-reviewer", "Read-only patch reviewer", planning_tools, planning
        ),
    )
    if planning:
        validate_planning_role_profiles(
            agent_dir, f"{compiled.profile['provider']}/{compiled.profile['modelId']}"
        )
    return NativeProfilePaths(
        profile_path=profile_path,
        agent_path=agent_path,
        env={"PITWALL_WORKBENCH_NATIVE_PROFILE": str(profile_path)},
    )
