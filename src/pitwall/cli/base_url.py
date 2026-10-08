"""The one default base URL clients use to reach the Pitwall API.

``PITWALL_API_URL`` or ``PITWALL_BASE_URL`` names it outright; otherwise it is the loopback
address on ``PITWALL_API_PORT`` (8080 when unset), so moving the API's port moves every client.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_API_PORT = 8080


def default_base_url(environ: Mapping[str, str] | None = None) -> str:
    """The API base URL from the environment, without a trailing slash."""
    env = os.environ if environ is None else environ
    for name in ("PITWALL_API_URL", "PITWALL_BASE_URL"):
        value = env.get(name, "").strip().rstrip("/")
        if value:
            return value
    raw_port = env.get("PITWALL_API_PORT", "").strip()
    port = int(raw_port) if raw_port.isdigit() and 1 <= int(raw_port) <= 65535 else DEFAULT_API_PORT
    return f"http://127.0.0.1:{port}"


def configured_base_url(configured: str, environ: Mapping[str, str] | None = None) -> str:
    """*configured* (a settings value) when it is set, else :func:`default_base_url`."""
    return configured.strip().rstrip("/") or default_base_url(environ)


__all__ = ["DEFAULT_API_PORT", "configured_base_url", "default_base_url"]
