"""Install and uninstall the Pitwall agent shims, plugins, and channel registration.

``install`` writes exactly these things and records each one in an install manifest:

* ``~/.claude/scripts/<harness>-shim.sh`` for every registered harness and
  ``route-shim.sh``, each a two-line wrapper around ``pitwall agents _shim``;
* the ``claude``, ``codex``, and ``copilot`` plugins, with the generated route
  references copied into each, plus one ``pitwall-local`` marketplace manifest per host;
* the ``pitwall-channel`` MCP server, registered as ``pitwall mcp serve channel``.

``uninstall`` reads the manifest and removes exactly that, hook registrations first.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .resources import read_resource_json

if TYPE_CHECKING:
    from .profiles_sync import SyncPlan

PYTHON_MIN = (3, 14)
PYTHON_MAX = (3, 15)
MARKETPLACE_NAME = "pitwall-local"
MANIFEST_NAME = "install-manifest.json"
ROUTE_SHIM = "route-shim.sh"
PLUGIN_HOSTS = ("claude", "codex", "copilot")
GENERATED_FILES = ("routes.generated.md", "harness-registry.generated.json")
REFERENCES = Path("skills") / "subagent-model-routing" / "references"
COMMAND_TIMEOUT_SECONDS = 120

Runner = Callable[[Sequence[str]], int]
Lister = Callable[[Sequence[str]], str]


class InstallationError(RuntimeError):
    """The install cannot proceed, or the install manifest is unusable."""


def supported_python(version_info: Any = sys.version_info) -> bool:
    """Return whether *version_info* is within the single supported minor."""

    minor = (int(version_info[0]), int(version_info[1]))
    return minor >= PYTHON_MIN and minor < PYTHON_MAX


def python_requirement() -> str:
    """Return the user-facing supported range."""

    return ">=3.14,<3.15"


@dataclass(frozen=True, slots=True)
class InstallLocations:
    """Where an install writes, derived from the user's home directory."""

    home: Path
    scripts: Path
    plugins: Path
    manifest: Path

    @classmethod
    def for_home(cls, home: Path, env: Mapping[str, str]) -> InstallLocations:
        data_home = (
            Path(env["XDG_DATA_HOME"]).expanduser()
            if env.get("XDG_DATA_HOME")
            else home / ".local" / "share"
        )
        root = data_home / "pitwall"
        return cls(home, home / ".claude" / "scripts", root / "plugins", root / MANIFEST_NAME)


@dataclass(slots=True)
class InstallResult:
    """What ``install`` or ``uninstall`` did."""

    files: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    mcp_harnesses: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def shim_names() -> tuple[str, ...]:
    """The shim file names: one per registered harness, then the route shim."""

    registry = read_resource_json("config/harness-registry.json")
    names = [str(harness["shim"]) for harness in registry["harnesses"].values()]
    return (*names, ROUTE_SHIM)


def shim_script(shim: str) -> str:
    """The two-line wrapper for one shim file name."""

    if not shim.endswith("-shim.sh"):
        raise InstallationError(f"not a shim file name: {shim}")
    target = shim.removesuffix("-shim.sh")
    # The explicit check keeps a missing `pitwall` at 127 everywhere: with an empty PATH the
    # shell's own exec error is 126 when the search meets a directory named `pitwall`.
    guard = (
        'command -v pitwall >/dev/null 2>&1 || { echo "pitwall: command not found" >&2; exit 127; }'
    )
    return f'#!/usr/bin/env bash\n{guard}; exec pitwall agents _shim {target} "$@"\n'


def plugins_source() -> Path:
    """The packaged ``plugins`` directory, or the checkout's when running from source."""

    packaged = Path(__file__).resolve().parent / "plugins"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "plugins"


def generated_source() -> Path:
    """The single packaged copy of the generated route references."""

    return Path(str(files("pitwall.agents").joinpath("resources", "generated")))


def _plugin_manifest(host: str, root: Path) -> dict[str, Any]:
    relative = {
        "claude": ".claude-plugin/plugin.json",
        "codex": ".codex-plugin/plugin.json",
        "copilot": "plugin.json",
    }[host]
    manifest: dict[str, Any] = json.loads((root / host / relative).read_text(encoding="utf-8"))
    return manifest


