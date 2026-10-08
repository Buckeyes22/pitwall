"""The relay keeps the harness connected across server restarts."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.hang_guard import HANG_GUARD_SECS

FAKE_SERVER = r"""
import json, os, sys
count_file = sys.argv[1]
n = (int(open(count_file).read()) if os.path.exists(count_file) else 0) + 1
open(count_file, "w").write(str(n))
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "crash":
        os._exit(3)
    if msg.get("method") == "junk":
        print("not json", flush=True)
    if msg.get("method") == "emit":
        print(msg["params"]["line"], flush=True)
    if msg.get("method") == "junkmany":
        for _ in range(250):
            print("not json", flush=True)
    if msg.get("method") == "big":
        print("x" * 5000, flush=True)
    if msg.get("method") == "progress":
        print("progress...", end="", flush=True)
    if msg.get("method") == "closestdin":
        os.close(0)
    if msg.get("method") == "fragment":
        print('{"jsonrpc":"2.0","id":', end="", flush=True)
    if "id" in msg and "method" in msg:
        result = {"generation": n, "method": msg["method"]}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
    if msg.get("method") == "closestdin":
        import time
        time.sleep(300)
"""


class _Client:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc

    async def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def recv(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=HANG_GUARD_SECS)
        return json.loads(line)

    async def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        assert await asyncio.wait_for(self.proc.wait(), timeout=HANG_GUARD_SECS) == 0


async def _relay(
    tmp_path: Path, *server: str, wait: str = "10", stderr: int | None = None
) -> _Client:
    env = {**os.environ, "PITWALL_MCP_RELAY_WAIT_SECONDS": wait}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pitwall.mcp.relay",
        "--",
        *server,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env=env,
    )
    return _Client(proc)


@pytest.mark.anyio
async def test_relay_replays_initialize_and_fails_in_flight_request(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text(FAKE_SERVER)
    count = tmp_path / "count"
    client = await _relay(tmp_path, sys.executable, str(server), str(count))
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert (await client.recv())["result"] == {"generation": 1, "method": "initialize"}
    await client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    await client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert (await client.recv())["result"]["generation"] == 1
    await client.send({"jsonrpc": "2.0", "id": 3, "method": "crash"})
    failed = await client.recv()
    assert failed["id"] == 3 and failed["error"]["data"] == {
        "error": "mcp_server_restarted",
        "retryable": True,
    }
    await client.send({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    after = await client.recv()
    assert (
        after["id"] == 4 and after["result"]["generation"] == 2
    )  # replayed initialize was not forwarded
    assert count.read_text() == "2"
    assert client.proc.stdin is not None
    client.proc.stdin.close()
    assert await asyncio.wait_for(client.proc.wait(), timeout=HANG_GUARD_SECS) == 0


@pytest.mark.anyio
async def test_unstartable_server_answers_requests_with_unavailable(tmp_path: Path) -> None:
    client = await _relay(tmp_path, str(tmp_path / "missing-binary"), wait="0.5")
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    failed = await client.recv()
    assert failed["id"] == 1 and failed["error"]["data"] == {
        "error": "mcp_server_unavailable",
        "retryable": True,
    }
    assert client.proc.stdin is not None
    client.proc.stdin.close()
    assert await asyncio.wait_for(client.proc.wait(), timeout=HANG_GUARD_SECS) == 0


def test_wait_seconds_from_env_uses_a_positive_number_and_falls_back_otherwise(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pitwall.mcp.relay import wait_seconds_from_env

    assert wait_seconds_from_env({"PITWALL_MCP_RELAY_WAIT_SECONDS": "12.5"}) == 12.5
    assert wait_seconds_from_env({}) == 30.0
    for bad in ("lots", "0", "-3", "nan", "inf"):
        assert wait_seconds_from_env({"PITWALL_MCP_RELAY_WAIT_SECONDS": bad}) == 30.0
    warning = capsys.readouterr().err
    assert "PITWALL_MCP_RELAY_WAIT_SECONDS must be a positive number of seconds" in warning
    assert "lots" not in warning


@pytest.mark.parametrize("client_input", ["devnull", "file"])
def test_relay_exits_at_end_of_client_input_that_cannot_be_polled(
    tmp_path: Path, client_input: str
) -> None:
    """stdin from /dev/null or a file cannot join the event loop's poller; the relay must still
    see end of input and exit instead of waiting forever (found by the CLI journey harness)."""
    import subprocess

    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"
    stdin_path = tmp_path / "client.jsonl"
    stdin_path.write_text(request)
    server = [sys.executable, "-c", FAKE_SERVER, str(tmp_path / "count")]
    with open(os.devnull, "rb") if client_input == "devnull" else stdin_path.open("rb") as stdin:
        done = subprocess.run(
            [sys.executable, "-m", "pitwall.mcp.relay", "--", *server],
            stdin=stdin,
            capture_output=True,
            timeout=HANG_GUARD_SECS,
            env={**os.environ, "PITWALL_MCP_RELAY_WAIT_SECONDS": "10"},
            check=False,
        )

    assert done.returncode == 0, done.stderr.decode()[-600:]
    assert b"PermissionError" not in done.stderr
    if client_input == "file":
        assert json.loads(done.stdout.splitlines()[0])["id"] == 1


@pytest.mark.anyio
async def test_relay_drops_non_json_child_output(tmp_path: Path) -> None:
    count = tmp_path / "count"
    client = await _relay(tmp_path, sys.executable, "-c", FAKE_SERVER, str(count))
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "junk"})
    reply = await client.recv()
    assert reply == {"jsonrpc": "2.0", "id": 1, "result": {"generation": 1, "method": "junk"}}
    await client.close()


@pytest.mark.anyio
async def test_relay_logs_dropped_child_output_without_echoing_it(tmp_path: Path) -> None:
    secret_server = FAKE_SERVER.replace('"not json"', '"not json sk-secret-token"')
    client = await _relay(
        tmp_path,
        sys.executable,
        "-c",
        secret_server,
        str(tmp_path / "count"),
        stderr=asyncio.subprocess.PIPE,
    )
    await client.send({"jsonrpc": "2.0", "id": 1, "method": "junk"})
    assert (await client.recv())["id"] == 1
    assert client.proc.stderr is not None
    assert client.proc.stdin is not None
    client.proc.stdin.close()
    err = await asyncio.wait_for(client.proc.stderr.read(), timeout=HANG_GUARD_SECS)
    await asyncio.wait_for(client.proc.wait(), timeout=HANG_GUARD_SECS)
    assert b"dropped a non-MCP line from the server (1 dropped so far)" in err
    assert b"sk-secret-token" not in err


@pytest.mark.anyio
async def test_relay_errors_use_the_application_code(tmp_path: Path) -> None:
    client = await _relay(tmp_path, str(tmp_path / "missing-binary"), wait="0.2")
    await client.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    reply = await client.recv()
    assert reply["error"]["code"] == -31010
    assert reply["error"]["data"] == {"error": "mcp_server_unavailable", "retryable": True}
    await client.close()


@pytest.mark.anyio
async def test_relay_forwards_modern_request_without_initialize(tmp_path: Path) -> None:
    from tests.mcp.raw_stdio import BROKER_ENV, MODERN_META

    env = {**os.environ, **BROKER_ENV, "PITWALL_MCP_RELAY_WAIT_SECONDS": "30"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "pitwall.mcp.relay",
        "--",
        sys.executable,
        "-m",
        "pitwall.mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    client = _Client(proc)
    await client.send(
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": MODERN_META}}
    )
    reply = await client.recv()
    assert reply["result"]["resultType"] == "complete"
    assert "2026-07-28" in reply["result"]["supportedVersions"]
    await client.close()


class _InProcess:
    """The relay run in this process, so tests can lower module constants and read its log."""

    def __init__(self, server: Sequence[str]) -> None:
        from pitwall.mcp.relay import Relay

        self.replies: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.logs: list[str] = []
        self.client = asyncio.StreamReader()
        self.relay = Relay(
            server,
            write=lambda data: self.replies.put_nowait(json.loads(data)),
            wait_seconds=10,
            backoff=(0.05, 0.1),
            log=self.logs.append,
        )
        self.task = asyncio.create_task(self.relay.run(self.client))

    def send(self, message: dict[str, Any]) -> None:
        self.client.feed_data((json.dumps(message) + "\n").encode())

    async def recv(self) -> dict[str, Any]:
        return await asyncio.wait_for(self.replies.get(), timeout=HANG_GUARD_SECS)

    async def close(self) -> None:
        self.client.feed_eof()
        assert await asyncio.wait_for(self.task, timeout=HANG_GUARD_SECS) == 0


def _fake(tmp_path: Path) -> list[str]:
    return [sys.executable, "-c", FAKE_SERVER, str(tmp_path / "count")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "line",
    ["42", "[]", '{"x":1}', '"text"', "null", '{"jsonrpc":"1.0","id":1,"result":{}}'],
)
async def test_relay_drops_valid_json_that_is_not_a_jsonrpc_message(
    tmp_path: Path, line: str
) -> None:
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "emit", "params": {"line": line}})
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    first = await relay.recv()
    second = await relay.recv()
    assert first == {"jsonrpc": "2.0", "id": 1, "result": {"generation": 1, "method": "emit"}}
    assert second["id"] == 2
    assert relay.replies.empty()
    await relay.close()


@pytest.mark.anyio
async def test_relay_logs_the_first_dropped_line_then_a_periodic_count(tmp_path: Path) -> None:
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "junkmany"})
    assert (await relay.recv())["id"] == 1
    await relay.close()
    assert relay.logs == [
        "pitwall mcp relay: dropped a non-MCP line from the server (1 dropped so far)",
        "pitwall mcp relay: dropped a non-MCP line from the server (100 dropped so far)",
        "pitwall mcp relay: dropped a non-MCP line from the server (200 dropped so far)",
    ]


@pytest.mark.anyio
async def test_relay_restarts_the_server_after_an_over_limit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.mcp import relay as relay_module

    monkeypatch.setattr(relay_module, "_LIMIT", 1024)
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert (await relay.recv())["result"]["generation"] == 1
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "big"})
    failed = await relay.recv()
    assert failed["id"] == 2
    assert failed["error"]["code"] == -31010
    assert failed["error"]["data"] == {"error": "mcp_server_restarted", "retryable": True}
    relay.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    after = await relay.recv()
    assert after["id"] == 3 and after["result"]["generation"] == 2
    await relay.close()
    assert any("exceeded the read limit" in entry for entry in relay.logs)
    assert not any("xxx" in entry for entry in relay.logs)


@pytest.mark.anyio
async def test_relay_fails_the_request_when_a_truncated_line_swallows_its_reply(
    tmp_path: Path,
) -> None:
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "fragment"})
    failed = await relay.recv()
    assert failed["id"] == 1
    assert failed["error"]["code"] == -31010
    assert failed["error"]["data"] == {"error": "mcp_server_restarted", "retryable": True}
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    after = await relay.recv()
    assert after["id"] == 2 and after["result"]["generation"] == 2
    await relay.close()
    assert any("damaged JSON-RPC framing" in entry for entry in relay.logs)


@pytest.mark.anyio
async def test_relay_restarts_when_unterminated_text_is_glued_to_a_real_reply(
    tmp_path: Path,
) -> None:
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "progress"})
    failed = await relay.recv()
    assert failed["id"] == 1 and failed["error"]["code"] == -31010
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert (await relay.recv())["result"]["generation"] == 2
    await relay.close()
    assert any("damaged JSON-RPC framing" in entry for entry in relay.logs)
    assert any("restarted the server after damaged output" in entry for entry in relay.logs)
    assert not any("exited with status" in entry for entry in relay.logs)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "line",
    [
        'DEBUG handling request {"jsonrpc": "2.0", "id": 9}',
        'DEBUG handling request {"jsonrpc": "2.0", "id": 1, "method": "emit"}',
        'DEBUG sent {"jsonrpc": "2.0", "method": "notifications/message"}',
        'DEBUG jsonrpc stub {"jsonrpc"',
    ],
)
async def test_relay_drops_a_log_line_that_mentions_jsonrpc_without_restarting(
    tmp_path: Path, line: str
) -> None:
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "emit", "params": {"line": line}})
    first = await relay.recv()
    assert first["result"] == {"generation": 1, "method": "emit"}
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert (await relay.recv())["result"]["generation"] == 1
    await relay.close()
    assert not any("damaged" in entry or "restarting" in entry for entry in relay.logs)


@pytest.mark.anyio
async def test_relay_survives_a_child_that_stops_reading_mid_write(tmp_path: Path) -> None:
    """A child that closed its stdin after ``_ready`` must not end the client session."""

    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert (await relay.recv())["result"]["generation"] == 1
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "closestdin"})
    assert (await relay.recv())["id"] == 2
    relay.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    failed = await asyncio.wait_for(relay.replies.get(), timeout=30)
    assert failed["id"] == 3
    assert failed["error"]["code"] == -31010
    assert failed["error"]["data"] == {"error": "mcp_server_restarted", "retryable": True}
    relay.send({"jsonrpc": "2.0", "id": 4, "method": "ping"})
    after = await asyncio.wait_for(relay.replies.get(), timeout=30)
    assert after["id"] == 4 and after["result"]["generation"] == 2
    assert not relay.task.done()
    await relay.close()
    assert any("restarted the server after the child's stdin closed" in e for e in relay.logs)
    assert not any("damaged output" in e for e in relay.logs)


@pytest.mark.anyio
async def test_relay_routes_a_client_request_that_reuses_the_old_replay_id(
    tmp_path: Path,
) -> None:
    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert (await relay.recv())["result"]["generation"] == 1
    relay.send({"jsonrpc": "2.0", "id": "pitwall-relay-replay", "method": "ping"})
    reply = await asyncio.wait_for(relay.replies.get(), timeout=30)
    assert reply["id"] == "pitwall-relay-replay"
    assert reply["result"] == {"generation": 1, "method": "ping"}
    await relay.close()


@pytest.mark.anyio
async def test_relay_replays_initialize_under_an_id_no_client_can_hold(tmp_path: Path) -> None:
    """The replay id is fresh per replay, and a replay reply is never forwarded."""

    relay = _InProcess(_fake(tmp_path))
    relay.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert (await relay.recv())["id"] == 1
    relay.send({"jsonrpc": "2.0", "id": 2, "method": "crash"})
    assert (await relay.recv())["error"]["code"] == -31010
    relay.send({"jsonrpc": "2.0", "id": "pitwall-relay-replay", "method": "ping"})
    reply = await asyncio.wait_for(relay.replies.get(), timeout=30)
    assert reply["id"] == "pitwall-relay-replay" and reply["result"]["generation"] == 2
    assert relay.replies.empty()
    await relay.close()
