"""Client for the Pitwall broker: capability metadata, synchronization metadata, and webhook automation over httpx, using the broker API's own Pydantic models."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib import parse
from urllib.parse import parse_qs, urlparse, urlsplit

if TYPE_CHECKING:
    import httpx

from .channel import _STEERABLE_STATES, load_channel_config, steer_refusal
from .events import EventEmitter
from .http_urls import ALLOWED_SCHEMES
from .mailbox import (
    MailboxCapError,
    MailboxError,
    MailboxOpenAskError,
    validate_steer_request,
)
from .run_store import RunStore, atomic_write_json, find_run, state_root, utc_now

PITWALL_URL_ENV = "PITWALL_API_URL"
PITWALL_TOKEN_ENV = "PITWALL_AGENTS_API_TOKEN"
PITWALL_SUBSCRIPTION_TOKEN_ENV = "PITWALL_AGENTS_SUBSCRIPTION_TOKEN"
DEFAULT_TIMEOUT = 10.0


class PitwallError(RuntimeError):
    """The Pitwall API was unreachable or returned an unusable capability document."""


class ServeRefused(PitwallError):
    """Pitwall refused a capability-only serve request."""

    def __init__(self, capability: str, code: str) -> None:
        known_codes = {
            "cap_exceeded",
            "price_unknown",
            "budget_exhausted",
            "kill_switch_engaged",
            "no_serve_history",
        }
        self.code = code if code in known_codes else "unknown"
        super().__init__(f"Pitwall refused to serve capability {capability!r}: {self.code}")


def resolve_pitwall_api_token(
    env: Mapping[str, str],
    api_key_env: str | None = None,
) -> tuple[str, str]:
    """Resolve normal routing credentials without broadening arbitrary env aliases."""
    requested = api_key_env or PITWALL_TOKEN_ENV
    return env.get(requested, ""), requested


def pitwall_api_token_requirement(api_key_env: str | None = None) -> str:
    return api_key_env or PITWALL_TOKEN_ENV


def resolve_subscription_token(env: Mapping[str, str]) -> tuple[str, str]:
    """Resolve the admin-only subscription credential and the variable it came from."""
    return env.get(PITWALL_SUBSCRIPTION_TOKEN_ENV, ""), PITWALL_SUBSCRIPTION_TOKEN_ENV


@dataclass(frozen=True, slots=True)
class CapabilityInfo:
    name: str
    served_model_id: str | None
    lease_id: str | None
    expires_at: str | None


def proxy_base_url(pitwall_url: str, capability: str) -> str:
    return f"{pitwall_url.rstrip('/')}/v1/openai/{parse.quote(capability, safe='.-_')}/v1"


def _normalize_expiry(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    text = re.sub(r"\.(\d+)(?=[+-]\d{2}:\d{2}$)", "", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PitwallError(f"unparseable active_lease.expires_at {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def refreshed_entry(
    entry: Mapping[str, Any], info: CapabilityInfo, pitwall_url: str
) -> dict[str, Any]:
    """A copy of a pitwall-origin route entry with live capability metadata applied.

    Only model, expiresAt, origin.leaseId/state/url, and endpoint.baseUrl change; the
    user's seat, args, env, effort, harness, and apiKeyEnv are preserved.
    """
    updated = dict(entry)
    origin = dict(updated["origin"])
    base = pitwall_url.rstrip("/")
    if info.served_model_id:
        updated["model"] = info.served_model_id
    if info.expires_at:
        updated["expiresAt"] = info.expires_at
    else:
        updated.pop("expiresAt", None)
    was_leased = bool(
        origin.get("leaseId")
        or entry.get("expiresAt")
        or origin.get("state") in {"active", "stopped"}
    )
    origin["leaseId"] = info.lease_id
    # Retain stopped history across refreshes; a never-leased capability stays
    # unknown and must pass the model endpoint probe on every dispatch.
    origin["state"] = (
        "active" if info.lease_id or info.expires_at else ("stopped" if was_leased else "unknown")
    )
    origin["url"] = base
    updated["origin"] = origin
    endpoint = dict(updated.get("endpoint") or {})
    endpoint["baseUrl"] = proxy_base_url(base, str(origin["capability"]))
    updated["endpoint"] = endpoint
    return updated


_MAX_RESPONSE_BYTES = 1024 * 1024
_SUBSCRIPTION_CONSUMER = "pitwall-agents"


def _send(
    method: str,
    url: str,
    token: str,
    *,
    body: Mapping[str, Any] | None = None,
    timeout: float,
    error_type: type[PitwallError] | type[PitwallSyncError],
) -> httpx.Response:
    """One httpx call to the broker; transport failures become ``error_type``."""
    # httpx loads on first broker call so the stdlib-only shim and hook paths stay import-light.
    import httpx

    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise error_type(
            f"refusing URL with scheme {scheme or '(none)'!r}; only http and https are allowed"
        )
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        response = httpx.request(
            method,
            url,
            headers=headers,
            json=None if body is None else dict(body),
            timeout=timeout,
        )
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise error_type(f"cannot reach Pitwall at {url}: {exc}") from exc
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise error_type(f"Pitwall returned an oversized document for {url}")
    return response


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    try:
        document = response.json()
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def fetch_capability(
    pitwall_url: str,
    capability: str,
    token: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    token_env: str = PITWALL_TOKEN_ENV,
) -> CapabilityInfo:
    from pydantic import ValidationError

    from pitwall.api.capability_schemas import CapabilityResponse

    url = f"{pitwall_url.rstrip('/')}/v1/capabilities/{parse.quote(capability, safe='.-_')}"
    response = _send("GET", url, token, timeout=timeout, error_type=PitwallError)
    status = response.status_code
    if status in (401, 403):
        raise PitwallError(f"Pitwall rejected the token in {token_env} (HTTP {status})")
    if status == 404:
        raise PitwallError(f"Pitwall has no capability named {capability!r}")
    if status >= 400:
        raise PitwallError(f"Pitwall returned HTTP {status} for {url}")
    payload = _json_object(response)
    if payload is None:
        raise PitwallError(f"Pitwall returned non-JSON for {url}")
    try:
        document = CapabilityResponse.model_validate(payload)
    except ValidationError as exc:
        raise PitwallError(f"Pitwall returned an unexpected document for {url}") from exc
    lease = document.active_lease or {}
    return CapabilityInfo(
        name=document.name,
        served_model_id=document.served_model_id or None,
        lease_id=lease.get("lease_id") or None,
        expires_at=_normalize_expiry(lease.get("expires_at")),
    )


def serve_capability(
    pitwall_url: str,
    capability: str,
    token: str,
    *,
    caps: Mapping[str, Any],
    timeout: float = DEFAULT_TIMEOUT,
    token_env: str = PITWALL_TOKEN_ENV,
) -> dict[str, Any]:
    from pydantic import ValidationError

    from pitwall.api.schemas.serve import ServeCreate, ServeResponse

    url = f"{pitwall_url.rstrip('/')}/v1/serve"
    cap_names = {
        "ttlMinutes": "ttl_minutes",
        "idleTimeoutMinutes": "idle_timeout_min",
        "maxUsdPerHour": "max_usd_per_hour",
    }
    values: dict[str, Any] = {"capability": capability}
    for route_name, request_name in cap_names.items():
        if route_name in caps:
            values[request_name] = caps[route_name]
    try:
        # The API's own request model builds (and validates) the payload; the
        # USD cap is a Decimal there and serializes as a JSON string.
        request_model = ServeCreate.model_validate(values)
    except ValidationError as exc:
        raise PitwallError(f"invalid serve request for capability {capability!r}: {exc}") from exc
    payload = request_model.model_dump(mode="json", exclude_unset=True)
    response = _send("POST", url, token, body=payload, timeout=timeout, error_type=PitwallError)
    status = response.status_code
    if status == 422:
        refusal = _json_object(response) or {}
        code = refusal.get("error")
        raise ServeRefused(capability, code if isinstance(code, str) else "unknown")
    if status in (401, 403):
        raise PitwallError(f"Pitwall rejected the token in {token_env} (HTTP {status})")
    if status == 404:
        raise PitwallError(f"Pitwall has no capability named {capability!r}")
    if status >= 400:
        raise PitwallError(f"Pitwall returned HTTP {status} for {url}")
    document = _json_object(response)
    if document is None:
        raise PitwallError(f"Pitwall returned non-JSON for {url}")
    try:
        return ServeResponse.model_validate(document).model_dump(mode="json", exclude_unset=True)
    except ValidationError as exc:
        raise PitwallError(f"Pitwall returned an unexpected document for {url}") from exc


def wait_until_served(
    pitwall_url: str,
    capability: str,
    token: str,
    *,
    deadline_seconds: float,
    poll_seconds: float = 5.0,
    sleep: Callable[[float], object] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    token_env: str = PITWALL_TOKEN_ENV,
) -> CapabilityInfo:
    deadline = now() + deadline_seconds
    while True:
        info = fetch_capability(pitwall_url, capability, token, token_env=token_env)
        if info.served_model_id and info.lease_id:
            return info
        remaining = deadline - now()
        if remaining <= 0:
            raise PitwallError(f"capability {capability!r} not ready after {deadline_seconds:g}s")
        sleep(min(poll_seconds, remaining))


SyncSource = Literal["refresh", "receiver", "self-heal"]
SYNC_FILE = "pitwall-sync.json"
PITWALL_WEBHOOK_SECRET_ENV = "PITWALL_WEBHOOK_SECRET_ENV"  # pragma: allowlist secret
DEFAULT_WEBHOOK_SECRET_ENV = "PITWALL_AGENTS_WEBHOOK_SECRET"  # pragma: allowlist secret
LEGACY_WEBHOOK_SECRET_ENV = "PITWALL_WEBHOOK_SECRET"  # pragma: allowlist secret
WEBHOOK_EVENTS = ("lease.ready", "lease.renewed", "lease.stopped", "lease.expiring")
MAX_DELIVERY_IDS = 100
_RECENT_DELIVERIES: deque[str] = deque()
_RECENT_DELIVERY_SET: set[str] = set()
_DELIVERY_LOCK = threading.Lock()


class PitwallSyncError(RuntimeError):
    """A receiver or subscription operation could not be completed."""


def resolve_webhook_secret(env: Mapping[str, str]) -> tuple[str, str]:
    """Resolve the receiver secret with explicit indirection kept fail-closed."""
    if PITWALL_WEBHOOK_SECRET_ENV in env:
        secret_env = env[PITWALL_WEBHOOK_SECRET_ENV]
        return env.get(secret_env, ""), secret_env
    value = env.get(DEFAULT_WEBHOOK_SECRET_ENV, "")
    if value:
        return value, DEFAULT_WEBHOOK_SECRET_ENV
    value = env.get(LEGACY_WEBHOOK_SECRET_ENV, "")
    if value:
        return value, LEGACY_WEBHOOK_SECRET_ENV
    return "", DEFAULT_WEBHOOK_SECRET_ENV


def webhook_secret_export_env(env: Mapping[str, str]) -> str:
    """Return the explicit receiver target or the new default for a fresh secret."""
    if PITWALL_WEBHOOK_SECRET_ENV in env and env[PITWALL_WEBHOOK_SECRET_ENV]:
        return env[PITWALL_WEBHOOK_SECRET_ENV]
    return DEFAULT_WEBHOOK_SECRET_ENV


def _sync_path(env: Mapping[str, str]) -> Path:
    return state_root(env) / SYNC_FILE


def load_sync(env: Mapping[str, str]) -> dict[str, Any]:
    """Load the Pitwall synchronization sidecar, or an empty document."""
    path = _sync_path(env)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"routes": {}}
    if not isinstance(value, dict) or not isinstance(value.get("routes"), dict):
        return {"routes": {}}
    return value


def record_sync(env: Mapping[str, str], name: str, source: SyncSource) -> None:
    """Record the last successful synchronization of one route."""
    state = load_sync(env)
    routes = dict(state["routes"])
    routes[name] = {"lastUpdatedAt": utc_now(), "source": source}
    atomic_write_json(_sync_path(env), {**state, "routes": routes})


def verify_signature(
    body: bytes,
    header: str,
    secret: str,
    *,
    now: Callable[[], float] = time.time,
    max_age_s: int = 300,
) -> bool:
    """Verify Pitwall's ``t=...,v1=...`` HMAC signature and replay window."""
    try:
        fields = dict(part.split("=", 1) for part in header.split(","))
        timestamp = int(fields["t"])
        supplied = fields["v1"]
    except KeyError, ValueError, TypeError:
        return False
    if abs(now() - timestamp) > max_age_s:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode("ascii") + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _normalize_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _remember_delivery(env: Mapping[str, str], delivery_id: str) -> None:
    state = load_sync(env)
    stored = state.get("deliveryIds")
    ids = [item for item in stored if isinstance(item, str)] if isinstance(stored, list) else []
    ids = [item for item in ids if item != delivery_id]
    ids.append(delivery_id)
    atomic_write_json(_sync_path(env), {**state, "deliveryIds": ids[-MAX_DELIVERY_IDS:]})
    if len(_RECENT_DELIVERIES) >= MAX_DELIVERY_IDS:
        removed = _RECENT_DELIVERIES.popleft()
        _RECENT_DELIVERY_SET.discard(removed)
    _RECENT_DELIVERIES.append(delivery_id)
    _RECENT_DELIVERY_SET.add(delivery_id)


