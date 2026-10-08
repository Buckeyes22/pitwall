"""Deterministic preflight doctor for the `pitwall agents` runtime.

The default doctor validates only what the local, read-only environment can
prove. Model discovery is available solely through an explicit mode, never
mutates harness authentication or configuration, and degrades to warnings
when harness output changes.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

from .broker import load_sync, pitwall_api_token_requirement, resolve_pitwall_api_token
from .discovery import discover_models as run_model_discovery
from .harnesses import adapter_ids, get_adapter
from .http_urls import open_http_url
from .installation import (
    default_home,
    hook_problems,
    python_requirement,
    shim_names,
    shim_script,
    supported_python,
)
from .managed_channel import CHANNEL_PROBE_TIMEOUT_SECONDS
from .paths import ledger_path
from .profiles import ProfilesError, expiry_state, load_profiles, profiles_path
from .profiles_probe import probe_profile
from .profiles_resolve import describe_profile
from .registry import RegistryError, load_registry, validate_source_layout
from .resources import ResourceError, read_resource_json
from .route_assets import generated_files as generate_route_files
from .run_store import config_root, state_root

SCHEMA_VERSION = 1
VALID_STATUSES = ("PASS", "WARN", "FAIL", "SKIP")
RUNTIME_CATEGORY = "runtime"
HARNESS_CATEGORY = "provider"  # wire value in doctor JSON output
PLUGIN_CATEGORY = "plugin"
SECURITY_CATEGORY = "security"
ROUTES_CATEGORY = "routes"
CHANNEL_CATEGORY = "channel"

DRIFT_FRAGMENTS = (
    "registry and adapter implementations must match",
    "registry and adapter disagree",
)

DEFAULT_VERSION_TIMEOUT = 5.0
DEFAULT_HELP_TIMEOUT = 5.0
# Ceiling, in seconds from process launch, on one channel probe (both replies). The probe waits on
# the server's reply while the child is alive, so a cold start on a loaded host cannot false-WARN;
# a child that exits fails at once, and only a live-but-silent child reaches the ceiling. It is the
# managed-channel probe's hang guard (see the rationale there), so the two cannot drift.
CHANNEL_HANDSHAKE_TIMEOUT = CHANNEL_PROBE_TIMEOUT_SECONDS
CHANNEL_EXIT_POLL_SECONDS = 0.2
DEFAULT_OVERSIZED_STATE_BYTES = 200 * 1024 * 1024
DEFAULT_WORKTREE_BACKLOG_COUNT = 25
PITWALL_SYNC_STALE_SECONDS = 30 * 60
PITWALL_RECEIVER_DEFAULT_PORT = 8765
PITWALL_RECEIVER_TIMEOUT = 1.0
GIT_MIN_VERSION = (2, 20)
WORLD_BITS = 0o077
INSTALL_ENTRYPOINTS = shim_names()

HARNESS_HELP: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "agy": (("--help",), ("--print", "--output-format")),
    "codex": (("exec", "--help"), ("exec", "--skip-git-repo-check")),
    "cline": (("--help",), ("--auto-approve", "--provider")),
    "claude": (("--help",), ("--no-session-persistence", "--output-format")),
    "dsh": (("--help",), ("--profile", "--patch")),
    "goose": (("run", "--help"), ("--no-session", "--output-format")),
    # The installed grok parser accepts --no-auto-update while --help omits it,
    # so the contract invocation proves parser acceptance and only requires
    # flags that --help actually prints.
    "grok": (("--no-auto-update", "--help"), ("--output-format", "--prompt-file")),
    "hermes": (("--help",), ("-z", "--yolo")),
    "kimi": (("--help",), ("--prompt", "--output-format", "doctor", "provider")),
    "muse": (("exec", "--help"), ("--prompt-file", "--yolo")),
    "opencode": (("run", "--help"), ("--format",)),
    "pi": (("--help",), ("--no-session", "--list-models")),
    "qwen": (("--help",), ("--prompt", "--output-format")),
    "zcode": (("--help",), ("--prompt", "--mode", "--cwd")),
}


SKILL_DIR = "subagent-model-routing"  # the plugins' skill directory name, not a state path


@dataclass(slots=True, frozen=True)
class DoctorCheck:
    id: str
    category: str
    status: str
    summary: str
    remediation: str | None = None
    harness: str | None = None
    details: Mapping[str, Any] | None = None


@dataclass(slots=True)
class DoctorReport:
    schema_version: int = SCHEMA_VERSION
    tool: str = "pitwall agents doctor"
    generated_at: str = ""
    modes: dict[str, Any] = field(default_factory=dict)
    status: str = "pass"
    summary: dict[str, int] = field(
        default_factory=lambda: {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    )
    checks: list[DoctorCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schemaVersion": self.schema_version,
            "tool": self.tool,
            "generatedAt": self.generated_at,
            "modes": dict(self.modes),
            "status": self.status,
            "summary": dict(self.summary),
            "checks": [_check_to_dict(check) for check in self.checks],
        }
        return payload

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def _check_to_dict(check: DoctorCheck) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": check.id,
        "category": check.category,
        "status": check.status,
        "summary": check.summary,
    }
    if check.remediation is not None:
        payload["remediation"] = check.remediation
    if check.harness is not None:
        payload["provider"] = check.harness
    if check.details:
        payload["details"] = _freeze_details(check.details)
    return payload


def _freeze_details(details: Mapping[str, Any]) -> Any:
    if isinstance(details, Mapping):
        return {str(key): _freeze_details(value) for key, value in details.items()}
    if isinstance(details, (list, tuple)):
        return [_freeze_details(item) for item in details]
    if isinstance(details, (str, int, float, bool)) or details is None:
        return details
    return repr(details)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resolve_harness_filter(
    requested: str | None,
    *,
    available_harnesses: Iterable[str],
) -> str | None:
    if requested is None:
        return None
    if requested not in set(available_harnesses):
        raise ValueError(f"unknown harness filter: {requested!r}")
    return requested


def _status_from_counts(counts: Mapping[str, int]) -> str:
    if counts.get("fail", 0) > 0:
        return "fail"
    if counts.get("warn", 0) > 0:
        return "warn"
    return "pass"


def _is_drift_error(message: str) -> bool:
    return any(fragment in message for fragment in DRIFT_FRAGMENTS)


def _read_registry(repo_root: Path | None) -> tuple[dict[str, Any] | None, DoctorCheck | None]:
    path = (
        repo_root / "src/pitwall/agents/resources/config/harness-registry.json"
        if repo_root is not None
        else Path("<packaged:pitwall/agents/resources/config/harness-registry.json>")
    )
    try:
        data = load_registry(path if repo_root is not None else None)
        if repo_root is not None:
            validate_source_layout(data, repo_root=repo_root)
    except RegistryError as exc:
        return None, _classify_registry_error(exc, path=path)
    return data, None


def _classify_registry_error(exc: RegistryError, *, path: Path) -> DoctorCheck:
    message = str(exc)
    if _is_drift_error(message):
        return DoctorCheck(
            id="runtime.registry_drift",
            category=RUNTIME_CATEGORY,
            status="WARN",
            summary=f"harness registry/adapter drift detected: {message}",
            remediation=(
                "reconcile src/pitwall/agents/resources/config/harness-registry.json with "
                "the adapters in src/pitwall/agents/harnesses/ "
                f"so they describe the same harnesses, promptDelivery, and binaryOverrideEnv ({path})"
            ),
            details={"error": message, "path": str(path)},
        )
    return DoctorCheck(
        id="runtime.registry_valid",
        category=RUNTIME_CATEGORY,
        status="FAIL",
        summary=f"harness registry is invalid: {message}",
        remediation=(
            f"run `python3 tools/agents/validate_registry.py` to inspect {path}, fix the flagged field, "
            "and run `python3 tools/agents/sync_routes.py` if the regenerated hosts need updating"
        ),
        details={"error": message, "path": str(path)},
    )


def _check_python_version(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:

    info = sys.version_info
    version = f"{info.major}.{info.minor}.{info.micro}"
    if supported_python(info):
        return DoctorCheck(
            id="runtime.python",
            category=RUNTIME_CATEGORY,
            status="PASS",
            summary=f"Python {version} meets the {python_requirement()} requirement",
            details={"version": version},
        )
    return DoctorCheck(
        id="runtime.python",
        category=RUNTIME_CATEGORY,
        status="FAIL",
        summary=f"Python {version} is unsupported ({python_requirement()} required)",
        remediation=(
            "install Python 3.14 (for example via Homebrew or pyenv) "
            "and rerun pitwall agents doctor"
        ),
        details={"version": version},
    )


def _check_git(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    path_dir = env.get("PATH")
    binary = shutil.which("git", path=path_dir)
    if not binary:
        return DoctorCheck(
            id="runtime.git",
            category=RUNTIME_CATEGORY,
            status="FAIL",
            summary="git executable not found on PATH",
            remediation="install git (https://git-scm.com/downloads) and rerun pitwall agents doctor",
        )
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            env=dict(env),
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(
            id="runtime.git",
            category=RUNTIME_CATEGORY,
            status="WARN",
            summary=f"could not invoke git --version: {exc}",
            remediation="verify the git installation is functional before relying on isolated-worktree dispatch",
            details={"binary": binary},
        )
    output = result.stdout.decode("utf-8", errors="replace") or result.stderr.decode(
        "utf-8", errors="replace"
    )
    match = re.search(r"git version (\d+)\.(\d+)", output)
    version = (
        f"{match.group(1)}.{match.group(2)}"
        if match
        else output.strip().splitlines()[0]
        if output
        else "unknown"
    )
    if match and (int(match.group(1)), int(match.group(2))) >= GIT_MIN_VERSION:
        return DoctorCheck(
            id="runtime.git",
            category=RUNTIME_CATEGORY,
            status="PASS",
            summary=f"git {version} available via {binary}",
            details={"binary": binary, "version": version},
        )
    return DoctorCheck(
        id="runtime.git",
        category=RUNTIME_CATEGORY,
        status="WARN",
        summary=f"git {version} available but {GIT_MIN_VERSION[0]}.{GIT_MIN_VERSION[1]}+ recommended",
        remediation=f"upgrade git to {GIT_MIN_VERSION[0]}.{GIT_MIN_VERSION[1]} or newer for reliable isolated-worktree dispatch",
        details={"binary": binary, "version": version},
    )


def _check_state_dir(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    root = state_root(env)
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
        return DoctorCheck(
            id="runtime.state_dir_writable",
            category=RUNTIME_CATEGORY,
            status="FAIL",
            summary=f"state directory cannot be created or written through {existing}",
            remediation=(
                "ensure the parent directory exists and is writable by the current user "
                f"(nearest existing parent={existing}); set PITWALL_AGENTS_STATE_HOME to a writable path if needed"
            ),
            details={"path": str(root)},
        )
    try:
        if root.exists() and root.stat().st_mode & WORLD_BITS:
            return DoctorCheck(
                id="runtime.state_dir_writable",
                category=RUNTIME_CATEGORY,
                status="WARN",
                summary=f"state directory is readable by other users: {root}",
                remediation=f"chmod 700 {root} so dispatch artifacts stay private",
                details={"path": str(root), "mode": oct(root.stat().st_mode & 0o777)},
            )
    except OSError:
        pass
    return DoctorCheck(
        id="runtime.state_dir_writable",
        category=RUNTIME_CATEGORY,
        status="PASS",
        summary=f"state directory is writable or creatable at {root}",
        details={"path": str(root)},
    )


def _check_ledger_parent(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    target = ledger_path(env)
    parent = target.parent
    existing = parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK | os.X_OK):
        return DoctorCheck(
            id="runtime.ledger_parent_writable",
            category=RUNTIME_CATEGORY,
            status="FAIL",
            summary=f"ledger parent directory cannot be created or written through {existing}",
            remediation=(
                "ensure the ledger parent directory exists and is writable; "
                "override PITWALL_AGENTS_LEDGER to a writable path if the default location is blocked"
            ),
            details={"path": str(parent)},
        )
    return DoctorCheck(
        id="runtime.ledger_parent_writable",
        category=RUNTIME_CATEGORY,
        status="PASS",
        summary=f"ledger parent directory is writable or creatable at {parent}",
        details={"path": str(parent)},
    )


def _check_generated_routes(
    repo_root: Path, env: Mapping[str, str], registry: Mapping[str, Any]
) -> DoctorCheck:
    generated = generate_route_files(dict(registry), root=repo_root)
    mismatches: list[dict[str, str]] = []
    for path, expected in generated.items():
        if not path.is_file():
            mismatches.append({"path": str(path), "issue": "missing"})
            continue
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError as exc:
            mismatches.append({"path": str(path), "issue": f"unreadable: {exc}"})
            continue
        if actual != expected:
            mismatches.append({"path": str(path), "issue": "content mismatch"})
    if mismatches:
        return DoctorCheck(
            id="runtime.generated_routes",
            category=RUNTIME_CATEGORY,
            status="WARN",
            summary=f"{len(mismatches)} generated route file(s) are out of sync with the registry",
            remediation="run `python3 tools/agents/sync_routes.py` to refresh the generated host-specific route assets",
            details={"mismatches": mismatches},
        )
    return DoctorCheck(
        id="runtime.generated_routes",
        category=RUNTIME_CATEGORY,
        status="PASS",
        summary=f"{len(generated)} generated route file(s) match the registry",
        details={"files": len(generated)},
    )


def _check_install_links(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    """Every shim `pitwall agents install` writes is present, executable, and unmodified."""

    scripts = default_home(env) / ".claude" / "scripts"
    missing: list[str] = []
    not_executable: list[str] = []
    misdirected: list[str] = []
    for name in shim_names():
        target = scripts / name
        if not target.is_file():
            missing.append(str(target))
            continue
        if not os.access(target, os.X_OK):
            not_executable.append(str(target))
        try:
            unmodified = target.read_text(encoding="utf-8") == shim_script(name)
        except OSError, UnicodeDecodeError:
            unmodified = False
        if not unmodified:
            misdirected.append(str(target))
    if missing or not_executable or misdirected:
        return DoctorCheck(
            id="runtime.install_links",
            category=RUNTIME_CATEGORY,
            status="WARN",
            summary="installed shims are not all present, executable, and unmodified",
            remediation="run `pitwall agents install` to rewrite the shims under ~/.claude/scripts",
            details={
                "missing": missing,
                "notExecutable": not_executable,
                "misdirected": misdirected,
            },
        )
    return DoctorCheck(
        id="runtime.install_links",
        category=RUNTIME_CATEGORY,
        status="PASS",
        summary=f"all {len(shim_names())} installed shims are present, executable, and unmodified",
    )


def _check_registered_hooks(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    """A registered plugin hook needs a `pitwall` command that can be run."""

    problems = hook_problems(env, default_home(env))
    if problems:
        return DoctorCheck(
            id="runtime.registered_hooks",
            category=RUNTIME_CATEGORY,
            status="FAIL",
            summary=problems[0],
            remediation=(
                "the steering gate blocks tool calls while its CLI cannot run: type "
                "`! uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` "  # noqa: E501  # reason: one-line URL so the release validator checks its version
                "at the prompt, or disable the plugin with /plugin"
            ),
            details={"problems": problems},
        )
    return DoctorCheck(
        id="runtime.registered_hooks",
        category=RUNTIME_CATEGORY,
        status="PASS",
        summary="registered plugin hooks (if any) call a runnable pitwall command",
    )


def _check_hooks_config(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    path = config_root(env) / "hooks.json"
    if not path.exists():
        return DoctorCheck(
            id="runtime.hooks_config",
            category=RUNTIME_CATEGORY,
            status="SKIP",
            summary=f"no portable hooks.json configured at {path}",
            remediation=(
                f"create {path} with an empty object (`{{}}`) if you want portable dispatch lifecycle hooks; "
                "see docs/lifecycle-hooks.md"
            ),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return DoctorCheck(
            id="runtime.hooks_config",
            category=RUNTIME_CATEGORY,
            status="FAIL",
            summary=f"hooks.json is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            remediation=f"repair or remove {path}; hooks are fail-open but malformed JSON disables them",
            details={"path": str(path)},
        )
    if not isinstance(data, dict):
        return DoctorCheck(
            id="runtime.hooks_config",
            category=RUNTIME_CATEGORY,
            status="FAIL",
            summary="hooks.json must be an object mapping event names to hook definitions",
            remediation=f"rewrite {path} as a JSON object (see examples/lifecycle-hooks.json)",
            details={"path": str(path)},
        )
    return DoctorCheck(
        id="runtime.hooks_config",
        category=RUNTIME_CATEGORY,
        status="PASS",
        summary=f"hooks.json at {path} parses cleanly ({len(data)} event(s))",
        details={"events": sorted(data)},
    )


def _check_harness_binary_resolved(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
) -> DoctorCheck:
    home = Path(env.get("HOME", "~")).expanduser()
    adapter = get_adapter(harness_id)
    binary = adapter.resolve_binary(env, home)
    resolved = (
        None
        if binary is None
        else binary
        if os.path.isabs(binary) and os.path.isfile(binary)
        else shutil.which(binary, path=env.get("PATH"))
    )
    if resolved:
        return DoctorCheck(
            id=f"harness.{harness_id}.binary_resolved",
            category=HARNESS_CATEGORY,
            status="PASS",
            summary=f"{harness_id} binary resolves to {resolved}",
            harness=harness_id,
            details={"binary": resolved},
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.binary_resolved",
        category=HARNESS_CATEGORY,
        status="WARN",
        summary=f"{harness_id} executable is not on PATH and no override is set",
        remediation=(
            f"install the {harness_id} CLI or set {adapter.binary_override_env or 'BIN_OVERRIDE'} "
            "to a valid executable path before dispatching work"
        ),
        harness=harness_id,
    )


def _check_harness_executable_bit(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    home = Path(env.get("HOME", "~")).expanduser()
    adapter = get_adapter(harness_id)
    binary = adapter.resolve_binary(env, home)
    if binary is None:
        return DoctorCheck(
            id=f"harness.{harness_id}.executable_bit",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"cannot check executable bit for {harness_id}: binary unresolved",
            harness=harness_id,
        )
    resolved = binary if os.path.isabs(binary) else shutil.which(binary, path=env.get("PATH"))
    if resolved is None or not os.path.isfile(resolved):
        return DoctorCheck(
            id=f"harness.{harness_id}.executable_bit",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"{harness_id} binary does not resolve to a file: {binary}",
            remediation=(
                f"set {adapter.binary_override_env or 'BIN_OVERRIDE'} to an existing executable, "
                "or unset it to fall back to PATH lookup"
            ),
            harness=harness_id,
            details={"binary": binary},
        )
    if os.access(resolved, os.X_OK):
        return DoctorCheck(
            id=f"harness.{harness_id}.executable_bit",
            category=HARNESS_CATEGORY,
            status="PASS",
            summary=f"{harness_id} binary at {resolved} has the executable bit set",
            harness=harness_id,
            details={"binary": resolved},
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.executable_bit",
        category=HARNESS_CATEGORY,
        status="WARN",
        summary=f"{harness_id} binary at {resolved} is not executable",
        remediation=f"run `chmod +x {resolved}` or restore a real executable at that path",
        harness=harness_id,
        details={"binary": resolved},
    )


def _check_harness_cli_contract(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
) -> DoctorCheck:
    """Inspect only local help/version surfaces; never query models or auth."""
    home = Path(env.get("HOME", "~")).expanduser()
    binary = get_adapter(harness_id).resolve_binary(env, home)
    if binary is None:
        return DoctorCheck(
            id=f"harness.{harness_id}.cli_contract",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"cannot inspect {harness_id} help/version because its binary is unresolved",
            harness=harness_id,
        )
    spec = HARNESS_HELP.get(harness_id)
    if spec is None:
        return DoctorCheck(
            id=f"harness.{harness_id}.cli_contract",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"{harness_id} declares no help contract in HARNESS_HELP",
            remediation="add the harness's help arguments and expected flags to doctor.HARNESS_HELP",
            harness=harness_id,
        )
    help_args, required = spec
    child_env = dict(env)
    child_env.update({"NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0"})
    outputs: dict[str, str] = {}
    for label, arguments in (("version", ("--version",)), ("help", help_args)):
        try:
            result = subprocess.run(
                [binary, *arguments],
                capture_output=True,
                env=child_env,
                timeout=DEFAULT_HELP_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return DoctorCheck(
                id=f"harness.{harness_id}.cli_contract",
                category=HARNESS_CATEGORY,
                status="WARN",
                summary=f"{harness_id} local {label} inspection failed: {exc}",
                remediation=f"run `{binary} {' '.join(arguments)}` and verify the installed CLI",
                harness=harness_id,
            )
        outputs[label] = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        if result.returncode != 0:
            return DoctorCheck(
                id=f"harness.{harness_id}.cli_contract",
                category=HARNESS_CATEGORY,
                status="WARN",
                summary=f"{harness_id} local {label} command exited {result.returncode}",
                remediation=f"run `{binary} {' '.join(arguments)}` and verify the installed CLI",
                harness=harness_id,
                details={"exitCode": result.returncode},
            )
    missing = [fragment for fragment in required if fragment not in outputs["help"]]
    if missing:
        return DoctorCheck(
            id=f"harness.{harness_id}.cli_contract",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"{harness_id} help output is missing expected flag(s): {', '.join(missing)}",
            remediation="upgrade the harness CLI or compare its current help with the adapter arguments before dispatching",
            harness=harness_id,
            details={"version": outputs["version"].strip()[:256], "missing": missing},
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.cli_contract",
        category=HARNESS_CATEGORY,
        status="PASS",
        summary=f"{harness_id} local version/help contract is available",
        harness=harness_id,
        details={"version": outputs["version"].strip()[:256]},
    )


def _check_harness_binary_override(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    adapter = get_adapter(harness_id)
    override_env = adapter.binary_override_env
    if not override_env:
        return DoctorCheck(
            id=f"harness.{harness_id}.binary_override_env",
            category=HARNESS_CATEGORY,
            status="PASS",
            summary=f"{harness_id} does not declare a binary override environment variable",
            harness=harness_id,
        )
    if env.get(override_env):
        return DoctorCheck(
            id=f"harness.{harness_id}.binary_override_env",
            category=HARNESS_CATEGORY,
            status="PASS",
            summary=f"{override_env} is set for {harness_id}",
            harness=harness_id,
            details={"overrideEnv": override_env, "value": env[override_env]},
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.binary_override_env",
        category=HARNESS_CATEGORY,
        status="SKIP",
        summary=f"{override_env} is unset for {harness_id}",
        harness=harness_id,
        details={"overrideEnv": override_env},
    )


def _check_harness_default_model(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    harness = registry["harnesses"][harness_id]
    default = harness.get("defaultModel", {})
    source = default.get("source")
    fallback = default.get("fallback")
    models = harness.get("models", {})
    if source == "registry":
        if fallback in models:
            return DoctorCheck(
                id=f"harness.{harness_id}.default_model",
                category=HARNESS_CATEGORY,
                status="PASS",
                summary=f"{harness_id} default model {fallback!r} is a known registry model",
                harness=harness_id,
                details={"defaultModel": fallback},
            )
        if harness.get("allowUnknownModels"):
            return DoctorCheck(
                id=f"harness.{harness_id}.default_model",
                category=HARNESS_CATEGORY,
                status="WARN",
                summary=f"{harness_id} default model {fallback!r} is not present in the registry models map",
                remediation=(
                    f"register {fallback!r} under harnesses.{harness_id}.models or pick a different fallback "
                    "so the registry/contract is closed-loop"
                ),
                harness=harness_id,
                details={"defaultModel": fallback},
            )
        return DoctorCheck(
            id=f"harness.{harness_id}.default_model",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"{harness_id} default model {fallback!r} is unknown and the harness does not allow unknown models",
            remediation=(
                f"add {fallback!r} to harnesses.{harness_id}.models in the packaged harness registry, "
                "or set allowUnknownModels=true to preserve pass-through"
            ),
            harness=harness_id,
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.default_model",
        category=HARNESS_CATEGORY,
        status="PASS",
        summary=f"{harness_id} default model source is {source!r}",
        harness=harness_id,
        details={"source": source, "fallback": fallback},
    )


def _check_harness_contract_drift(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    adapter = get_adapter(harness_id)
    harness = registry["harnesses"][harness_id]
    drift: list[str] = []
    expected_delivery = harness.get("promptDelivery")
    if adapter.prompt_delivery != expected_delivery:
        drift.append(
            f"promptDelivery: adapter={adapter.prompt_delivery!r}, registry={expected_delivery!r}"
        )
    expected_override = harness.get("binaryOverrideEnv")
    if adapter.binary_override_env != expected_override:
        drift.append(
            f"binaryOverrideEnv: adapter={adapter.binary_override_env!r}, registry={expected_override!r}"
        )
    if drift:
        return DoctorCheck(
            id=f"harness.{harness_id}.contract_drift",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"{harness_id} adapter and registry disagree about contract fields",
            remediation=(
                f"update src/pitwall/agents/harnesses/{harness_id}.py (harness_id, prompt_delivery, "
                "binary_override_env) or the matching packaged harness-registry entry so they describe the same contract"
            ),
            harness=harness_id,
            details={"drift": drift},
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.contract_drift",
        category=HARNESS_CATEGORY,
        status="PASS",
        summary=f"{harness_id} adapter matches registry contract fields",
        harness=harness_id,
    )


def _check_harness_effort_compat(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    harness = registry["harnesses"][harness_id]
    effort = harness.get("effort", {})
    if effort.get("kind") == "none":
        return DoctorCheck(
            id=f"harness.{harness_id}.effort_compat",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"{harness_id} exposes no per-invocation effort control",
            harness=harness_id,
        )
    values = effort.get("values", [])
    if not values:
        return DoctorCheck(
            id=f"harness.{harness_id}.effort_compat",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"{harness_id} effort values are harness-defined (no registry list to validate)",
            harness=harness_id,
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.effort_compat",
        category=HARNESS_CATEGORY,
        status="PASS",
        summary=f"{harness_id} effort values: {', '.join(values)}",
        harness=harness_id,
        details={"values": list(values)},
    )


def _check_harness_config_probe(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
    subprocess_runner: Callable[..., Any] | None = None,
) -> DoctorCheck:
    del repo_root
    harness = registry["harnesses"][harness_id]
    capabilities = harness.get("capabilities", {})
    if not capabilities.get("configProbe"):
        local_source = _local_configuration_source(harness_id, env)
        return DoctorCheck(
            id=f"harness.{harness_id}.config_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"{harness_id} does not declare a read-only configuration probe",
            harness=harness_id,
            details={
                "readiness": "configured" if local_source == "environment metadata" else "unknown",
                "metadataPresent": bool(local_source),
                "evidence": (
                    "known non-empty credential environment variable is present; no documented configuration probe"
                    if local_source == "environment metadata"
                    else "opaque local metadata is present; no documented configuration probe"
                    if local_source
                    else "no documented configuration probe or local metadata"
                ),
            },
        )
    home = Path(env.get("HOME", "~")).expanduser()
    adapter = get_adapter(harness_id)
    binary = adapter.resolve_binary(env, home)
    if binary is None:
        return DoctorCheck(
            id=f"harness.{harness_id}.config_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"no {harness_id} binary to run the configuration probe",
            harness=harness_id,
            remediation=f"install the {harness_id} CLI or set the appropriate override",
            details={"readiness": "absent", "evidence": "harness executable not found"},
        )
    probe_argv = _DOCUMENTED_CONFIG_PROBES.get(harness_id)
    if probe_argv is None:
        local_source = _local_configuration_source(harness_id, env)
        return DoctorCheck(
            id=f"harness.{harness_id}.config_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"no documented read-only configuration probe is defined for {harness_id}",
            harness=harness_id,
            details={
                "readiness": "configured" if local_source == "environment metadata" else "unknown",
                "metadataPresent": bool(local_source),
                "evidence": (
                    "known non-empty credential environment variable is present; no documented configuration probe"
                    if local_source == "environment metadata"
                    else "opaque local metadata is present; no documented configuration probe"
                    if local_source
                    else "no documented configuration probe or local metadata"
                ),
            },
        )
    argv = [binary, *probe_argv]
    runner = subprocess_runner or subprocess.run
    try:
        result = runner(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(
            id=f"harness.{harness_id}.config_probe",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"configuration probe for {harness_id} failed to execute: {exc}",
            remediation=f"run `{' '.join(argv)}` manually and repair the harness configuration",
            harness=harness_id,
            details={"readiness": "unknown", "evidence": "configuration probe did not complete"},
        )
    exit_code = getattr(result, "returncode", None)
    if exit_code == 0:
        return DoctorCheck(
            id=f"harness.{harness_id}.config_probe",
            category=HARNESS_CATEGORY,
            status="PASS",
            summary=f"{harness_id} read-only configuration probe succeeded",
            harness=harness_id,
            details={
                "exitCode": 0,
                "argv": argv,
                "readiness": "configured",
                "evidence": "documented local configuration probe succeeded",
            },
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.config_probe",
        category=HARNESS_CATEGORY,
        status="WARN",
        summary=f"{harness_id} configuration probe exited {exit_code}",
        remediation=f"run `{' '.join(argv)}` manually and repair the harness configuration",
        harness=harness_id,
        details={
            "exitCode": exit_code,
            "argv": argv,
            "readiness": "unknown",
            "evidence": "configuration probe reported a failure",
        },
    )


def _check_harness_auth_probe(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
    live_auth: bool,
    subprocess_runner: Callable[..., Any] | None = None,
) -> DoctorCheck:
    del repo_root
    if harness_id == "agy":
        return _check_agy_auth_probe(env, live_auth=live_auth, subprocess_runner=subprocess_runner)
    return _check_declared_auth_probe(
        env,
        harness_id=harness_id,
        registry=registry,
        live_auth=live_auth,
        subprocess_runner=subprocess_runner,
    )


def _check_agy_auth_probe(
    env: Mapping[str, str],
    *,
    live_auth: bool,
    subprocess_runner: Callable[..., Any] | None = None,
) -> DoctorCheck:
    local_source = _local_configuration_source("agy", env)
    remediation = (
        "agy has no status-only probe; consult its harness setup documentation separately, "
        "then pass `--live-auth` only when you explicitly authorize one bounded inference request"
    )
    if not live_auth:
        return DoctorCheck(
            id="harness.agy.auth_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=(
                "agy status-only authentication check is unavailable; pass --live-auth "
                "to explicitly send one bounded pong inference request"
            ),
            remediation=remediation,
            harness="agy",
            details={
                "readiness": "configured" if local_source == "environment metadata" else "unknown",
                "metadataPresent": bool(local_source),
                "evidence": (
                    "known non-empty credential environment variable is present; agy has no documented "
                    "local authentication-status command"
                    if local_source == "environment metadata"
                    else "opaque local metadata is present; agy has no documented local "
                    "authentication-status command"
                    if local_source
                    else "agy has no documented local authentication-status command"
                ),
                "inference": "not requested",
            },
        )
    binary = get_adapter("agy").resolve_binary(
        env,
        Path(env.get("HOME", "~")).expanduser(),
    )
    if binary is None:
        return DoctorCheck(
            id="harness.agy.auth_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary="no agy binary is available for the live pong check",
            remediation=remediation,
            harness="agy",
            details={"readiness": "absent", "evidence": "harness executable not found"},
        )
    argv = [
        binary,
        "--model",
        "gemini-3.8-flash",
        "--effort",
        "medium",
        "--add-dir",
        str(Path.cwd()),
        "--print-timeout",
        "30s",
        "--output-format",
        "text",
        "-p",
        "Reply with exactly: pong",
    ]
    runner = subprocess_runner or subprocess.run
    try:
        result = runner(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            timeout=45.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(
            id="harness.agy.auth_probe",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"agy live pong check failed to execute: {exc}",
            remediation=remediation,
            harness="agy",
            details={
                "readiness": "unknown",
                "evidence": "explicit inference request did not complete",
            },
        )
    passed = (
        result.returncode == 0
        and result.stdout.decode("utf-8", errors="replace").strip().lower() == "pong"
    )
    return DoctorCheck(
        id="harness.agy.auth_probe",
        category=HARNESS_CATEGORY,
        status="PASS" if passed else "WARN",
        summary=(
            "agy live pong check succeeded"
            if passed
            else f"agy live pong check exited {result.returncode} without exact pong output"
        ),
        remediation=None if passed else remediation,
        harness="agy",
        details={
            "exitCode": result.returncode,
            "argv": argv[:-1] + ["<prompt>"],
            "readiness": "verified-request" if passed else "unknown",
            "evidence": (
                "explicit bounded pong inference request succeeded"
                if passed
                else "explicit bounded pong inference request did not return exact pong"
            ),
            "inference": "requested",
        },
    )


def _check_declared_auth_probe(
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
    live_auth: bool,
    subprocess_runner: Callable[..., Any] | None = None,
) -> DoctorCheck:
    harness = registry["harnesses"][harness_id]
    capabilities = harness.get("capabilities", {})
    if not capabilities.get("authProbe"):
        local_source = _local_configuration_source(harness_id, env)
        return DoctorCheck(
            id=f"harness.{harness_id}.auth_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"{harness_id} does not declare authProbe in the registry",
            harness=harness_id,
            details={
                "readiness": "configured" if local_source == "environment metadata" else "unknown",
                "metadataPresent": bool(local_source),
                "evidence": (
                    "known non-empty credential environment variable is present; no documented authentication probe"
                    if local_source == "environment metadata"
                    else "opaque local metadata is present; no documented authentication probe"
                    if local_source
                    else "no documented authentication probe or local metadata"
                ),
            },
        )
    if not live_auth:
        local_source = _local_configuration_source(harness_id, env)
        return DoctorCheck(
            id=f"harness.{harness_id}.auth_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"{harness_id} auth probe skipped (pass --live-auth to enable)",
            remediation=(
                f"rerun pitwall agents doctor --harness {harness_id} --live-auth to invoke the documented "
                f"read-only {harness_id} authentication probe"
            ),
            harness=harness_id,
            details={
                "readiness": "configured" if local_source == "environment metadata" else "unknown",
                "metadataPresent": bool(local_source),
                "evidence": (
                    "known non-empty credential environment variable is present; authentication status probe was not requested"
                    if local_source == "environment metadata"
                    else "opaque local metadata is present; authentication status probe was not requested"
                    if local_source
                    else "authentication status probe was not requested"
                ),
                "inference": "not requested",
            },
        )
    return _run_documented_auth_probe(harness_id, env, subprocess_runner)


def _run_documented_auth_probe(
    harness_id: str,
    env: Mapping[str, str],
    subprocess_runner: Callable[..., Any] | None,
) -> DoctorCheck:
    home = Path(env.get("HOME", "~")).expanduser()
    adapter = get_adapter(harness_id)
    binary = adapter.resolve_binary(env, home)
    runner = subprocess_runner or subprocess.run
    if binary is None:
        return DoctorCheck(
            id=f"harness.{harness_id}.auth_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=f"no {harness_id} binary to probe; install the CLI or set the override to a real executable",
            harness=harness_id,
            remediation=f"install the {harness_id} CLI or set the appropriate override",
            details={"readiness": "absent", "evidence": "harness executable not found"},
        )
    probe_argv = _documented_auth_probe(harness_id)
    if probe_argv is None:
        return DoctorCheck(
            id=f"harness.{harness_id}.auth_probe",
            category=HARNESS_CATEGORY,
            status="SKIP",
            summary=(
                f"no documented read-only auth probe is defined for {harness_id}; "
                "skipping to avoid mutating harness configuration"
            ),
            remediation=(
                f"consult the {harness_id} documentation for the canonical read-only auth command and "
                "add it to doctor._DOCUMENTED_AUTH_PROBES if appropriate"
            ),
            harness=harness_id,
            details={"readiness": "unknown", "evidence": "no documented authentication probe"},
        )
    argv = [binary, *probe_argv]
    try:
        result = runner(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            timeout=10.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DoctorCheck(
            id=f"harness.{harness_id}.auth_probe",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"auth probe for {harness_id} failed to execute: {exc}",
            remediation=(f"verify the {harness_id} CLI is functional; the probe argv was {argv!r}"),
            harness=harness_id,
            details={"readiness": "unknown", "evidence": "authentication probe did not complete"},
        )
    exit_code = getattr(result, "returncode", None)
    if exit_code == 0:
        status, summary = "PASS", f"{harness_id} read-only auth probe succeeded"
    else:
        status, summary = (
            "WARN",
            (f"{harness_id} read-only auth probe exited {exit_code}; the probe argv was {argv!r}"),
        )
    return DoctorCheck(
        id=f"harness.{harness_id}.auth_probe",
        category=HARNESS_CATEGORY,
        status=status,
        summary=summary,
        remediation=(
            f"inspect the {harness_id} CLI's authentication status and rerun "
            "pitwall agents doctor --live-auth; unknown does not imply logged out"
            if status != "PASS"
            else None
        ),
        harness=harness_id,
        details={
            "exitCode": exit_code,
            "argv": argv,
            "readiness": "signed-in" if status == "PASS" else "unknown",
            "evidence": (
                "documented local authentication-status probe succeeded"
                if status == "PASS"
                else "authentication-status probe reported a failure"
            ),
        },
    )


_DOCUMENTED_AUTH_PROBES: dict[str, tuple[str, ...]] = {
    "claude": ("auth", "status"),
    "codex": ("login", "status"),
}

_DOCUMENTED_CONFIG_PROBES: dict[str, tuple[str, ...]] = {
    "kimi": ("doctor", "config"),
}

# These are metadata locations only. Doctor checks existence and non-empty
# size, never parses or serializes their contents. Presence is reported as
# metadata only; it cannot establish `configured`, identity, or server
# acceptance.
_LOCAL_CONFIGURATION_PATHS: dict[str, tuple[str, ...]] = {
    "cline": (".cline/data/settings/providers.json",),
    "codex": (".codex/auth.json",),
    "dsh": (".dsh/settings.yaml",),
    "goose": (".config/goose/config.yaml",),
    "grok": (".grok/auth.json",),
    "hermes": (".hermes/auth.json", ".hermes/.env"),
    "kimi": (".kimi-code/credentials/kimi-code.json", ".kimi-code/config.toml"),
    "muse": (".config/muse/auth.json",),
    "opencode": (".local/share/opencode/auth.json",),
    "pi": (".pi/agent/auth.json",),
    "qwen": (".qwen/settings.json", ".qwen/.env"),
}

_LOCAL_CONFIGURATION_ENV: dict[str, tuple[str, ...]] = {
    "agy": ("GEMINI_API_KEY",),
    "grok": ("XAI_API_KEY",),
}


def _local_configuration_source(harness_id: str, env: Mapping[str, str]) -> str | None:
    """Return a safe metadata marker without reading credential contents.

    Opaque files are deliberately metadata only: their presence does not
    establish that a credential is configured.  The environment markers are
    the small set of known non-empty API-key variables that support a
    ``configured`` result.
    """
    for name in _LOCAL_CONFIGURATION_ENV.get(harness_id, ()):
        if env.get(name):
            return "environment metadata"
    home = Path(env.get("HOME", "~")).expanduser()
    for relative in _LOCAL_CONFIGURATION_PATHS.get(harness_id, ()):
        path = home / relative
        try:
            if path.is_file() and path.stat().st_size > 0:
                return "opaque local metadata"
            if path.is_dir() and any(path.iterdir()):
                return "opaque local metadata"
        except OSError:
            continue
    return None


def _documented_auth_probe(harness_id: str) -> tuple[str, ...] | None:
    return _DOCUMENTED_AUTH_PROBES.get(harness_id)


def _check_plugin_marketplace(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    host_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    hosts = registry.get("hosts", {})
    host = hosts.get(host_id)
    if host is None:
        return DoctorCheck(
            id=f"plugin.{host_id}.marketplace_present",
            category=PLUGIN_CATEGORY,
            status="SKIP",
            summary=f"no host entry for {host_id} in the registry",
            harness=None,
        )
    package_path = repo_root / host["packagePath"]
    if not package_path.is_dir():
        return DoctorCheck(
            id=f"plugin.{host_id}.marketplace_present",
            category=PLUGIN_CATEGORY,
            status="WARN",
            summary=f"{host_id} host package directory missing at {package_path}",
            remediation=(
                f"restore plugins/{package_path.name} (host={host_id}) from the upstream checkout, "
                "or remove the host from the packaged harness registry"
            ),
            details={"path": str(package_path)},
        )
    package_id = package_path.name
    manifest: Path | None = None
    candidates = [
        package_path / ".claude-plugin" / "plugin.json",
        package_path / ".codex-plugin" / "plugin.json",
        package_path / "plugin.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            manifest = candidate
            break
    if manifest is None:
        return DoctorCheck(
            id=f"plugin.{host_id}.marketplace_present",
            category=PLUGIN_CATEGORY,
            status="WARN",
            summary=f"{host_id} package has no plugin manifest under {package_path}",
            remediation=(
                f"create one of `plugin.json`, `.claude-plugin/plugin.json`, or `.codex-plugin/plugin.json` "
                f"inside {package_id} so the host can load the routing skills"
            ),
        )
    try:
        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return DoctorCheck(
            id=f"plugin.{host_id}.marketplace_present",
            category=PLUGIN_CATEGORY,
            status="FAIL",
            summary=f"{host_id} plugin manifest is not valid JSON: {exc.msg}",
            remediation=f"repair {manifest} so it parses as JSON",
            details={"path": str(manifest)},
        )
    name = manifest_data.get("name") if isinstance(manifest_data, dict) else None
    version = manifest_data.get("version") if isinstance(manifest_data, dict) else None
    if not name or not version:
        return DoctorCheck(
            id=f"plugin.{host_id}.marketplace_present",
            category=PLUGIN_CATEGORY,
            status="FAIL",
            summary=f"{host_id} manifest must declare name and version",
            remediation=f"add `name` and `version` fields to {manifest}",
        )
    return DoctorCheck(
        id=f"plugin.{host_id}.marketplace_present",
        category=PLUGIN_CATEGORY,
        status="PASS",
        summary=f"{host_id} package found at {package_id} (name={name}, version={version})",
        details={"manifest": str(manifest), "name": name, "version": version},
    )


def _check_plugin_native_host_exclusions(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    host_id: str,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    host = registry.get("hosts", {}).get(host_id)
    if host is None:
        return DoctorCheck(
            id=f"plugin.{host_id}.native_host_exclusions",
            category=PLUGIN_CATEGORY,
            status="SKIP",
            summary=f"no host entry for {host_id} in the registry",
        )
    package_path = repo_root / host["packagePath"]
    native = [n for n in host.get("nativeHarnesses", []) if n]
    if not package_path.is_dir():
        return DoctorCheck(
            id=f"plugin.{host_id}.native_host_exclusions",
            category=PLUGIN_CATEGORY,
            status="SKIP",
            summary=f"{host_id} package not present; native-host exclusion check is moot",
        )
    if not native:
        return DoctorCheck(
            id=f"plugin.{host_id}.native_host_exclusions",
            category=PLUGIN_CATEGORY,
            status="PASS",
            summary=f"{host_id} declares no native harnesses; no exclusion needed",
        )
    return DoctorCheck(
        id=f"plugin.{host_id}.native_host_exclusions",
        category=PLUGIN_CATEGORY,
        status="PASS",
        summary=f"{host_id} native harnesses: {', '.join(native)} (excluded from generated routes)",
        details={"nativeHarnesses": list(native)},
    )


def _check_plugin_runtime_reference_shared(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    bundles: dict[str, Path] = {}
    for host_id, host in registry.get("hosts", {}).items():
        package_path = (
            repo_root
            / host["packagePath"]
            / "skills"
            / SKILL_DIR
            / "references"
            / "model-prompting.md"
        )
        if not package_path.is_file():
            continue
        bundles[host_id] = package_path
    if len(bundles) < 2:
        return DoctorCheck(
            id="plugin.runtime_reference_shared",
            category=PLUGIN_CATEGORY,
            status="SKIP",
            summary="fewer than two host bundles present; cannot compare the package-local model-prompting.md",
            details={"bundles": {key: str(value) for key, value in bundles.items()}},
        )
    reference = None
    mismatches: list[dict[str, str]] = []
    for host_id, path in sorted(bundles.items()):
        try:
            content = path.read_bytes()
        except OSError as exc:
            mismatches.append({"host": host_id, "path": str(path), "issue": f"unreadable: {exc}"})
            continue
        if reference is None:
            reference = content
            continue
        if content != reference:
            mismatches.append(
                {"host": host_id, "path": str(path), "issue": "content differs from reference host"}
            )
    if mismatches:
        return DoctorCheck(
            id="plugin.runtime_reference_shared",
            category=PLUGIN_CATEGORY,
            status="WARN",
            summary="package-local model-prompting.md is not byte-identical across hosts",
            remediation=(
                "sync the package-local references/model/model-prompting.md files so they share the canonical "
                "content; rerun pitwall agents doctor after a release-aligned regeneration"
            ),
            details={"mismatches": mismatches},
        )
    return DoctorCheck(
        id="plugin.runtime_reference_shared",
        category=PLUGIN_CATEGORY,
        status="PASS",
        summary=f"{len(bundles)} package-local model-prompting.md files are byte-identical",
        details={"hosts": sorted(bundles)},
    )


def _check_plugin_version_alignment(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    versions: dict[str, str] = {}
    missing: list[str] = []
    for host_id, host in registry.get("hosts", {}).items():
        package_path = repo_root / host["packagePath"]
        manifest: Path | None = None
        for candidate in (
            package_path / ".claude-plugin" / "plugin.json",
            package_path / ".codex-plugin" / "plugin.json",
            package_path / "plugin.json",
        ):
            if candidate.is_file():
                manifest = candidate
                break
        if manifest is None:
            missing.append(host_id)
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            missing.append(host_id)
            continue
        version = data.get("version") if isinstance(data, dict) else None
        if not version:
            missing.append(host_id)
            continue
        versions[host_id] = str(version)
    if not versions:
        return DoctorCheck(
            id="plugin.version_alignment",
            category=PLUGIN_CATEGORY,
            status="WARN",
            summary="no host package manifests could be read; cannot compare versions",
        )
    unique = set(versions.values())
    if len(unique) > 1:
        return DoctorCheck(
            id="plugin.version_alignment",
            category=PLUGIN_CATEGORY,
            status="WARN",
            summary=f"host package versions are out of sync: {versions}",
            remediation=(
                "bump the lagging packages so all host plugins share the same version; "
                "rerun pitwall agents doctor after a coordinated release"
            ),
            details={"versions": versions},
        )
    return DoctorCheck(
        id="plugin.version_alignment",
        category=PLUGIN_CATEGORY,
        status="PASS",
        summary=f"all host package versions align on {next(iter(unique))}",
        details={"versions": versions, "missing": missing},
    )


def _check_security_unrestricted_mode(
    repo_root: Path,
    env: Mapping[str, str],
) -> DoctorCheck:
    value = env.get("PITWALL_AGENTS_UNRESTRICTED")
    if value is None:
        return DoctorCheck(
            id="security.unrestricted_mode",
            category=SECURITY_CATEGORY,
            status="PASS",
            summary=(
                "PITWALL_AGENTS_UNRESTRICTED is unset; harness CLIs keep their own "
                "sandbox and approval policy (restricted)"
            ),
            details={"value": None},
        )
    if value == "1":
        return DoctorCheck(
            id="security.unrestricted_mode",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary="PITWALL_AGENTS_UNRESTRICTED=1 bypasses harness sandboxes and approval prompts",
            remediation=(
                "unset PITWALL_AGENTS_UNRESTRICTED (or set it to 0) for restricted execution, "
                "or keep the explicit opt-in if the wider sandbox is required"
            ),
            details={"value": value},
        )
    if value == "0":
        return DoctorCheck(
            id="security.unrestricted_mode",
            category=SECURITY_CATEGORY,
            status="PASS",
            summary="PITWALL_AGENTS_UNRESTRICTED=0 keeps restricted harness flags",
            details={"value": value},
        )
    return DoctorCheck(
        id="security.unrestricted_mode",
        category=SECURITY_CATEGORY,
        status="WARN",
        summary=f"PITWALL_AGENTS_UNRESTRICTED={value!r} is not a recognized value",
        remediation="set PITWALL_AGENTS_UNRESTRICTED to 0 (restricted) or 1 (unrestricted)",
        details={"value": value},
    )


def _world_readable_files(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for filename in filenames:
            candidate = Path(dirpath) / filename
            try:
                mode = candidate.stat().st_mode
            except OSError:
                continue
            if mode & WORLD_BITS:
                found.append(str(candidate))
                continue
        if dirnames:
            for dirname in dirnames:
                candidate = Path(dirpath) / dirname
                try:
                    mode = candidate.stat().st_mode
                except OSError:
                    continue
                if mode & WORLD_BITS:
                    found.append(str(candidate))
    return found


def _check_security_world_readable_state(
    repo_root: Path,
    env: Mapping[str, str],
) -> DoctorCheck:
    root = state_root(env) / "runs"
    if not root.is_dir():
        return DoctorCheck(
            id="security.state_files_world_readable",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary=f"no run directory yet at {root}; world-readability probe deferred until first dispatch",
        )
    exposed = _world_readable_files(root)
    if exposed:
        return DoctorCheck(
            id="security.state_files_world_readable",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"{len(exposed)} state artifact(s) are readable by other users",
            remediation=(
                "run `chmod 700` on dispatch directories and `chmod 600` on the contained files; "
                "rerun `pitwall agents doctor` after rotating permissions"
            ),
            details={"paths": exposed[:25]},
        )
    return DoctorCheck(
        id="security.state_files_world_readable",
        category=SECURITY_CATEGORY,
        status="PASS",
        summary="no world-readable state artifacts under the runs directory",
    )


def _check_security_world_readable_hooks(
    repo_root: Path,
    env: Mapping[str, str],
) -> DoctorCheck:
    path = config_root(env) / "hooks.json"
    if not path.exists():
        return DoctorCheck(
            id="security.hooks_world_readable",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary=f"no hooks.json at {path}; nothing to mask",
        )
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        return DoctorCheck(
            id="security.hooks_world_readable",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"cannot stat hooks.json: {exc}",
            remediation=f"verify file accessibility at {path}",
        )
    if mode & WORLD_BITS:
        return DoctorCheck(
            id="security.hooks_world_readable",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"hooks.json is readable by other users ({oct(mode & 0o777)})",
            remediation=f"run `chmod 600 {path}` so hook definitions stay private",
            details={"path": str(path), "mode": oct(mode & 0o777)},
        )
    return DoctorCheck(
        id="security.hooks_world_readable",
        category=SECURITY_CATEGORY,
        status="PASS",
        summary="hooks.json is private",
        details={"path": str(path)},
    )


def _check_security_retained_prompts(
    repo_root: Path,
    env: Mapping[str, str],
) -> DoctorCheck:
    root = state_root(env) / "runs"
    if not root.is_dir():
        return DoctorCheck(
            id="security.retained_prompts",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary="no run directory yet; retained-prompt scan deferred",
        )
    retained: list[str] = []
    for run_dir in root.iterdir():
        candidate = run_dir / "prompt.md"
        if candidate.is_file():
            try:
                if candidate.stat().st_size > 0:
                    retained.append(str(candidate))
            except OSError:
                continue
    if not retained:
        return DoctorCheck(
            id="security.retained_prompts",
            category=SECURITY_CATEGORY,
            status="PASS",
            summary="no retained prompt files under the runs directory",
        )
    return DoctorCheck(
        id="security.retained_prompts",
        category=SECURITY_CATEGORY,
        status="WARN",
        summary=f"{len(retained)} retained prompt file(s) under the runs directory",
        remediation=(
            "reminder: prompt bodies are only retained when --routing-retain-prompt is explicit; "
            "delete with `pitwall agents runs cleanup --all` if you want them removed"
        ),
        details={"paths": retained[:25]},
    )


def _check_security_oversized_state(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    max_bytes: int = DEFAULT_OVERSIZED_STATE_BYTES,
) -> DoctorCheck:
    root = state_root(env)
    if not root.is_dir():
        return DoctorCheck(
            id="security.oversized_state",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary=f"no state directory yet at {root}; size probe deferred",
        )
    total = 0
    breakdown: dict[str, int] = {}
    for sub in ("runs",):
        candidate = root / sub
        if not candidate.is_dir():
            continue
        bytes_total = 0
        for dirpath, _dirnames, filenames in os.walk(candidate):
            for filename in filenames:
                try:
                    bytes_total += (Path(dirpath) / filename).stat().st_size
                except OSError:
                    continue
        breakdown[sub] = bytes_total
        total += bytes_total
    if total > max_bytes:
        return DoctorCheck(
            id="security.oversized_state",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"state tree consumes {total} bytes (threshold {max_bytes})",
            remediation=(
                "rotate old runs with `pitwall agents runs cleanup --older-than DAYS` or "
                "`pitwall agents runs cleanup --all` to reclaim disk space"
            ),
            details={"bytes": total, "threshold": max_bytes, "byArea": breakdown},
        )
    return DoctorCheck(
        id="security.oversized_state",
        category=SECURITY_CATEGORY,
        status="PASS",
        summary=f"state tree consumes {total} bytes (threshold {max_bytes})",
        details={"bytes": total, "byArea": breakdown},
    )


def _check_security_shell_wrapper_hooks(
    repo_root: Path,
    env: Mapping[str, str],
) -> DoctorCheck:
    path = config_root(env) / "hooks.json"
    if not path.exists():
        return DoctorCheck(
            id="security.shell_wrapper_hooks",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary=f"no hooks.json at {path}; shell-wrapper probe moot",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DoctorCheck(
            id="security.shell_wrapper_hooks",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary="hooks.json is not valid JSON; hook-shape audit deferred until it parses",
        )
    findings: list[str] = []
    shell_names = ("bash", "sh", "zsh", "fish", "dash", "ksh", "csh")
    if isinstance(data, dict):
        for event, definitions in data.items():
            if not isinstance(definitions, list):
                continue
            for index, definition in enumerate(definitions):
                if not isinstance(definition, dict):
                    continue
                command = definition.get("command")
                if not isinstance(command, list) or not command:
                    continue
                head = command[0]
                if isinstance(head, str) and Path(head).name in shell_names:
                    findings.append(f"{event}[{index}] -> {head}")
    if findings:
        return DoctorCheck(
            id="security.shell_wrapper_hooks",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"{len(findings)} hook definition(s) invoke a shell directly",
            remediation=(
                "prefer argv-style hook commands (e.g. `/usr/bin/python3 -c ...`) so quoting and "
                "argument parsing are deterministic; rewrite shell wrappers before the next release"
            ),
            details={"hooks": findings},
        )
    return DoctorCheck(
        id="security.shell_wrapper_hooks",
        category=SECURITY_CATEGORY,
        status="PASS",
        summary="no hook definitions invoke a shell directly",
    )


def _check_security_worktree_cleanup_backlog(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    threshold: int = DEFAULT_WORKTREE_BACKLOG_COUNT,
) -> DoctorCheck:
    parent = state_root(env).parent
    if not parent.is_dir():
        return DoctorCheck(
            id="security.worktree_cleanup_backlog",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary=f"state root parent {parent} does not exist; worktree probe deferred",
        )
    worktrees_root = state_root(env) / "worktrees"
    if not worktrees_root.is_dir():
        return DoctorCheck(
            id="security.worktree_cleanup_backlog",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary="no worktrees directory yet; backlog probe deferred",
        )
    backlog = [path for path in worktrees_root.iterdir() if path.is_dir()]
    if len(backlog) > threshold:
        return DoctorCheck(
            id="security.worktree_cleanup_backlog",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"{len(backlog)} worktree entries exceed cleanup threshold ({threshold})",
            remediation=(
                "inspect the owning run and use `pitwall agents runs discard <id> --yes`; "
                "worktrees are never removed automatically"
            ),
            details={"count": len(backlog)},
        )
    return DoctorCheck(
        id="security.worktree_cleanup_backlog",
        category=SECURITY_CATEGORY,
        status="PASS",
        summary=f"{len(backlog)} worktree entries (threshold {threshold})",
        details={"count": len(backlog)},
    )


def _channel_reply(line: bytes | None) -> dict[str, Any] | None:
    """The JSON-RPC object on *line*; None at end of output or for a non-object line (a failure)."""
    if line is None:
        return None
    reply = json.loads(line)
    return reply if isinstance(reply, dict) else None


def _probe_channel_server(
    env: Mapping[str, str],
    dispatch_id: str | None,
    *,
    modern: bool = False,
    failure: list[str] | None = None,
) -> list[str] | None:
    """The tool names a fresh `pitwall-channel` server lists for *dispatch_id*, or None on any failure.

    *modern* probes the 2026-07-28 era (`server/discover`, then `tools/list`); the default probes
    the legacy `initialize` handshake. When the probe fails and *failure* is given, the first
    reason is appended to it: "exited with code N before replying", "no reply within the N s
    ceiling", or "protocol error: <fixed description>" (never raw server output).
    """

    def fail(reason: str) -> None:
        if failure is not None and not failure:
            failure.append(reason)

    import pitwall.agents

    process_env = {**os.environ, **env}
    process_env["PYTHONPATH"] = str(Path(pitwall.agents.__file__).resolve().parent.parent.parent)
    if dispatch_id is None:
        process_env.pop("PITWALL_AGENTS_CHANNEL_DISPATCH_ID", None)
    else:
        process_env["PITWALL_AGENTS_CHANNEL_DISPATCH_ID"] = dispatch_id
    process: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "pitwall.agents", "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=process_env,
        )
        assert process.stdin is not None and process.stdout is not None
        output: Queue[bytes | None] = Queue(maxsize=16)

        def read_output() -> None:
            assert process is not None and process.stdout is not None
            try:
                while True:
                    line = process.stdout.readline()
                    if not line:
                        output.put_nowait(None)
                        return
                    output.put_nowait(line)
            except Full, OSError, ValueError:
                # A noisy or already-closed child is an unsuccessful probe.
                return

        reader = threading.Thread(target=read_output, name="doctor-mcp-reader", daemon=True)
        reader.start()

        def send(message: dict[str, Any]) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            process.stdin.flush()

        def reply_result(reply: dict[str, Any] | None) -> dict[str, Any] | None:
            """The reply's `result` object, or None when it is missing or not an object."""
            result = reply.get("result") if reply is not None else None
            return result if isinstance(result, dict) else None

        deadline = time.monotonic() + CHANNEL_HANDSHAKE_TIMEOUT

        def child_exited() -> None:
            """Record why a child that stopped answering is gone."""
            assert process is not None
            try:
                code = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                fail("closed its output before replying")
            else:
                fail(f"exited with code {code} before replying")

        def receive(request_id: int) -> dict[str, Any] | None:
            """The reply to *request_id*; None (with a recorded reason) on any failure."""
            assert process is not None
            exited = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fail(f"no reply within the {CHANNEL_HANDSHAKE_TIMEOUT:g} s ceiling")
                    return None
                try:
                    line = output.get(timeout=min(remaining, CHANNEL_EXIT_POLL_SECONDS))
                except Empty:
                    if exited:
                        child_exited()  # a full poll slice after the exit drained nothing more
                        return None
                    exited = process.poll() is not None  # one more slice lets the reader drain
                    continue
                if line is None:
                    child_exited()
                    return None
                reply = _channel_reply(line)
                if reply is None:
                    fail("protocol error: a reply line was not a JSON object")
                    return None
                if reply.get("id") == request_id:
                    return reply

        if modern:
            meta = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "doctor", "version": "0"},
            }
            send(
                {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": meta}}
            )
            discovered = receive(1)
            discovery = reply_result(discovered)
            if discovery is None or "2026-07-28" not in discovery.get("supportedVersions", []):
                if discovered is not None:
                    fail("protocol error: server/discover did not offer 2026-07-28")
                return None
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": meta}})
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "doctor", "version": "0"},
                    },
                }
            )
            if receive(1) is None:
                return None
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = receive(2)
        listing = reply_result(listed)
        if listing is None:
            if listed is not None:
                fail("protocol error: tools/list reply had no result object")
            return None
        tools = listing.get("tools", [])
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            fail("protocol error: tools/list result was not a list of tool objects")
            return None
        return [str(tool.get("name")) for tool in tools]
    except OSError, ValueError, KeyError, TypeError, json.JSONDecodeError:
        fail("protocol error: the server could not be launched or sent an unreadable reply")
        return None
    finally:
        if process is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()  # type: ignore[union-attr]  # reason: stdin is a pipe because the process was opened with stdin=PIPE
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            with contextlib.suppress(OSError):
                process.stdout.close()  # type: ignore[union-attr]  # reason: stdout is a pipe because the process was opened with stdout=PIPE
            if reader is not None:
                reader.join(timeout=5)


