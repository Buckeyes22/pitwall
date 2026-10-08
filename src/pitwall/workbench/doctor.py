"""Workbench doctor: pinned Pi toolchain, runtime prerequisites, hosted-profile metadata.

Port of ``doctor.ts``, plus the pinned-toolchain check every Pi-launching command runs first and
the ``workbench`` section of ``pitwall doctor``. Nothing here starts Pi or contacts a provider.
"""

from __future__ import annotations

import json
import os
import platform as platform_module
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pitwall.workbench.hosted_profiles import (
    ApiKind,
    EndpointClass,
    HostedProfileReport,
    ProviderMetadata,
    credential_entry_status,
    discover_hosted_profiles,
    hosted_profile_doctor,
)
from pitwall.workbench.pi_pin import (
    PI_PACKAGE,
    PINNED_PI_VERSION,
    PINNED_SUBAGENTS_VERSION,
    SUBAGENTS_PACKAGE,
)
from pitwall.workbench.restricted import setpriv_supports_seccomp_filter

if TYPE_CHECKING:
    from pitwall.doctor import DoctorSection

SUBAGENTS_ENTRY = "dist/index.js"
INSTALL_COMMAND = (
    f"npm install -g --ignore-scripts {PI_PACKAGE}@{PINNED_PI_VERSION} "
    f"{SUBAGENTS_PACKAGE}@{PINNED_SUBAGENTS_VERSION}"
)
MINIMUM_NODE = (22, 22, 1)
BWRAP = "/usr/bin/bwrap"
SETPRIV = "/usr/bin/setpriv"
SUPPORTED_ARCHITECTURES = ("x64", "arm64")
_ARCHITECTURE_NAMES = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}


class ToolchainError(RuntimeError):
    """The pinned Pi toolchain is missing or at the wrong version."""


@dataclass(frozen=True)
class Toolchain:
    """What is installed: ``None`` fields mean not found (or version unreadable)."""

    pi_bin: Path | None
    pi_version: str | None
    subagents_entry: Path | None
    subagents_version: str | None


def _owning_version(start: Path, package: str) -> str | None:
    """Version of the ``package`` directory that contains ``start`` (a file inside it)."""
    for directory in Path(os.path.realpath(start)).parents:
        manifest = directory / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return None
        if isinstance(data, dict) and data.get("name") == package:
            version = data.get("version")
            return version if isinstance(version, str) else None
    return None


def inspect_toolchain() -> Toolchain:
    """Locate Pi and its subagents backend and read their installed versions, without running them."""
    from pitwall.workbench.comparison.session import pinned_node_module
    from pitwall.workbench.launcher import LauncherError, default_pi_bin

    try:
        pi_bin: Path | None = default_pi_bin()
    except LauncherError:
        pi_bin = None
    if pi_bin is None:
        return Toolchain(None, None, None, None)
    entry = pinned_node_module(SUBAGENTS_PACKAGE, SUBAGENTS_ENTRY, pi_bin)
    return Toolchain(
        pi_bin,
        _owning_version(pi_bin, PI_PACKAGE),
        entry,
        _owning_version(entry, SUBAGENTS_PACKAGE) if entry is not None else None,
    )


def _install_hint() -> str:
    return f"install the pinned toolchain with `pitwall agents setup pi` or `{INSTALL_COMMAND}`"


def toolchain_problem(toolchain: Toolchain, *, subagents: bool) -> str | None:
    """The reason the toolchain cannot run the workbench, or ``None`` when it can."""
    pins = f"{PI_PACKAGE} {PINNED_PI_VERSION} and {SUBAGENTS_PACKAGE} {PINNED_SUBAGENTS_VERSION}"
    if toolchain.pi_bin is None:
        return f"pi is not installed; the workbench needs {pins}; {_install_hint()}"
    if toolchain.pi_version != PINNED_PI_VERSION:
        found = toolchain.pi_version or "an unreadable version"
        return (
            f"{PI_PACKAGE} {found} is installed at {toolchain.pi_bin}; "
            f"{PINNED_PI_VERSION} is required; {_install_hint()}"
        )
    return _subagents_problem(toolchain) if subagents else None


