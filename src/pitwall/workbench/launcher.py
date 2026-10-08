"""Launch Pi under a Workbench profile (port of ``launcher.ts``).

Pi is started with every discovery surface disabled (``--no-extensions --no-skills
--no-prompt-templates --no-themes``) and only the explicit extension paths, which come from the
committed package data in ``pitwall.workbench.pi_extensions``. The trusted Pi process receives
only the selected credential and operational environment.

The packaged extensions import ``@earendil-works/pi-ai`` and ``@earendil-works/pi-coding-agent``.
Those bare imports resolve from a ``node_modules`` next to the extension file, which package data
does not have, so the launcher stages the extensions once into a content-addressed, read-only
runtime directory whose ``node_modules`` links the pinned Pi packages. That directory is also the
restricted sandbox's read-only runtime root, so tool children can never rewrite the code Pi runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pitwall.workbench.account_budget import default_account_budget_dir
from pitwall.workbench.admission import default_admission_dir, prepare_private_directory
from pitwall.workbench.profile import CompiledProfile
from pitwall.workbench.restricted import RestrictedPolicy, build_restricted_command
from pitwall.workbench.state_dir import workbench_state_dir

PI_EXTENSIONS_DIR = Path(__file__).resolve().parent / "pi_extensions"
PI_PACKAGE = "@earendil-works/pi-coding-agent"
PI_AI_PACKAGE = "@earendil-works/pi-ai"
ALLOWED_ENV = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "COLORTERM",
    "LANG",
    "LC_ALL",
    "TZ",
    "TMPDIR",
    "NO_COLOR",
    "PITWALL_WORKBENCH_NATIVE_PROFILE",
    "PITWALL_WORKBENCH_PROVIDER_PROFILE",
    "PITWALL_WORKBENCH_RESOURCE_DIR",
    "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR",
    "PITWALL_WORKBENCH_APPROVED_CHECKS",
    "PITWALL_WORKBENCH_ACCOUNTING_PATH",
)


class LauncherError(RuntimeError):
    """Pi cannot be launched with the given options."""


@dataclass(frozen=True)
class PiLaunchOptions:
    cwd: Path
    profile: CompiledProfile
    extension: Path | str | None = None
    extensions: Sequence[Path | str] = ()
    pi_bin: Path | str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    #: Explicit non-secret fixture/runtime variables to pass through.
    passthrough_env: Sequence[str] = ()
    continue_session: bool = False
    restricted: bool = False
    #: Where packaged extensions are staged; defaults to ``PITWALL_WORKBENCH_RUNTIME_DIR`` or state.
    runtime_dir: Path | None = None


def extension_path(name: str) -> Path:
    """Path of a packaged extension, for example ``extension_path("provider-extension")``."""
    path = PI_EXTENSIONS_DIR / f"{name}.js"
    if not path.is_file():
        raise LauncherError(f"packaged Pi extension not found: {path}")
    return path


def hoisted_dependency_roots(
    runtime_root: Path | str, dependency_package: Path | str
) -> list[Path]:
    """Read-only roots the restricted sandbox must also mount for a hoisted Pi dependency.

    npm may hoist the pinned Pi dependency outside the package's own ``node_modules``.
    """
    directory = Path(os.path.abspath(dependency_package))
    while directory.name != "node_modules" and directory.parent != directory:
        directory = directory.parent
    if directory.name != "node_modules":
        return []
    own = Path(os.path.abspath(runtime_root)) / "node_modules"
    return [] if directory == own else [directory]


def default_pi_bin() -> Path:
    """The Pi CLI script: ``PITWALL_WORKBENCH_PI_BIN``, else the ``pi`` found on ``PATH``."""
    configured = os.environ.get("PITWALL_WORKBENCH_PI_BIN") or shutil.which("pi")
    if not configured:
        raise LauncherError("pi is not installed: set PITWALL_WORKBENCH_PI_BIN or put pi on PATH")
    return Path(configured).resolve()


def _pi_bin(options: PiLaunchOptions) -> Path:
    return Path(options.pi_bin) if options.pi_bin else default_pi_bin()


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise LauncherError("node is not installed")
    return node


def _package_dir(pi_bin: Path) -> Path | None:
    """The installed ``@earendil-works/pi-coding-agent`` directory that owns ``pi_bin``."""
    for directory in Path(os.path.realpath(pi_bin)).parents:
        manifest = directory / "package.json"
        if manifest.is_file():
            try:
                name = json.loads(manifest.read_text()).get("name")
            except OSError, ValueError:
                return None
            return directory if name == PI_PACKAGE else None
    return None


def _pi_ai_dir(package_dir: Path) -> Path | None:
    for candidate in (package_dir / "node_modules" / PI_AI_PACKAGE, package_dir.parent / "pi-ai"):
        if candidate.is_dir():
            return candidate
    return None


def _runtime_base(options: PiLaunchOptions) -> Path:
    configured = options.runtime_dir or os.environ.get("PITWALL_WORKBENCH_RUNTIME_DIR")
    if configured:
        return Path(configured)
    return workbench_state_dir("runtime")


def runtime_root(options: PiLaunchOptions) -> Path:
    """Stage the packaged extensions where their Pi imports resolve and return that root.

    Falls back to the package data directory when ``pi_bin`` is not an installed Pi package
    (for example a fixture script), since nothing then loads the extensions.
    """
    package_dir = _package_dir(_pi_bin(options))
    pi_ai = _pi_ai_dir(package_dir) if package_dir else None
    if package_dir is None or pi_ai is None:
        return PI_EXTENSIONS_DIR
    sources = sorted([*PI_EXTENSIONS_DIR.glob("*.js"), PI_EXTENSIONS_DIR / "package.json"])
    digest = hashlib.sha256()
    for source in (*sources, package_dir, pi_ai):
        digest.update(source.name.encode() + b"\0")
        digest.update(str(source).encode() if source.is_dir() else source.read_bytes())
    base = prepare_private_directory(_runtime_base(options), "runtime")
    target = base / digest.hexdigest()[:24]
    if target.is_dir():
        return target
    pending = base / f".{target.name}.{os.getpid()}.tmp"
    shutil.rmtree(pending, ignore_errors=True)
    (pending / "node_modules/@earendil-works").mkdir(parents=True, mode=0o700)
    for source in sources:
        copy = pending / source.name
        shutil.copyfile(source, copy)
        copy.chmod(0o444)
    (pending / "node_modules" / PI_PACKAGE).symlink_to(package_dir)
    (pending / "node_modules" / PI_AI_PACKAGE).symlink_to(pi_ai)
    try:
        os.rename(pending, target)
    except OSError:
        shutil.rmtree(pending, ignore_errors=True)  # another launcher staged it first
        if not target.is_dir():
            raise
    return target


def _staged(extension: Path | str, root: Path) -> str:
    path = Path(os.path.abspath(extension))
    try:
        return str(root / path.relative_to(PI_EXTENSIONS_DIR))
    except ValueError:
        return str(path)  # not packaged data, for example a pinned third-party extension


def pi_args(options: PiLaunchOptions, *, rpc: bool = True, root: Path | None = None) -> list[str]:
    """Pi's argument vector; the order is part of the contract."""
    profile = options.profile.profile
    staged_root = root or PI_EXTENSIONS_DIR
    extensions = [
        *options.extensions,
        *([options.extension] if options.extension else []),
        *([extension_path("restricted-extension")] if options.restricted else []),
    ]
    return [
        "--provider",
        profile["provider"],
        "--model",
        profile["modelId"],
        *(["--continue"] if options.continue_session else []),
        *(["--thinking", profile["reasoningLevel"]] if profile.get("reasoningLevel") else []),
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        *(["--mode", "rpc"] if rpc else []),
        *[
            part
            for extension in extensions
            for part in ("--extension", _staged(extension, staged_root))
        ],
    ]


