"""Resolve a profile spec (``name[@harness]``) into a runnable dispatch, including self-heal."""

from __future__ import annotations

import fcntl
import os
import re
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pitwall.providers.model_studio import catalog as model_studio

from . import endpoints
from .broker import (
    PITWALL_TOKEN_ENV,
    PitwallError,
    ServeRefused,
    fetch_capability,
    pitwall_api_token_requirement,
    record_sync,
    refreshed_entry,
    resolve_pitwall_api_token,
    serve_capability,
    wait_until_served,
)
from .dispatch import (  # shared sentinel writer; kept private to the package
    _emit_sentinel,
    dispatch_legacy,
)
from .errors import EX_CONFIG, ProfileConfigError, UsageError
from .harnesses import get_adapter
from .profiles import (
    RESOLVE_SKEW_SECONDS,
    USAGE,
    EndpointReference,
    ProfilesError,
    _expiry_remedy,
    _known_registry_model,
    caller_sets_effort,
    effort_spec,
    entry_harness,
    expiry_state,
    find_vendor_harness,
    load_profiles,
    render_effort,
    resolved_endpoint,
    save_profiles,
    validate_effort,
)
from .registry import RegistryError, load_registry
from .run_store import (
    ensure_private_directory,
    find_run,
    state_root,
)

HARNESS_ID = re.compile(r"[a-z0-9-]+")


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    spec: str
    name: str
    harness: str
    model: str
    model_arg: str
    argv: tuple[str, ...]
    env_updates: Mapping[str, str]
    env_keys: tuple[str, ...]
    endpoint_host: str | None
    native_to: tuple[str, ...]
    sync_status: str
    notices: tuple[str, ...]
    entry: Mapping[str, Any]
    effort: str | None = None
    effort_source: str | None = None
    revived: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec,
            "name": self.name,
            "harness": self.harness,
            "model": self.model,
            "endpointHost": self.endpoint_host,
            "effort": self.effort,
            "effortSource": self.effort_source,
        }


def parse_spec(spec: str) -> tuple[str, str | None]:
    if not spec or spec.startswith("@"):
        raise UsageError(f"route-shim: invalid route spec {spec!r}; expected <name>[@<harness>]")
    name, separator, harness = spec.partition("@")
    if not separator:
        return name, None
    if not harness or "@" in harness or HARNESS_ID.fullmatch(harness) is None:
        raise UsageError(f"route-shim: invalid harness override in {spec!r}")
    return name, harness


def build_argv(
    harness: Mapping[str, Any],
    model_arg: str,
    prompt_source: str,
    entry_args: list[str],
    caller_args: tuple[str, ...] | list[str],
) -> list[str]:
    if harness["defaultModel"]["source"] == "positional":
        base = [model_arg, prompt_source]
    else:
        selectors = harness["modelSelectors"]
        base = [prompt_source] if not selectors else [prompt_source, selectors[0], model_arg]
    return [*base, *entry_args, *caller_args]


def _with_routing_defaults(argv: list[str], entry: Mapping[str, Any]) -> list[str]:
    result = list(argv)
    for key, flag in (("workspace", "--routing-workspace"), ("taskMode", "--routing-task-mode")):
        if key in entry and not any(
            argument == flag or argument.startswith(flag + "=") for argument in argv
        ):
            result.extend([flag, str(entry[key])])
    return result


