"""Endpoint key lifecycle and RunPod credential resolution. Never writes the RunPod key."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from pitwall.runpod_credentials import (
    CredentialSource,
    resolve_runpod_api_key,
    runpodctl_config_path,
)

__all__ = [
    "ENDPOINT_KEY_ENV",
    "CredentialSource",
    "config_file_is_shared",
    "ensure_endpoint_key",
    "read_endpoint_key",
    "resolve_runpod_api_key",
    "runpodctl_config_path",
]

ENDPOINT_KEY_ENV = "PITWALL_ENDPOINT_KEY"
_KEY_FILE = "endpoint.key"


def ensure_endpoint_key(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / _KEY_FILE
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32))
    os.chmod(path, 0o600)
    return path


def read_endpoint_key(root: Path) -> str | None:
    path = root / _KEY_FILE
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def config_file_is_shared(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o077)
