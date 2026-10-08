"""Per-endpoint concurrency slots, the local Token Plan exhaustion lockout, and model discovery for OpenAI-compatible endpoints (stdlib only)."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from pitwall.providers.model_studio.catalog import EXHAUSTED_MESSAGE

from .http_urls import open_http_url
from .run_store import atomic_write_json, ensure_private_directory, state_root

EX_TEMPFAIL = 75
_TAIL_BYTES = 64 * 1024


class EndpointBusy(RuntimeError):
    pass


def _endpoint_dir(env: Mapping[str, str], endpoint: str) -> Path:
    return state_root(env) / "endpoint-slots" / endpoint


@contextmanager
def acquire_slot(
    env: Mapping[str, str],
    endpoint: str,
    capacity: int,
    *,
    wait_seconds: float,
    poll_seconds: float = 0.25,
) -> Iterator[int]:
    """Hold one of ``capacity`` flock slots; the kernel frees it if the holder dies."""
    directory = _endpoint_dir(env, endpoint)
    ensure_private_directory(directory)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        for index in range(capacity):
            descriptor = os.open(directory / f"{index}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                continue
            try:
                yield index
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            return
        if time.monotonic() >= deadline:
            raise EndpointBusy(
                f"endpoint_busy: all {capacity} slots of endpoint {endpoint!r} are in use"
            )
        time.sleep(poll_seconds)


def lockout_path(env: Mapping[str, str], endpoint: str) -> Path:
    return _endpoint_dir(env, endpoint) / "exhausted.json"


def write_lockout(env: Mapping[str, str], endpoint: str, until: datetime) -> None:
    path = lockout_path(env, endpoint)
    ensure_private_directory(path.parent)
    atomic_write_json(path, {"until": until.isoformat()})


def read_lockout(env: Mapping[str, str], endpoint: str, *, now: datetime) -> str | None:
    try:
        until = str(json.loads(lockout_path(env, endpoint).read_text(encoding="utf-8"))["until"])
        locked = datetime.fromisoformat(until) > now
    except OSError, ValueError, KeyError, TypeError:
        return None
    return until if locked else None


def output_reports_exhaustion(run_dir: Path) -> bool:
    for name in ("stderr.log", "stdout.log"):
        path = run_dir / name
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - _TAIL_BYTES))
                tail = handle.read().decode("utf-8", errors="replace").lower()
        except OSError:
            continue
        if EXHAUSTED_MESSAGE in tail:
            return True
    return False


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    id: str
    state: str


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    base_url: str
    models: tuple[DiscoveredModel, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "models": [asdict(model) for model in self.models],
            "baseUrl": self.base_url,
        }


class DiscoveryError(Exception):
    """A catalog request failed with an actionable remedy."""


# Named so fronting proxies accept it: RunPod's Cloudflare edge answers 403 (error 1010)
# to urllib's default "Python-urllib" agent.
ENDPOINT_USER_AGENT = "pitwall-agents/1"


def _get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    try:
        with open_http_url(
            request.Request(
                url, headers={"User-Agent": ENDPOINT_USER_AGENT, **headers}, method="GET"
            ),
            timeout=timeout,
        ) as response:
            return int(response.status), response.read(1024 * 1024)
    except error.HTTPError as exc:
        exc.close()
        raise


def running_states(base_url: str, headers: dict[str, str], timeout: float) -> dict[str, str] | None:
    origin = base_url[:-3] if base_url.endswith("/v1") else base_url
    try:
        status, body = _get(origin + "/running", headers, timeout)
        if status != 200:
            return None
        payload = json.loads(body.decode("utf-8", errors="replace"))
        if not isinstance(payload, dict) or not isinstance(payload.get("running"), list):
            return None
    except TimeoutError, error.HTTPError, error.URLError, OSError, ValueError, json.JSONDecodeError:
        return None

    states: dict[str, str] = {}
    for item in payload["running"]:
        if not isinstance(item, dict):
            continue
        model = item.get("model")
        state = item.get("state")
        if isinstance(model, str) and state in {"ready", "starting"}:
            states[model] = str(state)
    return states


def discover_models(
    base_url: str,
    *,
    api_key: str | None = None,
    timeout: float = 10.0,
) -> DiscoveryResult:
    """Fetch the endpoint catalog and best-effort swapper readiness states."""

    normalized_base = base_url.rstrip("/")
    url = normalized_base + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        _status, body = _get(url, headers, timeout)
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            raise DiscoveryError(
                f"HTTP {exc.code} from {url}; pass --api-key-env VAR or check its value"
            ) from exc
        raise DiscoveryError(
            f"HTTP {exc.code} from {url}; check --base-url points to the endpoint's /v1 base"
        ) from exc
    except (TimeoutError, error.URLError, OSError, ValueError) as exc:
        raise DiscoveryError(f"cannot reach {url}: {exc}; check --base-url and --timeout") from exc

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
        data = payload["data"]
        if not isinstance(data, list):
            raise TypeError
        ids = tuple(
            str(item["id"])
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
    except json.JSONDecodeError, KeyError, TypeError:
        raise DiscoveryError(
            f"{url} returned a non-OpenAI models payload; check --base-url points to the endpoint's /v1 base"
        ) from None

    states = running_states(normalized_base, headers, timeout)
    return DiscoveryResult(
        normalized_base,
        tuple(
            # Without a /running state source (a plain always-on endpoint) the
            # honest label is "unknown"; "not-loaded" means the swapper said so.
            DiscoveredModel(
                model_id, states.get(model_id, "not-loaded") if states is not None else "unknown"
            )
            for model_id in ids
        ),
    )