def _is_replayed(env: Mapping[str, str], delivery_id: str) -> bool:
    if delivery_id in _RECENT_DELIVERY_SET:
        return True
    stored = load_sync(env).get("deliveryIds")
    return isinstance(stored, list) and delivery_id in stored


def apply_event(
    event: Mapping[str, Any], env: Mapping[str, str], registry: Mapping[str, Any]
) -> str:
    """Apply one verified Pitwall lease event and return its disposition."""
    from .broker import PITWALL_TOKEN_ENV
    from .profiles import add_profile, load_profiles, save_profiles

    delivery_id = event.get("delivery_id")
    event_name = event.get("event")
    capability = event.get("capability")
    data = event.get("data")
    if not isinstance(delivery_id, str) or not delivery_id:
        raise PitwallSyncError("webhook event has no delivery_id")
    if not isinstance(event_name, str) or not isinstance(capability, str):
        raise PitwallSyncError("webhook event is missing event or top-level capability")
    if not isinstance(data, Mapping):
        raise PitwallSyncError("webhook event data must be an object")

    with _DELIVERY_LOCK:
        if _is_replayed(env, delivery_id):
            return "replayed"
        if event_name not in {*WEBHOOK_EVENTS, "lease.terminated"}:
            _remember_delivery(env, delivery_id)
            return "ignored"

        config = load_profiles(env, registry=registry)
        models = config["models"]
        matched = [
            name
            for name, entry in models.items()
            if (entry.get("origin") or {}).get("capability") == capability
        ]
        changed = False
        if event_name == "lease.ready" and not matched:
            auto_register = (config.get("pitwall") or {}).get("autoRegister") or {}
            route_name = auto_register.get(capability)
            if isinstance(route_name, str):
                model = data.get("served_model_id")
                lease_id = data.get("lease_id")
                proxy_url = data.get("proxy_base_url")
                pitwall_url = env.get("PITWALL_API_URL", "").rstrip("/")
                if (
                    not isinstance(model, str)
                    or not model
                    or not isinstance(lease_id, str)
                    or not lease_id
                    or not isinstance(proxy_url, str)
                    or not proxy_url
                    or not pitwall_url
                ):
                    raise PitwallSyncError(
                        "lease.ready cannot auto-register without model, lease, proxy, and PITWALL_API_URL"
                    )
                origin = {
                    "kind": "pitwall",
                    "capability": capability,
                    "leaseId": lease_id,
                    "url": pitwall_url,
                    "state": "active",
                }
                config = add_profile(
                    config,
                    route_name,
                    model=model,
                    base_url=proxy_url,
                    api_key_env=PITWALL_TOKEN_ENV,
                    expires_at=_normalize_timestamp(data.get("expires_at")),
                    origin=origin,
                )
                models = config["models"]
                matched = [route_name]
                changed = True

        if event_name in {"lease.ready", "lease.renewed", "lease.stopped", "lease.terminated"}:
            for name in matched:
                entry = dict(models[name])
                origin = dict(entry["origin"])
                if event_name == "lease.ready":
                    model = data.get("served_model_id")
                    lease_id = data.get("lease_id")
                    proxy_url = data.get("proxy_base_url")
                    if isinstance(model, str) and model:
                        entry["model"] = model
                    expiry = _normalize_timestamp(data.get("expires_at"))
                    if expiry:
                        entry["expiresAt"] = expiry
                    else:
                        entry.pop("expiresAt", None)
                    if isinstance(lease_id, str) and lease_id:
                        origin["leaseId"] = lease_id
                    origin["state"] = "active"
                    if isinstance(proxy_url, str) and proxy_url:
                        endpoint = dict(entry.get("endpoint") or {})
                        endpoint["baseUrl"] = proxy_url
                        entry["endpoint"] = endpoint
                elif event_name == "lease.renewed":
                    expiry = _normalize_timestamp(data.get("expires_at"))
                    if not expiry:
                        raise PitwallSyncError("lease.renewed has no valid expires_at")
                    entry["expiresAt"] = expiry
                else:
                    origin["state"] = "stopped"
                    entry.pop("expiresAt", None)
                entry["origin"] = origin
                models[name] = entry
                changed = True

        if changed:
            save_profiles(env, config, registry=registry)
        if event_name == "lease.expiring":
            print(
                json.dumps(
                    {"event": event_name, "capability": capability, "delivery_id": delivery_id},
                    sort_keys=True,
                ),
                flush=True,
            )
        for name in matched:
            record_sync(env, name, "receiver")
        _remember_delivery(env, delivery_id)
        return f"applied:{','.join(matched)}" if matched else "ignored"