def _subagents_problem(toolchain: Toolchain) -> str | None:
    if toolchain.subagents_version == PINNED_SUBAGENTS_VERSION:
        return None
    if toolchain.subagents_entry is None:
        found = f"{SUBAGENTS_PACKAGE} is not installed"
    else:
        found = f"{SUBAGENTS_PACKAGE} {toolchain.subagents_version or 'unreadable'}"
    return f"{found}; {PINNED_SUBAGENTS_VERSION} is required; {_install_hint()}"


def require_pinned_toolchain(*, subagents: bool) -> Toolchain:
    """Check the pinned toolchain before a command creates any worktree or state.

    Raises :class:`ToolchainError` carrying the install command on a missing or wrong-version Pi
    (and, when ``subagents`` is true, its backend).
    """
    toolchain = inspect_toolchain()
    problem = toolchain_problem(toolchain, subagents=subagents)
    if problem is not None:
        raise ToolchainError(problem)
    return toolchain


def toolchain_summary(toolchain: Toolchain, *, subagents: bool) -> str:
    """One line naming the pinned versions in use."""
    parts = [f"{PI_PACKAGE} {toolchain.pi_version}"]
    if subagents:
        parts.append(f"{SUBAGENTS_PACKAGE} {toolchain.subagents_version}")
    return "workbench toolchain: " + ", ".join(parts)


# --- runtime prerequisites --------------------------------------------------------------------


@dataclass(frozen=True)
class DoctorProbe:
    """Overrides that make the runtime report deterministic; ``None`` means probe the host."""

    platform: str | None = None
    architecture: str | None = None
    node_version: str | None = None
    find_executable: Callable[[str, Mapping[str, str]], str | None] | None = None
    path_exists: Callable[[str], bool] | None = None
    setpriv_supports_seccomp_filter: Callable[[str], bool] | None = None


def _find_executable(name: str, env: Mapping[str, str]) -> str | None:
    path = env.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    for entry in path.split(os.pathsep):
        candidate = Path(entry or ".").resolve() / name
        if os.access(candidate, os.X_OK) and candidate.is_file():
            return str(candidate)
    return None


def _executable_exists(path: str) -> bool:
    return os.access(path, os.X_OK)


def _host_node_version(env: Mapping[str, str]) -> str | None:
    node = shutil.which("node", path=env.get("PATH"))
    if node is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603  # reason: fixed argv, absolute node path from PATH, no shell
            [node, "--version"], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.SubprocessError:
        return None
    return completed.stdout.strip().removeprefix("v") or None


def _supported_node(version: str | None) -> bool:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version or "")
    return bool(match) and tuple(int(part) for part in match.groups()) >= MINIMUM_NODE  # type: ignore[union-attr]  # reason: match is truthy on the right of the and


def _host_platform() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _host_architecture() -> str:
    machine = platform_module.machine().lower()
    return _ARCHITECTURE_NAMES.get(machine, machine)


