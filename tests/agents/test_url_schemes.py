"""Every outbound HTTP call in the agents package refuses non-http(s) URL schemes (ledger G-03)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any
from unittest import mock

import httpx
import pytest

from pitwall.agents import broker, channel_policy, endpoints, profiles_probe
from pitwall.agents.http_urls import UnsupportedUrlSchemeError, open_http_url

BAD_URLS = ["file:///etc/passwd", "ftp://x/"]
GOOD_URLS = ["http://127.0.0.1:1", "https://example.invalid"]
ASK = {
    "ask_id": "0001",
    "blocked_on": "naming",
    "severity": "normal",
    "question": "Which suffix?",
    "default": "a",
    "default_rationale": "conventional",
    "context": {
        "options": [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}],
        "files_touched": ["db/x.sql"],
    },
}


def _policy(url: str) -> str:
    entry = {"model": "m", "endpoint": {"baseUrl": url}}
    return channel_policy.request_choice(
        ASK, route_name="r", entry=entry, env={}, timeout=1.0
    ).reason


def _probe(url: str) -> str:
    entry = {"model": "m", "endpoint": {"baseUrl": url}, "args": [], "env": {}}
    return profiles_probe.probe_profile("r", entry, env={}, timeout=1.0).detail


def _discover(url: str) -> str:
    try:
        endpoints.discover_models(url, timeout=1.0)
    except endpoints.DiscoveryError as exc:
        return str(exc)
    return ""


def _running(url: str) -> str:
    states = endpoints.running_states(url, {}, 1.0)
    return "refused" if states is None else ""


def _raises(call: Callable[[], Any], error: type[Exception]) -> str:
    try:
        call()
    except error as exc:
        return str(exc)
    return ""


def _fetch(url: str) -> str:
    return _raises(lambda: broker.fetch_capability(url, "llm.x", "t"), broker.PitwallError)


def _serve(url: str) -> str:
    return _raises(lambda: broker.serve_capability(url, "llm.x", "t", caps={}), broker.PitwallError)


def _subscribe(url: str) -> str:
    return _raises(
        lambda: broker.create_subscription(url, "t", "http://127.0.0.1:8765"),
        broker.PitwallSyncError,
    )


ENTRY_POINTS = {
    "channel_policy.request_choice": _policy,
    "profiles_probe.probe_profile": _probe,
    "endpoints.discover_models": _discover,
    "endpoints.running_states": _running,
    "broker.fetch_capability": _fetch,
    "broker.serve_capability": _serve,
    "broker.create_subscription": _subscribe,
}


class _Reply(io.BytesIO):
    status = 200

    def __enter__(self) -> _Reply:
        return self


def _fake_urlopen(*_args: object, **_kwargs: object) -> _Reply:
    return _Reply(json.dumps({"data": [], "running": []}).encode())


@pytest.mark.parametrize("url", BAD_URLS)
@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_non_http_scheme_refused(name: str, url: str) -> None:
    with mock.patch("urllib.request.urlopen") as urlopen:
        message = ENTRY_POINTS[name](url)
    urlopen.assert_not_called()
    assert message == "refused" or "scheme" in message


@pytest.mark.parametrize("url", GOOD_URLS)
@pytest.mark.parametrize("name", [n for n in ENTRY_POINTS if not n.startswith("broker.")])
def test_http_and_https_allowed(name: str, url: str) -> None:
    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen) as urlopen:
        ENTRY_POINTS[name](url)
    urlopen.assert_called()


@pytest.mark.parametrize("url", GOOD_URLS)
@pytest.mark.parametrize("name", [n for n in ENTRY_POINTS if n.startswith("broker.")])
def test_broker_http_and_https_allowed(name: str, url: str) -> None:
    with mock.patch("httpx.request", side_effect=httpx.ConnectError("offline")) as send:
        ENTRY_POINTS[name](url)
    send.assert_called()


def test_doctor_receiver_probe_goes_through_the_helper() -> None:
    from pitwall.agents import doctor

    sync_state = {"routes": {"g": {"source": "receiver"}}, "receiver": {"port": 18765}}
    with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen) as urlopen:
        doctor._check_pitwall_receiver(sync_state)
    assert urlopen.call_args.args[0].full_url == "http://127.0.0.1:18765/health"


@pytest.mark.parametrize("url", BAD_URLS)
def test_helper_raises_typed_error(url: str) -> None:
    with (
        mock.patch("urllib.request.urlopen") as urlopen,
        pytest.raises(UnsupportedUrlSchemeError, match="scheme"),
    ):
        open_http_url(url, timeout=1.0)
    urlopen.assert_not_called()