def describe_profile(
    name: str,
    *,
    registry: Mapping[str, Any],
    routes: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Secret-free listing row; never raises for a single bad route."""
    entry = routes["models"][name]
    row_harness = entry_harness(entry, routes["defaults"], registry)
    current = now or datetime.now(UTC)
    state, remaining = expiry_state(entry, now=current)
    row: dict[str, Any] = {
        "name": name,
        "model": entry["model"],
        "harness": None,
        "seat": entry.get("seat"),
        "endpointHost": urlsplit(str(entry["endpoint"]["baseUrl"])).netloc
        if "endpoint" in entry
        else None,
        "syncStatus": "not-required",
        "status": "ok",
        "detail": "",
        "expiresAt": entry.get("expiresAt"),
        "expiresIn": remaining,
        "effort": entry.get("effort")
        or routes.get("harnesses", {}).get(row_harness, {}).get("effort"),
    }
    if state == "expired":
        row["status"] = "expired"
        row["detail"] = _expiry_remedy(name, entry)
    try:
        resolved = resolve_profile(
            name,
            registry=registry,
            routes=routes,
            env=env,
            home=home,
            prompt_source="<prompt>",
            caller_args=(),
            now=current,
        )
    except ProfileConfigError as exc:
        if state != "expired":
            text = str(exc)
            row["status"] = "needs-sync" if "profiles sync" in text else "needs-key"
            row["detail"] = text
        _, override = parse_spec(name)
        row["harness"] = override or effective_harness(entry, routes=routes, registry=registry)
        return row
    except UsageError as exc:
        row["status"], row["detail"] = "invalid", str(exc)
        return row
    row["harness"] = resolved.harness
    row["syncStatus"] = resolved.sync_status
    row["effort"] = resolved.effort
    return row


def effective_harness(
    entry: Mapping[str, Any], *, routes: Mapping[str, Any], registry: Mapping[str, Any]
) -> str:
    """The harness a route runs on: its own pin, else the endpoint or vendor default."""
    if "harness" in entry:
        return str(entry["harness"])
    if entry.get("endpoint"):
        return str(routes["defaults"]["endpointHarness"])
    return find_vendor_harness(registry, str(entry["model"])) or str(routes["defaults"]["harness"])


def resolve_profile(
    spec: str,
    *,
    registry: Mapping[str, Any],
    routes: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
    prompt_source: str,
    caller_args: tuple[str, ...] | list[str],
    now: datetime | None = None,
) -> ResolvedProfile:
    name, override = parse_spec(spec)
    harnesses = registry["harnesses"]
    if override is not None and override not in harnesses:
        raise UsageError(f"route-shim: unknown harness {override!r}")
    entry: Mapping[str, Any] | None = routes["models"].get(name)
    if entry is None:
        known = _known_registry_model(registry, name)
        if known is not None:
            entry = {"model": known[1], "args": [], "env": {}}
        elif find_vendor_harness(registry, name) is not None:
            # An id a harness's routeFamilies patterns claim (e.g. an effort-suffixed Antigravity
            # slug or any qwen* id) passes through to that vendor harness.
            entry = {"model": name, "args": [], "env": {}}
        else:
            guess = override or routes["defaults"]["harness"]
            if "/" in name and harnesses[guess]["harnessKind"] == "model-agnostic":
                entry = {"model": name, "args": [], "env": {}}
            else:
                raise UsageError(
                    f"route-shim: unknown route {name!r}; add it with `pitwall agents profiles add` or use a registry model id"
                )
    current = now or datetime.now(UTC)
    state, remaining = expiry_state(entry, now=current)
    if state == "expired" and remaining is not None and remaining < -RESOLVE_SKEW_SECONDS:
        raise ProfileConfigError(
            f"route-shim: route {name!r} expired at {entry['expiresAt']}; {_expiry_remedy(name, entry)}"
        )
    model = str(entry["model"])
    vendor = find_vendor_harness(registry, model)
    endpoint = entry.get("endpoint")
    harness = (
        override
        if override is not None
        else effective_harness(entry, routes=routes, registry=registry)
    )
    harness_def = harnesses[harness]
    notices: list[str] = []
    if harness_def["harnessKind"] == "model-bound":
        if endpoint:
            raise UsageError(
                f"route-shim: harness {harness!r} is model-bound and cannot accept a custom endpoint"
            )
        if vendor is not None and vendor != harness:
            raise UsageError(f"route-shim: model {model!r} belongs to {vendor}, not {harness}")
        if vendor is None and not harness_def["allowUnknownModels"]:
            raise UsageError(f"route-shim: model {model!r} is not a known {harness} model")
    harness_defaults = routes.get("harnesses", {}).get(harness, {})
    effort_value: str | None = None
    effort_source: str | None = None
    try:
        if entry.get("effort"):
            effort_value = validate_effort(
                registry, harness, model, entry["effort"], f"models.{name}.effort"
            )
            effort_source = "route"
        elif harness_defaults.get("effort"):
            effort_value = validate_effort(
                registry, harness, model, harness_defaults["effort"], f"harnesses.{harness}.effort"
            )
            effort_source = "harness"
    except ProfilesError as exc:
        raise ProfileConfigError(f"route-shim: {exc}") from exc
    if caller_sets_effort(registry, harness, caller_args):
        kind, key, _values = effort_spec(registry, harness)
        for index, argument in enumerate(caller_args):
            if key and argument.startswith(f"{key}="):
                effort_value = argument.split("=", 1)[1]
            elif key and argument == key and index + 1 < len(caller_args):
                effort_value = caller_args[index + 1]
        effort_source = "caller"
        effort_args: list[str] = []
    else:
        effort_args = render_effort(registry, harness, effort_value) if effort_value else []
    env_updates: dict[str, str] = {}
    sync_status = "not-required"
    endpoint_host: str | None = None
    model_arg = model
    extra_argv: list[str] = []
    if endpoint:
        delivery = harness_def["endpointDelivery"]
        if delivery == "none":
            raise UsageError(f"route-shim: harness {harness!r} does not accept endpoints")
        adapter = get_adapter(harness)
        endpoint_host = urlsplit(str(endpoint["baseUrl"])).netloc or None
        key_env = endpoint.get("apiKeyEnv")
        key_value = ""
        if key_env:
            key_value, resolved_key_env = resolve_pitwall_api_token(env, str(key_env))
            if key_value and resolved_key_env != key_env:
                env_updates[str(key_env)] = key_value
        if delivery == "env":
            names = harness_def["endpointEnv"]
            env_updates[names["baseUrl"]] = str(endpoint["baseUrl"])
            env_updates[names["model"]] = model
            if key_env:
                if not key_value:
                    raise ProfileConfigError(
                        f"route-shim: route {name!r} needs "
                        f"{pitwall_api_token_requirement(str(key_env))} set in the environment"
                    )
                env_updates[names["apiKey"]] = key_value
        else:
            sync_status = adapter.endpoint_sync_status(name, entry, env, home)
            if sync_status != "synced":
                raise ProfileConfigError(
                    f"route-shim: route {name!r} is not materialized in {harness}'s config ({sync_status}); "
                    f"run: pitwall agents profiles sync --harness {harness}"
                )
            if harness_def["defaultModel"]["source"] == "positional":
                model_arg = f"{name}/{model}"
        extra_argv = adapter.endpoint_argv(name, entry)
        env_updates.update(adapter.endpoint_environment_extras(entry, env_updates))
    elif harness_def["harnessKind"] == "model-agnostic" and vendor != harness:
        notices.append(
            f"route-shim: {name!r} relies on {harness}'s own harness configuration for {model!r}"
        )
    limits = entry.get("limits", {})
    if harness == "qwen" and "output" in limits:
        env_updates["QWEN_CODE_MAX_OUTPUT_TOKENS"] = str(limits["output"])
    env_updates.update({str(key): str(value) for key, value in entry.get("env", {}).items()})
    entry_args = [
        *extra_argv,
        *harness_defaults.get("args", []),
        *effort_args,
        *entry.get("args", []),
    ]
    argv = _with_routing_defaults(
        build_argv(harness_def, model_arg, prompt_source, entry_args, caller_args), entry
    )
    return ResolvedProfile(
        spec=spec,
        name=name,
        harness=harness,
        model=model,
        model_arg=model_arg,
        argv=tuple(argv),
        env_updates=env_updates,
        env_keys=tuple(sorted(env_updates)),
        endpoint_host=endpoint_host,
        native_to=tuple(harness_def["nativeHosts"]),
        sync_status=sync_status,
        notices=tuple(notices),
        entry=entry,
        effort=effort_value,
        effort_source=effort_source,
    )


def _needs_self_heal(entry: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    origin = entry.get("origin") or {}
    if origin.get("kind") != "pitwall":
        return False
    state, remaining = expiry_state(entry, now=now or datetime.now(UTC))
    expiry_trigger = state == "expired" or (
        state == "expiring" and remaining is not None and remaining <= RESOLVE_SKEW_SECONDS
    )
    return expiry_trigger or origin.get("state", "unknown") != "active"


@contextmanager
def _profile_lock(name: str, env: Mapping[str, str]) -> Iterator[None]:
    directory = state_root(env) / "locks"
    ensure_private_directory(directory)
    path = directory / f"route-{name}.lock"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _unavailable_message(name: str, entry: Mapping[str, Any], refusal: str | None = None) -> str:
    message = f"route {name!r} has no reachable live Pitwall lease; {_expiry_remedy(name, entry)}"
    if refusal == "no_serve_history":
        message += "; serve the capability once with `pitwall serve` so Pitwall has a configuration to replay"
    if refusal is not None:
        message += f" (not revived: {refusal})"
    return message


def _self_heal(
    name: str,
    entry: Mapping[str, Any],
    config: dict[str, Any],
    registry: Mapping[str, Any],
    env: Mapping[str, str],
    home: Path,
) -> tuple[dict[str, Any], bool]:
    """Refresh and, when explicitly enabled, revive one Pitwall route."""
    del home  # reserved for harness-specific self-heal materialization
    if not _needs_self_heal(entry):
        return config, False
    with _profile_lock(name, env):
        current_config = load_profiles(env, registry=registry)
        current_entry = current_config.get("models", {}).get(name)
        if not isinstance(current_entry, Mapping) or not _needs_self_heal(current_entry):
            return current_config, False
        origin = current_entry["origin"]
        capability = str(origin["capability"])
        pitwall_url = str(origin["url"])
        key_env = str((current_entry.get("endpoint") or {}).get("apiKeyEnv") or PITWALL_TOKEN_ENV)
        token, token_env = resolve_pitwall_api_token(env, key_env)
        if not token:
            raise ProfileConfigError(
                f"route {name!r} needs {pitwall_api_token_requirement(key_env)} set in the environment"
            )

        print(f"route-shim: self-heal {name}: refresh", file=sys.stderr)
        info = fetch_capability(pitwall_url, capability, token, token_env=token_env)
        refreshed = refreshed_entry(current_entry, info, pitwall_url)
        current_config["models"][name] = refreshed
        save_profiles(env, current_config, registry=registry)
        record_sync(env, name, "self-heal")

        live = bool(info.served_model_id and info.lease_id)
        if live and origin.get("state", "unknown") == "active":
            return current_config, False
        unleased = bool(
            refreshed.get("model")
            and not info.lease_id
            and not info.expires_at
            and refreshed["origin"].get("state") == "unknown"
        )
        if live or unleased:
            from .profiles_probe import probe_profile

            print(f"route-shim: self-heal {name}: probe", file=sys.stderr)
            if probe_profile(name, refreshed, env=env).status == "reachable":
                return current_config, False

        caps = current_entry.get("autoServe")
        if not isinstance(caps, Mapping):
            raise ProfileConfigError(_unavailable_message(name, refreshed))
        print(f"route-shim: self-heal {name}: serve", file=sys.stderr)
        try:
            serve_capability(
                pitwall_url,
                capability,
                token,
                caps=caps,
                token_env=token_env,
            )
        except ServeRefused as exc:
            raise ProfileConfigError(_unavailable_message(name, refreshed, exc.code)) from exc
        timeout_minutes = caps.get("readyTimeoutMinutes", 15)
        print(f"route-shim: self-heal {name}: wait-ready", file=sys.stderr)
        ready = wait_until_served(
            pitwall_url,
            capability,
            token,
            deadline_seconds=float(timeout_minutes) * 60,
            token_env=token_env,
        )
        revived_entry = refreshed_entry(refreshed, ready, pitwall_url)
        current_config["models"][name] = revived_entry
        save_profiles(env, current_config, registry=registry)
        record_sync(env, name, "self-heal")
        from .profiles_probe import probe_profile

        print(f"route-shim: self-heal {name}: probe", file=sys.stderr)
        result = probe_profile(name, revived_entry, env=env)
        if result.status != "reachable":
            raise ProfileConfigError(_unavailable_message(name, revived_entry))
        return current_config, True


def _model_studio_preflight(
    resolved: Any, env: Mapping[str, str]
) -> tuple[str, Mapping[str, Any]] | None:
    """Return (endpoint name, endpoint) for a Model Studio route after its gates, else None."""
    entry = getattr(resolved, "entry", None) or {}
    endpoint = resolved_endpoint(entry)
    if endpoint is None or endpoint.get("kind") != model_studio.KIND:
        return None
    raw = entry.get("endpoint")
    name = (
        str(raw) if isinstance(raw, EndpointReference) else str(getattr(resolved, "name", "route"))
    )
    plan = str(endpoint["plan"])
    if model_studio.is_token_plan(plan) and not model_studio.endpoint_automation_accepted(endpoint):
        raise ProfileConfigError(
            f"automation_not_accepted: endpoint {name!r} is a Token Plan endpoint and the Token Plan terms allow "
            f"interactive use only; to accept that risk for headless dispatch run "
            f"`pitwall agents profiles add-model-studio-endpoint {name} ... --accept-token-plan-automation` "
            f"(or set {model_studio.AUTOMATION_ENV}=accept when creating it)"
        )
    key = env.get(str(endpoint["apiKeyEnv"]), "")
    if not key:
        raise ProfileConfigError(
            f"missing_key: export {endpoint['apiKeyEnv']} for endpoint {name!r}"
        )
    try:
        model_studio.check_key(plan, key)
    except model_studio.ModelStudioConfigError as exc:
        raise ProfileConfigError(
            f"{exc} (endpoint {name!r}, key from {endpoint['apiKeyEnv']})"
        ) from exc
    return name, endpoint


def dispatch_profile(argv: list[str], *, environ: Mapping[str, str] | None = None) -> int:
    """Resolve a route spec and hand off to the harness dispatch path. Returns the exit code."""
    env = dict(os.environ if environ is None else environ)
    model_studio_target: tuple[str, Mapping[str, Any]] | None = None
    try:
        if len(argv) < 2:
            raise UsageError(USAGE)
        spec, prompt_source, caller = argv[0], argv[1], tuple(argv[2:])
        registry = load_registry()
        config = load_profiles(env, registry=registry)
        name, _override = parse_spec(spec)
        entry = config["models"].get(name)
        revived = False
        home = Path(env.get("HOME", "~")).expanduser()
        if isinstance(entry, Mapping) and _needs_self_heal(entry):
            config, revived = _self_heal(name, entry, config, registry, env, home)
        resolved = resolve_profile(
            spec,
            registry=registry,
            routes=config,
            env=env,
            home=home,
            prompt_source=prompt_source,
            caller_args=caller,
        )
        expected_harness = env.pop("PITWALL_AGENTS_MANAGED_EXPECTED_HARNESS", None)
        if expected_harness is not None and resolved.harness != expected_harness:
            raise ProfileConfigError(
                f"managed route changed harness after preflight: expected {expected_harness!r}, "
                f"resolved {resolved.harness!r}"
            )
        if revived:
            resolved = replace(resolved, revived=True)
        model_studio_target = _model_studio_preflight(resolved, env)
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        _emit_sentinel(64, leading_newline=False)
        return 64
    except (PitwallError, ProfileConfigError, ProfilesError, RegistryError) as exc:
        print(
            f"route-shim: {exc}" if not str(exc).startswith("route-shim:") else str(exc),
            file=sys.stderr,
        )
        _emit_sentinel(EX_CONFIG, leading_newline=False)
        return EX_CONFIG
    endpoint = f" (endpoint {resolved.endpoint_host})" if resolved.endpoint_host else ""
    print(
        f"route-shim: {resolved.name} -> {resolved.harness} {resolved.model}{endpoint}",
        file=sys.stderr,
    )
    for notice in resolved.notices:
        print(notice, file=sys.stderr)
    child_env = dict(env)
    child_env.update(resolved.env_updates)
    if model_studio_target is None:
        return dispatch_legacy(
            resolved.harness, list(resolved.argv), environ=child_env, route=resolved
        )
    endpoint_name, ms_endpoint = model_studio_target
    locked_until = endpoints.read_lockout(env, endpoint_name, now=datetime.now(UTC))
    if locked_until is not None:
        print(
            f"route-shim: credits_exhausted: endpoint {endpoint_name!r} is locked until {locked_until}",
            file=sys.stderr,
        )
        _emit_sentinel(endpoints.EX_TEMPFAIL, leading_newline=False)
        return endpoints.EX_TEMPFAIL
    dispatch_id = child_env.setdefault("PITWALL_AGENTS_DISPATCH_ID", str(uuid.uuid4()))
    capacity = int(ms_endpoint.get("concurrency") or 1_000_000)
    try:
        wait_seconds = float(env.get("PITWALL_AGENTS_TIMEOUT_SECS", "1140"))
    except ValueError:
        wait_seconds = 1140.0
    try:
        with endpoints.acquire_slot(env, endpoint_name, capacity, wait_seconds=wait_seconds):
            code = dispatch_legacy(
                resolved.harness, list(resolved.argv), environ=child_env, route=resolved
            )
    except endpoints.EndpointBusy as exc:
        print(f"route-shim: {exc}", file=sys.stderr)
        _emit_sentinel(endpoints.EX_TEMPFAIL, leading_newline=False)
        return endpoints.EX_TEMPFAIL
    if code != 0 and model_studio.is_token_plan(str(ms_endpoint["plan"])):
        try:
            exhausted = endpoints.output_reports_exhaustion(find_run(env, dispatch_id))
        except FileNotFoundError:
            exhausted = False
        if exhausted:
            now = datetime.now(UTC)
            renews_on = ms_endpoint.get("renewsOn")
            until = (
                model_studio.credits_window(str(renews_on), now)[1]
                if renews_on
                else now + timedelta(days=1)
            )
            endpoints.write_lockout(env, endpoint_name, until)
            print(
                f"route-shim: credits_exhausted: endpoint {endpoint_name!r} locked until {until.isoformat()}",
                file=sys.stderr,
            )
    return code