def runtime_doctor(
    env: Mapping[str, str] | None = None, probe: DoctorProbe | None = None
) -> dict[str, Any]:
    """Deterministic runtime prerequisites: Node, flock, and the restricted-mode tools."""
    env = os.environ if env is None else env
    probe = probe or DoctorProbe()
    system = probe.platform or _host_platform()
    architecture = probe.architecture or _host_architecture()
    node_version = probe.node_version if probe.node_version is not None else _host_node_version(env)
    find = probe.find_executable or _find_executable
    exists = probe.path_exists or _executable_exists
    supports_seccomp = probe.setpriv_supports_seccomp_filter or setpriv_supports_seccomp_filter
    linux = system == "linux"
    flock_path = find("flock", env) if linux else None
    # Restricted launch uses these exact absolute paths; a different PATH entry must not make
    # doctor claim that restricted mode is ready.
    bubblewrap_path = BWRAP if linux and exists(BWRAP) else None
    setpriv_path = SETPRIV if linux and exists(SETPRIV) else None
    restricted: dict[str, Any]
    if not linux:
        restricted = {"status": "unsupported-platform"}
    elif architecture not in SUPPORTED_ARCHITECTURES:
        restricted = {"status": "unsupported-architecture", "architecture": architecture}
    elif bubblewrap_path is None:
        restricted = {"status": "missing-bubblewrap"}
    elif setpriv_path is None:
        restricted = {"status": "missing-setpriv"}
    elif not supports_seccomp(setpriv_path):
        restricted = {"status": "setpriv-lacks-seccomp-filter", "setpriv": setpriv_path}
    else:
        restricted = {
            "status": "available",
            "architecture": architecture,
            "bubblewrap": bubblewrap_path,
            "setpriv": setpriv_path,
        }
    unsupported = {"status": "unsupported-platform"}
    return {
        "node": {"version": node_version, "supported": _supported_node(node_version)},
        "platform": system,
        "architecture": {
            "name": architecture,
            "supported": architecture in SUPPORTED_ARCHITECTURES,
        },
        "flock": unsupported
        if not linux
        else {"status": "available", "path": flock_path}
        if flock_path
        else {"status": "missing-flock"},
        "bubblewrap": {**unsupported, "optional": True}
        if not linux
        else {"status": "available", "optional": True, "path": bubblewrap_path}
        if bubblewrap_path
        else {"status": "missing-bubblewrap", "optional": True},
        "setpriv": unsupported
        if not linux
        else {"status": "available", "path": setpriv_path}
        if setpriv_path
        else {"status": "missing-setpriv"},
        "restricted": restricted,
    }


# --- hosted-profile metadata ------------------------------------------------------------------

_OFFICIAL_ENDPOINTS = {
    "alibaba-token-plan": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
    "minimax": "https://api.minimax.io/anthropic",
    "minimax-coding-plan": "https://api.minimax.io/anthropic",
    "zai": "https://api.z.ai/api/coding/paas/v4",
    "zai-coding-plan": "https://api.z.ai/api/coding/paas/v4",
}
_BUILTIN: dict[str, tuple[list[str], str, ApiKind]] = {
    "minimax-coding-plan": (
        ["MiniMax-M2.7", "MiniMax-M2.7-highspeed", "MiniMax-M3"],
        "https://api.minimax.io/anthropic",
        "anthropic-messages",
    ),
    "zai-coding-plan": (
        ["glm-4.7", "glm-5-turbo", "glm-5.2", "glm-5.3"],
        "https://api.z.ai/api/coding/paas/v4",
        "openai-completions",
    ),
}


