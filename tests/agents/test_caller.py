from pitwall.agents.caller import SESSION_ENV, ProcessInfo, resolve_caller

TREE = {
    500: ProcessInfo(ppid=400, names=frozenset({"pitwall", "python"})),
    400: ProcessInfo(ppid=300, names=frozenset({"bash"})),
    300: ProcessInfo(ppid=1, names=frozenset({"opencode"})),
}


def fake(pid: int) -> ProcessInfo | None:
    return TREE.get(pid)


def test_inherited_session_wins_over_everything() -> None:
    env = {
        SESSION_ENV: "claude:root",
        "CLAUDE_CODE_SESSION_ID": "other",
        "PITWALL_AGENTS_CHANNEL_DISPATCH_ID": "11111111-1111-4111-8111-111111111111",
    }
    caller = resolve_caller(env, start_pid=500, read_process=fake)
    assert (caller.session, caller.source) == ("claude:root", "inherited")
    assert caller.parent_dispatch_id == "11111111-1111-4111-8111-111111111111"


def test_claude_session_id_is_used_when_nothing_is_inherited() -> None:
    caller = resolve_caller({"CLAUDE_CODE_SESSION_ID": "abc"}, start_pid=500, read_process=fake)
    assert (caller.session, caller.source, caller.host) == ("claude:abc", "host-env", "claude")


def test_nearest_host_ancestor_identifies_hosts_without_a_session_variable(monkeypatch) -> None:
    monkeypatch.setattr("pitwall.agents.caller.process_identity", lambda pid: f"linux:{pid}9")
    caller = resolve_caller({}, start_pid=500, read_process=fake)
    assert (caller.session, caller.source, caller.host) == (
        "opencode:pid:300:linux:3009",
        "host-process",
        "opencode",
    )


def test_no_host_found_is_unknown_not_an_error() -> None:
    caller = resolve_caller({}, start_pid=400, read_process=lambda pid: None)
    assert (caller.session, caller.source, caller.host) == ("unknown", "unknown", None)


def test_ancestor_walk_stops_on_a_cycle() -> None:
    loop = {
        10: ProcessInfo(ppid=11, names=frozenset({"bash"})),
        11: ProcessInfo(ppid=10, names=frozenset({"bash"})),
    }
    assert resolve_caller({}, start_pid=10, read_process=loop.get).source == "unknown"


def test_read_process_sees_this_test_process() -> None:
    import os

    from pitwall.agents.caller import read_process

    info = read_process(os.getpid())
    assert info is not None and info.ppid == os.getppid()