def _claude_launch_guard_state(env: Mapping[str, str]) -> tuple[bool | None, str]:
    config = Path(
        env.get("CLAUDE_CONFIG_DIR") or Path(env.get("HOME", "~")) / ".claude"
    ).expanduser()
    if not config.is_dir():
        return None, "no Claude Code configuration found"
    try:
        installed = json.loads(
            (config / "plugins/installed_plugins.json").read_text(encoding="utf-8")
        )
        settings = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return False, "the pitwall Claude plugin is not installed"
    for key, entries in (installed.get("plugins") or {}).items():
        if not key.startswith("pitwall@"):
            continue
        if (settings.get("enabledPlugins") or {}).get(key) is not True:
            return False, f"the {key} Claude plugin is installed but not enabled"
        for entry in entries or []:
            try:
                hooks = json.loads(
                    (Path(entry["installPath"]) / "hooks/hooks.json").read_text(encoding="utf-8")
                )
            except KeyError, OSError, TypeError, json.JSONDecodeError:
                continue
            for group in (hooks.get("hooks") or {}).get("PreToolUse", []):
                if any(
                    "launch-guard.py" in str(h.get("command", "")) for h in group.get("hooks", [])
                ):
                    return True, f"{key} registers the PreToolUse launch guard"
        return False, f"the {key} Claude plugin has no PreToolUse launch guard"
    return False, "the pitwall Claude plugin is not installed"