CHANNEL_BODY_CAP = 1024 * 1024
# Oversized bodies are drained up to this many bytes so the peer can read the 413.
_DRAIN_CAP = 8 * 1024 * 1024
CHANNEL_INBOX_PUBLIC_ENV = "PITWALL_CHANNEL_PUBLIC_INBOX"


class ChannelError(PitwallSyncError):
    """A channel request failed with an explicit HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _channel_delivery_id(payload: Mapping[str, Any]) -> str:
    delivery_id = payload.get("delivery_id")
    if not isinstance(delivery_id, str) or not delivery_id:
        raise ChannelError("channel post requires a delivery_id", status=400)
    return delivery_id


def _channel_store(env: Mapping[str, str], dispatch_id: Any) -> RunStore:
    if not isinstance(dispatch_id, str) or not dispatch_id:
        raise ChannelError("channel post requires a dispatch_id", status=400)
    try:
        run_path = find_run(env, dispatch_id)
    except FileNotFoundError as exc:
        raise ChannelError(str(exc), status=404) from exc
    return RunStore(state_root(env), run_path.name)


def _channel_broker_emitter(store: RunStore) -> EventEmitter:
    return EventEmitter(store, harness="broker", model="channel")


def apply_channel_ask(
    payload: Mapping[str, Any], raw: bytes, env: Mapping[str, str]
) -> dict[str, Any]:
    """Validate and file one subagent ASK; returns the stored ask summary."""
    if not isinstance(payload, dict):
        raise ChannelError("ask body must be an object", status=400)
    delivery_id = _channel_delivery_id(payload)
    with _DELIVERY_LOCK:
        if _is_replayed(env, delivery_id):
            raise ChannelError("replayed_delivery_id", status=409)
        store = _channel_store(env, payload.get("dispatch_id"))
        box = store.mailbox()
        worktree = payload.get("worktree")
        rationale = payload.get("default_rationale")
        requested_deadline = payload.get("deadline_s")
        deadline: Any = requested_deadline
        if isinstance(requested_deadline, int) and not isinstance(requested_deadline, bool):
            from .channel import load_channel_config, write_deadline_cap_s

            deadline = min(
                requested_deadline,
                write_deadline_cap_s(load_channel_config(store.path), time.time()),
            )
        try:
            from .channel import load_channel_config

            config = load_channel_config(store.path)
            existing = box.find_by_delivery("asks", delivery_id)
            if existing is not None:
                _remember_delivery(env, delivery_id)
                return {
                    "status": f"recorded:asks/{existing['ask_id']}",
                    "askId": existing["ask_id"],
                    "idempotent": True,
                }
            ask = box.write_ask(
                blocked_on=payload.get("blocked_on", ""),
                question=payload.get("question", ""),
                options=payload.get("options", []),
                default=payload.get("default"),
                deadline_s=deadline,
                files_touched=payload.get("files_touched", []),
                worktree=worktree if isinstance(worktree, str) else None,
                default_rationale=rationale if isinstance(rationale, str) else None,
                severity=payload.get("severity", "normal"),
                max_open=config.max_open_asks if config else 1,
                delivery_id=delivery_id,
            )
        except MailboxOpenAskError as exc:
            # §10: one unresolved ask per programmatic writer; not a schema fault.
            raise ChannelError("ask_already_open", status=409) from exc
        except MailboxCapError as exc:
            # The mailbox owns the D1 cap (including any per-run override);
            # a refused ask is not a schema fault, so it is not dead-lettered.
            raise ChannelError("ask_cap_exceeded", status=429) from exc
        except MailboxError as exc:
            box.quarantine("asks", delivery_id, raw, str(exc))
            raise ChannelError("invalid ask schema", status=400) from exc
        _remember_delivery(env, delivery_id)
    return {"status": f"recorded:asks/{ask['ask_id']}", "askId": ask["ask_id"]}


def apply_channel_answer(
    ask_id: str, payload: Mapping[str, Any], raw: bytes, env: Mapping[str, str]
) -> dict[str, Any]:
    """File the broker-side answer resolving one ask and emit ask.resolved."""
    if not ask_id:
        raise ChannelError("answer path requires an ask id", status=400)
    if not isinstance(payload, dict):
        raise ChannelError("answer body must be an object", status=400)
    delivery_id = _channel_delivery_id(payload)
    with _DELIVERY_LOCK:
        if _is_replayed(env, delivery_id):
            raise ChannelError("replayed_delivery_id", status=409)
        store = _channel_store(env, payload.get("dispatch_id"))
        box = store.mailbox()
        note = payload.get("note")
        try:
            answer = box.write_answer(
                ask_id,
                choice=payload.get("choice", ""),
                answered_by=payload.get("answered_by", ""),
                note=note if isinstance(note, str) else None,
            )
        except MailboxError as exc:
            stored = box.get_answer(ask_id)
            if stored is not None:
                # The broker died between "file written" and "delivery remembered".
                if stored.get("choice") == payload.get("choice") and stored.get(
                    "answered_by"
                ) == payload.get("answered_by"):
                    _remember_delivery(env, delivery_id)
                    return {"status": f"recorded:answers/{ask_id}", "idempotent": True}
                raise ChannelError("ask_already_answered", status=409) from exc
            box.quarantine("answers", ask_id, raw, str(exc))
            raise ChannelError("invalid answer", status=400) from exc
        _channel_broker_emitter(store).emit_ask_resolved(ask_id, str(answer["answered_by"]))
        _remember_delivery(env, delivery_id)
    return {"status": f"recorded:answers/{ask_id}"}


def _steer_refusal(store: RunStore, kind: str) -> str | None:
    try:
        state = json.loads((store.path / "run.json").read_text(encoding="utf-8")).get("state")
    except OSError, json.JSONDecodeError, AttributeError:
        state = None
    if state not in _STEERABLE_STATES:
        # The same eligibility CLI and MCP steering apply: no directive for a finished run.
        return f"run {store.path.name} is {state}; steering applies to running or paused runs"
    return steer_refusal(store.path, kind, state)


def apply_channel_steer(
    payload: Mapping[str, Any], raw: bytes, env: Mapping[str, str]
) -> dict[str, Any]:
    """File one orchestrator STEER directive.

    A repeated delivery id gets its original reply first, but only a mailbox that
    already exists is searched. Then the payload is validated without touching the
    filesystem, a steer the run can never read is refused, and only then is the
    mailbox opened. A malformed or refused steer to a run without a channel record
    leaves no trace in the run directory.
    """
    if not isinstance(payload, dict):
        raise ChannelError("steer body must be an object", status=400)
    delivery_id = _channel_delivery_id(payload)
    with _DELIVERY_LOCK:
        if _is_replayed(env, delivery_id):
            raise ChannelError("replayed_delivery_id", status=409)
        store = _channel_store(env, payload.get("dispatch_id"))
        if (store.path / "mailbox").is_dir():
            existing = store.mailbox().find_by_delivery("steer", delivery_id)
            if existing is not None:
                _remember_delivery(env, delivery_id)
                return {
                    "status": f"recorded:steer/{existing['steer_id']}",
                    "steerId": existing["steer_id"],
                    "idempotent": True,
                }
        kind = payload.get("kind", "")
        try:
            validate_steer_request(
                store.dispatch_id,
                kind=kind,
                message=payload.get("message", ""),
                requires_ack=payload.get("requires_ack", True),
                deadline_s=payload.get("deadline_s", 300),
                delivery_id=delivery_id,
            )
        except MailboxError as exc:
            if load_channel_config(store.path) is not None:
                store.mailbox().quarantine("steer", delivery_id, raw, str(exc))
            # else: a run with no channel record gets no mailbox just to hold a dead letter.
            raise ChannelError("invalid steer schema", status=400) from exc
        refusal = _steer_refusal(store, kind)
        if refusal is not None:
            raise ChannelError(refusal, status=409)
        box = store.mailbox()
        try:
            steer = box.write_steer(
                kind=kind,
                message=payload.get("message", ""),
                requires_ack=payload.get("requires_ack", True),
                deadline_s=payload.get("deadline_s", 300),
                delivery_id=delivery_id,
            )
        except MailboxError as exc:
            box.quarantine("steer", delivery_id, raw, str(exc))
            raise ChannelError("invalid steer schema", status=400) from exc
        _remember_delivery(env, delivery_id)
    return {"status": f"recorded:steer/{steer['steer_id']}", "steerId": steer["steer_id"]}


def read_channel_inbox(env: Mapping[str, str], dispatch_id: str | None) -> dict[str, Any]:
    """``channel.read_channel_inbox`` with the HTTP 404 mapping for unknown runs."""
    from .channel import read_channel_inbox as _read_inbox

    try:
        return _read_inbox(env, dispatch_id)
    except FileNotFoundError as exc:
        raise ChannelError(str(exc), status=404) from exc


def parse_interval(value: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", value.strip().lower())
    if match is None:
        raise ValueError(f"invalid interval {value!r}; use seconds or a suffix s, m, or h")
    amount = float(match.group(1))
    if amount <= 0:
        raise ValueError("interval must be greater than zero")
    return amount * {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group(2)]


class _IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def run_receiver(
    host: str,
    port: int,
    env: Mapping[str, str],
    registry: Mapping[str, Any],
) -> None:
    """Run the loopback webhook receiver until interrupted."""
    if host not in {"127.0.0.1", "::1"}:
        raise PitwallSyncError("receiver host must be loopback (127.0.0.1 or ::1)")
    secret, secret_env = resolve_webhook_secret(env)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok"})
                return
            parsed_path = urlparse(self.path)
            if parsed_path.path == "/inbox":
                query = parse_qs(parsed_path.query)
                self._channel_inbox(query.get("dispatch_id", [None])[0])
                return
            self._send(404, {"error": "not_found"})

        def _channel_inbox(self, dispatch_id: str | None) -> None:
            if env.get(CHANNEL_INBOX_PUBLIC_ENV) != "1":
                if not secret:
                    self._send(503, {"error": f"set {secret_env}"})
                    return
                header = self.headers.get("X-Pitwall-Signature", "")
                if not verify_signature(b"", header, secret):
                    self._send(401, {"error": "invalid_signature"})
                    return
            try:
                payload = read_channel_inbox(env, dispatch_id)
            except ChannelError as exc:
                self._send(exc.status, {"error": str(exc)})
                return
            self._send(200, payload)

        def do_POST(self) -> None:
            if self.path == "/pitwall":
                self._webhook()
                return
            if self.path == "/asks":
                self._channel_post(lambda parsed, raw: apply_channel_ask(parsed, raw, env))
                return
            if self.path == "/steer":
                self._channel_post(lambda parsed, raw: apply_channel_steer(parsed, raw, env))
                return
            if self.path.startswith("/answers/"):
                ask_id = self.path[len("/answers/") :].split("?", 1)[0].split("/", 1)[0]
                self._channel_post(
                    lambda parsed, raw: apply_channel_answer(ask_id, parsed, raw, env)
                )
                return
            self._send(404, {"error": "not_found"})

        def _channel_post(self, apply: Callable[[dict[str, Any], bytes], dict[str, Any]]) -> None:
            if not secret:
                self._send(503, {"error": f"set {secret_env}"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_content_length"})
                return
            if length <= 0:
                self._send(400, {"error": "invalid_body_size"})
                return
            if length > CHANNEL_BODY_CAP:
                # Drain what the client is still sending before answering, otherwise the
                # peer can hit a broken pipe mid-write and never see the 413 (macOS).
                self._drain(length)
                self._send(413, {"error": "body_too_large"}, close=True)
                return
            body = self.rfile.read(length)
            header = self.headers.get("X-Pitwall-Signature", "")
            if not verify_signature(body, header, secret):
                self._send(401, {"error": "invalid_signature"})
                return
            try:
                parsed = json.loads(body)
                if not isinstance(parsed, dict):
                    raise ValueError("body must be an object")
            except (json.JSONDecodeError, ValueError) as exc:
                self._send(400, {"error": str(exc)})
                return
            try:
                disposition = apply(parsed, body)
            except ChannelError as exc:
                self._send(exc.status, {"error": str(exc)})
                return
            except MailboxError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(202, disposition)

        def _webhook(self) -> None:
            if not secret:
                self._send(503, {"error": f"set {secret_env}"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_content_length"})
                return
            if length <= 0 or length > 1024 * 1024:
                self._send(400, {"error": "invalid_body_size"})
                return
            body = self.rfile.read(length)
            header = self.headers.get("X-Pitwall-Signature", "")
            if not verify_signature(body, header, secret):
                self._send(401, {"error": "invalid_signature"})
                return
            try:
                parsed = json.loads(body)
                if not isinstance(parsed, dict):
                    raise ValueError("event must be an object")
                disposition = apply_event(parsed, env, registry)
            except (json.JSONDecodeError, ValueError, PitwallSyncError) as exc:
                self._send(400, {"error": str(exc)})
                return
            if disposition == "replayed":
                self._send(409, {"error": "replayed_delivery_id"})
                return
            self._send(202, {"status": disposition})

        def _drain(self, length: int) -> None:
            remaining = min(length, _DRAIN_CAP)
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            if length > _DRAIN_CAP:
                self.close_connection = True

        def _send(self, status: int, value: Mapping[str, Any], *, close: bool = False) -> None:
            payload = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_: object) -> None:
            return None

    server_class = _IPv6ThreadingHTTPServer if host == "::1" else ThreadingHTTPServer
    server = server_class((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def create_subscription(
    pitwall_url: str,
    token: str,
    receiver_url: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    from pydantic import ValidationError

    from pitwall.core.models import WebhookSubscriptionCreate, WebhookSubscriptionCreated

    url = f"{pitwall_url.rstrip('/')}/v1/webhook-subscriptions"
    try:
        request_model = WebhookSubscriptionCreate.model_validate(
            {
                "consumer": _SUBSCRIPTION_CONSUMER,
                "webhook_url": receiver_url,
                "event_types": list(WEBHOOK_EVENTS),
            }
        )
    except ValidationError as exc:
        raise PitwallSyncError(f"invalid subscription request: {exc}") from exc
    response = _send(
        "POST",
        url,
        token,
        body=request_model.model_dump(mode="json"),
        timeout=timeout,
        error_type=PitwallSyncError,
    )
    status = response.status_code
    if status >= 400:
        detail = _json_object(response) or {}
        code = detail.get("error")
        if status == 422 and code == "webhook_target_not_allowed":
            raise PitwallSyncError(
                "Pitwall refused the loopback webhook target; on the API host set "
                "PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST=127.0.0.1:8765 and restart the API "
                "(subscription management requires the webhook:admin scope)"
            )
        if status in {401, 403}:
            raise PitwallSyncError(
                f"Pitwall rejected the token (HTTP {status}); subscription management requires the webhook:admin scope"
            )
        raise PitwallSyncError(f"Pitwall returned HTTP {status} for {url}")
    document = _json_object(response)
    if document is None:
        raise PitwallSyncError("Pitwall returned non-JSON subscription data")
    try:
        created = WebhookSubscriptionCreated.model_validate(document)
    except ValidationError as exc:
        raise PitwallSyncError("Pitwall returned an unexpected subscription document") from exc
    return created.model_dump(mode="json")