def marketplace_documents(source: Path) -> dict[str, dict[str, Any]]:
    """The three ``pitwall-local`` marketplace manifests, keyed by path under the plugin root."""

    claude = _plugin_manifest("claude", source)
    codex = _plugin_manifest("codex", source)
    copilot = _plugin_manifest("copilot", source)
    return {
        ".claude-plugin/marketplace.json": {
            "name": MARKETPLACE_NAME,
            "description": "Pitwall plugins for Claude Code.",
            "owner": {"name": "Pitwall"},
            "plugins": [
                {
                    "name": claude["name"],
                    "version": claude["version"],
                    "source": "./claude",
                    "description": claude["description"],
                }
            ],
        },
        ".agents/plugins/marketplace.json": {
            "name": MARKETPLACE_NAME,
            "interface": {"displayName": "Pitwall"},
            "plugins": [
                {
                    "name": codex["name"],
                    "version": codex["version"],
                    "source": {"source": "local", "path": "./codex"},
                    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                    "category": "Productivity",
                }
            ],
        },
        ".github/plugin/marketplace.json": {
            "name": MARKETPLACE_NAME,
            "owner": {"name": "Pitwall"},
            "metadata": {"description": "Pitwall plugins for GitHub Copilot CLI."},
            "plugins": [
                {
                    "name": copilot["name"],
                    "version": copilot["version"],
                    "source": "copilot",
                    "description": copilot["description"],
                    "category": "Productivity",
                }
            ],
        },
    }


def _plugin_entries(host: str) -> tuple[str, str]:
    """The installed plugin id and its marketplace-qualified form for *host*."""

    name = {"claude": "pitwall", "codex": "pitwall-codex", "copilot": "pitwall-copilot"}[host]
    return name, f"{name}@{MARKETPLACE_NAME}"


def _register_commands(host: str, root: Path) -> tuple[tuple[str, ...], ...]:
    _name, qualified = _plugin_entries(host)
    if host == "claude":
        return (
            ("claude", "plugin", "marketplace", "add", "--scope", "user", str(root)),
            ("claude", "plugin", "install", qualified, "--scope", "user"),
        )
    if host == "codex":
        return (
            ("codex", "plugin", "marketplace", "add", str(root)),
            ("codex", "plugin", "add", qualified),
        )
    return (
        ("copilot", "plugin", "marketplace", "add", str(root)),
        ("copilot", "plugin", "install", qualified),
    )


def _unregister_commands(host: str) -> tuple[tuple[str, ...], ...]:
    name, qualified = _plugin_entries(host)
    if host == "claude":
        return (
            ("claude", "plugin", "uninstall", "--keep-data", "--scope", "user", qualified),
            ("claude", "plugin", "marketplace", "remove", "--scope", "user", MARKETPLACE_NAME),
        )
    if host == "codex":
        return (
            ("codex", "plugin", "remove", qualified),
            ("codex", "plugin", "marketplace", "remove", MARKETPLACE_NAME),
        )
    return (
        ("copilot", "plugin", "uninstall", name),
        ("copilot", "plugin", "marketplace", "remove", MARKETPLACE_NAME),
    )


def run_command(env: Mapping[str, str]) -> Runner:
    """The real runner: execute an argv with *env* and return its exit status.

    Host CLIs (claude, codex, copilot, ...) are only ever run through a runner, so a caller or
    test that passes its own ``runner`` to ``install``/``uninstall`` never reaches a real one.
    """

    def run(argv: Sequence[str]) -> int:
        try:
            return subprocess.run(
                list(argv),
                env=dict(env),
                check=False,
                capture_output=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            ).returncode
        except OSError, subprocess.TimeoutExpired:
            return 127

    return run