def _channel_checks(env: Mapping[str, str], registry: Mapping[str, Any]) -> list[DoctorCheck]:
    from .capability_inventory import CHANNEL_HARNESSES, CHANNEL_SERVER_NAME, mcp_channel_registered
    from .mcp_registration import current_entry
    from .setup import resolve_harness_binary

    checks: list[DoctorCheck] = []
    subagent_id = "00000000-0000-4000-8000-000000000000"
    # The four probes are independent, so they run together: a silent server costs one ceiling,
    # not four. Results are read in submission order, which keeps the details deterministic.
    probe_specs = ((None, False), (subagent_id, False), (None, True), (subagent_id, True))
    probe_failures: list[list[str]] = [[] for _ in probe_specs]
    with ThreadPoolExecutor(max_workers=len(probe_specs)) as pool:
        futures = [
            pool.submit(_probe_channel_server, env, did, modern=modern, failure=failure)
            for (did, modern), failure in zip(probe_specs, probe_failures, strict=True)
        ]
        probed = [future.result() for future in futures]
    orchestrator_tools, subagent_tools, orchestrator_tools_modern, subagent_tools_modern = probed
    orchestrator_expected = {
        "inbox",
        "answer_ask",
        "dispatch_and_wait",
        "answer_and_wait",
        "wait_dispatch",
        "steer_and_wait",
    }
    subagent_expected = {"ask_orchestrator", "read_steering", "ack_steer"}
    expected_tools = (
        orchestrator_expected,
        subagent_expected,
        orchestrator_expected,
        subagent_expected,
    )
    probe_labels = (
        "orchestrator (legacy)",
        "subagent (legacy)",
        "orchestrator (2026-07-28)",
        "subagent (2026-07-28)",
    )
    reasons: list[str | None] = []
    for tools, expected, failure in zip(probed, expected_tools, probe_failures, strict=True):
        if tools is None:
            reasons.append(failure[0] if failure else "the probe failed")
        elif set(tools) != expected:
            reasons.append("unexpected tool list")
        else:
            reasons.append(None)
    handshake_ok = all(reason is None for reason in reasons)
    failed = "; ".join(
        f"{label}: {reason}"
        for label, reason in zip(probe_labels, reasons, strict=True)
        if reason is not None
    )
    checks.append(
        DoctorCheck(
            id="channel.mcp_server",
            category=CHANNEL_CATEGORY,
            status="PASS" if handshake_ok else "WARN",
            summary=(
                "pitwall-channel answers the 2026-07-28 discover/tools path and the legacy handshake "
                "with the orchestrator and subagent tools"
                if handshake_ok
                else f"the pitwall-channel MCP server did not answer with the expected per-role tools ({failed})"
            ),
            remediation=None
            if handshake_ok
            else "check `pitwall mcp serve channel` output on stderr; tier 4 (the file contract) still works without it",
            details={
                "orchestratorTools": orchestrator_tools,
                "subagentTools": subagent_tools,
                "orchestratorToolsModern": orchestrator_tools_modern,
                "subagentToolsModern": subagent_tools_modern,
                "orchestratorFailure": reasons[0],
                "subagentFailure": reasons[1],
                "orchestratorFailureModern": reasons[2],
                "subagentFailureModern": reasons[3],
                "handshakeCeilingSeconds": CHANNEL_HANDSHAKE_TIMEOUT,
            },
        )
    )
    checks.append(
        DoctorCheck(
            id="channel.interactive_delivery",
            category=CHANNEL_CATEGORY,
            status="SKIP",
            summary="default doctor does not test live parent-child message delivery",
            remediation="verify an installed Claude parent receives and answers a real child ask without calling inbox",
            details={"registrationIsNotDelivery": True},
        )
    )
    available, reason = _claude_launch_guard_state(env)
    if available is None:
        launch_status, launch_summary = "SKIP", reason
    elif available:
        launch_status, launch_summary = (
            "PASS",
            f"launch guard installed: {reason}; default doctor does not exercise a live denial",
        )
    else:
        launch_status, launch_summary = "WARN", f"launch enforcement unavailable: {reason}"
    checks.append(
        DoctorCheck(
            id="channel.launch_enforcement",
            category=CHANNEL_CATEGORY,
            status=launch_status,
            summary=launch_summary,
            remediation="install and enable the pitwall Claude plugin (`claude plugin install pitwall@pitwall-local`)",
            details={"enforcementAvailable": available, "enforcementVerified": False},
        )
    )
    home = Path(env.get("HOME", "~")).expanduser()
    for harness in CHANNEL_HARNESSES:
        if harness == "copilot":
            installed = shutil.which("copilot", path=env.get("PATH")) is not None
        else:
            installed = resolve_harness_binary(harness, env, home) is not None
        if not installed:
            continue
        entry = current_entry(harness, env, home)
        command = entry.get("command") if isinstance(entry, Mapping) else None
        if isinstance(command, list):  # OpenCode keeps argv in one list
            command = command[0] if command else None
        executable = (
            isinstance(command, str)
            and bool(command)
            and os.path.isabs(command)
            and os.path.isfile(command)
            and os.access(command, os.X_OK)
        )
        registered = mcp_channel_registered(harness, env, home)
        ok = registered and executable
        setup = f"pitwall agents setup mcp --harness {harness}"
        unreadable = None if registered else _yaml_channel_config_error(harness, env, home)
        remediation: str | None = setup
        if unreadable is not None:
            summary = (
                f"{CHANNEL_SERVER_NAME} registration for {harness} is unreadable: {unreadable}"
            )
            remediation = f"fix the config file named above, then run {setup}"
        elif ok:
            summary, remediation = f"{CHANNEL_SERVER_NAME} registered for {harness}", None
        else:
            summary = f"{CHANNEL_SERVER_NAME} is not registered for {harness}"
        checks.append(
            DoctorCheck(
                id="channel.registration",
                category=CHANNEL_CATEGORY,
                harness=harness,
                status="PASS" if ok else "WARN",
                summary=summary,
                remediation=remediation,
            )
        )
    return checks


