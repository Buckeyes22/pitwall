from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

from pitwall.agents import ask_watch, channel
from pitwall.agents.channel import ChannelConfig, listener_active, write_channel_config
from pitwall.agents.run_store import RunStore


def _event(
    event: str = "ask.created",
    *,
    dispatch_id: str = "d1",
    ask_id: str = "0001",
    session: str | None = "sess-a",
    tier: str = "1",
    question: str = "Which file?",
    options: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "eventId": f"{dispatch_id}-{ask_id}-{event}",
        "event": event,
        "dispatchId": dispatch_id,
        "data": {
            "askId": ask_id,
            "question": question,
            "options": options or [{"id": "a", "text": "x.py"}, {"id": "b", "text": "y.py"}],
            "default": "b",
            "deadlineS": 170,
            "tier": tier,
            "orchestratorSession": session,
            "harness": "opencode",
        },
    }


def _append(path: Path, *events: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event) + "\n")


def _start(
    events_path: Path, env: dict[str, str], session: str | None = "sess-a", poll_s: float = 0.05
) -> tuple[threading.Event, io.StringIO, threading.Thread]:
    stop = threading.Event()
    out = io.StringIO()
    thread = threading.Thread(
        target=ask_watch.watch,
        args=(events_path, env, session),
        kwargs={"out": out, "stop": stop, "poll_s": poll_s},
        daemon=True,
    )
    thread.start()
    if session is not None:
        # The watcher writes its heartbeat only after it has taken its starting log
        # offset; appending before that would land behind the offset and never print.
        heartbeat = channel.watcher_heartbeat_path(env, session)
        assert heartbeat is not None
        _wait_for(heartbeat.exists)
    return stop, out, thread


def _wait_for(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "timed out waiting for watcher"


def _stop(stop: threading.Event, thread: threading.Thread) -> int:
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    return 0


def test_format_ask_line_names_reply_tool() -> None:
    assert ask_watch.format_ask_line(_event()) == (
        "PITWALL ASK dispatch=d1 ask=0001 harness=opencode tier=1 deadline_s=170 default=b "
        "| Which file? | options: a=x.py; b=y.py "
        '| reply: answer_ask(dispatch_id="d1", ask_id="0001", choice=<option id>)'
    )
    tier4 = ask_watch.format_ask_line(_event(tier="4"))
    assert tier4.endswith("| paused: answer, then pitwall agents runs resume d1")
    long_event = _event(question="q" * 401, options=[{"id": "a", "text": "x" * 61}])
    formatted = ask_watch.format_ask_line(long_event)
    assert "q" * 400 in formatted and "q" * 401 not in formatted
    assert "x" * 60 in formatted and "x" * 61 not in formatted


def test_watch_streams_only_new_asks_for_this_session(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    stop, out, thread = _start(events, {"HOME": str(tmp_path)})
    _append(events, _event(), {**_event("dispatch.output", ask_id="0002"), "data": {}})
    _wait_for(lambda: "ask=0001" in out.getvalue())
    result = _stop(stop, thread)
    assert result == 0
    assert out.getvalue().count("PITWALL ASK") == 1
    assert "dispatch.output" not in out.getvalue()


def test_watch_ignores_other_sessions(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    stop, out, thread = _start(events, {"HOME": str(tmp_path)})
    _append(events, _event(session="sess-b"))
    time.sleep(0.15)
    _stop(stop, thread)
    assert out.getvalue() == ""


def test_watch_dedupes_repeated_ask_events(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    stop, out, thread = _start(events, {"HOME": str(tmp_path)})
    _append(events, _event(), _event())
    _wait_for(lambda: "PITWALL ASK" in out.getvalue())
    _append(events, _event("ask.escalated"))
    _wait_for(lambda: "PITWALL ASK ESCALATED" in out.getvalue())
    _stop(stop, thread)
    assert out.getvalue().count("PITWALL ASK dispatch=d1") == 1
    assert out.getvalue().count("PITWALL ASK ESCALATED") == 1


def test_watch_announces_open_asks_at_start(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path), "PITWALL_AGENTS_STATE_HOME": str(tmp_path / "state")}
    store = RunStore(tmp_path / "state", "d1")
    store.path.mkdir(parents=True)
    write_channel_config(
        store, ChannelConfig("d1", orchestrator_session="sess-a", harness="opencode")
    )
    store.mailbox().write_ask(
        blocked_on="choice",
        question="Backlog question?",
        options=[{"id": "a", "text": "yes"}],
        default="a",
        deadline_s=100,
    )
    stop, out, thread = _start(tmp_path / "events.jsonl", env)
    _wait_for(lambda: "Backlog question?" in out.getvalue())
    _stop(stop, thread)
    assert out.getvalue().splitlines()[0].startswith("PITWALL ASK dispatch=d1")


def test_watch_survives_bad_missing_and_truncated_log(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    stop, out, thread = _start(events, {"HOME": str(tmp_path)})
    time.sleep(0.1)
    _append(events, _event(ask_id="0001"))
    with events.open("a", encoding="utf-8") as stream:
        stream.write("not json\n")
    _wait_for(lambda: "ask=0001" in out.getvalue())
    events.write_text("", encoding="utf-8")
    time.sleep(0.1)
    _append(events, _event(ask_id="0002"))
    _wait_for(lambda: "ask=0002" in out.getvalue())
    assert thread.is_alive()
    assert _stop(stop, thread) == 0
    assert out.getvalue().count("PITWALL ASK dispatch=d1") == 2


def test_watch_keeps_heartbeat_fresh_and_removes_it_on_stop(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(channel, "WATCHER_HEARTBEAT_S", 0.1)
    env = {"HOME": str(tmp_path), "PITWALL_AGENTS_STATE_HOME": str(tmp_path / "state")}
    stop, _, thread = _start(tmp_path / "events.jsonl", env)
    heartbeat = channel.watcher_heartbeat_path(env, "sess-a")
    assert heartbeat is not None
    _wait_for(lambda: heartbeat.exists())
    assert listener_active(env, "sess-a")
    first = json.loads(heartbeat.read_text(encoding="utf-8"))["updatedEpoch"]
    time.sleep(0.15)
    second = json.loads(heartbeat.read_text(encoding="utf-8"))["updatedEpoch"]
    assert second > first
    assert _stop(stop, thread) == 0
    assert not heartbeat.exists()

    stop, _, thread = _start(tmp_path / "events.jsonl", env, session=None)
    time.sleep(0.1)
    _stop(stop, thread)
    assert not list((tmp_path / "state" / "watchers").glob("*.json"))


def test_heartbeat_path_rejects_unsafe_session_ids(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    assert channel.watcher_heartbeat_path(env, "../x") is None
    assert channel.watcher_heartbeat_path(env, "") is None
    assert not listener_active(env, "../x")
    assert not listener_active(env, "")
    assert not listener_active(env, None)


def test_cli_inbox_watch_flag_parses() -> None:
    from pitwall.agents.cli import build_parser

    args = build_parser().parse_args(["inbox", "--watch", "--all-sessions"])
    assert args.watch is True
    assert args.all_sessions is True
