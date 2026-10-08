"""The one place the agents package opens a URL, restricted to http and https."""

from __future__ import annotations

from typing import Any
from urllib import request
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsupportedUrlSchemeError(ValueError):
    """A URL used a scheme other than http or https (for example file:// or ftp://)."""


def open_http_url(target: str | request.Request, *, timeout: float) -> Any:
    """Open an http(s) URL or Request; refuse every other scheme before any I/O."""

    req = request.Request(target) if isinstance(target, str) else target
    scheme = urlsplit(req.full_url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsupportedUrlSchemeError(
            f"refusing URL with scheme {scheme or '(none)'!r}; only http and https are allowed"
        )
    return request.urlopen(req, timeout=timeout)  # noqa: S310  # reason: scheme validated above  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