def _yaml_channel_config_error(harness: str, env: Mapping[str, str], home: Path) -> str | None:
    """Why *harness*'s YAML channel config cannot be read, or None.

    None when the harness has no YAML config, the file is absent, or it reads. Registration checks
    treat an unreadable file as unregistered (fail closed); doctor names the cause.
    """
    from .capability_inventory import CHANNEL_SERVER_NAME, CHANNEL_YAML_SECTION, channel_config_path
    from .yaml_channel import YamlChannelError, read_entry

    section = CHANNEL_YAML_SECTION.get(harness)
    if section is None:
        return None
    path = channel_config_path(harness, env, home)
    if not path.is_file():
        return None
    try:
        read_entry(path.read_text(encoding="utf-8"), section, CHANNEL_SERVER_NAME)
    except (OSError, UnicodeDecodeError, YamlChannelError) as exc:
        return f"{path}: {exc}"
    return None


def _capability_mcp_channel_registered(harness: str, env: Mapping[str, str], home: Path) -> bool:
    from .capability_inventory import mcp_channel_registered

    return mcp_channel_registered(harness, env, home)


def _routes_checks(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
    probe_routes: bool = False,
) -> list[DoctorCheck]:
    path = profiles_path(env)
    sync_state = load_sync(env)
    if not path.exists():
        return [
            DoctorCheck(
                id="runtime.routes_config",
                category=RUNTIME_CATEGORY,
                status="SKIP",
                summary=f"no agent profiles (pitwall.toml [agents.profiles]) at {path}",
                remediation="create route profiles with `pitwall agents profiles add` or `pitwall agents setup profiles`; see docs/routes.md",
            ),
            _check_pitwall_receiver(sync_state),
        ]
    try:
        config = load_profiles(env, registry=registry)
    except ProfilesError as exc:
        return [
            DoctorCheck(
                id="runtime.routes_config",
                category=RUNTIME_CATEGORY,
                status="FAIL",
                summary=f"agent profiles (pitwall.toml [agents.profiles]) are invalid: {exc}",
                remediation=f"repair or remove {path}; keys must never be inline (use apiKeyEnv)",
                details={"path": str(path)},
            )
        ]
    sync_routes = sync_state.get("routes")
    if not isinstance(sync_routes, Mapping):
        sync_routes = {}
    checks = [
        DoctorCheck(
            id="runtime.routes_config",
            category=RUNTIME_CATEGORY,
            status="PASS",
            summary=f"agent profiles (pitwall.toml [agents.profiles]) at {path} define {len(config['models'])} route(s)",
            details={"routes": sorted(config["models"])},
        )
    ]
    home = Path(env.get("HOME", "~")).expanduser()
    for name, entry in config["models"].items():
        row = describe_profile(name, registry=registry, routes=config, env=env, home=home)
        harness = row["harness"]
        binary = get_adapter(harness).resolve_binary(env, home) if harness else None
        resolved = (
            None
            if binary is None
            else binary
            if os.path.isabs(binary) and os.path.isfile(binary)
            else shutil.which(binary, path=env.get("PATH"))
        )
        checks.append(
            DoctorCheck(
                id=f"routes.{name}.harness_installed",
                category=ROUTES_CATEGORY,
                status="PASS" if resolved else "WARN",
                summary=f"route {name} resolves to {harness}"
                + (f" at {resolved}" if resolved else f" but the {harness} CLI is not installed"),
                remediation=None
                if resolved
                else f"install the {harness} CLI (`pitwall agents setup harnesses`) or pin another harness in the agent profiles (pitwall.toml [agents.profiles])",
                harness=harness,
            )
        )
        now = datetime.now(UTC)
        state, remaining = expiry_state(entry, now=now)
        origin = entry.get("origin") or {}
        if "expiresAt" in entry:
            remediation = (
                f"renew the lease, then `pitwall agents profiles refresh {name}`"
                if origin.get("kind") == "pitwall"
                else "update or remove expiresAt"
            )
            if state == "expired":
                summary = f"route {name} expired at {entry['expiresAt']}"
            elif state == "expiring" and remaining is not None:
                summary = f"route {name} expires in {remaining // 60} minutes"
            else:
                summary = f"route {name} expiry is valid"
            checks.append(
                DoctorCheck(
                    id=f"routes.{name}.expiry",
                    category=ROUTES_CATEGORY,
                    status="PASS" if state == "ok" else "WARN",
                    summary=summary,
                    remediation=None if state == "ok" else remediation,
                    harness=harness,
                )
            )
        if origin.get("kind") == "pitwall":
            route_sync = sync_routes.get(name)
            updated_at = (
                route_sync.get("lastUpdatedAt") if isinstance(route_sync, Mapping) else None
            )
            updated = _parse_utc_timestamp(updated_at)
            stale = updated is None or (now - updated).total_seconds() >= PITWALL_SYNC_STALE_SECONDS
            soon = state in {"expired", "expiring"}
            checks.append(
                DoctorCheck(
                    id=f"routes.{name}.pitwall_sync",
                    category=ROUTES_CATEGORY,
                    status="WARN" if soon and stale else "PASS",
                    summary=(
                        f"route {name} is within 15 minutes of expiry and has no Pitwall update in 30 minutes"
                        if soon and stale
                        else f"route {name} Pitwall synchronization does not require a staleness warning"
                    ),
                    remediation=(
                        f"start `pitwall agents broker receiver` or `pitwall agents broker watch`; "
                        f"use `pitwall agents profiles refresh {name}` for a one-time update"
                        if soon and stale
                        else None
                    ),
                    harness=harness,
                    details={"lastUpdatedAt": updated_at},
                )
            )
        if "endpoint" in entry:
            if probe_routes:
                probe = probe_profile(name, entry, env=env)
                checks.append(
                    DoctorCheck(
                        id=f"routes.{name}.probe",
                        category=ROUTES_CATEGORY,
                        status="PASS" if probe.status in {"reachable", "warming"} else "WARN",
                        summary=f"route {name} probe is {probe.status}: {probe.detail}",
                        remediation=probe.remedy,
                        harness=harness,
                        details=probe.to_dict(),
                    )
                )
            delivery = (
                registry["harnesses"][harness]["endpointDelivery"]
                if harness in registry["harnesses"]
                else "none"
            )
            if delivery == "config-sync":
                status = row["syncStatus"]
                if row["status"] == "needs-sync":
                    status = "stale" if "stale" in row["detail"] else "missing"
                checks.append(
                    DoctorCheck(
                        id=f"routes.{name}.sync_status",
                        category=ROUTES_CATEGORY,
                        status="PASS" if status == "synced" else "WARN",
                        summary=f"route {name} endpoint is {status} in {harness} config",
                        remediation=None
                        if status == "synced"
                        else f"run `pitwall agents profiles sync --harness {harness}`",
                        harness=harness,
                    )
                )
            key_env = entry["endpoint"].get("apiKeyEnv")
            if key_env:
                token, _resolved_key_env = resolve_pitwall_api_token(env, str(key_env))
                present = bool(token)
                requirement = pitwall_api_token_requirement(str(key_env))
                checks.append(
                    DoctorCheck(
                        id=f"routes.{name}.api_key_env",
                        category=ROUTES_CATEGORY,
                        status="PASS" if present else "WARN",
                        summary=f"route {name} key credential {requirement} is {'set' if present else 'not set'}",
                        remediation=None
                        if present
                        else f"set {requirement} before dispatching {name}; the value is never stored by pitwall",
                        harness=harness,
                    )
                )
    checks.append(_check_pitwall_receiver(sync_state))
    return checks


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _check_pitwall_receiver(sync_state: Mapping[str, Any]) -> DoctorCheck:
    sync_routes = sync_state.get("routes")
    receiver_seen = isinstance(sync_routes, Mapping) and any(
        isinstance(value, Mapping) and value.get("source") == "receiver"
        for value in sync_routes.values()
    )
    receiver = sync_state.get("receiver")
    if isinstance(receiver, Mapping):
        receiver_seen = True
        configured_port = receiver.get("port", PITWALL_RECEIVER_DEFAULT_PORT)
    else:
        configured_port = PITWALL_RECEIVER_DEFAULT_PORT
    port = (
        configured_port
        if isinstance(configured_port, int)
        and not isinstance(configured_port, bool)
        and 1 <= configured_port <= 65535
        else PITWALL_RECEIVER_DEFAULT_PORT
    )
    if not receiver_seen:
        return DoctorCheck(
            id="pitwall.receiver",
            category=ROUTES_CATEGORY,
            status="SKIP",
            summary="Pitwall receiver health probe skipped because the sync sidecar has no receiver update",
        )
    url = f"http://127.0.0.1:{port}/health"
    try:
        with open_http_url(url, timeout=PITWALL_RECEIVER_TIMEOUT) as response:
            status = response.status
    except (OSError, TimeoutError, ValueError) as exc:
        return DoctorCheck(
            id="pitwall.receiver",
            category=ROUTES_CATEGORY,
            status="SKIP",
            summary=f"Pitwall receiver is not reachable on loopback: {exc}",
            remediation=f"start `pitwall agents broker receiver --port {port}` or use `pitwall agents broker watch`",
            details={"url": url},
        )
    if status != 200:
        return DoctorCheck(
            id="pitwall.receiver",
            category=ROUTES_CATEGORY,
            status="SKIP",
            summary=f"Pitwall receiver health endpoint returned HTTP {status}",
            remediation=f"restart `pitwall agents broker receiver --port {port}` or use `pitwall agents broker watch`",
            details={"url": url, "httpStatus": status},
        )
    return DoctorCheck(
        id="pitwall.receiver",
        category=ROUTES_CATEGORY,
        status="PASS",
        summary=f"Pitwall receiver is healthy on 127.0.0.1:{port}",
        details={"url": url, "httpStatus": status},
    )


