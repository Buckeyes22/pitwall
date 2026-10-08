"""RunPod API-key resolution, shared by `pitwall.personal` and `pitwall.runpod_client`.

This module is intentionally neutral: `pitwall.personal` depends on
`pitwall.runpod_client` (e.g. `personal.service` imports `runpod_client.workloads`), so
`runpod_client` must not import from `personal` without creating a package-level cycle.
Both sides import the resolver from here instead, so a saved `runpodctl` credential
works identically everywhere: the personal CLI (`setup`/`serve`/`status`/`stop`) and the
lower-level RunPod REST clients (pods, registry auth, serverless).

Never logs, prints, or otherwise includes the resolved key value in any message.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

RUNPOD_API_KEY_ENV = "RUNPOD_API_KEY"

#: Default RunPod REST base URL; ``RUNPOD_REST_API_URL`` overrides it. Every client reads this one value.
DEFAULT_RUNPOD_REST_URL = "https://api.runpod.io/v2"

CredentialSource = Literal["env", "runpodctl", "none"]

#: Shown when neither credential route produces a usable key. Names both ways to fix
#: it; never interpolate the key value into this or any other message.
MISSING_CREDENTIAL_MESSAGE = (
    "RUNPOD_API_KEY not set in process env: export RUNPOD_API_KEY, or install "
    "runpodctl and run `runpodctl doctor` to authenticate."
)


def runpodctl_config_path(environ: Mapping[str, str]) -> Path:
    """Return the path to runpodctl's saved credential file for *environ*'s HOME."""

    home = environ.get("HOME") or str(Path.home())
    return Path(home) / ".runpod" / "config.toml"


def resolve_runpod_api_key(
    environ: Mapping[str, str],
    runpodctl_config: Path | None = None,
) -> tuple[str | None, CredentialSource]:
    """Resolve the RunPod API key: the environment variable first, then runpodctl's
    saved credential at ``~/.runpod/config.toml``.

    Returns a ``(key, source)`` tuple. ``source`` is ``"env"`` or ``"runpodctl"`` when a
    key was found, and ``"none"`` when neither route produced one.
    """

    from_env = environ.get(RUNPOD_API_KEY_ENV, "").strip()
    if from_env:
        return from_env, "env"
    path = runpodctl_config if runpodctl_config is not None else runpodctl_config_path(environ)
    if path.exists():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except OSError, tomllib.TOMLDecodeError:
            return None, "none"
        value = data.get("apikey") or data.get("api_key") or data.get("apiKey")
        if isinstance(value, str) and value.strip():
            return value.strip(), "runpodctl"
    return None, "none"


__all__ = [
    "DEFAULT_RUNPOD_REST_URL",
    "MISSING_CREDENTIAL_MESSAGE",
    "RUNPOD_API_KEY_ENV",
    "CredentialSource",
    "resolve_runpod_api_key",
    "runpodctl_config_path",
]
