"""First-run setup: credential, endpoint key, shell profile, plugin CLI, backend."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pitwall.personal.backend import BackendName, backend_status_line, select_backend
from pitwall.personal.keys import (
    ENDPOINT_KEY_ENV,
    CredentialSource,
    config_file_is_shared,
    ensure_endpoint_key,
    resolve_runpod_api_key,
    runpodctl_config_path,
)
from pitwall.personal.routes import DEFAULT_ROUTING_CLI, routing_available

_PROFILE_MARK = "# pitwall endpoint key"


@dataclass(frozen=True, slots=True)
class SetupReport:
    credential_source: CredentialSource
    config_tightened: bool
    endpoint_key_path: Path
    profile_updated: bool
    routing_cli_found: bool
    backend: BackendName


def _shell_kind(environ: Mapping[str, str]) -> str:
    """``zsh``, ``fish``, or ``bash`` from ``$SHELL`` (zsh when unset on macOS, else bash)."""
    name = Path(environ.get("SHELL", "")).name
    if name in {"zsh", "fish"}:
        return name
    if not name and sys.platform == "darwin":
        return "zsh"
    return "bash"


def _profile_for(environ: Mapping[str, str], home: Path) -> Path:
    """The startup file the user's shell reads: login shells on macOS skip ``~/.bashrc``."""
    kind = _shell_kind(environ)
    if kind == "zsh":
        return home / ".zshrc"
    if kind == "fish":
        return home / ".config" / "fish" / "config.fish"
    return home / (".bash_profile" if sys.platform == "darwin" else ".bashrc")


def _export_line(environ: Mapping[str, str], key_path: Path) -> str:
    """The profile line that exports the endpoint key, in the syntax of the user's shell."""
    quoted = shlex.quote(str(key_path))
    if _shell_kind(environ) == "fish":
        return f"set -gx {ENDPOINT_KEY_ENV} (cat {quoted})"
    return f'export {ENDPOINT_KEY_ENV}="$(cat {quoted})"'


def run_setup(
    *,
    environ: Mapping[str, str],
    home: Path,
    state_root: Path,
    prompt: Callable[[str], bool],
    run: Callable[..., object] = subprocess.run,
    out: Callable[[str], None] = print,
) -> SetupReport:
    key, source = resolve_runpod_api_key(environ, runpodctl_config_path({"HOME": str(home)}))
    if key is None:
        if shutil.which("runpodctl", path=environ.get("PATH")) and prompt(
            "No RunPod credential found. Run `runpodctl doctor` to sign in now?"
        ):
            run(["runpodctl", "doctor"], check=False)
            key, source = resolve_runpod_api_key(
                environ, runpodctl_config_path({"HOME": str(home)})
            )
        else:
            out(
                "No RunPod credential: export RUNPOD_API_KEY, or install runpodctl and "
                "run `runpodctl doctor`."
            )
    out(f"RunPod credential: {source}")

    tightened = False
    config = runpodctl_config_path({"HOME": str(home)})
    if (
        config.exists()
        and config_file_is_shared(config)
        and prompt(f"{config} is readable by other users. Make it owner-only?")
    ):
        os.chmod(config, 0o600)
        tightened = True

    key_path = ensure_endpoint_key(state_root)
    line = _export_line(environ, key_path)
    profile = _profile_for(environ, home)
    profile_updated = False
    existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
    if _PROFILE_MARK in existing or line in existing:
        out(f"Endpoint key already exported in {profile}")
    elif prompt(f"Add the endpoint key export to {profile}?"):
        profile.parent.mkdir(parents=True, exist_ok=True)
        with profile.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{_PROFILE_MARK}\n{line}\n")
        profile_updated = True
        out(f"Added to {profile}; open a new shell before using Claude Code.")
    else:
        out(f"Run this in shells that use Claude Code: {line}")

    routing_cli = environ.get("PITWALL_ROUTING_CLI", "").strip() or DEFAULT_ROUTING_CLI
    routing_found = routing_available(routing_cli, environ)
    if not routing_found:
        out(
            f"The routing command `{routing_cli}` is not on PATH; unset PITWALL_ROUTING_CLI "
            "to use the built-in `pitwall agents`."
        )

    budget = environ.get("PITWALL_MONTHLY_BUDGET_USD", "").strip()
    if budget:
        out(f"Monthly budget: {budget} USD (serve refuses leases that would exceed it)")
    else:
        out(
            "No monthly budget: export PITWALL_MONTHLY_BUDGET_USD=<usd> in your shell profile; "
            "serve refuses to launch without one."
        )

    backend = select_backend(environ)
    out(backend_status_line(backend))
    return SetupReport(source, tightened, key_path, profile_updated, routing_found, backend)