def _check_security_routes_file_mode(repo_root: Path, env: Mapping[str, str]) -> DoctorCheck:
    path = profiles_path(env)
    if not path.exists():
        return DoctorCheck(
            id="security.routes_file_mode",
            category=SECURITY_CATEGORY,
            status="SKIP",
            summary="no agent profiles (pitwall.toml [agents.profiles]) present",
        )
    mode = path.stat().st_mode & 0o777
    if mode & WORLD_BITS:
        return DoctorCheck(
            id="security.routes_file_mode",
            category=SECURITY_CATEGORY,
            status="WARN",
            summary=f"agent profiles (pitwall.toml [agents.profiles]) file mode is {mode:o}; it names private endpoints",
            remediation=f"chmod 600 {path}",
        )
    return DoctorCheck(
        id="security.routes_file_mode",
        category=SECURITY_CATEGORY,
        status="PASS",
        summary=f"agent profiles (pitwall.toml [agents.profiles]) file mode is {mode:o}",
    )


def _harness_checks(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    harness_id: str,
    registry: Mapping[str, Any],
    live_auth: bool,
) -> list[DoctorCheck]:
    return [
        _check_harness_binary_resolved(repo_root, env, harness_id=harness_id),
        _check_harness_executable_bit(repo_root, env, harness_id=harness_id, registry=registry),
        _check_harness_cli_contract(repo_root, env, harness_id=harness_id),
        _check_harness_binary_override(repo_root, env, harness_id=harness_id, registry=registry),
        _check_harness_default_model(repo_root, env, harness_id=harness_id, registry=registry),
        _check_harness_contract_drift(repo_root, env, harness_id=harness_id, registry=registry),
        _check_harness_effort_compat(repo_root, env, harness_id=harness_id, registry=registry),
        _check_harness_config_probe(
            repo_root,
            env,
            harness_id=harness_id,
            registry=registry,
        ),
        _check_harness_auth_probe(
            repo_root,
            env,
            harness_id=harness_id,
            registry=registry,
            live_auth=live_auth,
        ),
    ]