def list_command(env: Mapping[str, str]) -> Lister:
    """The real lister: execute an argv with *env* and return its stdout ("" on any failure)."""

    def list_output(argv: Sequence[str]) -> str:
        try:
            done = subprocess.run(
                list(argv),
                env=dict(env),
                check=False,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except OSError, subprocess.TimeoutExpired:
            return ""
        return done.stdout if done.returncode == 0 else ""

    return list_output


def _failed_registration(
    host: str, root: Path, run: Runner, lister: Lister | None
) -> tuple[str, ...] | None:
    """The first plugin registration command that really failed, or None when all hold.

    Copilot, unlike claude and codex, exits non-zero when ``marketplace add`` finds the
    marketplace already registered. That is the state a re-run wants, so a failed add counts
    as success when ``copilot plugin marketplace list`` shows the Pitwall marketplace.
    """

    for argv in _register_commands(host, root):
        if run(argv) == 0:
            continue
        if (
            host == "copilot"
            and argv[:4] == ("copilot", "plugin", "marketplace", "add")
            and lister is not None
            and MARKETPLACE_NAME in lister(("copilot", "plugin", "marketplace", "list"))
        ):
            continue
        return argv
    return None


def _read_manifest(locations: InstallLocations) -> dict[str, Any] | None:
    if not locations.manifest.is_file():
        return None
    try:
        document = json.loads(locations.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallationError(
            f"install manifest {locations.manifest} is unreadable: {exc}"
        ) from exc
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise InstallationError(f"install manifest {locations.manifest} has an unknown format")
    return document


def _plan_writes(source: Path, locations: InstallLocations) -> dict[Path, bytes]:
    """Every file ``install`` writes, mapped to its bytes."""

    planned: dict[Path, bytes] = {}
    for shim in shim_names():
        planned[locations.scripts / shim] = shim_script(shim).encode("utf-8")
    generated = generated_source()
    for host in PLUGIN_HOSTS:
        host_source = source / host
        if not host_source.is_dir():
            raise InstallationError(f"plugin source is missing: {host_source}")
        for item in sorted(host_source.rglob("*")):
            if item.is_file() and "__pycache__" not in item.parts and item.suffix != ".pyc":
                planned[locations.plugins / host / item.relative_to(host_source)] = (
                    item.read_bytes()
                )
        for name in GENERATED_FILES:
            reference = generated / name
            if not reference.is_file():
                raise InstallationError(f"generated reference is missing: {reference}")
            planned[locations.plugins / host / REFERENCES / name] = reference.read_bytes()
    for relative, document in marketplace_documents(source).items():
        planned[locations.plugins / relative] = (json.dumps(document, indent=2) + "\n").encode()
    return planned


def _executable(path: Path) -> bool:
    return path.suffix == ".sh" and path.parent.name == "scripts"


def _missing_directories(paths: Sequence[Path]) -> list[Path]:
    """The directories that do not exist yet for *paths*, shallowest first."""

    missing: dict[Path, None] = {}
    for path in paths:
        for parent in reversed(path.parents):
            if not parent.exists():
                missing[parent] = None
    return list(missing)


def _mcp_targets(
    harnesses: Sequence[str] | None, env: Mapping[str, str], home: Path
) -> tuple[str, ...]:
    if harnesses is not None:
        return tuple(harnesses)
    from .capability_inventory import CHANNEL_HARNESSES
    from .setup import resolve_harness_binary

    detection_env = dict(env)
    detection_env.setdefault("PATH", "/usr/bin:/bin")
    found: list[str] = []
    for harness in CHANNEL_HARNESSES:
        if harness == "copilot":
            present = shutil.which("copilot", path=detection_env.get("PATH")) is not None
        else:
            present = resolve_harness_binary(harness, detection_env, home) is not None
        if present:
            found.append(harness)
    return tuple(found)


def _channel_command(env: Mapping[str, str], home: Path, *, required: bool) -> str:
    """The absolute ``pitwall`` path to register for the channel server.

    ``pitwall`` on PATH wins; otherwise the running executable (or its venv sibling, or
    ``~/.local/bin/pitwall``). A bare ``pitwall`` is never written: a host CLI launched with
    a minimal PATH could not start the server. With nothing to register the name is only
    recorded, so an install that registers no channel harness still succeeds.
    """

    from pitwall.install_hint import install_command

    from .mcp_registration import RegistrationError, channel_server_command

    try:
        return channel_server_command(env)
    except RegistrationError:
        pass
    for candidate in (
        Path(sys.argv[0]) if sys.argv else None,
        Path(sys.executable).parent / "pitwall",
        home / ".local" / "bin" / "pitwall",
    ):
        if (
            candidate is not None
            and candidate.name == "pitwall"
            and candidate.is_file()
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate.resolve())
    if not required:
        return "pitwall"
    raise InstallationError(
        "pitwall is not on PATH and its executable could not be located, so the channel "
        f"server cannot be registered with an absolute command; install it (`{install_command()}`) "
        "and run `pitwall agents install` again"
    )


def install(
    env: Mapping[str, str],
    home: Path,
    *,
    harnesses: Sequence[str] | None = None,
    plugin_hosts: Sequence[str] | None = None,
    runner: Runner | None = None,
    lister: Lister | None = None,
    source: Path | None = None,
) -> InstallResult:
    """Write the shims, plugins, and channel registration, and record them.

    *harnesses* selects the MCP registration targets (default: every detected channel
    harness). *plugin_hosts* selects which host CLIs get plugin registration commands
    (default: those found on ``PATH``). Nothing is written when a destination holds a
    file that is neither ours nor identical to what install would write. *lister* captures a
    host CLI's stdout (default: the real one when no *runner* is injected, otherwise none).
    """

    from .mcp_registration import RegistrationError, plan_registration

    locations = InstallLocations.for_home(home, env)
    run = runner or run_command(env)
    if lister is None and runner is None:
        lister = list_command(env)
    previous = _read_manifest(locations)
    owned = {Path(item) for item in previous["files"]} if previous else set()
    planned = _plan_writes(source or plugins_source(), locations)
    conflicts = [
        str(path)
        for path, content in planned.items()
        if path.exists()
        and path not in owned
        and (not path.is_file() or path.read_bytes() != content)
    ]
    if conflicts:
        raise InstallationError(
            "refusing to overwrite files that Pitwall did not write: " + ", ".join(conflicts)
        )
    targets = _mcp_targets(harnesses, env, home)
    command = _channel_command(env, home, required=bool(targets))
    plans = []
    for harness in targets:
        try:
            plans.append(plan_registration(harness, env, home, command=command))
        except (RegistrationError, KeyError) as exc:
            raise InstallationError(
                f"cannot register the channel server for {harness}: {exc}"
            ) from exc

    created_dirs = _missing_directories(
        [*planned, locations.manifest, *(plan.path for plan in plans)]
    )
    registrations: dict[str, dict[str, Any]] = dict(
        previous["mcpRegistrations"] if previous else {}
    )
    result = InstallResult()
    for path, content in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755 if _executable(path) else 0o644)
        result.files.append(path)
    for plan in plans:
        existed = plan.path.exists()
        changed = plan.changed
        backup = _apply_registration(plan, run)
        result.mcp_harnesses.append(plan.harness)
        if plan.harness not in registrations or changed:
            registrations[plan.harness] = {
                "path": str(plan.path),
                "created": not existed,
                "backup": str(backup) if backup else None,
                "installedSha256": _digest(plan.after),
            }

    hosts = tuple(plugin_hosts) if plugin_hosts is not None else PLUGIN_HOSTS
    registered: list[str] = []
    for host in hosts:
        if plugin_hosts is None and shutil.which(host, path=env.get("PATH")) is None:
            continue
        failed = _failed_registration(host, locations.plugins, run, lister)
        if failed:
            result.warnings.append(f"{host}: plugin registration failed: {' '.join(failed)}")
        else:
            registered.append(host)

    recorded_dirs = [Path(item) for item in previous["directories"]] if previous else []
    document = {
        "schemaVersion": 1,
        "files": sorted({str(path) for path in [*result.files, *owned]}),
        "directories": [str(path) for path in dict.fromkeys([*recorded_dirs, *created_dirs])],
        "mcpRegistrations": registrations,
        "pluginHosts": sorted(set(registered) | set(previous["pluginHosts"] if previous else ())),
        "mcpCommand": command,
    }
    locations.manifest.parent.mkdir(parents=True, exist_ok=True)
    locations.manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return result


def _apply_registration(plan: SyncPlan, run: Runner) -> Path | None:
    """Write the plan's config file, then run its commands through *run*."""

    from .profiles_sync import apply_plan

    backup = apply_plan(replace(plan, commands=()))
    for command in plan.commands:
        if run(command) != 0:
            raise InstallationError(f"{plan.harness}: command failed: {' '.join(command)}")
    return backup


def _remove_file(path: Path, result: InstallResult) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        result.warnings.append(f"cannot remove {path}: {exc}")
        return
    result.removed.append(path)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _config_is_empty(path: Path) -> bool:
    """Whether a harness config holds nothing but empty containers."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.strip():
        return True
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(document, dict) and all(value == {} for value in document.values())


def _tidy_registration(
    config: Path,
    registration: Mapping[str, Any],
    unchanged_since_install: bool,
    removal_backup: Path | None,
    result: InstallResult,
) -> None:
    """Undo the traces of registration: the config install created, or its backups.

    Backups are only removed when the config was untouched since install, so removing the
    channel entry has restored the file the user had before.
    """

    if not unchanged_since_install:
        return  # the user's config changed after install: keep every backup
    for backup in (registration.get("backup"), removal_backup):
        if backup is not None and Path(backup).is_file():
            _remove_file(Path(backup), result)
    if registration.get("created") and _config_is_empty(config):
        _remove_file(config, result)


def uninstall(
    env: Mapping[str, str],
    home: Path,
    *,
    runner: Runner | None = None,
) -> InstallResult:
    """Remove exactly what ``install`` recorded, hook registrations first.

    The order is: plugin hook registrations (each installed ``hooks/hooks.json`` and the
    host plugin registrations), then the channel MCP registrations, then every other
    installed file, then the directories install created, and the manifest last. No step
    can leave a registered hook pointing at a missing CLI.
    """

    from .mcp_registration import RegistrationError, plan_registration

    locations = InstallLocations.for_home(home, env)
    run = runner or run_command(env)
    document = _read_manifest(locations)
    result = InstallResult()
    if document is None:
        return result
    recorded = [Path(item) for item in document["files"]]

    hook_files = [
        path for path in recorded if path.name == "hooks.json" and path.parent.name == "hooks"
    ]
    for path in hook_files:
        _remove_file(path, result)
    for host in document["pluginHosts"]:
        for argv in _unregister_commands(host):
            if run(argv) != 0:
                result.warnings.append(f"{host}: plugin removal failed: {' '.join(argv)}")

    for harness, registration in document["mcpRegistrations"].items():
        try:
            plan = plan_registration(
                harness, env, home, command=document["mcpCommand"], remove=True
            )
            unchanged = _digest(plan.before) == registration.get("installedSha256")
            removal_backup = _apply_registration(plan, run)
        except (RegistrationError, KeyError) as exc:
            result.warnings.append(f"{harness}: channel registration not removed: {exc}")
            continue
        result.mcp_harnesses.append(harness)
        _tidy_registration(plan.path, registration, unchanged, removal_backup, result)

    for path in recorded:
        if path not in hook_files:
            _remove_file(path, result)
    _remove_file(locations.manifest, result)
    for directory in sorted((Path(item) for item in document["directories"]), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue  # reason: not empty or already gone; only directories install created are candidates
    return result


def hook_problems(env: Mapping[str, str], home: Path) -> list[str]:
    """Problems with installed plugin hooks: each registered hook needs a runnable CLI.

    Empty when no hooks are registered or the ``pitwall`` command runs.
    """

    locations = InstallLocations.for_home(home, env)
    try:
        document = _read_manifest(locations)
    except InstallationError as exc:
        return [str(exc)]
    if document is None:
        return []
    registered = [
        Path(item)
        for item in document["files"]
        if Path(item).name == "hooks.json" and Path(item).parent.name == "hooks"
    ]
    registered = [path for path in registered if path.is_file()]
    if not registered:
        return []
    command = shutil.which("pitwall", path=env.get("PATH"))
    if command is None:
        return [f"{len(registered)} registered hook(s) call `pitwall`, which is not on PATH"]
    try:
        completed = subprocess.run(
            [command, "--version"],
            env=dict(env),
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"registered hooks call `{command}`, which cannot be run: {exc}"]
    if completed.returncode != 0:
        return [f"registered hooks call `{command}`, which exited {completed.returncode}"]
    return []


def installed_shims(home: Path) -> dict[str, Path]:
    """The shim files expected under ``~/.claude/scripts``, keyed by file name."""

    return {name: home / ".claude" / "scripts" / name for name in shim_names()}


def default_home(env: Mapping[str, str]) -> Path:
    """The home directory an install targets."""

    return Path(env.get("HOME") or Path.home()).expanduser()


__all__ = [
    "InstallLocations",
    "InstallResult",
    "InstallationError",
    "default_home",
    "generated_source",
    "hook_problems",
    "install",
    "installed_shims",
    "plugins_source",
    "python_requirement",
    "list_command",
    "run_command",
    "shim_names",
    "shim_script",
    "supported_python",
    "uninstall",
]
