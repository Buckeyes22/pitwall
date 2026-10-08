"""Accounts: a plan plus the login or key its usage is read with (stdlib only)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pitwall.providers.model_studio import catalog

from ..paths import codex_home
from ..profiles import EndpointReference, entry_harness, resolved_endpoint

PLAN_ORDER = (
    "claude",
    "codex",
    "glm",
    "minimax",
    "model-studio",
    "kimi",
    "grok",
    "muse",
    "gemini",
    "opencode-go",
)
LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "glm": "GLM",
    "minimax": "MiniMax",
    "model-studio": "Model Studio",
    "kimi": "Kimi",
    "grok": "Grok",
    "muse": "Muse",
    "gemini": "Gemini",
    "opencode-go": "OpenCode Go",
}
# plan -> (harness, login directory variable, default directory name, credential file)
LOGIN = {
    "claude": ("claude", "CLAUDE_CONFIG_DIR", ".claude", ".credentials.json"),
    "codex": ("codex", "CODEX_HOME", ".codex", "auth.json"),
}
# plan -> (key variable, entry in OpenCode's auth store)
KEYED = {
    "glm": ("GLM_API_KEY", "zai-coding-plan"),
    "minimax": ("MINIMAX_API_KEY", "minimax-coding-plan"),
}
# plan -> harness whose installation makes the plan present
HARNESS_ONLY = {"kimi": "kimi", "grok": "grok", "muse": "muse", "gemini": "agy"}
OPENCODE_GO_ENTRY = "opencode-go"
_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")


@dataclass(frozen=True, slots=True)
class Account:
    plan: str
    label: str
    routes: tuple[str, ...] = ()
    login_dir: Path | None = None
    endpoints: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    problem: str = ""


def expand(value: str, home: Path) -> Path:
    if value == "~":
        return home
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value)


def derived_label(plan: str, directory: Path) -> str:
    name = directory.name.lstrip(".")
    if name.lower().startswith(plan):
        name = name[len(plan) :]
    return _UNSAFE.sub("-", name).strip("-")[:16].strip("-") or "2"


def opencode_key(env: Mapping[str, str], home: Path, entry: str) -> str | None:
    base = Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else home / ".local" / "share"
    try:
        document = json.loads((base / "opencode" / "auth.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    record = document.get(entry) if isinstance(document, dict) else None
    key = record.get("key") if isinstance(record, dict) else None
    return key if isinstance(key, str) and key else None


def api_key(plan: str, env: Mapping[str, str], home: Path) -> str | None:
    variable, entry = KEYED[plan]
    return env.get(variable) or opencode_key(env, home, entry)


def _unique(label: str, used: set[str]) -> str:
    candidate, index = label, 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = label[: 16 - len(suffix)] + suffix
        index += 1
    used.add(candidate)
    return candidate


def _login_accounts(
    plan: str,
    routes_config: Mapping[str, Any],
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
) -> list[Account]:
    harness, variable, default_name, credential = LOGIN[plan]
    if plan == "codex":
        default_dir = codex_home(env, home)
    else:
        default_dir = expand(env[variable], home) if env.get(variable) else home / default_name
    default_label = ""
    default_routes: list[str] = []
    groups: dict[Path, dict[str, Any]] = {}
    models = routes_config.get("models", {})
    for name in sorted(models):
        entry = models[name]
        if entry_harness(entry, routes_config["defaults"], registry) != harness:
            continue
        value = entry.get("env", {}).get(variable)
        label = entry.get("account")
        directory = expand(value, home) if value else default_dir
        if directory == default_dir:
            if value:
                default_routes.append(name)
            if label and not default_label:
                default_label = str(label)
            continue
        group = groups.setdefault(directory, {"routes": [], "label": None})
        group["routes"].append(name)
        if label and group["label"] is None:
            group["label"] = str(label)
    found: list[Account] = []
    used: set[str] = set()
    if (default_dir / credential).is_file():
        label = _unique(default_label, used) if groups and default_label else ""
        found.append(Account(plan, label, tuple(default_routes), default_dir))
    seconds: list[Account] = []
    for directory, group in groups.items():
        routes = tuple(group["routes"])
        problem = ""
        if not directory.is_absolute():
            problem = f"route {routes[0]} names a login directory that is not an absolute path"
        elif not (directory / credential).is_file():
            problem = f"route {routes[0]} names a login directory with no login in it"
        label = _unique(group["label"] or derived_label(plan, directory), used)
        seconds.append(Account(plan, label, routes, directory, problem=problem))
    return found + sorted(seconds, key=lambda account: account.label)


def _token_plan_account(routes_config: Mapping[str, Any]) -> Account | None:
    endpoints: dict[str, Mapping[str, Any]] = {}
    routes: list[str] = []
    models = routes_config.get("models", {})
    for name in sorted(models):
        entry = models[name]
        endpoint = resolved_endpoint(entry)
        if endpoint is None or endpoint.get("kind") != catalog.KIND:
            continue
        if not catalog.is_token_plan(str(endpoint["plan"])):
            continue
        raw = entry.get("endpoint")
        endpoints.setdefault(str(raw) if isinstance(raw, EndpointReference) else name, endpoint)
        routes.append(name)
    for name in sorted(routes_config.get("endpoints", {})):
        endpoint = routes_config["endpoints"][name]
        if endpoint.get("kind") == catalog.KIND and catalog.is_token_plan(str(endpoint["plan"])):
            endpoints.setdefault(name, endpoint)
    if not endpoints:
        return None
    return Account("model-studio", "", tuple(routes), endpoints=tuple(sorted(endpoints.items())))


def discover(
    routes_config: Mapping[str, Any],
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
    *,
    installed: Callable[[str], bool],
) -> list[Account]:
    found: list[Account] = []
    for plan in PLAN_ORDER:
        if plan in LOGIN:
            found.extend(_login_accounts(plan, routes_config, registry, env, home))
        elif plan in KEYED:
            if api_key(plan, env, home):
                found.append(Account(plan, ""))
        elif plan == "model-studio":
            account = _token_plan_account(routes_config)
            if account is not None:
                found.append(account)
        elif plan in HARNESS_ONLY:
            if installed(HARNESS_ONLY[plan]):
                found.append(Account(plan, ""))
        elif opencode_key(env, home, OPENCODE_GO_ENTRY):
            found.append(Account(plan, ""))
    return found