def _plugin_checks(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
) -> list[DoctorCheck]:
    hosts = registry.get("hosts", {})
    per_host = [
        _check_plugin_marketplace(repo_root, env, host_id=host_id, registry=registry)
        for host_id in hosts
    ]
    per_host.extend(
        [
            _check_plugin_native_host_exclusions(repo_root, env, host_id=host_id, registry=registry)
            for host_id in hosts
        ]
    )
    aggregates = [
        _check_plugin_runtime_reference_shared(repo_root, env, registry=registry),
        _check_plugin_version_alignment(repo_root, env, registry=registry),
    ]
    return per_host + aggregates


def _source_integrity_skip(check_id: str, category: str, summary: str) -> DoctorCheck:
    return DoctorCheck(
        id=check_id,
        category=category,
        status="SKIP",
        summary=summary,
        remediation=(
            "run doctor from a complete Pitwall source clone to validate clone-only files"
        ),
    )


def _artifact_plugin_skips(registry: Mapping[str, Any]) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for host_id in registry.get("hosts", {}):
        checks.extend(
            [
                _source_integrity_skip(
                    f"plugin.{host_id}.marketplace_present",
                    PLUGIN_CATEGORY,
                    f"{host_id} plugin package integrity requires a full source clone",
                ),
                _source_integrity_skip(
                    f"plugin.{host_id}.native_host_exclusions",
                    PLUGIN_CATEGORY,
                    f"{host_id} generated plugin routing requires a full source clone",
                ),
            ]
        )
    checks.extend(
        [
            _source_integrity_skip(
                "plugin.runtime_reference_shared",
                PLUGIN_CATEGORY,
                "package-local runtime-reference bundle comparison requires a full source clone",
            ),
            _source_integrity_skip(
                "plugin.version_alignment",
                PLUGIN_CATEGORY,
                "plugin manifest version alignment requires a full source clone",
            ),
        ]
    )
    return checks