def _read_json_if_present(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _endpoint_class(name: str, url: str | None) -> EndpointClass | None:
    return "official-api" if url is not None and _OFFICIAL_ENDPOINTS.get(name) == url else None


def read_opencode_metadata(
    config_path: Path | str, auth_path: Path | str
) -> list[ProviderMetadata]:
    """Provider metadata from the local OpenCode config and auth store; missing files are empty.

    A file that exists but is not valid JSON raises ``ValueError``.
    """
    config = _read_json_if_present(config_path) or {}
    auth = _read_json_if_present(auth_path) or {}
    providers = config.get("provider") if isinstance(config, dict) else None
    providers = providers if isinstance(providers, dict) else {}
    auth_names = list(auth) if isinstance(auth, dict) else []
    entries: list[ProviderMetadata] = []
    for name, value in providers.items():
        value = value if isinstance(value, dict) else {}
        options = value.get("options")
        base_url = options.get("baseURL") if isinstance(options, dict) else None
        if name == "alibaba-token-plan" and isinstance(base_url, str) and base_url.endswith("/v1"):
            base_url = base_url[:-3]
        models = value.get("models")
        npm = value.get("npm")
        entries.append(
            ProviderMetadata(
                name=name,
                base_url=base_url,
                models=list(models) if isinstance(models, dict) else [],
                auth_store_entry=name
                if credential_entry_status(auth, name) == "available"
                else None,
                endpoint_class=_endpoint_class(name, base_url),
                api="anthropic-messages"
                if isinstance(npm, str) and "anthropic" in npm
                else "openai-completions",
            )
        )
    configured = set(providers)
    for name in auth_names:
        if name in configured:
            continue
        models, base_url, api = _BUILTIN.get(name, ([], None, None))
        entries.append(
            ProviderMetadata(
                name=name,
                models=list(models),
                auth_store_entry=name
                if credential_entry_status(auth, name) == "available"
                else None,
                base_url=base_url,
                endpoint_class=_endpoint_class(name, base_url) if base_url else None,
                api=api,
            )
        )
    return entries


def _hosted_report(report: HostedProfileReport) -> dict[str, Any]:
    return {
        "provider": report.provider,
        "account": report.account,
        "endpointClass": report.endpoint_class,
        "modelConfigured": report.model_configured,
        "credentialConfigured": report.credential_configured,
        "retry": {
            "maxAttempts": report.retry.max_attempts,
            "quotaErrors": report.retry.quota_errors,
            "backoffMs": list(report.retry.backoff_ms),
        },
        "status": report.status,
    }


def doctor(
    env: Mapping[str, str] | None = None,
    metadata: list[ProviderMetadata] | None = None,
    probe: DoctorProbe | None = None,
) -> dict[str, Any]:
    """The ``pitwall workbench doctor`` report; it never contains a credential value."""
    env = os.environ if env is None else env
    return {
        "hosted": [
            _hosted_report(hosted_profile_doctor(profile, env))
            for profile in discover_hosted_profiles(metadata or [])
        ],
        "native": {"status": "local-profile-only"},
        "runtime": runtime_doctor(env, probe),
    }


# --- the `workbench` section of `pitwall doctor` ----------------------------------------------

Status = Literal["ok", "warn", "fail", "skip"]


def workbench_section() -> DoctorSection:
    """Pinned toolchain and runtime readiness. The workbench is optional, so gaps warn or skip."""
    from pitwall.doctor import DoctorCheck, DoctorSection

    phase = "workbench"
    toolchain = inspect_toolchain()
    checks: list[DoctorCheck] = []

    pi_problem = toolchain_problem(toolchain, subagents=False)
    checks.append(
        DoctorCheck(
            "workbench.pi",
            phase,
            "warn" if pi_problem else "ok",
            pi_problem or f"{PI_PACKAGE} {toolchain.pi_version} at {toolchain.pi_bin}",
            _install_hint() if pi_problem else None,
        )
    )
    backend_problem = (
        "not checked; pi is not installed"
        if toolchain.pi_bin is None
        else _subagents_problem(toolchain)
    )
    checks.append(
        DoctorCheck(
            "workbench.pi_subagents",
            phase,
            "warn" if backend_problem else "ok",
            backend_problem or f"{SUBAGENTS_PACKAGE} {toolchain.subagents_version}",
            _install_hint() if backend_problem else None,
        )
    )

    runtime = runtime_doctor()
    node = runtime["node"]
    node_status: Status = "ok" if node["supported"] else "warn"
    checks.append(
        DoctorCheck(
            "workbench.node",
            phase,
            node_status,
            f"node {node['version']}"
            if node["supported"]
            else f"node {node['version'] or 'not found'}; 22.22.1 or newer is required by Pi",
            None
            if node["supported"]
            else "install Node.js 22.22.1 or newer from https://nodejs.org",
        )
    )
    flock = runtime["flock"]
    flock_ok = flock["status"] == "available"
    checks.append(
        DoctorCheck(
            "workbench.flock",
            phase,
            "ok" if flock_ok else "warn",
            f"flock at {flock['path']}" if flock_ok else f"flock: {flock['status']}",
            None if flock_ok else "install util-linux so shared admission can lock",
        )
    )
    restricted = runtime["restricted"]
    available = restricted["status"] == "available"
    checks.append(
        DoctorCheck(
            "workbench.restricted",
            phase,
            "ok" if available else "skip",
            "restricted mode is available"
            if available
            else f"restricted mode unavailable: {restricted['status']} (optional)",
            None
            if available
            else "restricted mode needs Linux, bubblewrap, and setpriv from util-linux 2.41 or newer",
        )
    )
    return DoctorSection("workbench", tuple(checks))
