"""Provider URL helpers for the capability resolution path.

Given a ``Provider`` record and its ``provider_type``, resolve the concrete
RunPod URL(s) that Pitwall should call.  Each provider type maps to a bounded
RunPod surface:

    serverless_queue  → ``https://api.runpod.ai/v2/{endpoint_id}/…``
    serverless_lb     → ``https://{endpoint_id}.api.runpod.ai/{path}``
    public_endpoint   → ``https://api.runpod.ai/v2/{endpoint_id}/openai/v1``
    pod_lease         → no URL (TCP proxy via lease endpoints)

All functions accept a ``Provider`` model (from ``pitwall.core.models``) and
return plain strings.  Callers that need more control (custom path, timeout,
auth header) should use the lower-level ``runpod_client.queue`` /
``runpod_client.lb`` helpers directly.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from pitwall.core.enums import ProviderType
from pitwall.core.models import Provider

# RunPod endpoint ids are short alphanumeric tokens. This allow-list pins the
# charset so an id can never inject a host label, path segment, port, userinfo,
# or metadata IP into the outbound URL (SSRF). Leading char must be
# alphanumeric; the remainder may include '-'/'_' (2–64 chars total).
_ENDPOINT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")


def openai_base_url(provider: Provider) -> str:
    """Return the OpenAI-compatible base URL for *provider*.

    Valid for ``serverless_queue``, ``public_endpoint``, and
    ``serverless_lb`` providers that expose an ``/openai/v1`` surface.
    ``pod_lease`` providers do not have an OpenAI-compatible URL.

    Raises:
        ValueError: If the provider type is ``pod_lease``.
        ValueError: If the provider has no ``runpod_endpoint_id``.
    """
    if provider.provider_type == ProviderType.POD_LEASE:
        raise ValueError("pod_lease providers do not expose an OpenAI-compatible URL")
    _require_endpoint_id(provider)
    endpoint_id = provider.runpod_endpoint_id
    assert endpoint_id is not None
    if provider.provider_type == ProviderType.SERVERLESS_LB:
        return f"https://{endpoint_id}.api.runpod.ai/openai/v1"
    return f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1"


def queue_url(provider: Provider, path: str = "") -> str:
    """Return the queue-based serverless URL for *provider*.

    For ``serverless_queue`` and ``public_endpoint`` providers the base URL
    is ``https://api.runpod.ai/v2/{endpoint_id}``.  An optional *path*
    (e.g. ``"/runsync"``) is appended.

    Raises:
        ValueError: If the provider type is not queue-based.
        ValueError: If the provider has no ``runpod_endpoint_id``.
    """
    _require_endpoint_id(provider)
    if provider.provider_type not in (
        ProviderType.SERVERLESS_QUEUE,
        ProviderType.PUBLIC_ENDPOINT,
    ):
        raise ValueError(
            f"queue_url is not applicable to provider_type={provider.provider_type.value!r}"
        )
    endpoint_id = provider.runpod_endpoint_id
    assert endpoint_id is not None
    base = f"https://api.runpod.ai/v2/{endpoint_id}"
    if path:
        return f"{base}{path}"
    return base


def lb_url(provider: Provider, path: str = "/") -> str:
    """Return the load-balancing serverless URL for *provider*.

    For ``serverless_lb`` providers the base URL is
    ``https://{endpoint_id}.api.runpod.ai``.  An optional *path* (e.g.
    ``"/embed"``) is appended.

    Raises:
        ValueError: If the provider type is not ``serverless_lb``.
        ValueError: If the provider has no ``runpod_endpoint_id``.
    """
    _require_endpoint_id(provider)
    if provider.provider_type != ProviderType.SERVERLESS_LB:
        raise ValueError(
            f"lb_url is not applicable to provider_type={provider.provider_type.value!r}"
        )
    endpoint_id = provider.runpod_endpoint_id
    assert endpoint_id is not None
    path = path if path.startswith("/") else f"/{path}"
    return f"https://{endpoint_id}.api.runpod.ai{path}"


def public_endpoint_url(provider: Provider) -> str:
    """Return the public endpoint URL for *provider*.

    For ``public_endpoint`` providers this is
    ``https://api.runpod.ai/v2/{endpoint_id}/openai/v1``, identical to
    ``openai_base_url`` but restricted to ``public_endpoint`` only.

    Raises:
        ValueError: If the provider type is not ``public_endpoint``.
        ValueError: If the provider has no ``runpod_endpoint_id``.
    """
    _require_endpoint_id(provider)
    if provider.provider_type != ProviderType.PUBLIC_ENDPOINT:
        raise ValueError(
            f"public_endpoint_url is not applicable to provider_type="
            f"{provider.provider_type.value!r}"
        )
    endpoint_id = provider.runpod_endpoint_id
    assert endpoint_id is not None
    return f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1"


def provider_url(provider: Provider) -> str:
    """Return the primary URL for *provider* based on its ``provider_type``.

    This is a convenience dispatcher:

    - ``serverless_queue``  → queue base URL
    - ``serverless_lb``     → LB base URL (``/``)
    - ``public_endpoint``   → OpenAI-compatible base URL
    - ``pod_lease``         → raises ``ValueError``

    Raises:
        ValueError: If the provider type is ``pod_lease``.
        ValueError: If the provider has no ``runpod_endpoint_id``.
    """
    pt = provider.provider_type
    if pt == ProviderType.SERVERLESS_QUEUE:
        return queue_url(provider)
    if pt == ProviderType.SERVERLESS_LB:
        return lb_url(provider)
    if pt == ProviderType.PUBLIC_ENDPOINT:
        return public_endpoint_url(provider)
    raise ValueError("pod_lease providers do not have a single primary URL")


def _require_endpoint_id(provider: Provider) -> None:
    endpoint_id = provider.runpod_endpoint_id
    if not endpoint_id:
        raise ValueError(f"provider {provider.id!r} has no runpod_endpoint_id configured")
    if not _ENDPOINT_ID_RE.match(endpoint_id):
        raise ValueError(
            f"provider {provider.id!r} has an invalid runpod_endpoint_id "
            f"{endpoint_id!r}: must match {_ENDPOINT_ID_RE.pattern}"
        )


_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata"})


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and for any loopback IP literal (127.0.0.0/8, ::1)."""
    host = host.lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_base_url(base_url: str) -> bool:
    """True when *base_url* points at the loopback fork (Task 19 auth policy)."""
    return is_loopback_host(urlsplit(base_url).hostname or "")


