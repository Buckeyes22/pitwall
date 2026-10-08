"""The gateway route table (port of readRoutes and resolveUpstreamTarget in the Node gateway).

``PITWALL_GATEWAY_ROUTES`` names a JSON file mapping a provider name (the broker's
``x-pitwall-route`` header) to one upstream. The key value is read from the environment at load
time and never logged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from pitwall.gateway.relay import RouteTarget


class RouteTableError(ValueError):
    """The route table file is unreadable or malformed."""


@dataclass(frozen=True)
class GatewayRoute:
    """One provider's upstream, resolved from the route table and the boot environment."""

    base_url: str
    model_id: str
    api_key_env: str | None = None
    api_key: str | None = field(default=None, repr=False)
    key_required: bool = False


@dataclass(frozen=True)
class RouteRefusal:
    """A request the route table refuses before any upstream is contacted."""

    status: int
    type: str
    code: str
    message: str


def valid_url(value: str) -> bool:
    """Whether ``value`` parses as an absolute URL with a scheme and host."""
    try:
        parts = urlsplit(value)
        return bool(parts.scheme and parts.netloc and parts.hostname)
    except ValueError:
        return False


def load_route_table(path: str, env: Mapping[str, str]) -> dict[str, GatewayRoute]:
    """Read and validate the route table file at ``path``."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise RouteTableError(f"PITWALL_GATEWAY_ROUTES could not be read as JSON: {path}") from None
    routes = raw.get("routes") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(routes, dict):
        raise RouteTableError(
            "PITWALL_GATEWAY_ROUTES must be schema_version 1 with a routes object"
        )
    table: dict[str, GatewayRoute] = {}
    for name, entry in routes.items():
        value = entry if isinstance(entry, dict) else {}
        base_url, model_id = value.get("base_url"), value.get("model_id")
        if not isinstance(base_url, str) or not isinstance(model_id, str):
            raise RouteTableError(f"route {name} needs base_url and model_id strings")
        if not valid_url(base_url):
            raise RouteTableError(f"route {name} base_url is not a valid URL")
        env_name = value.get("api_key_env")
        api_key_env = env_name if isinstance(env_name, str) and env_name else None
        api_key = (env.get(api_key_env, "").strip() or None) if api_key_env else None
        table[name] = GatewayRoute(
            base_url=base_url,
            model_id=model_id,
            api_key_env=api_key_env,
            api_key=api_key,
            key_required=value.get("key_required") is True,
        )
    return table


def resolve_route(
    routes: Mapping[str, GatewayRoute], name: str | None
) -> RouteTarget | RouteRefusal:
    """Pick the upstream a request names in ``x-pitwall-route``, or refuse it."""
    if not name:
        return RouteRefusal(
            400, "invalid_request_error", "route_required", "x-pitwall-route is required."
        )
    route = routes.get(name)
    if route is None:
        return RouteRefusal(
            404, "invalid_request_error", "route_not_found", f"Unknown route {name}."
        )
    if route.key_required and not route.api_key:
        return RouteRefusal(
            503,
            "api_error",
            "upstream_key_missing",
            f"Route {name} has no upstream key configured.",
        )
    return RouteTarget(base_url=route.base_url, api_key=route.api_key, model_id=route.model_id)
