"""Configuration parsing and fail-closed validation for the gateway (port of config.ts).

The gateway refuses to boot unless ``PITWALL_GATEWAY_TOKEN`` is set, ``--bind`` is a loopback
address, ``--port`` is a valid TCP port, and either ``PITWALL_GATEWAY_ROUTES`` or
``PITWALL_GATEWAY_UPSTREAM_URL`` names an upstream.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pitwall.gateway.compression import CompressionPolicy
from pitwall.gateway.routes_table import (
    GatewayRoute,
    RouteTableError,
    load_route_table,
    valid_url,
)

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 20130
DEFAULT_BODY_CAP_BYTES = 1024 * 1024
DEFAULT_RATE_LIMIT_RPM = 120
DEFAULT_UPSTREAM_TIMEOUT_S = 30.0

_LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost", "ip6-loopback"})
_UNSIGNED_DECIMAL = re.compile(r"[0-9]+")
_IPV4_LOOPBACK = ipaddress.ip_network("127.0.0.0/8")
_IPV6_LOOPBACK = ipaddress.IPv6Address("::1")


class ConfigError(ValueError):
    """The gateway configuration is invalid; the message is safe to show the operator."""


def is_loopback(host: str) -> bool:
    """Whether ``host`` is 127.0.0.0/8, ``::1``, or a well-known loopback name.

    IPv4-mapped IPv6 forms and DNS names that merely start with ``127.`` are not loopback.
    """
    lower = host.lower()
    if lower in _LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(lower)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv4Address):
        return address in _IPV4_LOOPBACK
    return address == _IPV6_LOOPBACK


@dataclass(frozen=True)
class GatewayConfig:
    """Resolved gateway settings. Construction refuses a non-loopback bind."""

    token: str = field(repr=False)
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    body_cap_bytes: int = DEFAULT_BODY_CAP_BYTES
    rate_limit_rpm: int = DEFAULT_RATE_LIMIT_RPM
    upstream_timeout_s: float = DEFAULT_UPSTREAM_TIMEOUT_S
    upstream_base_url: str | None = None
    upstream_api_key: str | None = field(default=None, repr=False)
    routes: Mapping[str, GatewayRoute] | None = None

    def __post_init__(self) -> None:
        if not self.token:
            raise ConfigError(
                "PITWALL_GATEWAY_TOKEN is required; the gateway refuses to boot without a "
                "bearer token."
            )
        if not is_loopback(self.bind):
            raise ConfigError(
                f"Refusing non-loopback bind: {self.bind}. The gateway only accepts loopback "
                "addresses (127.0.0.0/8, ::1, localhost). Pass --bind 127.0.0.1 to override."
            )
        if not 0 <= self.port <= 65535:
            raise ConfigError(
                f"Invalid --port value: {self.port}. Expected an integer in [0, 65535]."
            )
        if self.body_cap_bytes <= 0 or self.rate_limit_rpm <= 0 or self.upstream_timeout_s <= 0:
            raise ConfigError("body cap, rate limit, and upstream timeout must be positive")
        if self.upstream_base_url is None and not self.routes:
            raise ConfigError("set PITWALL_GATEWAY_ROUTES or PITWALL_GATEWAY_UPSTREAM_URL")


def _read_port(raw: str | None) -> int:
    text = str(DEFAULT_PORT) if raw is None else raw
    # A full unsigned decimal integer only: junk, fractions, signs, exponents, hex, and
    # whitespace are rejected. Port 0 stays valid for ephemeral binding.
    if _UNSIGNED_DECIMAL.fullmatch(text) is None or int(text) > 65535:
        raise ConfigError(f"Invalid --port value: {text}. Expected an integer in [0, 65535].")
    return int(text)


def _read_positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if _UNSIGNED_DECIMAL.fullmatch(raw) is None or int(raw) == 0:
        raise ConfigError(f"{name} must be a positive integer: {raw}")
    return int(raw)


def resolve_config(
    env: Mapping[str, str], *, bind: str | None = None, port: str | None = None
) -> GatewayConfig:
    """Resolve the gateway settings from an environment snapshot and the ``--bind``/``--port`` values.

    Raises :class:`ConfigError` on any failure; callers surface the message to the operator.
    """
    port_number = _read_port(port)
    upstream = env.get("PITWALL_GATEWAY_UPSTREAM_URL", "").strip() or None
    if upstream is not None and not valid_url(upstream):
        raise ConfigError(f"PITWALL_GATEWAY_UPSTREAM_URL is not a valid URL: {upstream}")
    routes_path = env.get("PITWALL_GATEWAY_ROUTES", "").strip()
    try:
        routes = load_route_table(routes_path, env) if routes_path else None
    except RouteTableError as exc:
        raise ConfigError(str(exc)) from None
    return GatewayConfig(
        token=env.get("PITWALL_GATEWAY_TOKEN", ""),
        bind=DEFAULT_BIND if bind is None else bind,
        port=port_number,
        body_cap_bytes=_read_positive_int(
            env, "PITWALL_GATEWAY_BODY_CAP_BYTES", DEFAULT_BODY_CAP_BYTES
        ),
        rate_limit_rpm=_read_positive_int(
            env, "PITWALL_GATEWAY_RATE_LIMIT_RPM", DEFAULT_RATE_LIMIT_RPM
        ),
        upstream_base_url=upstream,
        upstream_api_key=env.get("PITWALL_GATEWAY_UPSTREAM_API_KEY", "").strip() or None,
        routes=routes,
    )


def parse_compression_header(value: str | None) -> CompressionPolicy:
    """Map the ``x-pitwall-compression`` header to a policy; unknown or missing is ``off``."""
    if value == "rtk":
        return "rtk"
    if value == "caveman":
        return "caveman"
    if value == "stacked":
        return "stacked"
    return "off"