def _internal_host_kind(host: str) -> str | None:
    """Name why *host* is an internal target, or ``None`` for anything else.

    Only literal addresses and the fixed names below are recognised; a hostname is never
    resolved, so a public name that resolves to a private address is not caught here.
    """
    host = host.lower().rstrip(".")
    if host in _METADATA_HOSTS:
        return "metadata"
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link-local"
    if address.is_private or address.is_reserved or address.is_unspecified or address.is_multicast:
        return "private"
    return None


def validate_openai_base_url(value: str, *, allow_local: bool = False) -> str:
    """Validate and normalize an OpenAI-compatible base URL and return it without a trailing slash.

    Always rejects a missing host, user info, query strings, and fragments. Then, unless
    *allow_local* is set because the provider is explicitly local:

    - ``http`` is allowed only for loopback hosts (``localhost`` or a loopback IP literal,
      the same rule as ``providers.gateway.is_loopback_base_url``).
    - ``https`` is rejected for private, link-local, loopback, reserved, or unspecified IP
      literals, ``localhost`` names, and the cloud metadata names.

    Hostnames are not resolved: this guards what the URL text says, not where DNS points. It is
    the shared validator behind ``GatewayCredentials.base_url``.
    """

    stripped = value.strip()
    if not stripped:
        raise ValueError("openai_base_url must be non-empty")
    parsed = urlsplit(stripped)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("openai_base_url must use the http or https scheme")
    if not parsed.netloc:
        raise ValueError("openai_base_url must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("openai_base_url must not include user info")
    if parsed.query or parsed.fragment:
        raise ValueError("openai_base_url must not include query strings or fragments")
    if not allow_local:
        host = parsed.hostname or ""
        if parsed.scheme == "http":
            if not is_loopback_host(host):
                raise ValueError("openai_base_url http scheme is only allowed for loopback hosts")
        elif (kind := _internal_host_kind(host)) is not None:
            raise ValueError(
                f"openai_base_url must not point at a {kind} address unless the provider is local"
            )
    return stripped.rstrip("/")


__all__ = [
    "is_loopback_base_url",
    "is_loopback_host",
    "lb_url",
    "openai_base_url",
    "provider_url",
    "public_endpoint_url",
    "queue_url",
    "validate_openai_base_url",
]
