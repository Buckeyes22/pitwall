"""Explicit, bounded liveness probe for endpoint routes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib import error, request

from pitwall.providers.model_studio import catalog as model_studio
from pitwall.providers.model_studio import openapi as model_studio_openapi

from . import endpoints
from .broker import resolve_pitwall_api_token
from .endpoints import ENDPOINT_USER_AGENT, running_states
from .http_urls import open_http_url
from .profiles import expiry_state, resolved_endpoint

DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    status: str
    http_status: int | None
    models: tuple[str, ...]
    expires_at: str | None
    remedy: str | None
    detail: str
    lease_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["models"] = list(self.models)
        payload["leaseId"] = payload.pop("lease_id")
        return payload


def _remedy(name: str, entry: Mapping[str, Any], status: str) -> str | None:
    if status == "warming":
        return "the model is loading; retry shortly — cold starts on swapper endpoints commonly take 1–2 minutes"
    origin = entry.get("origin") or {}
    if origin.get("kind") == "pitwall":
        capability = origin["capability"]
        return (
            f"serve or renew capability {capability!r} in Pitwall (`pitwall serve --capability {capability} …` or "
            f"`POST /v1/leases/<id>/renew`), then `pitwall agents profiles refresh {name}`"
        )
    if status == "unauthorized":
        return (
            f"check the value of {entry.get('endpoint', {}).get('apiKeyEnv') or 'the endpoint key'}"
        )
    if status in {"down", "expired"}:
        return "start the endpoint or update the route with `pitwall agents profiles add`"
    if status == "model-missing":
        return "serve the route's model id on the endpoint or change the route's model"
    return None


def probe_profile(
    name: str,
    entry: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProbeResult:
    result = _probe_route(name, entry, env=env, now=now, timeout=timeout)
    lease = (entry.get("origin") or {}).get("leaseId")
    return replace(result, lease_id=str(lease) if lease else None)


def _probe_route(
    name: str,
    entry: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    now: datetime | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProbeResult:
    endpoint = entry.get("endpoint")
    expires_at = entry.get("expiresAt")
    if not endpoint:
        return ProbeResult(
            name,
            "not-applicable",
            None,
            (),
            expires_at,
            None,
            "route has no endpoint; the harness's own configuration applies",
        )
    state, _remaining = expiry_state(entry, now=now or datetime.now(UTC))
    if state == "expired":
        return ProbeResult(
            name,
            "expired",
            None,
            (),
            expires_at,
            _remedy(name, entry, "expired"),
            f"expired at {expires_at}",
        )
    resolved = resolved_endpoint(entry)
    if resolved is not None and resolved.get("kind") == model_studio.KIND:
        return _probe_model_studio(
            name,
            entry,
            resolved,
            env=env,
            now=now or datetime.now(UTC),
            timeout=timeout,
            expires_at=expires_at,
        )
    url = str(endpoint["baseUrl"]).rstrip("/") + "/models"
    headers = {"Accept": "application/json", "User-Agent": ENDPOINT_USER_AGENT}
    key_env = endpoint.get("apiKeyEnv")
    if key_env:
        key_value, _resolved_key_env = resolve_pitwall_api_token(env, str(key_env))
        if key_value:
            headers["Authorization"] = f"Bearer {key_value}"
    try:
        with open_http_url(
            request.Request(url, headers=headers, method="GET"), timeout=timeout
        ) as response:
            status_code = int(response.status)
            body = response.read(1024 * 1024)
    except error.HTTPError as exc:
        exc.close()
        status = "unauthorized" if exc.code in (401, 403) else "down"
        return ProbeResult(
            name,
            status,
            exc.code,
            (),
            expires_at,
            _remedy(name, entry, status),
            f"HTTP {exc.code} from {url}",
        )
    except (TimeoutError, error.URLError, OSError, ValueError) as exc:
        return ProbeResult(
            name, "down", None, (), expires_at, _remedy(name, entry, "down"), f"{url}: {exc}"
        )
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise TypeError("model list is not a list of objects")
        models = tuple(str(item["id"]) for item in data if "id" in item)
    except json.JSONDecodeError, TypeError:
        return ProbeResult(
            name,
            "down",
            status_code,
            (),
            expires_at,
            _remedy(name, entry, "down"),
            f"{url} returned a non-OpenAI models payload",
        )
    model = str(entry["model"])
    states = running_states(str(endpoint["baseUrl"]).rstrip("/"), headers, timeout)
    if model in models and states is not None and states.get(model) == "starting":
        return ProbeResult(
            name,
            "warming",
            status_code,
            models,
            expires_at,
            _remedy(name, entry, "warming"),
            f"{model!r} is starting",
        )
    if model in models:
        return ProbeResult(
            name,
            "reachable",
            status_code,
            models,
            expires_at,
            None,
            f"{len(models)} model(s) listed",
        )
    return ProbeResult(
        name,
        "model-missing",
        status_code,
        models,
        expires_at,
        _remedy(name, entry, "model-missing"),
        f"{model!r} not in {list(models)}",
    )


def _probe_model_studio(
    name: str,
    entry: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    now: datetime,
    timeout: float,
    expires_at: str | None,
) -> ProbeResult:
    raw = entry.get("endpoint")
    endpoint_name = str(raw) if isinstance(raw, str) else name
    plan = str(endpoint["plan"])
    model = str(entry["model"])
    key = env.get(str(endpoint["apiKeyEnv"]), "")
    try:
        if not key:
            raise model_studio.ModelStudioConfigError(
                "missing_key", f"export {endpoint['apiKeyEnv']}"
            )
        model_studio.check_key(plan, key)
        model_studio.check_model(plan, model)
    except model_studio.ModelStudioConfigError as exc:
        return ProbeResult(name, "misconfigured", None, (), expires_at, None, str(exc))
    # The OpenAI-compatible model list, not the native /api/v1/models: the Token Plan host
    # answers the native path with 404 (live check, 2026-09-26).
    url = f"{model_studio.base_url(endpoint, protocol='openai').rstrip('/')}/models"
    headers = {
        "Accept": "application/json",
        "User-Agent": ENDPOINT_USER_AGENT,
        "Authorization": f"Bearer {key}",
    }
    try:
        with open_http_url(
            request.Request(url, headers=headers, method="GET"), timeout=timeout
        ) as response:
            status_code = int(response.status)
            payload = json.loads(response.read(1024 * 1024).decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        exc.close()
        status = "unauthorized" if exc.code in (401, 403) else "down"
        return ProbeResult(
            name,
            status,
            exc.code,
            (),
            expires_at,
            None,
            f"HTTP {exc.code} from the Model Studio models API",
        )
    except (TimeoutError, error.URLError, OSError, ValueError) as exc:
        return ProbeResult(
            name, "down", None, (), expires_at, None, f"Model Studio models API: {exc}"
        )
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, list) or not all(isinstance(item, Mapping) for item in data):
        return ProbeResult(
            name,
            "down",
            status_code,
            (),
            expires_at,
            None,
            "Model Studio models API returned a malformed model list",
        )
    listed = tuple(str(item.get("id")) for item in data)
    if model not in listed:
        return ProbeResult(
            name,
            "model-missing",
            status_code,
            listed,
            expires_at,
            None,
            f"{model} is not listed for this key",
        )
    locked_until = endpoints.read_lockout(env, endpoint_name, now=now)
    if locked_until is not None:
        return ProbeResult(
            name,
            "quota-exhausted",
            status_code,
            listed,
            expires_at,
            None,
            f"Credits exhausted; locked until {locked_until}",
        )
    detail = "reachable"
    if model_studio.is_token_plan(plan):
        try:
            stats = model_studio_openapi.get_subscription_stats_sync(env, now=now, timeout=timeout)
        except (error.URLError, OSError, ValueError, KeyError) as exc:
            stats = None
            detail = f"reachable; Credits stats unavailable ({type(exc).__name__})"
        if stats is not None:
            if stats.remaining_credits <= 0:
                return ProbeResult(
                    name,
                    "quota-exhausted",
                    status_code,
                    listed,
                    expires_at,
                    None,
                    f"0 Credits remaining; renews {stats.reset_at.date().isoformat()}",
                )
            detail = f"reachable; {stats.remaining_credits:.0f} of {stats.total_credits:.0f} Credits remaining; renews {stats.reset_at.date().isoformat()}"
        elif endpoint.get("renewsOn"):
            detail = f"{detail}; renews {model_studio.credits_window(str(endpoint['renewsOn']), now)[1].date().isoformat()}"
    return ProbeResult(name, "reachable", status_code, listed, expires_at, None, detail)