def _runtime_checks(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
    source_mode: bool,
) -> list[DoctorCheck]:
    checks = [
        _check_python_version(repo_root, env),
        _check_git(repo_root, env),
        _check_state_dir(repo_root, env),
        _check_ledger_parent(repo_root, env),
        _check_hooks_config(repo_root, env),
        _check_registered_hooks(repo_root, env),
    ]
    if source_mode:
        checks.extend(
            [
                DoctorCheck(
                    id="runtime.source_registry_layout",
                    category=RUNTIME_CATEGORY,
                    status="PASS",
                    summary="registry clone paths and runtime-reference anchors are intact",
                ),
                _check_generated_routes(repo_root, env, registry),
                _check_install_links(repo_root, env),
            ]
        )
    else:
        checks.extend(
            [
                _source_integrity_skip(
                    "runtime.source_registry_layout",
                    RUNTIME_CATEGORY,
                    "prompt, capability-card, plugin, shim, and reference paths require a full source clone",
                ),
                _source_integrity_skip(
                    "runtime.generated_routes",
                    RUNTIME_CATEGORY,
                    "generated route and plugin parity requires a full source clone",
                ),
                _source_integrity_skip(
                    "runtime.install_links",
                    RUNTIME_CATEGORY,
                    "source install-entry inventory requires a full source clone",
                ),
            ]
        )
    return checks


