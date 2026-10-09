"""Doctor proves the channel's modern era as well as the handshake (F18)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from pitwall.agents import doctor
from pitwall.agents.doctor import _probe_channel_server
from tests.hang_guard import HANG_GUARD_SECS


def test_probe_lists_orchestrator_tools_in_both_eras() -> None:
    env = {k: v for k, v in os.environ.items() if k != "PITWALL_AGENTS_CHANNEL_DISPATCH_ID"}
    legacy = _probe_channel_server(env, None)
    modern = _probe_channel_server(env, None, modern=True)
    assert legacy is not None and modern is not None
    assert set(legacy) == set(modern) >= {"inbox", "dispatch_and_wait"}


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("result", ["null", "5", "[]", '"text"'])
def test_probe_reports_failure_for_a_non_object_result(
    monkeypatch: pytest.MonkeyPatch, modern: bool, result: str
) -> None:
    script = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if 'id' in message:\n"
        f"        print(json.dumps({{'jsonrpc': '2.0', 'id': message['id'], 'result': {result}}}), flush=True)\n"
    ).replace("null", "None")
    real_popen = subprocess.Popen

    def fake_popen(_argv: object, **kwargs: Any) -> Any:
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(doctor.subprocess, "Popen", fake_popen)
    assert _probe_channel_server({}, None, modern=modern) is None


def test_modern_era_failure_alone_turns_the_channel_check_to_warn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    orchestrator = ["inbox", "answer_ask", "dispatch_and_wait"]
    orchestrator += ["answer_and_wait", "wait_dispatch", "steer_and_wait"]
    subagent = ["ask_orchestrator", "read_steering", "ack_steer"]

    def probe(
        _env: Any, dispatch_id: str | None, *, modern: bool = False, **_kwargs: Any
    ) -> list[str] | None:
        if modern:
            return None
        return orchestrator if dispatch_id is None else subagent

    env = {"HOME": str(tmp_path), "PATH": ""}
    monkeypatch.setattr(doctor, "_probe_channel_server", probe)
    checks = {c.id: c for c in doctor._channel_checks(env, {})}
    assert checks["channel.mcp_server"].status == "WARN"
    assert checks["channel.mcp_server"].details["orchestratorTools"] == orchestrator
    assert checks["channel.mcp_server"].details["orchestratorToolsModern"] is None

    def both(
        _env: Any, dispatch_id: str | None, *, modern: bool = False, **_kwargs: Any
    ) -> list[str] | None:
        return orchestrator if dispatch_id is None else subagent

    monkeypatch.setattr(doctor, "_probe_channel_server", both)
    checks = {c.id: c for c in doctor._channel_checks(env, {})}
    assert checks["channel.mcp_server"].status == "PASS"


def _fake_server_popen(monkeypatch: pytest.MonkeyPatch, script: str) -> list[int]:
    """Replace the server with *script*; the returned list collects every spawned pid."""
    real_popen = subprocess.Popen
    spawned: list[int] = []

    def fake_popen(_argv: object, **kwargs: Any) -> Any:
        process = real_popen([sys.executable, "-c", script], **kwargs)
        spawned.append(process.pid)
        return process

    monkeypatch.setattr(doctor.subprocess, "Popen", fake_popen)
    return spawned


@pytest.mark.parametrize("modern", [False, True])
def test_probe_reports_failure_for_a_non_object_json_line(
    monkeypatch: pytest.MonkeyPatch, modern: bool
) -> None:
    _fake_server_popen(
        monkeypatch,
        "import sys\nfor line in sys.stdin:\n    print('[1, 2]', flush=True)\n",
    )
    assert _probe_channel_server({}, None, modern=modern) is None


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("tools", ["[1]", '["inbox"]', '{"inbox": 1}', '"inbox"'])
def test_probe_reports_failure_for_a_malformed_tool_list(
    monkeypatch: pytest.MonkeyPatch, modern: bool, tools: str
) -> None:
    script = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if 'id' not in message:\n"
        "        continue\n"
        "    result = {'supportedVersions': ['2026-07-28'], 'tools': TOOLS}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)\n"
    ).replace("TOOLS", tools)
    _fake_server_popen(monkeypatch, script)
    assert _probe_channel_server({}, None, modern=modern) is None


def test_probe_does_not_swallow_a_genuine_attribute_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise AttributeError("a bug in the probe body")

    monkeypatch.setattr(doctor.subprocess, "Popen", broken)
    with pytest.raises(AttributeError, match="a bug in the probe body"):
        _probe_channel_server({}, None)


_ECHO_SERVER = (
    "import json,sys\n"
    "for line in sys.stdin:\n"
    "    message = json.loads(line)\n"
    "    if 'id' not in message:\n"
    "        continue\n"
    "    result = {'supportedVersions': ['2026-07-28'], 'tools': [{'name': 'inbox'}]}\n"
    "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)\n"
)


def test_the_handshake_ceiling_is_a_hang_guard_below_the_test_hang_guard() -> None:
    # Generous enough for a cold, loaded start; finite so a dead server cannot hold doctor.
    assert 20.0 <= doctor.CHANNEL_HANDSHAKE_TIMEOUT < HANG_GUARD_SECS


@pytest.mark.parametrize("modern", [False, True])
def test_probe_waits_for_a_server_that_starts_slower_than_five_seconds(
    monkeypatch: pytest.MonkeyPatch, modern: bool
) -> None:
    # A freshly launched server on a loaded host can take longer than 5 s just to import.
    _fake_server_popen(monkeypatch, "import time\ntime.sleep(5.5)\n" + _ECHO_SERVER)
    started = time.monotonic()
    assert _probe_channel_server({}, None, modern=modern) == ["inbox"]
    assert time.monotonic() - started >= 5.5


@pytest.mark.parametrize("modern", [False, True])
def test_probe_warns_at_once_when_the_server_exits_before_replying(
    monkeypatch: pytest.MonkeyPatch, modern: bool
) -> None:
    # The server takes the probe's first request and then dies without replying. Exiting before
    # reading it would race the probe's write (a scheduling delay between spawn and write turns
    # the exit into a broken pipe, which is reported as an unreadable reply, not an exit code).
    _fake_server_popen(monkeypatch, "import sys\nsys.stdin.readline()\nsys.exit(3)\n")
    ceiling = HANG_GUARD_SECS
    monkeypatch.setattr(doctor, "CHANNEL_HANDSHAKE_TIMEOUT", ceiling)
    started = time.monotonic()
    failure: list[str] = []
    assert _probe_channel_server({}, None, modern=modern, failure=failure) is None
    assert (
        time.monotonic() - started < ceiling / 3
    )  # the exit was noticed, the ceiling not waited out
    assert failure == ["exited with code 3 before replying"]  # not the ceiling wording


@pytest.mark.parametrize("modern", [False, True])
def test_probe_names_the_exit_code_when_the_server_dies_before_the_first_write(
    monkeypatch: pytest.MonkeyPatch, modern: bool
) -> None:
    # The server is already gone when the probe writes, so the write hits a closed pipe.
    real_popen = subprocess.Popen

    def exited_popen(_argv: object, **kwargs: Any) -> Any:
        process = real_popen([sys.executable, "-c", "import sys\nsys.exit(3)\n"], **kwargs)
        process.wait(timeout=HANG_GUARD_SECS)
        return process

    monkeypatch.setattr(doctor.subprocess, "Popen", exited_popen)
    failure: list[str] = []
    assert _probe_channel_server({}, None, modern=modern, failure=failure) is None
    assert failure == ["exited with code 3 before replying"]


@pytest.mark.parametrize("modern", [False, True])
def test_probe_gives_up_on_a_silent_live_server_at_the_ceiling(
    monkeypatch: pytest.MonkeyPatch, modern: bool
) -> None:
    spawned = _fake_server_popen(monkeypatch, f"import time\ntime.sleep({2 * HANG_GUARD_SECS})\n")
    monkeypatch.setattr(doctor, "CHANNEL_HANDSHAKE_TIMEOUT", 1.0)
    failure: list[str] = []
    started = time.monotonic()
    assert _probe_channel_server({}, None, modern=modern, failure=failure) is None
    elapsed = time.monotonic() - started
    assert 1.0 <= elapsed < 10.0
    assert failure == ["no reply within the 1 s ceiling"]
    assert len(spawned) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(spawned[0], 0)  # the probe killed and reaped its child


@pytest.mark.parametrize("modern", [False, True])
def test_probe_names_a_protocol_error_without_echoing_server_output(
    monkeypatch: pytest.MonkeyPatch, modern: bool
) -> None:
    _fake_server_popen(
        monkeypatch,
        "import sys\nfor line in sys.stdin:\n    print('SECRET-GARBAGE', flush=True)\n",
    )
    failure: list[str] = []
    assert _probe_channel_server({}, None, modern=modern, failure=failure) is None
    assert len(failure) == 1 and failure[0].startswith("protocol error: ")
    assert "SECRET" not in failure[0]


def test_the_channel_warning_names_each_probes_actual_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def probe(
        _env: Any,
        dispatch_id: str | None,
        *,
        modern: bool = False,
        failure: list[str] | None = None,
    ) -> list[str] | None:
        assert failure is not None
        if dispatch_id is None and not modern:
            failure.append("exited with code 3 before replying")
        elif dispatch_id is None:
            failure.append("no reply within the 30 s ceiling")
        elif not modern:
            return ["inbox"]  # a wrong tool list
        else:
            failure.append("protocol error: a reply line was not a JSON object")
        return None

    monkeypatch.setattr(doctor, "_probe_channel_server", probe)
    checks = {c.id: c for c in doctor._channel_checks({"HOME": str(tmp_path), "PATH": ""}, {})}
    check = checks["channel.mcp_server"]
    assert check.status == "WARN"
    assert check.details["orchestratorFailure"] == "exited with code 3 before replying"
    assert check.details["orchestratorFailureModern"] == "no reply within the 30 s ceiling"
    assert check.details["subagentFailure"] == "unexpected tool list"
    assert check.details["subagentFailureModern"].startswith("protocol error: ")
    assert check.details["handshakeCeilingSeconds"] == doctor.CHANNEL_HANDSHAKE_TIMEOUT
    for reason in (
        "orchestrator (legacy): exited with code 3 before replying",
        "orchestrator (2026-07-28): no reply within the 30 s ceiling",
        "subagent (legacy): unexpected tool list",
    ):
        assert reason in check.summary


def test_a_silent_server_costs_one_ceiling_across_all_four_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spawned = _fake_server_popen(monkeypatch, f"import time\ntime.sleep({2 * HANG_GUARD_SECS})\n")
    ceiling = 2.0
    monkeypatch.setattr(doctor, "CHANNEL_HANDSHAKE_TIMEOUT", ceiling)
    started = time.monotonic()
    checks = {c.id: c for c in doctor._channel_checks({"HOME": str(tmp_path), "PATH": ""}, {})}
    elapsed = time.monotonic() - started
    assert len(spawned) == 4
    # Each probe waits out its own ceiling (never less), and running them serially would cost four.
    assert ceiling <= elapsed < 4 * ceiling
    check = checks["channel.mcp_server"]
    assert check.status == "WARN"
    assert "no reply within the 2 s ceiling" in check.summary
    assert [
        check.details[k]
        for k in (
            "orchestratorFailure",
            "subagentFailure",
            "orchestratorFailureModern",
            "subagentFailureModern",
        )
    ] == ["no reply within the 2 s ceiling"] * 4
