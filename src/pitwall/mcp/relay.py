"""Restart-tolerant stdio relay for the Pitwall MCP server.

Harnesses start an MCP server once and do not reconnect when it dies. The broker's server
runs inside the API container (``docker exec … pitwall mcp serve``), so every container
restart used to end every agent's broker connection. The relay stays up for the harness,
restarts the server command when it exits, replays the MCP initialize handshake, and answers
requests that were in flight with a retryable error.

The server's stdout is line-framed JSON-RPC. A line is forwarded only when it parses as a JSON
object with ``"jsonrpc": "2.0"``; anything else (stray prints, ``42``, ``[]``, ``{"x": 1}``) is
dropped and counted, never forwarded. Framing damage cannot be repaired line by line, so the
relay restarts the server and answers every in-flight request with a retryable error when:

- a line is longer than the read limit (the relay logs a fixed message, never the content);
- a line that does not parse ends, from its last ``{"jsonrpc"`` onward, in a JSON-RPC response
  (no ``"method"``) whose id is a pending request: an unterminated fragment with no newline
  was joined to that reply by the line framing, so the reply is lost with it. Without the
  restart its request would wait forever, because requests have no response timeout (tool
  calls can legitimately run long). A stray log line that merely mentions ``jsonrpc`` (an
  echoed request, a notification, a response to an id nobody is waiting for) is dropped with
  no restart.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import stat
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

ERROR_CODE = -31010  # application-defined; outside the JSON-RPC reserved range (MCP 2026-07-28)
_REPLAY_ID_PREFIX = "pitwall-relay-replay-"
_LIMIT = 64 * 1024 * 1024
_DROP_LOG_EVERY = 100


def _key(request_id: Any) -> str:
    return json.dumps(request_id, sort_keys=True)


class Relay:
    def __init__(
        self,
        command: Sequence[str],
        *,
        write: Callable[[bytes], None],
        wait_seconds: float = 30.0,
        backoff: tuple[float, float] = (0.5, 10.0),
        log: Callable[[str], None] = lambda message: print(message, file=sys.stderr, flush=True),
    ) -> None:
        self._command = list(command)
        self._write = write
        self._wait_seconds = wait_seconds
        self._backoff = backoff
        self._log = log
        self._child: asyncio.subprocess.Process | None = None
        self._ready = asyncio.Event()
        self._closing = False
        self._init: dict[str, Any] | None = None
        self._initialized: dict[str, Any] | None = None
        self._pending: dict[str, Any] = {}
        self._replay: asyncio.Future[dict[str, Any]] | None = None
        self._replay_id: str | None = None
        self._dropped = 0
        self._restarting = False
        self._restart_cause = ""

    def _emit(self, message: dict[str, Any]) -> None:
        self._write((json.dumps(message, separators=(",", ":")) + "\n").encode())

    def _fail(self, request_id: Any, error: str, message: str) -> None:
        self._emit(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": ERROR_CODE,
                    "message": message,
                    "data": {"error": error, "retryable": True},
                },
            }
        )

    async def run(self, client: asyncio.StreamReader) -> int:
        supervisor = asyncio.create_task(self._supervise())
        try:
            while line := await client.readline():
                await self._from_client(line)
        finally:
            self._closing = True
            await self._stop_child()
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
        return 0

    async def _from_client(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self._log("pitwall mcp relay: dropped a non-JSON line from the client")
            return
        if not isinstance(message, dict):
            return
        method = message.get("method")
        if method == "initialize" and self._init is None:
            self._init = message
        elif method == "notifications/initialized":
            self._initialized = message
        is_request = method is not None and "id" in message
        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=self._wait_seconds)
            except TimeoutError:
                if is_request:
                    self._fail(
                        message["id"],
                        "mcp_server_unavailable",
                        "the Pitwall MCP server is not running; retry shortly",
                    )
                return
        child = self._child
        if child is None or child.stdin is None:
            if is_request:
                self._fail(
                    message["id"],
                    "mcp_server_unavailable",
                    "the Pitwall MCP server is not running; retry shortly",
                )
            return
        if is_request:
            self._pending[_key(message["id"])] = message["id"]
        try:
            child.stdin.write(line if line.endswith(b"\n") else line + b"\n")
            await child.stdin.drain()
        except BrokenPipeError, ConnectionResetError:
            # The child stopped reading between _ready and this write. The request is
            # already pending, so the supervisor answers it with the retryable restart error.
            self._abort_child(
                child, "the server stopped reading its input", "the child's stdin closed"
            )

    def _is_replay_reply(self, request_id: Any) -> bool:
        """Whether *request_id* is the id of the replay still waiting for its reply."""
        return (
            self._replay is not None
            and not self._replay.done()
            and self._replay_id is not None
            and request_id == self._replay_id
        )

    def _drop(self) -> None:
        """Count a dropped server line; log the first, then every ``_DROP_LOG_EVERY``th."""
        self._dropped += 1
        if self._dropped == 1 or self._dropped % _DROP_LOG_EVERY == 0:
            self._log(
                "pitwall mcp relay: dropped a non-MCP line from the server "
                f"({self._dropped} dropped so far)"
            )

    def _swallowed_reply(self, line: bytes) -> bool:
        """Whether an unparsable line ends in a response the framing glued onto a fragment."""
        start = line.rfind(b'{"jsonrpc"')
        if start < 0:
            return False
        try:
            tail = json.loads(line[start:])
        except ValueError:
            return False
        if not isinstance(tail, dict) or "method" in tail or "id" not in tail:
            return False
        return self._is_replay_reply(tail["id"]) or _key(tail["id"]) in self._pending

    def _abort_child(
        self, child: asyncio.subprocess.Process, reason: str, cause: str = "damaged output"
    ) -> None:
        """Restart the server: the supervisor fails the pending requests and starts a new child."""
        self._restarting = True
        self._restart_cause = cause
        self._log(f"pitwall mcp relay: {reason}; restarting the server")
        if self._replay is not None and not self._replay.done():
            self._replay.set_exception(ConnectionError("server output was corrupted"))
        if child.returncode is None:
            child.kill()

    async def _from_child(self, child: asyncio.subprocess.Process) -> None:
        assert child.stdout is not None
        while True:
            try:
                line = await child.stdout.readline()
            except ValueError:
                self._abort_child(child, "a server line exceeded the read limit")
                return
            if not line:
                return
            try:
                message = json.loads(line)
            except ValueError:
                if self._swallowed_reply(line):
                    self._abort_child(child, "a server line was damaged JSON-RPC framing")
                    return
                self._drop()
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                self._drop()
                continue
            if "method" not in message and self._is_replay_reply(message.get("id")):
                assert self._replay is not None
                self._replay.set_result(message)
                continue
            if "method" not in message and "id" in message:
                self._pending.pop(_key(message["id"]), None)
            self._write(line if line.endswith(b"\n") else line + b"\n")

    async def _start(self) -> asyncio.subprocess.Process:
        child = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            limit=_LIMIT,
        )
        return child

    async def _replay_handshake(self, child: asyncio.subprocess.Process) -> None:
        assert child.stdin is not None and self._init is not None
        self._replay = asyncio.get_running_loop().create_future()
        # A fresh id per replay, never one a pending client request holds, so a client reply
        # is never taken for the replay's and a replay reply never reaches the client.
        self._replay_id = f"{_REPLAY_ID_PREFIX}{uuid.uuid4().hex}"
        while _key(self._replay_id) in self._pending:
            self._replay_id = f"{_REPLAY_ID_PREFIX}{uuid.uuid4().hex}"
        child.stdin.write((json.dumps({**self._init, "id": self._replay_id}) + "\n").encode())
        await child.stdin.drain()
        await asyncio.wait_for(self._replay, timeout=self._wait_seconds)
        if self._initialized is not None:
            child.stdin.write((json.dumps(self._initialized) + "\n").encode())
            await child.stdin.drain()

    async def _supervise(self) -> None:
        loop = asyncio.get_running_loop()
        delay = self._backoff[0]
        while not self._closing:
            try:
                child = await self._start()
            except OSError as exc:
                self._log(f"pitwall mcp relay: cannot start the server: {exc}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff[1])
                continue
            started = loop.time()
            self._child = child
            self._restarting = False
            reader = asyncio.create_task(self._from_child(child))
            try:
                if self._init is not None:
                    await self._replay_handshake(child)
                self._ready.set()
                code = await child.wait()
            except TimeoutError, ConnectionError, BrokenPipeError:
                if child.returncode is None:
                    child.kill()
                code = await child.wait()
            finally:
                self._ready.clear()
                self._child = None
            await asyncio.gather(reader, return_exceptions=True)
            for request_id in list(self._pending.values()):
                self._fail(
                    request_id,
                    "mcp_server_restarted",
                    "the Pitwall MCP server restarted; retry the request",
                )
            self._pending.clear()
            if self._closing:
                return
            if self._restarting:
                self._log(f"pitwall mcp relay: restarted the server after {self._restart_cause}")
            else:
                self._log(f"pitwall mcp relay: server exited with status {code}; restarting")
            if loop.time() - started > 30:
                delay = self._backoff[0]
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff[1])

    async def _stop_child(self) -> None:
        child = self._child
        if child is None or child.returncode is not None:
            return
        if child.stdin is not None:
            child.stdin.close()
        try:
            await asyncio.wait_for(child.wait(), timeout=5)
        except TimeoutError:
            child.kill()
            await child.wait()


DEFAULT_WAIT_SECONDS = 30.0


def wait_seconds_from_env(environ: Mapping[str, str]) -> float:
    """PITWALL_MCP_RELAY_WAIT_SECONDS as a positive number; anything else uses the default.

    The relay exists to keep the harness connected, so a bad value warns (naming the
    variable, not echoing the value) instead of stopping it.
    """
    raw = environ.get("PITWALL_MCP_RELAY_WAIT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_WAIT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        print(
            "pitwall mcp relay: PITWALL_MCP_RELAY_WAIT_SECONDS must be a positive number of "
            f"seconds; using {DEFAULT_WAIT_SECONDS:g}",
            file=sys.stderr,
        )
        return DEFAULT_WAIT_SECONDS
    return value


def _pollable(fd: int) -> bool:
    """Whether the event loop can watch ``fd``: pipes, sockets, and terminals, but not
    ``/dev/null`` or regular files, which epoll refuses."""
    mode = os.fstat(fd).st_mode
    return stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode) or os.isatty(fd)


def _feed_from_thread(reader: asyncio.StreamReader, loop: asyncio.AbstractEventLoop) -> None:
    """Read unpollable stdin on a thread so end of input still reaches the relay."""

    fd = sys.stdin.fileno()

    def pump() -> None:
        while chunk := os.read(fd, 65536):
            loop.call_soon_threadsafe(reader.feed_data, chunk)
        loop.call_soon_threadsafe(reader.feed_eof)

    threading.Thread(target=pump, name="pitwall-mcp-relay-stdin", daemon=True).start()


async def _main(command: Sequence[str]) -> int:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_LIMIT)
    if _pollable(sys.stdin.fileno()):
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    else:
        _feed_from_thread(reader, loop)
    out = sys.stdout.buffer

    def write(data: bytes) -> None:
        out.write(data)
        out.flush()

    wait = wait_seconds_from_env(os.environ)
    return await Relay(command, write=write, wait_seconds=wait).run(reader)


def run_relay(command: Sequence[str]) -> int:
    args = list(command)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        print("usage: pitwall mcp relay -- COMMAND [ARGS...]", file=sys.stderr)
        return 64
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(run_relay(sys.argv[1:]))