def _check_harness_summary(
    repo_root: Path,
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
) -> DoctorCheck:
    from .harnesses.inventory import inspect_harnesses
    from .profiles import ProfilesError, load_profiles

    try:
        installers = read_resource_json("config/harness-installers.json")
    except ResourceError as exc:
        return DoctorCheck(
            id="harness.summary",
            category=HARNESS_CATEGORY,
            status="FAIL",
            summary=f"harness installer metadata is unavailable: {exc}",
            remediation="reinstall pitwall from a complete artifact",
        )
    home = Path(env.get("HOME", "~")).expanduser()
    try:
        config: dict[str, Any] | None = load_profiles(env, registry=registry)
    except ProfilesError:
        config = None
    rows = inspect_harnesses(
        registry,
        installers,
        config,
        env,
        home,
        probe=lambda *_args, **_kwargs: None,
    )
    installed = [row.id for row in rows if row.installed]
    missing = [row.id for row in rows if not row.installed]
    routes_on_missing = {name: row.id for row in rows if not row.installed for name in row.routes}
    summary = (
        f"{len(installed)} of {len(rows)} harnesses installed: "
        f"{', '.join(installed) or 'none'}; missing: {', '.join(missing) or 'none'}"
    )
    details = {
        "installed": installed,
        "missing": missing,
        "routesOnMissing": routes_on_missing,
    }
    if routes_on_missing:
        pinned = ", ".join(
            f"{name} -> {harness}" for name, harness in sorted(routes_on_missing.items())
        )
        return DoctorCheck(
            id="harness.summary",
            category=HARNESS_CATEGORY,
            status="WARN",
            summary=f"{summary}; routes on missing harnesses: {pinned}",
            remediation=(
                "install them with `pitwall agents setup harnesses` or change those routes' harness"
            ),
            details=details,
        )
    return DoctorCheck(
        id="harness.summary",
        category=HARNESS_CATEGORY,
        status="PASS",
        summary=summary,
        details=details,
    )


def _security_checks(
    repo_root: Path,
    env: Mapping[str, str],
) -> list[DoctorCheck]:
    return [
        _check_security_unrestricted_mode(repo_root, env),
        _check_security_routes_file_mode(repo_root, env),
        _check_security_world_readable_state(repo_root, env),
        _check_security_world_readable_hooks(repo_root, env),
        _check_security_retained_prompts(repo_root, env),
        _check_security_oversized_state(repo_root, env),
        _check_security_shell_wrapper_hooks(repo_root, env),
        _check_security_worktree_cleanup_backlog(repo_root, env),
    ]


def _filter_checks(checks: Iterable[DoctorCheck], *, harness: str | None) -> list[DoctorCheck]:
    if harness is None:
        return list(checks)
    filtered: list[DoctorCheck] = []
    for check in checks:
        if check.category == HARNESS_CATEGORY and check.harness != harness:
            continue
        if (
            check.category == PLUGIN_CATEGORY
            and check.harness is not None
            and check.harness != harness
        ):
            continue
        if (
            check.category == ROUTES_CATEGORY
            and check.harness is not None
            and check.harness != harness
        ):
            continue
        filtered.append(check)
    return filtered


def run_doctor(
    repo_root: Path | None,
    env: Mapping[str, str],
    *,
    harness: str | None = None,
    installation_only: bool = False,
    live_auth: bool = False,
    discover_models: bool = False,
    probe_routes: bool = False,
) -> dict[str, Any]:
    """Run a deterministic, read-only doctor and return the JSON-serializable report."""

    if installation_only and discover_models:
        raise ValueError("--installation-only cannot be combined with --discover-models")
    source_root = Path(repo_root).resolve() if repo_root is not None else None
    runtime_root = source_root if source_root is not None else Path("/")
    candidate_harnesses = sorted(adapter_ids())
    if harness is not None:
        resolved = _resolve_harness_filter(harness, available_harnesses=candidate_harnesses)
        if resolved is not None:
            harness = resolved

    registry, registry_failure = _read_registry(source_root)
    checks: list[DoctorCheck] = []
    if registry_failure is not None:
        checks.append(registry_failure)
    elif installation_only:
        assert registry is not None
        checks.extend(
            _runtime_checks(
                runtime_root,
                env,
                registry=registry,
                source_mode=source_root is not None,
            )
        )
    else:
        assert registry is not None
        checks.extend(
            _runtime_checks(
                runtime_root,
                env,
                registry=registry,
                source_mode=source_root is not None,
            )
        )
        checks.append(_check_harness_summary(runtime_root, env, registry=registry))
        checks.extend(
            _routes_checks(
                runtime_root,
                env,
                registry=registry,
                probe_routes=probe_routes,
            )
        )
        checks.extend(_channel_checks(env, registry))
        target_harnesses = [harness] if harness is not None else candidate_harnesses
        for harness_id in target_harnesses:
            checks.extend(
                _harness_checks(
                    runtime_root,
                    env,
                    harness_id=harness_id,
                    registry=registry,
                    live_auth=live_auth,
                )
            )
        if discover_models:
            for discovered_check in run_model_discovery(
                runtime_root,
                env,
                registry,
                harness=harness,
            ):
                checks.append(
                    DoctorCheck(
                        id=discovered_check["id"],
                        category=discovered_check["category"],
                        status=discovered_check["status"],
                        summary=discovered_check["summary"],
                        remediation=discovered_check.get("remediation"),
                        harness=discovered_check.get("provider"),
                        details=discovered_check.get("details"),
                    )
                )
        if source_root is not None:
            checks.extend(_plugin_checks(source_root, env, registry=registry))
        else:
            checks.extend(_artifact_plugin_skips(registry))
        checks.extend(_security_checks(runtime_root, env))

    filtered = _filter_checks(checks, harness=harness)

    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for check in filtered:
        normalized = check.status.upper()
        if normalized not in VALID_STATUSES:
            normalized = "FAIL"
        counts[normalized.lower()] = counts.get(normalized.lower(), 0) + 1

    normalized_checks: list[DoctorCheck] = []
    for check in filtered:
        status = check.status.upper()
        if status not in VALID_STATUSES:
            status = "FAIL"
        normalized_checks.append(
            DoctorCheck(
                id=check.id,
                category=check.category,
                status=status,
                summary=check.summary,
                remediation=check.remediation,
                harness=check.harness,
                details=check.details,
            )
        )

    modes = {
        "installationOnly": bool(installation_only),
        "liveAuth": bool(live_auth),
        "discoverModels": bool(discover_models),
        "probeRoutes": bool(probe_routes),
        "providerFilter": harness,
        "sourceMode": source_root is not None,
    }

    report = DoctorReport(
        modes=modes,
        status=_status_from_counts(counts),
        summary=counts,
        checks=normalized_checks,
        generated_at=_now_iso(),
    )
    return report.to_dict()