def launch_environment(options: PiLaunchOptions, root: Path | None = None) -> dict[str, str]:
    source = {**os.environ, **options.env}
    key = options.profile.profile.get("apiKeyEnv")
    if key and not source.get(key):
        raise LauncherError(f"missing credential environment variable: {key}")
    env = {
        name: source[name] for name in (*ALLOWED_ENV, *options.passthrough_env) if name in source
    }
    if key:
        env[key] = source[key]
    resource_dir = source.get("PITWALL_WORKBENCH_RESOURCE_DIR") or (
        str(default_admission_dir()) if options.restricted else None
    )
    if resource_dir:
        env["PITWALL_WORKBENCH_RESOURCE_DIR"] = resource_dir
    env["PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR"] = source.get(
        "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR"
    ) or str(default_account_budget_dir())
    env["PI_CODING_AGENT_DIR"] = str(options.profile.agent_dir)
    env["PI_TELEMETRY"] = "0"
    env["PI_OFFLINE"] = "1"
    if options.restricted:
        env["PITWALL_WORKBENCH_RESTRICTED"] = "1"
        env["PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT"] = str(root or PI_EXTENSIONS_DIR)
        if key:
            env["PITWALL_WORKBENCH_RESTRICTED_CREDENTIAL_ENV"] = key
    return env


def launch_command(
    options: PiLaunchOptions, *, rpc: bool = True, root: Path | None = None
) -> list[str]:
    """The full argv, wrapped in the restricted sandbox when requested."""
    pi_bin = _pi_bin(options)
    root = root or runtime_root(options)
    argv = [_node(), str(pi_bin), *pi_args(options, rpc=rpc, root=root)]
    if not options.restricted:
        return argv
    source = {**os.environ, **options.env}
    admission = Path(source.get("PITWALL_WORKBENCH_RESOURCE_DIR") or default_admission_dir())
    budget = Path(
        source.get("PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR") or default_account_budget_dir()
    )
    admission.mkdir(mode=0o700, parents=True, exist_ok=True)
    budget.mkdir(mode=0o700, parents=True, exist_ok=True)
    package_dir = _package_dir(pi_bin) or pi_bin.resolve().parent
    policy = RestrictedPolicy(
        agent_dir=options.profile.agent_dir,
        runtime_root=root,
        runtime_dependencies=hoisted_dependency_roots(root, package_dir),
        admission_dir=admission,
        account_budget_dir=budget,
    )
    return build_restricted_command(argv, options.cwd, policy)


def launch_pi(options: PiLaunchOptions, *, rpc: bool = True) -> subprocess.Popen[bytes]:
    """Start Pi. ``rpc=True`` pipes stdio for ``--mode rpc``; otherwise Pi is interactive."""
    root = runtime_root(options)
    command = launch_command(options, rpc=rpc, root=root)
    env = launch_environment(options, root)
    stdio = subprocess.PIPE if rpc else None
    return subprocess.Popen(  # noqa: S603  # reason: argv is built from validated profile fields, never a shell string
        command, cwd=options.cwd, env=env, stdin=stdio, stdout=stdio, stderr=stdio
    )
