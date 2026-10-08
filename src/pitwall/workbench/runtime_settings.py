"""Pi runtime settings enforcement (port of ``runtime-settings.ts``).

Workbench policy owns three Pi settings: no retries, derived compaction, and the HTTP
inactivity timeout. Project settings may restate the policy but never weaken it.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuntimeSettingsError(ValueError):
    """A settings file conflicts with Workbench policy or is not safe to rewrite."""


@dataclass(frozen=True)
class DerivedCompactionSettings:
    reserve_tokens: int
    keep_recent_tokens: int
    safety_reserve_tokens: int
    enabled: bool = True


def is_safe_integer(value: object) -> bool:
    """Mirror ``Number.isSafeInteger`` for JSON values."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        if not value.is_integer():
            return False
        value = int(value)
    return isinstance(value, int) and abs(value) <= 2**53 - 1


def js_equal(left: object, right: object) -> bool:
    """Strict equality where ``False`` is not ``0`` and ``True`` is not ``1``."""
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    return left == right


def safety_reserve_tokens(profile: Mapping[str, Any]) -> int:
    reserve = profile.get("contextReserveTokens")
    if reserve is not None:
        return int(reserve)
    return min(
        int(profile["maxCompletionTokens"]),
        max(1, int(profile["servedContextTokens"] * 0.1)),
    )


def derive_compaction_settings(profile: Mapping[str, Any]) -> DerivedCompactionSettings:
    served = int(profile["servedContextTokens"])
    completion = int(profile["maxCompletionTokens"])
    safety = safety_reserve_tokens(profile)
    reserve = completion + safety
    if reserve >= served:
        raise RuntimeSettingsError(
            "profile maxCompletionTokens plus context safety reserve must be below served context"
        )
    # Pi uses up to 80% of reserveTokens for the generated summary. Keep at most half of the
    # remaining context as recent history: the other half absorbs the system prompt, tool
    # schemas, and provider overhead, and leaves a real cut point for automatic compaction.
    summary_budget = min(int(reserve * 0.8), completion)
    remaining = served - reserve - summary_budget - safety
    keep_recent = min(20_000, remaining // 2)
    if keep_recent <= 0:
        raise RuntimeSettingsError(
            "profile leaves no room for recent context after compaction reserve"
        )
    return DerivedCompactionSettings(
        reserve_tokens=reserve, keep_recent_tokens=keep_recent, safety_reserve_tokens=safety
    )


def _check_project_retry(project: Mapping[str, Any]) -> None:
    conflict = "project retry settings conflict with Workbench no-retry policy"
    if "retry" not in project:
        return
    retry = project["retry"]
    if not isinstance(retry, dict):
        raise RuntimeSettingsError(conflict)
    if "enabled" in retry and retry["enabled"] is not False:
        raise RuntimeSettingsError(conflict)
    if "maxRetries" in retry and not js_equal(retry["maxRetries"], 0):
        raise RuntimeSettingsError(conflict)
    if "provider" in retry:
        provider = retry["provider"]
        if not isinstance(provider, dict):
            raise RuntimeSettingsError(conflict)
        if "maxRetries" in provider and not js_equal(provider["maxRetries"], 0):
            raise RuntimeSettingsError(conflict)


def _check_project_compaction(
    project: Mapping[str, Any], compaction: DerivedCompactionSettings
) -> None:
    if "compaction" not in project:
        return
    conflict = "project compaction settings conflict with Workbench profile policy"
    value = project["compaction"]
    if not isinstance(value, dict):
        raise RuntimeSettingsError(conflict)
    if "enabled" in value and value["enabled"] is not True:
        raise RuntimeSettingsError(conflict)
    if "reserveTokens" in value and not js_equal(value["reserveTokens"], compaction.reserve_tokens):
        raise RuntimeSettingsError(conflict)
    if "keepRecentTokens" in value and not js_equal(
        value["keepRecentTokens"], compaction.keep_recent_tokens
    ):
        raise RuntimeSettingsError(conflict)


def _read_json_file(path: Path) -> Any | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeSettingsError(f"invalid JSON in {path}: {error}") from error


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(mode):  # lstat: a symlink is not a regular file
        raise RuntimeSettingsError("runtime settings must be a regular file")
    settings = _read_json_file(path)
    if not isinstance(settings, dict):
        raise RuntimeSettingsError("invalid runtime settings")
    return settings


def _object_or_empty(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def enforce_runtime_settings(
    agent_dir: Path | str,
    cwd: Path | str | None = None,
    profile: Mapping[str, Any] | None = None,
) -> None:
    """Write no-retry, compaction, and HTTP idle policy into ``agent_dir/settings.json``."""
    agent = Path(agent_dir)
    path = agent / "settings.json"
    settings = _read_settings(path)
    idle_timeout = None
    if profile is not None:
        idle_timeout = profile.get("requestInactivityTimeoutMs", profile.get("requestTimeoutMs"))
    if cwd is not None:
        project = _read_json_file(Path(cwd) / ".pi" / "settings.json")
        if project is not None:
            if not isinstance(project, dict):
                raise RuntimeSettingsError("invalid project settings")
            _check_project_retry(project)
            if profile is not None:
                _check_project_compaction(project, derive_compaction_settings(profile))
            if (
                idle_timeout is not None
                and "httpIdleTimeoutMs" in project
                and not js_equal(project["httpIdleTimeoutMs"], idle_timeout)
            ):
                raise RuntimeSettingsError(
                    "project HTTP inactivity timeout conflicts with Workbench profile policy"
                )
    derived = derive_compaction_settings(profile) if profile is not None else None
    retry = _object_or_empty(settings.get("retry"))
    updated: dict[str, Any] = dict(settings)
    if idle_timeout is not None:
        updated["httpIdleTimeoutMs"] = idle_timeout
    if derived is not None:
        updated["compaction"] = {
            **_object_or_empty(settings.get("compaction")),
            "enabled": True,
            "reserveTokens": derived.reserve_tokens,
            "keepRecentTokens": derived.keep_recent_tokens,
        }
    updated["retry"] = {
        **retry,
        "enabled": False,
        "maxRetries": 0,
        "provider": {**_object_or_empty(retry.get("provider")), "maxRetries": 0},
    }
    if json.dumps(settings) == json.dumps(updated):
        return
    temporary = agent / f".settings-{uuid.uuid4()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(updated, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)
